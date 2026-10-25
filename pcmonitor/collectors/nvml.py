"""NVIDIA 显卡采集:直接 ctypes 调 NVML(nvml.dll)。

原来是每帧拉一次 `nvidia-smi` 子进程,一次 30~50ms —— 贵在**进程启动
和 NVML 初始化**,不是查询本身(加不加字段耗时几乎一样)。
直接调 nvml.dll 之后是几十微秒,而且不用再拉子进程。

nvml.dll 随显卡驱动一起装在 `%SystemRoot%\\System32\\nvml.dll`。
本文用到的接口从 NVML 2.0 起就没变过,老驱动也认这些符号;
个别老驱动缺某个函数(比如 PCIe 链路信息),绑定不上就当"拿不到",
界面上显示"不可用",不编数字。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, Callable, Optional

from ..models import GpuSnapshot

#: NVML 所有函数成功时都返回 0
_NVML_SUCCESS = 0

#: 温度传感器类型
_NVML_TEMPERATURE_GPU = 0
#: 时钟类型
_NVML_CLOCK_GRAPHICS = 0
_NVML_CLOCK_MEM = 2

#: nvmlDeviceGetName 的缓冲区大小
_NAME_BUFFER_SIZE = 96


class _Utilization(ctypes.Structure):
    """nvmlUtilization_t:{ unsigned int gpu; unsigned int memory; }"""

    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Memory(ctypes.Structure):
    """nvmlMemory_t(第一版):total / free / used,全是 64 位。"""

    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class NvmlSource:
    """NVML 客户端。一个进程里只初始化一次,采样只是读几个寄存器。"""

    def __init__(self) -> None:
        self.available = False
        self.error: Optional[str] = None
        self._lib = None
        self._handles: list[Any] = []
        self._fn: dict[str, Optional[Callable]] = {}
        self._init()

    # ---------------------------------------------------------------- 初始化

    def _init(self) -> None:
        if sys.platform != "win32":
            self.error = "NVML 只支持 Windows"
            return

        try:
            lib = ctypes.WinDLL("nvml.dll")
        except OSError as exc:
            self.error = f"加载 nvml.dll 失败: {exc}"
            return

        self._lib = lib
        self._fn["init"] = self._bind("nvmlInit_v2", [])
        self._fn["shutdown"] = self._bind("nvmlShutdown", [])
        self._fn["count"] = self._bind(
            "nvmlDeviceGetCount_v2", [ctypes.POINTER(ctypes.c_uint)]
        )
        self._fn["handle"] = self._bind(
            "nvmlDeviceGetHandleByIndex_v2",
            [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)],
        )
        self._fn["name"] = self._bind(
            "nvmlDeviceGetName",
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint],
        )
        self._fn["util"] = self._bind(
            "nvmlDeviceGetUtilizationRates",
            [ctypes.c_void_p, ctypes.POINTER(_Utilization)],
        )
        self._fn["temp"] = self._bind(
            "nvmlDeviceGetTemperature",
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)],
        )
        self._fn["power"] = self._bind(
            "nvmlDeviceGetPowerUsage", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        )
        self._fn["memory"] = self._bind(
            "nvmlDeviceGetMemoryInfo", [ctypes.c_void_p, ctypes.POINTER(_Memory)]
        )
        self._fn["clock"] = self._bind(
            "nvmlDeviceGetClockInfo",
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)],
        )
        self._fn["fan"] = self._bind(
            "nvmlDeviceGetFanSpeed", [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        )
        # PCIe 链路:老驱动上是 nvmlDeviceGetPcieLinkGeneration,新驱动改名成
        # nvmlDeviceGetCurrPcieLinkGeneration,两个都试。
        self._fn["pcie_gen"] = self._bind(
            "nvmlDeviceGetCurrPcieLinkGeneration",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        ) or self._bind(
            "nvmlDeviceGetPcieLinkGeneration",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        )
        self._fn["pcie_width"] = self._bind(
            "nvmlDeviceGetCurrPcieLinkWidth",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        ) or self._bind(
            "nvmlDeviceGetPcieLinkWidth",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        )
        self._fn["pcie_gen_max"] = self._bind(
            "nvmlDeviceGetMaxPcieLinkGeneration",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        ) or self._bind(
            "nvmlDeviceGetGpuMaxPcieLinkGeneration",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        )
        self._fn["pcie_width_max"] = self._bind(
            "nvmlDeviceGetMaxPcieLinkWidth",
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)],
        )

        init = self._fn.get("init")
        count_fn = self._fn.get("count")
        handle_fn = self._fn.get("handle")
        if not init or not count_fn or not handle_fn:
            self.error = "nvml.dll 缺少必要的接口"
            return

        if init() != _NVML_SUCCESS:
            self.error = "nvmlInit 失败(驱动未加载或版本不匹配)"
            return

        count = ctypes.c_uint(0)
        if count_fn(ctypes.byref(count)) != _NVML_SUCCESS:
            self.error = "读取显卡数量失败"
            return

        for index in range(count.value):
            handle = ctypes.c_void_p()
            if handle_fn(index, ctypes.byref(handle)) != _NVML_SUCCESS:
                continue
            if handle.value:
                self._handles.append(handle)

        if not self._handles:
            self.error = "没有找到 NVIDIA 显卡"
            return

        self.available = True
        self.error = None

    def _bind(self, name: str, argtypes: list) -> Optional[Callable]:
        """绑定一个 NVML 函数;老驱动没这个符号就返回 None。"""
        try:
            fn = getattr(self._lib, name)
        except AttributeError:
            return None
        fn.argtypes = argtypes
        fn.restype = ctypes.c_int
        return fn

    # ---------------------------------------------------------------- 采样

    def sample(self) -> list[GpuSnapshot]:
        if not self.available:
            return []
        gpus: list[GpuSnapshot] = []
        for index, handle in enumerate(self._handles):
            gpu = GpuSnapshot()
            gpu.index = index
            gpu.name = self._name(handle)
            self._fill_util(handle, gpu)
            self._fill_temp(handle, gpu)
            self._fill_power(handle, gpu)
            self._fill_memory(handle, gpu)
            self._fill_clocks(handle, gpu)
            self._fill_fan(handle, gpu)
            self._fill_pcie(handle, gpu)
            gpu.utilization_gpu_percent = gpu.usage_percent
            gpus.append(gpu)
        return gpus

    # ------------------------------------------------------------ 单项读取

    def _name(self, handle: Any) -> str:
        fn = self._fn.get("name")
        if not fn:
            return ""
        buf = ctypes.create_string_buffer(_NAME_BUFFER_SIZE)
        try:
            if fn(handle, buf, ctypes.c_uint(_NAME_BUFFER_SIZE)) != _NVML_SUCCESS:
                return ""
        except Exception:
            return ""
        return buf.value.decode("utf-8", "replace")

    def _fill_util(self, handle: Any, gpu: GpuSnapshot) -> None:
        fn = self._fn.get("util")
        if not fn:
            return
        util = _Utilization()
        try:
            if fn(handle, ctypes.byref(util)) == _NVML_SUCCESS:
                gpu.usage_percent = float(util.gpu)
                gpu.utilization_mem_percent = float(util.memory)
        except Exception:
            pass

    def _fill_temp(self, handle: Any, gpu: GpuSnapshot) -> None:
        fn = self._fn.get("temp")
        if not fn:
            return
        value = ctypes.c_uint(0)
        try:
            if fn(handle, _NVML_TEMPERATURE_GPU, ctypes.byref(value)) == _NVML_SUCCESS:
                gpu.temp_c = float(value.value)
        except Exception:
            pass

    def _fill_power(self, handle: Any, gpu: GpuSnapshot) -> None:
        fn = self._fn.get("power")
        if not fn:
            return
        value = ctypes.c_uint(0)
        try:
            if fn(handle, ctypes.byref(value)) == _NVML_SUCCESS:
                # NVML 给的是毫瓦
                gpu.power_w = value.value / 1000.0
        except Exception:
            pass

    def _fill_memory(self, handle: Any, gpu: GpuSnapshot) -> None:
        fn = self._fn.get("memory")
        if not fn:
            return
        mem = _Memory()
        try:
            if fn(handle, ctypes.byref(mem)) != _NVML_SUCCESS:
                return
        except Exception:
            return
        total = int(mem.total)
        used = int(mem.used)
        if total <= 0:
            return
        gpu.mem_total_bytes = total
        gpu.mem_used_bytes = used
        gpu.mem_percent = used / total * 100.0

    def _fill_clocks(self, handle: Any, gpu: GpuSnapshot) -> None:
        fn = self._fn.get("clock")
        if not fn:
            return
        for clock_type, attr in (
            (_NVML_CLOCK_GRAPHICS, "freq_mhz"),
            (_NVML_CLOCK_MEM, "mem_freq_mhz"),
        ):
            value = ctypes.c_uint(0)
            try:
                if fn(handle, clock_type, ctypes.byref(value)) == _NVML_SUCCESS:
                    setattr(gpu, attr, float(value.value))
            except Exception:
                pass

    def _fill_fan(self, handle: Any, gpu: GpuSnapshot) -> None:
        fn = self._fn.get("fan")
        if not fn:
            return
        value = ctypes.c_uint(0)
        try:
            if fn(handle, ctypes.byref(value)) == _NVML_SUCCESS:
                gpu.fan_percent = float(value.value)
        except Exception:
            pass

    def _fill_pcie(self, handle: Any, gpu: GpuSnapshot) -> None:
        for key, attr in (
            ("pcie_gen", "pcie_gen_current"),
            ("pcie_width", "pcie_width_current"),
            ("pcie_gen_max", "pcie_gen_max"),
            ("pcie_width_max", "pcie_width_max"),
        ):
            fn = self._fn.get(key)
            if not fn:
                continue
            value = ctypes.c_uint(0)
            try:
                if fn(handle, ctypes.byref(value)) == _NVML_SUCCESS:
                    setattr(gpu, attr, int(value.value))
            except Exception:
                pass

    # ---------------------------------------------------------------- 收尾

    def close(self) -> None:
        shutdown = self._fn.get("shutdown")
        if shutdown is not None:
            try:
                shutdown()
            except Exception:
                pass
        self._handles.clear()
        self.available = False
