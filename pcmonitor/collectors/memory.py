"""内存采集:物理内存、交换分区、Windows 提交量、内存条信息。"""

from __future__ import annotations

import ctypes
from typing import Optional

import psutil

from ..models import DimmInfo, MemorySnapshot
from .base import BackgroundRefresher
from .powershell import as_list, run_powershell_json

# SMBIOS 内存类型枚举
_SMBIOS_TYPES = {
    20: "DDR",
    21: "DDR2",
    22: "DDR2 FB-DIMM",
    24: "DDR3",
    26: "DDR4",
    34: "DDR5",
    35: "LPDDR4",
    36: "LPDDR5",
}

# SMBIOS 内存形态枚举(Win32_PhysicalMemory.FormFactor)
_FORM_FACTORS = {
    0: "未知",
    1: "其它",
    3: "DIMM",
    4: "SIMM",
    5: "TSOP",
    6: "Razor",
    7: "RIMM",
    8: "SODIMM",
    9: "SRIMM",
    10: "FB-DIMM",
    11: "Die",
    12: "ZIF",
    13: "Exposed Card",
    14: "Flash",
    15: "DIP",
    17: "SOG",
    18: "SOB",
    19: "CSP",
    20: "SOTP",
    21: "STAMP",
    22: "SODIMM",
    24: "SODIMM",
}

