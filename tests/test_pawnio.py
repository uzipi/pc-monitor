"""PawnIO / MSR 相关测试。

解码逻辑是纯计算,一定可以测;真正读硬件的部分在没装驱动的机器上自动跳过。
"""

from __future__ import annotations

import pytest

from pcmonitor.collectors.cpu import MsrSource


def test_temperature_requires_valid_bit():
    """IA32_THERM_STATUS 的 bit31 是 "Reading Valid",没置位说明这核没数据。"""
    status = 0x80000000 | (30 << 16)
    assert MsrSource._temperature(status, 100.0) == 70.0
    assert MsrSource._temperature(status & ~0x80000000, 100.0) is None
    assert MsrSource._temperature(None, 100.0) is None


def test_temperature_uses_7bit_delta():
    # delta 是 7 bit(0~127),温度 = TjMax - delta
    status = 0x80000000 | (0x50 << 16)
    assert MsrSource._temperature(status, 100.0) == 20.0
    # delta 用满 127 时温度会是负数,数学上成立,物理上说明数据有问题
    status = 0x80000000 | (0x7F << 16)
    assert MsrSource._temperature(status, 100.0) == -27.0


def test_msr_temperatures_are_plausible():
    """装了 PawnIO 驱动的话,读出来的温度要像真的。"""
    from pcmonitor.collectors import CpuCollector

    cpu = CpuCollector(enable_msr=True)
    try:
        if not cpu.msr.available:
            pytest.skip(f"PawnIO 不可用: {cpu.msr_error}")
        snap = cpu.sample()
        if snap.package_temp_c is not None:
            assert 20 < snap.package_temp_c < 120
        for temp in snap.core_temps_c:
            if temp is not None:
                assert 20 < temp < 120
    finally:
        cpu.close()


def test_per_core_temperatures_cover_all_cores():
    from pcmonitor.collectors import CpuCollector

    cpu = CpuCollector(enable_msr=True)
    try:
        if not cpu.msr.available:
            pytest.skip("PawnIO 不可用")
        snap = cpu.sample()
        assert len(snap.core_temps_c) == snap.logical_cores
    finally:
        cpu.close()
