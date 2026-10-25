"""温度采集汇总。

三类来源,优先级从高到低:

1. **CPU 温度**:来自 PawnIO 读 MSR(见 cpu.py),是真实的封装/每核温度。
2. **GPU 温度**:来自 nvidia-smi。
3. **硬盘温度 / SMART**:来自 PowerShell 的 Get-StorageReliabilityCounter,
   底层走 SMART,不需要管理员权限也不需要额外驱动。
4. **ACPI 热区**:兜底。实测在多数主板上这个值不随负载变化,只能当参考,
   所以只标注为"近似",并且不做高频采样。
"""

from __future__ import annotations

import time
from typing import Optional

from ..models import PhysicalDisk, ThermalSnapshot
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
    """通过 Windows 的存储可靠性计数器读取硬盘 SMART 信息。"""

    def __init__(self) -> None:
        self.available = True
        self.error: Optional[str] = None
        self._last: list[PhysicalDisk] = []
        self._last_refresh = 0.0
        self._min_interval = 10.0  # SMART 查询较慢,10 秒刷新一次足够

    def sample(self, force: bool = False) -> list[PhysicalDisk]:
        now = time.monotonic()
        if not force and (now - self._last_refresh) < self._min_interval:
            return self._last
        self._last_refresh = now

        data = run_powershell_json(_PHYSICAL_DISK_SCRIPT, timeout=25)
        if data is None:
            self.error = "读取磁盘 SMART 信息失败"
            return self._last

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

        self._last = disks
        self.error = None
        return disks


class AcpiThermalSource:
    """ACPI 热区温度,仅作为兜底参考。"""

    def __init__(self) -> None:
        self._last: list[tuple[str, float]] = []
        self._last_refresh = 0.0

    def sample(self, force: bool = False) -> list[tuple[str, float]]:
        now = time.monotonic()
        if not force and (now - self._last_refresh) < 30.0:
            return self._last
        self._last_refresh = now

        data = run_powershell_json(_ACPI_SCRIPT, timeout=15)
        if data is None:
            return self._last
        zones: list[tuple[str, float]] = []
        for item in as_list(data):
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or "").strip()
            temp = item.get("TempC")
            if name and isinstance(temp, (int, float)):
                zones.append((name, float(temp)))
        self._last = zones
        return zones


class ThermalCollector:
    """汇总所有温度来源。"""

    def __init__(self, enable_acpi: bool = True) -> None:
        self.smart = SmartDiskSource()
        self.acpi = AcpiThermalSource() if enable_acpi else None

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
