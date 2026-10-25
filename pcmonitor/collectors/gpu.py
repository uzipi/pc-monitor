"""NVIDIA 显卡采集。

用 nvidia-smi 的 CSV 输出模式,一次拿到所有需要的字段。
注意两点:
* 部分显卡不支持某些字段(如无风扇的卡 fan.speed 会返回 "N/A"),
  所以每个字段都要容错。
* nvidia-smi 是同步阻塞的,调用耗时约 30~80ms;在 2 秒刷新间隔下可接受。
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

from ..models import GpuSnapshot

_QUERY_FIELDS = (
    "index,name,utilization.gpu,utilization.memory,temperature.gpu,"
    "memory.used,memory.total,power.draw,clocks.gr,clocks.mem,fan.speed,"
    "pcie.link.gen.current,pcie.link.gen.max,"
    "pcie.link.width.current,pcie.link.width.max"
)

_CREATE_NO_WINDOW = 0x08000000


def _to_float(text: str) -> Optional[float]:
    text = text.strip()
    if not text or text.upper() in ("N/A", "[N/A]", "NA", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(text: str) -> Optional[int]:
    value = _to_float(text)
    return int(value) if value is not None else None


class GpuCollector:
    """通过 nvidia-smi 采集 NVIDIA 显卡状态。"""

    def __init__(self) -> None:
        self._exe = shutil.which("nvidia-smi")
        self.available = self._exe is not None
        self.error: Optional[str] = None if self.available else "未找到 nvidia-smi"

    def sample(self) -> list[GpuSnapshot]:
        if not self.available:
            return []

        cmd = [
            self._exe,
            f"--query-gpu={_QUERY_FIELDS}",
            "--format=csv,noheader,nounits",
        ]
        startupinfo = None
        creationflags = 0
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = _CREATE_NO_WINDOW

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            self.error = f"nvidia-smi 调用失败: {exc}"
            return []

        if proc.returncode != 0:
            self.error = (proc.stderr or "nvidia-smi 返回非零").strip()[:200]
            return []

        self.error = None
        gpus: list[GpuSnapshot] = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < len(_QUERY_FIELDS.split(",")):
                continue

            gpu = GpuSnapshot()
            gpu.index = _to_int(parts[0]) or 0
            gpu.name = parts[1]
            gpu.usage_percent = _to_float(parts[2])
            gpu.utilization_mem_percent = _to_float(parts[3])
            gpu.temp_c = _to_float(parts[4])
            # nvidia-smi 的显存单位是 MiB,统一换算成字节
            used_mib = _to_int(parts[5])
            total_mib = _to_int(parts[6])
            gpu.mem_used_bytes = used_mib * 1024 * 1024 if used_mib is not None else None
            gpu.mem_total_bytes = (
                total_mib * 1024 * 1024 if total_mib is not None else None
            )
            gpu.power_w = _to_float(parts[7])
            gpu.freq_mhz = _to_float(parts[8])
            gpu.mem_freq_mhz = _to_float(parts[9])
            gpu.fan_percent = _to_float(parts[10])
            gpu.pcie_gen_current = _to_int(parts[11])
            gpu.pcie_gen_max = _to_int(parts[12])
            gpu.pcie_width_current = _to_int(parts[13])
            gpu.pcie_width_max = _to_int(parts[14])

            if (
                gpu.mem_used_bytes is not None
                and gpu.mem_total_bytes
                and gpu.mem_total_bytes > 0
            ):
                gpu.mem_percent = gpu.mem_used_bytes / gpu.mem_total_bytes * 100.0
            # utilization.gpu 就是 GPU 核心占用,单独留一份字段方便 UI 取用
            gpu.utilization_gpu_percent = gpu.usage_percent
            gpus.append(gpu)

        return gpus
