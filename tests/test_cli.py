"""命令行入口测试。"""

from __future__ import annotations

import csv
import io
import json
import sys
from contextlib import redirect_stdout

from pcmonitor import driver
from pcmonitor.cli import build_parser, main, prepare_driver, setup_output_encoding


def _run(args: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(args)
    return buf.getvalue()


def test_json_output_is_valid_json():
    out = _run(["--json", "--once", "--no-msr", "--no-process"])
    payload = json.loads(out.strip().splitlines()[-1])
    assert "cpu" in payload
    assert "memory" in payload
    assert payload["cpu"]["usage_percent"] is not None


def test_csv_output_has_header_and_row():
    out = _run(["--csv", "--once", "--no-msr", "--no-process"])
    rows = list(csv.reader(io.StringIO(out)))
    assert rows[0][:3] == ["timestamp", "cpu_percent", "cpu_freq_mhz"]
    assert len(rows) >= 2


def test_dashboard_output_has_panels():
    out = _run(["--once", "--no-msr", "--no-process", "--width", "100"])
    assert "PC Monitor" in out
    assert "CPU" in out
    assert "内存" in out
    assert "磁盘" in out


def test_setup_output_encoding_switches_to_utf8(monkeypatch):
    """中文 Windows 控制台默认 GBK,画框线会直接崩,必须切成 UTF-8。"""

    class FakeStream:
        def __init__(self) -> None:
            self.encoding = "gbk"
            self.calls: list[dict] = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)
            self.encoding = kwargs.get("encoding", self.encoding)

    out, err = FakeStream(), FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    setup_output_encoding()
    assert out.encoding == "utf-8"
    assert err.encoding == "utf-8"
    assert out.calls[0]["errors"] == "replace"


# ---------------------------------------------------------------- 驱动准备


def _args(*argv: str):
    return build_parser().parse_args(list(argv))


def test_no_msr_never_touches_the_driver(monkeypatch):
    """--no-msr 表示用户明确不要驱动,一个字节的驱动相关调用都不该有。"""

    def boom(*a, **k):
        raise AssertionError("--no-msr 时不该碰驱动")

    monkeypatch.setattr(driver, "is_installed", boom)
    monkeypatch.setattr(driver, "install", boom)
    monkeypatch.setattr(driver, "probe_access", boom)
    assert prepare_driver(_args("--no-msr")) is True


def test_no_driver_install_skips_the_install(monkeypatch):
    monkeypatch.setattr(driver, "is_installed", lambda: False)

    def boom(*a, **k):
        raise AssertionError("--no-driver-install 时不该装驱动")

    monkeypatch.setattr(driver, "install", boom)
    monkeypatch.setattr(driver, "elevate_self", boom)
    assert prepare_driver(_args("--no-driver-install")) is True


def test_missing_driver_self_elevates_first(monkeypatch):
    """没装驱动时先把自己提权重开,让提权后的实例去装 —— 只弹一次 UAC。"""
    monkeypatch.setattr(driver, "is_installed", lambda: False)
    monkeypatch.setattr(driver, "is_elevated", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(driver, "elevate_self", lambda *a, **k: calls.append("elevate") or True)
    monkeypatch.setattr(driver, "install", lambda *a, **k: calls.append("install"))

    assert prepare_driver(_args()) is False
    assert calls == ["elevate"], "应该先提权,提权后才装"


def test_already_elevated_installs_directly(monkeypatch):
    """已经是管理员了,直接装,不用再提权。"""
    monkeypatch.setattr(driver, "is_installed", lambda: False)
    monkeypatch.setattr(driver, "is_elevated", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(driver, "elevate_self", lambda *a, **k: calls.append("elevate"))
    monkeypatch.setattr(
        driver,
        "install",
        lambda *a, **k: calls.append("install") or driver.InstallResult(True, "ok"),
    )

    assert prepare_driver(_args()) is True
    assert calls == ["install"]


def test_install_failure_does_not_abort_the_program(monkeypatch):
    """装不上驱动也要能跑,只是 MSR 指标没有而已。"""
    monkeypatch.setattr(driver, "is_installed", lambda: False)
    monkeypatch.setattr(driver, "is_elevated", lambda: True)
    monkeypatch.setattr(
        driver, "install", lambda *a, **k: driver.InstallResult(False, "boom")
    )
    monkeypatch.setattr(driver, "probe_access", lambda: None)
    assert prepare_driver(_args()) is True


def test_access_denied_triggers_elevation(monkeypatch):
    """驱动在、但没权限 -> 以管理员身份重开自己。"""
    monkeypatch.setattr(driver, "is_installed", lambda: True)
    monkeypatch.setattr(driver, "is_elevated", lambda: False)
    monkeypatch.setattr(driver, "probe_access", lambda: driver.ACCESS_DENIED_HRESULT)
    calls: list[str] = []
    monkeypatch.setattr(driver, "elevate_self", lambda *a, **k: calls.append("elevate") or True)

    assert prepare_driver(_args()) is False
    assert calls == ["elevate"]


def test_json_mode_does_not_self_elevate(monkeypatch):
    """--json 的输出要留在标准输出上,重开进程会把流弄丢。"""
    monkeypatch.setattr(driver, "is_installed", lambda: True)
    monkeypatch.setattr(driver, "is_elevated", lambda: False)
    monkeypatch.setattr(driver, "probe_access", lambda: driver.ACCESS_DENIED_HRESULT)

    def boom(*a, **k):
        raise AssertionError("--json 不该自动提权")

    monkeypatch.setattr(driver, "elevate_self", boom)
    assert prepare_driver(_args("--json")) is True
