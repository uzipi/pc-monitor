"""NVML 采集器测试。

真实硬件上跑不了 CI,所以这里只测"拿不到的时候怎么办"和
"两条路径(NVML / nvidia-smi)怎么选"这些纯逻辑。

nvidia-smi 那条降级路径由 `fake_smi` 夹具接管(见 conftest.py)。
"""

from __future__ import annotations

import ctypes

import pytest

from pcmonitor.collectors import gpu as gpu_module
from pcmonitor.collectors import nvml as nvml_module
from pcmonitor.collectors.gpu import GpuCollector
from pcmonitor.models import GpuSnapshot


def _patch_no_nvml(monkeypatch):
    """让 NVML 初始化失败(模拟没有 N 卡 / 没装驱动)。"""

    def boom(*_args, **_kwargs):
        raise OSError("找不到 nvml.dll")

    monkeypatch.setattr(nvml_module.ctypes, "WinDLL", boom)


def test_missing_nvml_is_reported_not_raised(monkeypatch):
    _patch_no_nvml(monkeypatch)
    source = nvml_module.NvmlSource()
    assert source.available is False
    assert source.error
    assert source.sample() == []


def test_close_is_safe_when_nvml_is_missing(monkeypatch):
    _patch_no_nvml(monkeypatch)
    source = nvml_module.NvmlSource()
    source.close()  # 不能抛异常
    assert source.available is False


def test_collector_falls_back_to_smi_when_nvml_missing(monkeypatch, fake_smi):
    _patch_no_nvml(monkeypatch)
    fake_smi.stdout = "0, Quadro K420, 10, 5, 40, 1024, 2048, 12.5, 900, 5000, 20, 1, 1, 2, 2\n"
    collector = GpuCollector()
    gpus = collector.sample()
    assert len(gpus) == 1
    assert gpus[0].name == "Quadro K420"
    assert collector.source == "nvidia-smi"


def test_collector_prefers_nvml_over_smi(monkeypatch, fake_smi):
    """NVML 有数据时就不该再拉 nvidia-smi 子进程。"""
    fake_smi.stdout = "0, 不该被调用, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1\n"

    class _FakeNvml:
        available = True
        error = None

        def sample(self):
            gpu = GpuSnapshot()
            gpu.name = "RTX 5060 Ti"
            gpu.usage_percent = 42.0
            return [gpu]

        def close(self):
            pass

    monkeypatch.setattr(gpu_module, "NvmlSource", _FakeNvml)
    collector = GpuCollector()
    gpus = collector.sample()
    assert len(gpus) == 1
    assert gpus[0].name == "RTX 5060 Ti"
    assert gpus[0].usage_percent == 42.0
    assert collector.source == "NVML"
    assert fake_smi.cmd is None, "走了 NVML 就不该再调 nvidia-smi"


def test_collector_falls_back_when_nvml_returns_nothing(monkeypatch, fake_smi):
    """NVML 初始化成功却没采到东西,要退回 nvidia-smi 兜底。"""
    fake_smi.stdout = "0, Quadro K420, 10, 5, 40, 1024, 2048, 12.5, 900, 5000, 20, 1, 1, 2, 2\n"

    class _EmptyNvml:
        available = True
        error = None

        def sample(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(gpu_module, "NvmlSource", _EmptyNvml)
    collector = GpuCollector()
    gpus = collector.sample()
    assert len(gpus) == 1
    assert gpus[0].name == "Quadro K420"
    assert collector.source == "nvidia-smi"


@pytest.mark.skipif(not hasattr(ctypes, "WinDLL"), reason="只在 Windows 上有 NVML")
def test_real_nvml_sample_does_not_raise():
    """本机有 N 卡就真采一次,没有就当空列表,两种都不能抛异常。"""
    source = nvml_module.NvmlSource()
    was_available = source.available
    try:
        gpus = source.sample()
    finally:
        source.close()
    if was_available:
        assert gpus
        assert gpus[0].name
    else:
        assert gpus == []
