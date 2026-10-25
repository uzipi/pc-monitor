"""把所有采集器汇总成一帧 Snapshot。"""

from __future__ import annotations

import platform
import socket
import time

import psutil

from .collectors import (
    CpuCollector,
    DiskCollector,
    GpuCollector,
    MemoryCollector,
    NetworkCollector,
    ProcessCollector,
    ThermalCollector,
)
from .models import HostSnapshot, Snapshot


class Monitor:
    """持有全部采集器,按需产出一帧完整快照。

    采集器之间没有依赖,所以这里顺序调用即可;真正耗时的是
    PowerShell(SMART / SMBIOS)和 nvidia-smi,单帧大约 100~300ms。
    """

    def __init__(self, enable_msr: bool = True, top: int = 10) -> None:
        self.top = top
        self.cpu = CpuCollector(enable_msr=enable_msr)
        self.memory = MemoryCollector()
        self.disk = DiskCollector()
        self.network = NetworkCollector()
        self.gpu = GpuCollector()
        self.thermal = ThermalCollector()
        self.process = ProcessCollector()
        self.started_at = time.time()
        self._started = False

    def start(self) -> None:
        """启动后台刷新线程(硬盘 SMART / SMBIOS 内存条 / ACPI 热区)。

        这些查询都要拉起 PowerShell 进程,单次 1 秒以上,
        放到后台线程里,界面就不会因为它们卡顿。
        """
        if self._started:
            return
        self._started = True
        self.memory.start()
        self.thermal.start()

    def warmup(self) -> None:
        """先采一帧当基线。

        psutil 的 CPU 占用、网速/磁盘速率、RAPL 功率都是"两次采样求差",
        没有基线的话第一帧全是 0 或 None。
        """
        self.start()
        try:
            self.sample()
        except Exception:
            pass
        # 等后台第一轮 SMART / SMBIOS 查询结束,免得开头几秒是空的
        self.memory.wait_ready(2.0)
        self.thermal.smart.wait_ready(3.0)

    # ------------------------------------------------------------ 采样

    def sample(self) -> Snapshot:
        snap = Snapshot()
        snap.timestamp = time.time()

        snap.cpu = self.cpu.sample()
        snap.memory = self.memory.sample()
        snap.disks = self.disk.sample()
        snap.disk_io = self.disk.sample_io()
        snap.network = self.network.sample()
        snap.gpus = self.gpu.sample()
        snap.physical_disks = self.thermal.sample_disks()

        # 温度汇总:CPU 来自 MSR,GPU 来自 nvidia-smi,硬盘来自 SMART
        gpu_temp = None
        if snap.gpus:
            gpu_temp = snap.gpus[0].temp_c
        core_temps = [
            (f"核心 {i}", t)
            for i, t in enumerate(snap.cpu.core_temps_c)
            if t is not None
        ]
        snap.thermal = self.thermal.sample(
            cpu_package_c=snap.cpu.package_temp_c,
            cpu_cores=core_temps,
            gpu_temp_c=gpu_temp,
            gpu_source=self.gpu.source,
        )
        for disk in snap.physical_disks:
            if disk.temp_c is not None:
                snap.thermal.nvme.append((disk.model or disk.bus_type, disk.temp_c))

        snap.host = self._host()
        try:
            snap.processes = self.process.sample(top=self.top)
        except Exception:
            snap.processes = None
        return snap

    def _host(self) -> HostSnapshot:
        host = HostSnapshot()
        host.hostname = socket.gethostname()
        host.os_name = f"{platform.system()} {platform.release()}"
        try:
            host.boot_time = psutil.boot_time()
            host.uptime_seconds = time.time() - host.boot_time
        except Exception:
            pass
        return host

    # ------------------------------------------------------------ 生命周期

    def warnings(self) -> list[str]:
        """采集过程中降级或失败的项,展示给用户而不是静默吞掉。"""
        notes: list[str] = []
        if self.cpu.msr_error:
            notes.append(f"MSR/温度不可用: {self.cpu.msr_error}")
        if self.gpu.error:
            notes.append(f"GPU 不可用: {self.gpu.error}")
        if self.thermal.smart.error:
            notes.append(f"硬盘 SMART 不可用: {self.thermal.smart.error}")
        return notes

    def close(self) -> None:
        self.cpu.close()
        self.memory.stop()
        self.thermal.stop()
        self.gpu.close()
