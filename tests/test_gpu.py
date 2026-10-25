"""nvidia-smi 输出的解析测试。

用假的 subprocess 返回值覆盖"字段缺失 / N/A / 显存单位换算"这些情况,
不需要真的装显卡。

这些用例专门测 nvidia-smi 这条降级路径,所以一律 `use_nvml=False` 关掉
NVML —— 否则在有 N 卡的机器上会直接走 NVML,根本轮不到假的 subprocess。

nvidia-smi 存不存在由 `fake_smi` 夹具接管(见 conftest.py):不光替换
`subprocess.run`,连 `shutil.which` 一起替换,不然在没装 N 卡驱动的机器上
这条路径压根不会被执行,用例会"通过"但什么都没测到。
"""

from __future__ import annotations

from pcmonitor.collectors.gpu import GpuCollector


def test_parses_full_row(fake_smi):
    fake_smi.stdout = (
        "0, NVIDIA GeForce RTX 5060 Ti, 98, 38, 70, 5539, 16311, 180.01, "
        "2625, 14001, 64, 5, 5, 8, 16\n"
    )
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


def test_handles_na_fields(fake_smi):
    fake_smi.stdout = (
        "0, Quadro K420, [N/A], [N/A], 40, 1024, 2048, 12.5, 900, 5000, "
        "[N/A], 1, 1, 2, 2\n"
    )
    gpus = GpuCollector(use_nvml=False).sample()
    assert len(gpus) == 1
    gpu = gpus[0]
    assert gpu.usage_percent is None
    assert gpu.fan_percent is None
    assert gpu.mem_used_bytes == 1024 * 1024 * 1024
    assert gpu.mem_percent == 50.0


def test_non_zero_return_code_sets_error(fake_smi):
    fake_smi.returncode = 9
    fake_smi.stderr = "boom"
    collector = GpuCollector(use_nvml=False)
    assert collector.sample() == []
    assert collector.error
    # 错误信息要能看出是 nvidia-smi 报的,而不是"根本找不到 nvidia-smi"
    assert "boom" in collector.error


def test_query_includes_pcie_fields(fake_smi):
    GpuCollector(use_nvml=False).sample()
    cmd = " ".join(fake_smi.cmd or [])
    assert "pcie.link.gen.current" in cmd
    assert "pcie.link.width.current" in cmd
