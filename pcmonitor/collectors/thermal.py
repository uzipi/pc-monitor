"""温度采集汇总。

三类来源,优先级从高到低:

1. **CPU 温度**:来自 PawnIO 读 MSR(见 cpu.py),是真实的封装/每核温度。
2. **GPU 温度**:来自 NVML(见 nvml.py),拿不到才退回 nvidia-smi。
3. **硬盘温度 / SMART**:来自 WMI 的存储可靠性计数器,底层走 SMART。
   型号/总线/健康度普通用户就能读,温度和通电时长要管理员权限。
4. **ACPI 热区**:兜底。实测在多数主板上这个值不随负载变化,只能当参考,
   所以只标注为"近似",并且不做高频采样。

3 和 4 都要拉起一个 PowerShell 进程(一次 0.2~0.6 秒)。放在采集路径里
会让界面每隔几秒就卡一下,所以统一交给后台线程刷新,前台只读缓存。
"""

from __future__ import annotations

from typing import Optional

from ..models import PhysicalDisk, ThermalSnapshot
from .base import BackgroundRefresher
from .powershell import as_list, run_powershell_json

# 取物理磁盘的 SMART 信息(温度、健康度、通电时长、磨损)
#
# 这里刻意不用 `Get-PhysicalDisk | Get-StorageReliabilityCounter`,
# 那条老路实测要 1.2 秒墙钟 / 0.9 秒 CPU(一个 PowerShell 进程自己就烧掉将近
# 一秒 CPU),而且**非管理员身份下直接报错**,什么都读不到。
#
# 换成直接问 WMI:
# * `MSFT_PhysicalDisk` 拿型号/总线/容量/健康度 —— 普通用户也能读;
# * `MSFT_StorageReliabilityCounter`(靠 MSFT_PhysicalDiskToStorageReliabilityCounter
#   关联)拿温度/通电时长/磨损 —— 这一项要管理员。
#
# 同样的数据只要 0.25 秒 CPU,快了 3~4 倍,于是硬盘温度可以 10 秒刷一次
# 而不是 30 秒,总体 CPU 反而比原来更低。
_PHYSICAL_DISK_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$busNames = @{
    0='未知'; 1='SCSI'; 2='ATAPI'; 3='ATA/IDE'; 4='1394'; 5='SSA'; 6='光纤通道';
    7='USB'; 8='RAID'; 9='iSCSI'; 10='SAS'; 11='SATA'; 12='SD'; 13='MMC';
    14='虚拟磁盘'; 15='文件虚拟磁盘'; 16='存储空间'; 17='NVMe'; 18='SCM'; 19='UFS'
}
$mediaNames = @{ 0=''; 3='HDD'; 4='SSD' }
$healthNames = @{ 0='Healthy'; 1='Warning'; 2='Unhealthy' }
$out = @()
foreach ($d in (Get-CimInstance -Namespace root/Microsoft/Windows/Storage -ClassName MSFT_PhysicalDisk)) {
    $r = $null
    try { $r = Get-CimAssociatedInstance -InputObject $d -ResultClassName MSFT_StorageReliabilityCounter -ErrorAction Stop } catch {}
    $bus = [int]$d.BusType
    $media = [int]$d.MediaType
    $health = [int]$d.HealthStatus
    $out += [PSCustomObject]@{
        DeviceId = [int]$d.DeviceId
        Model    = [string]$d.FriendlyName
        Bus      = $(if ($busNames.ContainsKey($bus)) { $busNames[$bus] } else { '' })
        Media    = $(if ($mediaNames.ContainsKey($media)) { $mediaNames[$media] } else { '' })
        Size     = [int64]$d.Size
        Health   = $(if ($healthNames.ContainsKey($health)) { $healthNames[$health] } else { $null })
        TempC    = $(if ($r) { $r.Temperature } else { $null })
        Hours    = $(if ($r) { $r.PowerOnHours } else { $null })
        Wear     = $(if ($r) { $r.Wear } else { $null })
    }
}
$out | ConvertTo-Json -Compress
"""

# ACPI 热区(兜底,基本不可信)
_ACPI_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = @()
foreach ($z in (Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature)) {
    $out += [PSCustomObject]@{
        Name  = [string]$z.InstanceName
        TempC = [math]::Round($z.CurrentTemperature / 10 - 273.15, 1)
    }
}
$out | ConvertTo-Json -Compress
"""


