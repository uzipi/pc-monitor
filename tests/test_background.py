"""后台刷新器测试。

这一组用例盯住的是"慢查询不能拖住界面"这条约束:
采样接口必须立刻返回缓存,不能同步去跑 PowerShell。
"""

from __future__ import annotations

import time

import pytest

from pcmonitor.collectors.base import BackgroundRefresher
from pcmonitor.collectors.thermal import AcpiThermalSource, SmartDiskSource


def test_refresher_produces_value_in_background():
    calls: list[int] = []

    def refresh():
        calls.append(1)
        return {"n": len(calls)}

    ref = BackgroundRefresher(refresh, interval=0.05, name="t-refresher")
    ref.start()
    try:
        assert ref.wait_ready(2.0) is True
        value, error = ref.get()
        assert value == {"n": 1}
        assert error is None
    finally:
        ref.stop()


def test_refresher_records_error_without_dying():
    def refresh():
        raise RuntimeError("查询失败")

    ref = BackgroundRefresher(refresh, interval=0.05, name="t-err")
    ref.start()
    try:
        assert ref.wait_ready(2.0) is False
        value, error = ref.get()
        assert value is None
        assert error == "查询失败"
    finally:
        ref.stop()


def test_get_is_instant_even_when_refresh_is_slow():
    """慢查询在后台跑,前台读缓存必须立刻返回。"""

    def refresh():
        time.sleep(1.0)
        return "done"

    ref = BackgroundRefresher(refresh, interval=60.0, name="t-slow")
    ref.start()
    try:
        time.sleep(0.1)  # 让后台线程进到 sleep 里
        t0 = time.perf_counter()
        value, _ = ref.get()
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1
        assert value is None  # 还没刷新完
    finally:
        ref.stop()


def test_refresh_now_is_synchronous():
    ref = BackgroundRefresher(lambda: 42, interval=60.0, name="t-now")
    ref.refresh_now()
    assert ref.get()[0] == 42


def test_refresher_stops_after_one_success():
    """`stop_after_success`:查成功一次就收工,不再反复拉起 PowerShell。"""
    calls: list[int] = []

    def refresh():
        calls.append(1)
        return "ok"

    ref = BackgroundRefresher(
        refresh, interval=0.05, name="t-once", stop_after_success=True
    )
    ref.start()
    try:
        assert ref.wait_ready(2.0) is True
        time.sleep(0.3)  # 够它再转好几轮了
        assert len(calls) == 1
        assert ref.get()[0] == "ok"
    finally:
        ref.stop()


def test_refresher_retries_when_it_keeps_failing():
    """一次都没成功就不算成功,还得按间隔重试。"""
    calls: list[int] = []

    def refresh():
        calls.append(1)
        raise RuntimeError("还是不行")

    ref = BackgroundRefresher(
        refresh, interval=0.05, name="t-once-fail", stop_after_success=True
    )
    ref.start()
    try:
        time.sleep(0.3)
        assert len(calls) > 1
        assert ref.get()[1] == "还是不行"
    finally:
        ref.stop()


def test_smart_script_avoids_the_slow_storage_cmdlets():
    """守护这条性能约定:别再退回 `Get-PhysicalDisk` 那条 0.9 秒 CPU 的老路。

    `Get-PhysicalDisk` / `Get-StorageReliabilityCounter` 实测要 0.9 秒 CPU
    一次,非管理员还直接报错;换成直接问 WMI 之后只要 0.25 秒。
    """
    from pcmonitor.collectors import thermal

    script = thermal._PHYSICAL_DISK_SCRIPT
    assert "Get-PhysicalDisk" not in script
    assert "Get-StorageReliabilityCounter" not in script
    assert "MSFT_PhysicalDisk" in script
    assert "MSFT_StorageReliabilityCounter" in script


def test_smart_source_reads_cache_not_powershell(monkeypatch):
    """没开后台线程时 sample() 不该去跑 PowerShell,直接返回空列表。"""
    from pcmonitor.collectors import thermal

    def boom(*_args, **_kwargs):
        raise AssertionError("sample() 不应该同步调用 PowerShell")

    monkeypatch.setattr(thermal, "run_powershell_json", boom)
    source = SmartDiskSource()
    assert source.sample() == []


def test_acpi_source_reads_cache(monkeypatch):
    from pcmonitor.collectors import thermal

    def boom(*_args, **_kwargs):
        raise AssertionError("sample() 不应该同步调用 PowerShell")

    monkeypatch.setattr(thermal, "run_powershell_json", boom)
    source = AcpiThermalSource()
    assert source.sample() == []


def test_smart_source_force_refresh_goes_through_query(monkeypatch):
    from pcmonitor.collectors import thermal

    payload = [
        {
            "DeviceId": 0,
            "Model": "TEST SSD",
            "Bus": "NVMe",
            "Media": "SSD",
            "Size": 1024,
            "Health": "Healthy",
            "TempC": 41,
            "Hours": 100,
            "Wear": 3,
        }
    ]
    monkeypatch.setattr(thermal, "run_powershell_json", lambda *a, **k: payload)

    source = SmartDiskSource()
    disks = source.sample(force=True)
    assert len(disks) == 1
    assert disks[0].model == "TEST SSD"
    assert disks[0].temp_c == 41
    assert disks[0].health == "Healthy"
    assert disks[0].power_on_hours == 100
    assert disks[0].wear_percent == 3


@pytest.mark.parametrize("source_cls", [SmartDiskSource, AcpiThermalSource])
def test_source_stop_is_safe_without_start(source_cls):
    source = source_cls()
    source.stop()
    assert source.sample() == []