# 读取内存条信息(SMBIOS)
_DIMM_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = @()
foreach ($m in (Get-CimInstance Win32_PhysicalMemory)) {
    $out += [PSCustomObject]@{
        Locator   = [string]$m.DeviceLocator
        Maker     = [string]$m.Manufacturer
        Part      = [string]$m.PartNumber
        Capacity  = [int64]$m.Capacity
        Speed     = $m.Speed
        Configured = $m.ConfiguredClockSpeed
        Type      = $m.SMemoryType
        MemoryType = $m.MemoryType
        Form      = $m.FormFactor
        Voltage   = $m.ConfiguredVoltage
        Serial    = [string]$m.SerialNumber
    }
}
$out | ConvertTo-Json -Compress
"""


def _guess_dimm_type(locator: str) -> Optional[str]:
    """部分主板(如本机)不上报 SMBIOS 内存类型,但插槽名里带着代数,
    例如 "DDR4-A2" / "DIMM_B2"。类型拿不到时至少别把这条信息丢掉。"""
    upper = (locator or "").upper()
    for key in ("DDR5", "DDR4", "DDR3", "DDR2", "LPDDR5", "LPDDR4", "LPDDR3"):
        if key in upper:
            return key
    return None


class _PerformanceInformation(ctypes.Structure):
    """对应内核实际使用的 PERFORMANCE_INFORMATION 结构。

    注意两点坑:
    1. SDK 头文件里的版本只有 88 字节,但内核里的结构是 104 字节
       (多出 PoolPaged/PoolNonpaged 两个字段),cb 传 88 会直接
       ERROR_BAD_LENGTH 失败。
    2. 内存类字段的单位是**页**而不是字节,要用 PageSize 换算。
    """

    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCacheTotal", ctypes.c_size_t),
        ("SystemCacheAvailable", ctypes.c_size_t),
        ("PoolPagedTotal", ctypes.c_size_t),
        ("PoolNonpagedTotal", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_uint32),
        ("ProcessCount", ctypes.c_uint32),
        ("ThreadCount", ctypes.c_uint32),
    ]


def get_performance_info() -> Optional[_PerformanceInformation]:
    """调用 K32GetPerformanceInfo,拿不到就返回 None。

    用 kernel32 的 K32 前缀版本而不是 psapi.dll 的转发层,行为一致。
    """
    try:
        fn = ctypes.WinDLL("kernel32", use_last_error=True).K32GetPerformanceInfo
        fn.argtypes = [ctypes.POINTER(_PerformanceInformation), ctypes.c_uint32]
        fn.restype = ctypes.c_bool
        info = _PerformanceInformation()
        size = ctypes.sizeof(_PerformanceInformation)
        info.cb = size
        if not fn(ctypes.byref(info), size):
            return None
        return info
    except Exception:
        return None


# 兼容旧名字
_get_performance_info = get_performance_info


def _get_commit_info() -> tuple[Optional[int], Optional[int]]:
    """取提交量与提交上限(字节)。

    这是 Windows 上判断内存压力的关键指标:物理内存用完之前,
    提交量就已经先触顶,随后开始大量换页。
    """
    info = _get_performance_info()
    if info is None or not info.PageSize:
        return None, None
    page = int(info.PageSize)
    return int(info.CommitTotal) * page, int(info.CommitLimit) * page


class MemoryCollector:
    def __init__(self) -> None:
        # SMBIOS 查询要拉起 PowerShell,约 1 秒,放后台线程刷新
        self._dimm_refresher = BackgroundRefresher(
            self._query_dimms, interval=60.0, name="dimm-info"
        )

    def start(self) -> None:
        self._dimm_refresher.start()

    def stop(self) -> None:
        self._dimm_refresher.stop()

    def wait_ready(self, timeout: float = 3.0) -> bool:
        return self._dimm_refresher.wait_ready(timeout)

    def sample(self) -> MemorySnapshot:
        snap = MemorySnapshot()
        try:
            vm = psutil.virtual_memory()
            snap.total_bytes = int(vm.total)
            snap.used_bytes = int(vm.used)
            snap.available_bytes = int(vm.available)
            snap.percent = float(vm.percent)
        except Exception:
            pass

        try:
            sw = psutil.swap_memory()
            snap.swap_total_bytes = int(sw.total)
            snap.swap_used_bytes = int(sw.used)
        except Exception:
            pass

        snap.committed_bytes, snap.commit_limit_bytes = _get_commit_info()
        info = _get_performance_info()
        if info is not None and info.PageSize:
            snap.commit_peak_bytes = int(info.CommitPeak) * int(info.PageSize)
        snap.dimms = self.sample_dimms()
        return snap

    def _query_dimms(self) -> list[DimmInfo]:
        data = run_powershell_json(_DIMM_SCRIPT, timeout=20)
        if data is None:
            raise RuntimeError("读取内存条信息失败")

        dimms: list[DimmInfo] = []
        for item in as_list(data):
            if not isinstance(item, dict):
                continue
            dimm = DimmInfo()
            dimm.locator = str(item.get("Locator") or "").strip()
            dimm.manufacturer = str(item.get("Maker") or "").strip()
            dimm.part_number = str(item.get("Part") or "").strip()
            dimm.capacity_bytes = int(item.get("Capacity") or 0)
            speed = item.get("Speed")
            dimm.speed_mhz = int(speed) if isinstance(speed, (int, float)) else None
            configured = item.get("Configured")
            dimm.configured_speed_mhz = (
                int(configured) if isinstance(configured, (int, float)) else None
            )
            type_code = item.get("Type")
            if not isinstance(type_code, (int, float)) or type_code == 0:
                type_code = item.get("MemoryType")
            dimm.smbios_type = None
            if isinstance(type_code, (int, float)):
                dimm.smbios_type = _SMBIOS_TYPES.get(int(type_code))
            if dimm.smbios_type is None:
                dimm.smbios_type = _guess_dimm_type(dimm.locator)
            form = item.get("Form")
            if isinstance(form, (int, float)):
                dimm.form_factor = _FORM_FACTORS.get(int(form))
            else:
                dimm.form_factor = str(form or "").strip() or None
            voltage = item.get("Voltage")
            if isinstance(voltage, (int, float)) and voltage > 0:
                dimm.voltage_v = float(voltage) / 1000.0
            dimms.append(dimm)
        return dimms

    def sample_dimms(self, force: bool = False) -> list[DimmInfo]:
        """内存条信息(频率/型号等),来自 SMBIOS。

        查询较慢,交给后台线程每 60 秒刷新一次,这里只读缓存。
        """
        if force:
            self._dimm_refresher.refresh_now()
        dimms, _ = self._dimm_refresher.get()
        return dimms or []
