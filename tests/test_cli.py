"""命令行入口测试。"""

from __future__ import annotations

import csv
import io
import json
import sys
from contextlib import redirect_stdout

from pcmonitor.cli import main, setup_output_encoding


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