class SmartDiskSource:
    """通过 Windows 的存储可靠性计数器读取硬盘 SMART 信息。

    PowerShell 查询约 0.25 秒 CPU,由后台线程每 10 秒刷新一次;
    `sample()` 只是读缓存,不会阻塞界面。

    间隔定 10 秒是因为查询本身已经变便宜了(见 `_PHYSICAL_DISK_SCRIPT` 的注释):
    每 10 秒一次的开销比原来"每 30 秒一次但贵 4 倍"还低,硬盘温度却能跟着负载变化。
    """

    def __init__(self, interval: float = 10.0) -> None:
        self.available = True
        self._refresher = BackgroundRefresher(
            self._query, interval=interval, name="smart-disk"
        )

    # ---------------------------------------------------------- 后台刷新

    def _query(self) -> list[PhysicalDisk]:
        data = run_powershell_json(_PHYSICAL_DISK_SCRIPT, timeout=25)
        if data is None:
            raise RuntimeError("读取磁盘 SMART 信息失败")

        disks: list[PhysicalDisk] = []
        for item in as_list(data):
            if not isinstance(item, dict):
                continue
            disk = PhysicalDisk()
            disk.device_id = int(item.get("DeviceId") or 0)
            disk.model = str(item.get("Model") or "").strip()
            disk.bus_type = str(item.get("Bus") or "").strip()
            disk.media_type = str(item.get("Media") or "").strip()
            disk.size_bytes = int(item.get("Size") or 0)
            disk.health = str(item.get("Health") or "").strip() or None
            temp = item.get("TempC")
            disk.temp_c = float(temp) if isinstance(temp, (int, float)) else None
            hours = item.get("Hours")
            disk.power_on_hours = int(hours) if isinstance(hours, (int, float)) else None
            wear = item.get("Wear")
            disk.wear_percent = float(wear) if isinstance(wear, (int, float)) else None
            disks.append(disk)
        return disks

    # ---------------------------------------------------------- 对外接口

    def start(self) -> None:
        self._refresher.start()

    def stop(self) -> None:
        self._refresher.stop()

    def wait_ready(self, timeout: float = 3.0) -> bool:
        return self._refresher.wait_ready(timeout)

    @property
    def error(self) -> Optional[str]:
        return self._refresher.get()[1]

    def sample(self, force: bool = False) -> list[PhysicalDisk]:
        if force:
            self._refresher.refresh_now()
        disks, _ = self._refresher.get()
        return disks or []


class AcpiThermalSource:
    """ACPI 热区温度,仅作为兜底参考。同样放到后台刷新。

    间隔给到 120 秒:这个值本身就不随负载变化,基本只是个静态参考,
    没必要为它频繁拉起 PowerShell。
    """

    def __init__(self, interval: float = 120.0) -> None:
        self._refresher = BackgroundRefresher(
            self._query, interval=interval, name="acpi-thermal"
        )

    def _query(self) -> list[tuple[str, float]]:
        data = run_powershell_json(_ACPI_SCRIPT, timeout=15)
        if data is None:
            return []
        zones: list[tuple[str, float]] = []
        for item in as_list(data):
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or "").strip()
            temp = item.get("TempC")
            if name and isinstance(temp, (int, float)):
                zones.append((name, float(temp)))
        return zones

    def start(self) -> None:
        self._refresher.start()

    def stop(self) -> None:
        self._refresher.stop()

    def wait_ready(self, timeout: float = 3.0) -> bool:
        return self._refresher.wait_ready(timeout)

    def sample(self, force: bool = False) -> list[tuple[str, float]]:
        if force:
            self._refresher.refresh_now()
        zones, _ = self._refresher.get()
        return zones or []


class ThermalCollector:
    """汇总所有温度来源。"""

    def __init__(self, enable_acpi: bool = True) -> None:
        self.smart = SmartDiskSource()
        self.acpi = AcpiThermalSource() if enable_acpi else None

    def start(self) -> None:
        """启动后台刷新线程。"""
        self.smart.start()
        if self.acpi:
            self.acpi.start()

    def stop(self) -> None:
        self.smart.stop()
        if self.acpi:
            self.acpi.stop()

    def sample(
        self,
        cpu_package_c: Optional[float] = None,
        cpu_cores: Optional[list] = None,
        gpu_temp_c: Optional[float] = None,
        gpu_source: str = "",
    ) -> ThermalSnapshot:
        snap = ThermalSnapshot()

        if cpu_package_c is not None:
            snap.cpu_package_c = cpu_package_c
            snap.sources["cpu"] = "MSR"
        if cpu_cores:
            snap.cpu_cores = list(cpu_cores)
        if gpu_temp_c is not None:
            snap.gpu_temp_c = gpu_temp_c
            snap.sources["gpu"] = gpu_source or "nvidia-smi"

        if self.acpi:
            zones = self.acpi.sample()
            if zones:
                snap.acpi_zones = zones
                if snap.cpu_package_c is None:
                    # ACPI 热区不随负载变化,只能当粗略参考
                    snap.cpu_package_c = zones[0][1]
                    snap.sources["cpu"] = "ACPI(近似)"

        return snap

    def sample_disks(self) -> list[PhysicalDisk]:
        return self.smart.sample()
