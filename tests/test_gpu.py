"""nvidia-smi 输出的解析测试。

用假的 subprocess 返回值覆盖"字段缺失 / N/A / 显存单位换算"这些情况,
不需要真的装显卡。

这些用例专门测 nvidia-smi 这条降级路径,所以一律 `use_nvml=False` 关掉
NVML —— 否则在有 N 卡的机器上会直接走 NVML,根本轮不到假的 subprocess。
"""

from __future__ import annotations

from pcmonitor.collectors import gpu as gpu_module
from pcmonitor.collectors.gpu import GpuCollector


class _FakeProc:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _patch_run(monkeypatch, stdout: str, returncode: int = 0, stderr: str = ""):
    def fake_run(cmd, **kwargs):  # noqa: ANN001
        fake_run.cmd = cmd
        return _FakeProc(stdout, returncode, stderr)

    monkeypatch.setattr(gpu_module.subprocess, "run", fake_run)
    return fake_run


def test_parses_full_row(monkeypatch):
    row = (
        "0, NVIDIA GeForce RTX 5060 Ti, 98, 38, 70, 5539, 16311, 180.01, "
        "2625, 14001, 64, 5, 5, 8, 16\n"
    )
    _patch_run(monkeypatch, row)
    gpus = GpuCollector(use_nvml=False).sample()
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu.name == "NVIDIA GeForce RTX 5060 Ti"
    assert gpu.usage_percent == 98
    assert gpu.temp_c == 70
    assert gpu.power_w == 180.01
    assert gpu.fan_percent == 64
    # 显存单位是 MiB,要换算成字节
    assert gpu.mem_used_bytes == 5539 * 1024 * 1024
    assert gpu.mem_total_bytes == 16311 * 1024 * 1024
    assert 30 < gpu.mem_percent < 40
    assert gpu.pcie_gen_current == 5
    assert gpu.pcie_width_current == 8


def test_handles_na_fields(monkeypatch):
    row = "0, Quadro K420, [N/A], [N/A], 40, 1024, 2048, 12.5, 900, 5000, [N/A], 1, 1, 2, 2\n"
    _patch_run(monkeypatch, row)
    gpus = GpuCollector(use_nvml=False).sample()
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu.usage_percent is None
    assert gpu.fan_percent is None
    assert gpu.mem_used_bytes == 1024 * 1024 * 1024
    assert gpu.mem_percent == 50.0


def test_non_zero_return_code_sets_error(monkeypatch):
    _patch_run(monkeypatch, "", returncode=9, stderr="boom")
    collector = GpuCollector(use_nvml=False)
    assert collector.sample() == []
    assert collector.error


def test_query_includes_pcie_fields(monkeypatch):
    fake = _patch_run(monkeypatch, "")
    GpuCollector(use_nvml=False).sample()
    cmd = " ".join(fake.cmd)
    assert "pcie.link.gen.current" in cmd
    assert "pcie.link.width.current" in cmd
