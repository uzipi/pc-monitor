"""CPU 采集:使用率来自 psutil,温度/频率/电压/功耗来自 PawnIO 读 MSR。

MSR 解码规则来自 Intel SDM 与 LibreHardwareMonitor 的实现:

  IA32_THERM_STATUS (0x19C)      每核温度
  IA32_PACKAGE_THERM_STATUS(0x1B1) 封装温度
  IA32_TEMPERATURE_TARGET (0x1A2) TjMax(降频温度上限),bits 23:16
  IA32_PERF_STATUS (0x198)       频率 bits 15:8 × 100MHz;电压 = edx&0xFFFF/8192
  MSR_RAPL_POWER_UNIT (0x606)    RAPL 能量单位,bits 12:8
  MSR_PKG_ENERGY_STATUS (0x611)  封装能耗计数
  MSR_PP0_ENERGY_STATUS (0x639)  核心域能耗计数
  MSR_PP1_ENERGY_STATUS (0x641)  核显域能耗计数

温度公式:temp = TjMax - ((status >> 16) & 0x7F),前提是 status 的 bit31 为 1。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import psutil

from ..models import CpuSnapshot
from .pawnio import PawnIoClient, PawnIoModule, pin_current_thread

# MSR 地址
MSR_PERF_STATUS = 0x198
MSR_THERM_STATUS = 0x19C
MSR_TEMPERATURE_TARGET = 0x1A2
MSR_PACKAGE_THERM_STATUS = 0x1B1
MSR_RAPL_POWER_UNIT = 0x606
MSR_PKG_ENERGY_STATUS = 0x611
MSR_PP0_ENERGY_STATUS = 0x639
MSR_PP1_ENERGY_STATUS = 0x641

# TSC 频率相对 100MHz 基准的倍数,用于把 IA32_PERF_STATUS 的 ratio 换算成 MHz
_TSC_BUS_CLOCK_MHZ = 100.0


class _CoreWorker(threading.Thread):
    """常驻线程,固定绑在某个逻辑核上,按需读取该核的 MSR。

    MSR 是按核隔离的,想拿到每一核的温度,读 MSR 的线程必须跑在对应核上。
    与其每次采样都新建 20 个线程,不如让它们常驻,收到请求再读。
    """

    def __init__(
        self,
        core_id: int,
        module: PawnIoModule,
        request: threading.Event,
        done: threading.Event,
    ) -> None:
        super().__init__(daemon=True, name=f"msr-core-{core_id}")
        self.core_id = core_id
        self._module = module
        self._request = request
        self._done = done
        self.value: Optional[int] = None
        self.pinned = False

    def run(self) -> None:
        self.pinned = pin_current_thread(self.core_id)
        while True:
            self._request.wait()
            self._request.clear()
            out = self._module.execute("ioctl_read_msr", (MSR_THERM_STATUS,), 1)
            self.value = out[0] if out else None
            self._done.set()


class MsrSource:
    """封装 PawnIO 的 MSR 读取。"""

    def __init__(self) -> None:
        self.client = PawnIoClient()
        self.module: Optional[PawnIoModule] = None
        self.available = False
        self.error: Optional[str] = None
        self._workers: list[_CoreWorker] = []
        self._requests: list[threading.Event] = []
        self._dones: list[threading.Event] = []
        self._energy_prev: dict[int, tuple[int, float]] = {}
        self._energy_multiplier: Optional[float] = None
        self._tjmax: Optional[float] = None
        self._lock = threading.Lock()

    def start(self, core_count: int) -> bool:
        if not self.client.open():
            self.error = self.client.error
            return False

        self.module = self.client.load_module("IntelMSR.bin")
        if self.module is None:
            self.error = self.client.error or "加载 IntelMSR 模块失败"
            return False

        self.available = True
        self.error = None

        # 每个逻辑核一个常驻读取线程
        for core_id in range(core_count):
            request = threading.Event()
            done = threading.Event()
            worker = _CoreWorker(core_id, self.module, request, done)
            worker.start()
            self._workers.append(worker)
            self._requests.append(request)
            self._dones.append(done)
        return True

    # ------------------------------------------------------------ 基础读取

    def read_msr_raw(self, addr: int) -> Optional[int]:
        """读取 MSR 的完整 64 位值(eax 在低 32 位,edx 在高 32 位)。"""
        if self.module is None:
            return None
        out = self.module.execute("ioctl_read_msr", (addr,), 1)
        if not out:
            return None
        value = out[0] & 0xFFFFFFFFFFFFFFFF
        if value & 0xFFFFFFFF == 0x35353535:
            return None
        return value

    def read_msr(self, addr: int) -> Optional[int]:
        """读取 MSR 的 eax(低 32 位)。"""
        value = self.read_msr_raw(addr)
        return None if value is None else value & 0xFFFFFFFF

    def read_per_core_therm(self) -> list[Optional[int]]:
        """读取每个逻辑核的 IA32_THERM_STATUS。"""
        if not self._workers:
            return []
        for request in self._requests:
            request.set()
        for done in self._dones:
            done.wait(timeout=2.0)
        return [w.value for w in self._workers]

    # ------------------------------------------------------------ 解码

    @staticmethod
    def _temperature(status: Optional[int], tjmax: float) -> Optional[float]:
        if status is None:
            return None
        if not (status & 0x80000000):
            return None
        delta = (status & 0x007F0000) >> 16
        return round(tjmax - delta, 1)

    def _ensure_tjmax(self) -> float:
        if self._tjmax is not None:
            return self._tjmax
        value = self.read_msr(MSR_TEMPERATURE_TARGET)
        if value is not None:
            tjmax = (value >> 16) & 0xFF
            if 40 <= tjmax <= 150:
                self._tjmax = float(tjmax)
                return self._tjmax
        self._tjmax = 100.0
        return self._tjmax

    def _ensure_energy_multiplier(self) -> Optional[float]:
        if self._energy_multiplier is not None:
            return self._energy_multiplier
        value = self.read_msr(MSR_RAPL_POWER_UNIT)
        if value is None:
            return None
        bits = (value >> 8) & 0x1F
        if bits == 0:
            return None
        self._energy_multiplier = 1.0 / (1 << bits)
        return self._energy_multiplier

    def _power_watts(self, msr: int) -> Optional[float]:
        """把 RAPL 能耗计数器换算成瞬时功率(瓦)。"""
        multiplier = self._ensure_energy_multiplier()
        if multiplier is None:
            return None
        counter = self.read_msr(msr)
        if counter is None:
            return None

        now = time.monotonic()
        with self._lock:
            prev = self._energy_prev.get(msr)
            self._energy_prev[msr] = (counter, now)
        if prev is None:
            return None

        prev_counter, prev_time = prev
        elapsed = now - prev_time
        if elapsed < 0.05:
            return None
        delta = counter - prev_counter
        if delta < 0:
            delta += 1 << 32  # 32 位计数器回绕
        return round(multiplier * delta / elapsed, 2)

    # ------------------------------------------------------------ 对外采样

    def sample(self) -> dict:
        """返回 CPU 的温度/频率/电压/功耗。"""
        result: dict = {}
        if not self.available:
            return result

        tjmax = self._ensure_tjmax()
        result["tjmax_c"] = tjmax

        package_status = self.read_msr(MSR_PACKAGE_THERM_STATUS)
        result["package_temp_c"] = self._temperature(package_status, tjmax)

        per_core_status = self.read_per_core_therm()
        temps = [
            self._temperature(s, tjmax)
            for s in per_core_status
        ]
        result["core_temps_c"] = temps
        valid = [t for t in temps if t is not None]
        if valid:
            result["core_max_temp_c"] = max(valid)
            result["core_avg_temp_c"] = round(sum(valid) / len(valid), 1)

        perf = self.read_msr_raw(MSR_PERF_STATUS)
        if perf is not None:
            eax = perf & 0xFFFFFFFF
            edx = (perf >> 32) & 0xFFFFFFFF
            ratio = (eax >> 8) & 0xFF
            if ratio:
                result["freq_mhz"] = round(ratio * _TSC_BUS_CLOCK_MHZ, 1)
            # IA32_PERF_STATUS 的 edx 低 16 位是 VID 电压标识
            vid = edx & 0xFFFF
            if vid:
                result["voltage_v"] = round(vid / 8192.0, 3)

        result["package_power_w"] = self._power_watts(MSR_PKG_ENERGY_STATUS)
        result["core_power_w"] = self._power_watts(MSR_PP0_ENERGY_STATUS)
        result["graphics_power_w"] = self._power_watts(MSR_PP1_ENERGY_STATUS)
        return result

    def close(self) -> None:
        self.client.close()


class CpuCollector:
    """CPU 总览:使用率走 psutil,温度/功耗走 MSR。"""

    def __init__(self, enable_msr: bool = True) -> None:
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)

        self.core_count = psutil.cpu_count(logical=True) or 1
        self.msr = MsrSource()
        self.msr_error: Optional[str] = None
        if enable_msr:
            if not self.msr.start(self.core_count):
                self.msr_error = self.msr.error

    def sample(self) -> CpuSnapshot:
        snap = CpuSnapshot()
        snap.usage_percent = psutil.cpu_percent(interval=None)
        try:
            snap.per_core_percent = psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            snap.per_core_percent = []
        snap.logical_cores = self.core_count

        try:
            freq = psutil.cpu_freq()
            if freq is not None:
                snap.freq_mhz = float(freq.current)
                snap.max_freq_mhz = float(freq.max)
        except Exception:
            pass

        if self.msr.available:
            data = self.msr.sample()
            snap.package_temp_c = data.get("package_temp_c")
            snap.core_temps_c = data.get("core_temps_c", [])
            snap.core_max_temp_c = data.get("core_max_temp_c")
            snap.core_avg_temp_c = data.get("core_avg_temp_c")
            snap.tjmax_c = data.get("tjmax_c")
            snap.voltage_v = data.get("voltage_v")
            snap.package_power_w = data.get("package_power_w")
            snap.core_power_w = data.get("core_power_w")
            snap.graphics_power_w = data.get("graphics_power_w")
            # MSR 频率比 SMBIOS 更实时,优先采用
            if data.get("freq_mhz"):
                snap.freq_mhz = data["freq_mhz"]
            snap.sensor_source = "MSR"

        return snap

    def close(self) -> None:
        self.msr.close()
