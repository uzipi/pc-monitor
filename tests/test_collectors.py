"""采集器冒烟测试。

原则:这些用例不追求"必须有值",而是保证接口稳定、不抛异常、
并且拿到的数值在物理上说得过去。依赖硬件的用例在硬件缺失时自动跳过。
"""

from __future__ import annotations

import pytest

from pcmonitor.collectors import (
    CpuCollector,
    DiskCollector,
    GpuCollector,
    MemoryCollector,
    NetworkCollector,
    ProcessCollector,
    ThermalCollector,
)


@pytest.fixture(scope="module")
def memory():
    return MemoryCollector()


def test_memory_totals_are_sane(memory):
    snap = memory.sample()
    assert snap.total_bytes > 0
    assert 0 <= snap.percent <= 100
    assert snap.used_bytes <= snap.total_bytes


def test_commit_info_matches_physical_memory(memory):
    """提交上限应该不小于物理内存(Windows 的提交量 = 内存 + 页面文件)。"""
    snap = memory.sample()
    if snap.committed_bytes is None or snap.commit_limit_bytes is None:
        pytest.skip("GetPerformanceInfo 不可用")
    assert snap.commit_limit_bytes >= snap.total_bytes
    assert snap.committed_bytes <= snap.commit_limit_bytes


def test_dimms_have_capacity(memory):
    dimms = memory.sample_dimms(force=True)
    if not dimms:
        pytest.skip("没有读到内存条信息")
    for dimm in dimms:
        assert dimm.capacity_bytes > 0
        assert dimm.locator


def test_cpu_collector_returns_snapshot():
    cpu = CpuCollector(enable_msr=False)
    try:
        snap = cpu.sample()
        assert 0 <= snap.usage_percent <= 100
        assert snap.logical_cores >= 1
    finally:
        cpu.close()


def test_disk_collector_lists_partitions():
    disks = DiskCollector().sample()
    assert isinstance(disks, list)
    for disk in disks:
        if disk.total_bytes:
            assert 0 <= disk.percent <= 100


def test_network_collector_returns_interfaces():
    net = NetworkCollector().sample()
    assert isinstance(net.interfaces, list)
    for iface in net.interfaces:
        assert iface.rx_bytes_per_sec >= 0
        assert iface.tx_bytes_per_sec >= 0


def test_gpu_collector_handles_missing_nvidia_smi():
    gpus = GpuCollector().sample()
    assert isinstance(gpus, list)
    for gpu in gpus:
        if gpu.mem_total_bytes:
            assert 0 <= gpu.mem_percent <= 100


def test_process_collector_normalizes_cpu_percent():
    """进程 CPU 占用要归一化到 0~100,不能出现几百上千。"""
    proc = ProcessCollector()
    top = proc.sample(top=5)
    for stat in top.by_cpu:
        assert 0 <= stat.cpu_percent <= 100
    assert top.total_processes > 0


def test_thermal_collector_reads_smart():
    disks = ThermalCollector(enable_acpi=False).sample_disks()
    for disk in disks:
        assert disk.size_bytes >= 0
        if disk.temp_c is not None:
            assert -20 < disk.temp_c < 120
