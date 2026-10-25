"""温度采集汇总。

三类来源,优先级从高到低:

1. **CPU 温度**:来自 PawnIO 读 MSR(见 cpu.py),是真实的封装/每核温度。
2. **GPU 温度**:来自 nvidia-smi。
3. **硬盘温度 / SMART**:来自 PowerShell 的 Get-StorageReliabilityCounter,
   底层走 SMART,不需要管理员权限也不需要额外驱动。
4. **ACPI 热区**:兜底。实测在多数主板上这个值不随负载变化,只能当参考,
   所以只标注为"近似",并且不做高频采样。

3 和 4 都要拉起一个 PowerShell 进程,实测一次要 1 秒多。放在采集路径里
会让界面每隔几秒就卡一下,所以统一交给后台线程刷新,前台只读缓存。
"""

from __future__ import annotations

from typing import Optional

from ..models import PhysicalDisk, ThermalSnapshot
from .base import BackgroundRefresher
from .powershell import as_list, run_powershell_json

# 取物理磁盘的 SMART 信息(温度、健康度、通电时长、磨损)
_PHYSICAL_DISK_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = @()
foreach ($d in (Get-PhysicalDisk)) {
    $r = $null
    try { $r = $d | Get-StorageReliabilityCounter -ErrorAction Stop } catch {}
    $out += [PSCustomObject]@{
        DeviceId = [int]$d.DeviceId
        Model    = [string]$d.FriendlyName
        Bus      = [string]$d.BusType
        Media    = [string]$d.MediaType
        Size     = [int64]$d.Size
        Health   = [string]$d.HealthStatus
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

    PowerShell 查询约 1 秒,由后台线程每 10 秒刷新一次;
    `sample()` 只是读缓存,不会阻塞界面。
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
    """ACPI 热区温度,仅作为兜底参考。同样放到后台刷新。"""

    def __init__(self, interval: float = 30.0) -> None:
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
    ) -> ThermalSnapshot:
        snap = ThermalSnapshot()

        if cpu_package_c is not None:
            snap.cpu_package_c = cpu_package_c
            snap.sources["cpu"] = "MSR"
        if cpu_cores:
            snap.cpu_cores = list(cpu_cores)
        if gpu_temp_c is not None:
            snap.gpu_temp_c = gpu_temp_c
            snap.sources["gpu"] = "nvidia-smi"

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
