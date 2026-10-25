"""测试公用夹具。"""

from __future__ import annotations

from typing import Optional

import pytest

from pcmonitor.collectors import gpu as gpu_module


class _FakeProc:
    """假装是 subprocess.CompletedProcess。"""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


class FakeNvidiaSmi:
    """假装机器上装了 nvidia-smi,并按需返回指定的 CSV。

    **必须连 `shutil.which` 一起改。** `GpuCollector.__init__` 在构造时就按
    PATH 找 nvidia-smi,找不到就把 `_exe` 设成 None,后面压根不会走
    `subprocess.run` —— 那这条降级路径就等于没测。

    之前就是踩了这个坑:测试在有 N 卡的开发机上全绿,推到没有 N 卡驱动的
    CI runner 上就全挂(而且挂的方式是"返回空列表",看起来像解析逻辑坏了)。
    """

    def __init__(self) -> None:
        #: 假 nvidia-smi 的输出;测试里先设好这个,再让采集器采样
        self.stdout: str = ""
        self.returncode: int = 0
        self.stderr: str = ""
        #: 上一次调用时传进来的命令行
        self.cmd: Optional[list[str]] = None

    def run(self, cmd, **kwargs):  # noqa: ANN001
        self.cmd = list(cmd)
        return _FakeProc(self.stdout, self.returncode, self.stderr)


@pytest.fixture
def fake_smi(monkeypatch):
    """把 nvidia-smi 这条降级路径整个接管掉。

    用法:

        def test_xxx(fake_smi):
            fake_smi.stdout = "0, NVIDIA ..., 98, ...\\n"
            gpus = GpuCollector(use_nvml=False).sample()
    """
    fake = FakeNvidiaSmi()
    real_which = gpu_module.shutil.which

    def fake_which(name, *args, **kwargs):  # noqa: ANN001
        # 只接管 nvidia-smi,别的照常查(shutil.which 是全局的,
        # powershell.py 之类的地方还要用它)
        if name == "nvidia-smi":
            return r"C:\fake\nvidia-smi.exe"
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(gpu_module.shutil, "which", fake_which)
    monkeypatch.setattr(gpu_module.subprocess, "run", fake.run)
    return fake
