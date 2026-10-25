"""界面渲染测试。

重点是"任何情况下都不能炸",以及"每一行的显示宽度都要对得上",
否则终端里会变成一锅粥。
"""

from __future__ import annotations

import pytest

from pcmonitor.models import (
    CpuSnapshot,
    DimmInfo,
    DiskIo,
    DiskSnapshot,
    GpuSnapshot,
    HostSnapshot,
    MemorySnapshot,
    NetworkIface,
    NetworkSnapshot,
    PhysicalDisk,
    ProcessStat,
    ProcessTop,
    Snapshot,
    ThermalSnapshot,
)
from pcmonitor.ui import Dashboard, Theme
from pcmonitor.util import Style, dwidth


def _snapshot() -> Snapshot:
    snap = Snapshot()
    snap.timestamp = 1_700_000_000.0
    snap.host = HostSnapshot(hostname="测试机", os_name="Windows 11", uptime_seconds=90061)

    snap.cpu = CpuSnapshot(
        usage_percent=42.5,
        per_core_percent=[10.0, 20.0] * 10,
        freq_mhz=5300.0,
        max_freq_mhz=5200.0,
        logical_cores=20,
        package_temp_c=71.5,
        core_temps_c=[60.0 + i for i in range(20)],
        core_max_temp_c=79.0,
        core_avg_temp_c=69.5,
        tjmax_c=100.0,
        voltage_v=1.28,
        package_power_w=95.0,
        core_power_w=88.0,
        graphics_power_w=0.0,
        sensor_source="MSR",
    )

    snap.memory = MemorySnapshot(
        total_bytes=16 * 1024**3,
        used_bytes=9 * 1024**3,
        available_bytes=7 * 1024**3,
        percent=57.0,
        swap_total_bytes=28 * 1024**3,
        swap_used_bytes=1024**3,
        committed_bytes=19 * 1024**3,
        commit_limit_bytes=44 * 1024**3,
        commit_peak_bytes=40 * 1024**3,
        dimms=[
            DimmInfo(
                locator="DDR4-A2",
                manufacturer="Kingston",
                part_number="LV26D4U9S8HJ-8",
                capacity_bytes=8 * 1024**3,
                speed_mhz=2667,
                configured_speed_mhz=2666,
                smbios_type="DDR4",
                form_factor="SODIMM",
                voltage_v=1.2,
            )
        ],
    )

    snap.gpus = [
        GpuSnapshot(
            name="NVIDIA GeForce RTX 5060 Ti",
            index=0,
            usage_percent=95.0,
            temp_c=71.0,
            fan_percent=60.0,
            power_w=178.0,
            freq_mhz=2620.0,
            mem_freq_mhz=14001.0,
            mem_used_bytes=5539 * 1024**2,
            mem_total_bytes=16311 * 1024**2,
            mem_percent=34.0,
            utilization_gpu_percent=95.0,
            utilization_mem_percent=33.0,
            pcie_gen_current=5,
            pcie_gen_max=5,
            pcie_width_current=8,
            pcie_width_max=16,
        )
    ]

    snap.physical_disks = [
        PhysicalDisk(
            device_id=0,
            model="ST4000NM0035",
            bus_type="SATA",
            media_type="HDD",
            size_bytes=4 * 1024**4,
            temp_c=43.0,
            health="Healthy",
            power_on_hours=11254,
            wear_percent=0.0,
        )
    ]
    snap.disks = [
        DiskSnapshot(
            device="C:\\",
            mountpoint="C:\\",
            filesystem="NTFS",
            total_bytes=200 * 1024**3,
            used_bytes=95 * 1024**3,
            percent=47.4,
        )
    ]
    snap.disk_io = [
        DiskIo(name="PhysicalDrive0", read_bytes_per_sec=1024.0, write_bytes_per_sec=2048.0)
    ]
    snap.network = NetworkSnapshot(
        interfaces=[
            NetworkIface(
                name="WLAN",
                rx_bytes_per_sec=36 * 1024,
                tx_bytes_per_sec=5 * 1024,
            )
        ],
        rx_bytes_per_sec=36 * 1024,
        tx_bytes_per_sec=5 * 1024,
        physical_rx_bytes_per_sec=36 * 1024,
        physical_tx_bytes_per_sec=5 * 1024,
    )
    snap.thermal = ThermalSnapshot(cpu_package_c=71.5, gpu_temp_c=71.0)
    snap.processes = ProcessTop(
        by_cpu=[
            ProcessStat(pid=1234, name="游戏.exe", cpu_percent=22.6, memory_bytes=2 * 1024**3)
        ],
        by_memory=[
            ProcessStat(pid=1234, name="游戏.exe", cpu_percent=22.6, memory_bytes=2 * 1024**3)
        ],
        total_processes=195,
        total_threads=3328,
    )
    return snap


@pytest.mark.parametrize("width", [80, 100, 160])
@pytest.mark.parametrize("color", [False, True])
def test_render_lines_fit_width(width: int, color: bool):
    theme = Theme(style=Style(enabled=color), width=width)
    dash = Dashboard(theme, top=5)
    lines = dash.render(_snapshot(), ["一条警告"])
    for line in lines:
        assert dwidth(line) <= width, repr(line)


def test_render_contains_key_metrics():
    theme = Theme(style=Style(enabled=False), width=120)
    dash = Dashboard(theme, top=5)
    text = "\n".join(dash.render(_snapshot()))
    assert "CPU" in text
    assert "71.5" in text or "72" in text  # 封装温度
    assert "RTX 5060 Ti" in text
    assert "ST4000NM0035" in text
    assert "11254h" in text  # 通电时长
    assert "PCIe" in text
    assert "不可用" in text  # DIMM 温度/时序要明说


def test_render_handles_empty_snapshot():
    """所有字段都拿不到时也不能崩。"""
    theme = Theme(style=Style(enabled=True), width=80)
    dash = Dashboard(theme)
    lines = dash.render(Snapshot())
    assert lines
    for line in lines:
        assert dwidth(line) <= 80
