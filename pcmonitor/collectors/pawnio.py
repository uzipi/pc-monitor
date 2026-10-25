"""PawnIO 客户端:通过 ctypes 直接调用官方 PawnIOLib.dll。

PawnIO 是一个通用的内核态驱动,提供受控的 ring0 能力(读 MSR、走 SMBus、
访问 I/O 端口)。本项目用它读取 Intel CPU 的温度/频率/电压/功耗。

为什么不用 LibreHardwareMonitor 的 .NET 库:
LHM 依赖 pythonnet + .NET Framework 运行时,打包体积大、启动慢,而且在
非 .NET 配置的机器上容易因程序集版本绑定失败而静默枚举不到硬件。
直接走 PawnIO 的官方 C API 只有三个调用,纯 ctypes 即可,无任何运行时依赖。

使用前提:系统里需要安装并运行 PawnIO 内核驱动。
    https://github.com/n3v/PawnIO
检测:注册表 HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\PawnIO
"""

from __future__ import annotations

import ctypes
import sys
import threading
from pathlib import Path
from typing import Optional, Sequence

from .. import resources

# PawnIO 的 HRESULT:0x80070057 表示"函数名或参数不合法",通常意味着
# 模块没加载成功或调用约定不对,而不是硬件读失败。
_E_INVALIDARG = 0x80070057

_CREATE_NO_WINDOW = 0x08000000


class PawnIoUnavailable(RuntimeError):
    """PawnIO 驱动不可用(未安装 / 未运行 / 无权限)。"""


class PawnIoModule:
    """已加载进 PawnIO 驱动的一个 AMX 模块。"""

    def __init__(self, client: "PawnIoClient", handle: int, name: str) -> None:
        self._client = client
        self._handle = handle
        self.name = name

    def execute(
        self, fn: str, inputs: Sequence[int] = (), out_count: int = 4
    ) -> Optional[list[int]]:
        """调用模块里的一个函数,返回输出数组;失败返回 None。"""
        return self._client.execute(self._handle, fn, inputs, out_count)

    def close(self) -> None:
        self._client.close_module(self._handle)
        self._handle = 0


class PawnIoClient:
    """PawnIO 用户态客户端。"""

    def __init__(self, lib_path: Optional[Path] = None) -> None:
        self._lib = None
        self._handle = None
        self._lock = threading.Lock()
        self._modules: list[PawnIoModule] = []
        self._lib_path = lib_path or self._default_lib_path()
        self.error: Optional[str] = None

    # ---------------------------------------------------------------- 路径

    @staticmethod
    def _default_lib_path() -> Optional[Path]:
        """优先用随程序分发的副本,其次用系统安装的。"""
        bundled = resources.vendor_dir() / "PawnIOLib.dll"
        if bundled.is_file():
            return bundled
        installed = Path(r"C:\Program Files\PawnIO\PawnIOLib.dll")
        if installed.is_file():
            return installed
        return None

    @staticmethod
    def module_path(name: str) -> Optional[Path]:
        path = resources.vendor_dir() / "modules" / name
        return path if path.is_file() else None

    @staticmethod
    def is_installed() -> bool:
        """通过注册表判断 PawnIO 是否已安装。"""
        if sys.platform != "win32":
            return False
        try:
            import winreg

            for hive, path in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"),
            ):
                try:
                    with winreg.OpenKey(hive, path) as key:
                        winreg.QueryValueEx(key, "DisplayVersion")
                        return True
                except OSError:
                    continue
        except Exception:
            pass
        return False

    # ---------------------------------------------------------------- 生命周期

    def open(self) -> bool:
        """打开 PawnIO 执行器。失败时返回 False 并填写 self.error。"""
        if self._handle is not None:
            return True

        if not self._lib_path or not self._lib_path.is_file():
            self.error = f"找不到 PawnIOLib.dll(查找路径:{self._lib_path})"
            return False

        try:
            lib = ctypes.CDLL(str(self._lib_path))
        except OSError as exc:
            self.error = f"加载 PawnIOLib.dll 失败: {exc}"
            return False

        lib.pawnio_open.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.pawnio_open.restype = ctypes.c_long
        lib.pawnio_load.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_size_t,
        ]
        lib.pawnio_load.restype = ctypes.c_long
        lib.pawnio_execute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_longlong),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_longlong),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.pawnio_execute.restype = ctypes.c_long
        lib.pawnio_close.argtypes = [ctypes.c_void_p]
        lib.pawnio_close.restype = ctypes.c_long

        handle = ctypes.c_void_p()
        hr = lib.pawnio_open(ctypes.byref(handle))
        if hr != 0 or not handle.value:
            self.error = (
                "PawnIO 驱动未运行或当前进程无权访问"
                f"(HRESULT=0x{hr & 0xFFFFFFFF:08X})。"
                "请确认已安装 PawnIO 并以管理员身份运行。"
            )
            return False

        self._lib = lib
        self._handle = handle
        self.error = None
        return True

    def load_module(self, name: str) -> Optional[PawnIoModule]:
        """加载一个 AMX 模块(IntelMSR.bin 等)。失败返回 None。"""
        if self._handle is None:
            return None
        path = self.module_path(name)
        if path is None:
            self.error = f"缺少模块文件:{name}"
            return None

        try:
            blob = path.read_bytes()
        except OSError as exc:
            self.error = f"读取模块 {name} 失败: {exc}"
            return None

        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        hr = self._lib.pawnio_load(self._handle, buf, len(blob))
        if hr != 0:
            self.error = f"加载 PawnIO 模块 {name} 失败(0x{hr & 0xFFFFFFFF:08X})"
            return None

        module = PawnIoModule(self, self._handle.value, name)
        self._modules.append(module)
        return module

    def execute(
        self,
        handle: int,
        fn: str,
        inputs: Sequence[int] = (),
        out_count: int = 4,
    ) -> Optional[list[int]]:
        """在模块中执行一个函数。

        PawnIO 的执行器句柄不是线程安全的,这里用锁串行化。
        """
        if self._lib is None or self._handle is None:
            return None

        in_count = len(inputs)
        in_buf = (ctypes.c_longlong * max(in_count, 1))()
        for i, value in enumerate(inputs):
            in_buf[i] = value
        out_buf = (ctypes.c_longlong * max(out_count, 1))()
        returned = ctypes.c_size_t(0)

        with self._lock:
            hr = self._lib.pawnio_execute(
                self._handle,
                fn.encode("ascii"),
                in_buf,
                in_count,
                out_buf,
                out_count,
                ctypes.byref(returned),
            )
        if hr != 0:
            return None
        return [int(out_buf[i]) for i in range(out_count)]

    def close_module(self, handle: int) -> None:
        # PawnIO 的模块随执行器一起释放,这里只清掉本地引用
        self._modules = [m for m in self._modules if m._handle != handle]

    def close(self) -> None:
        if self._lib is not None and self._handle is not None:
            try:
                self._lib.pawnio_close(self._handle)
            except Exception:
                pass
        self._handle = None
        self._lib = None


# ---------------------------------------------------------------- 线程绑核

_kernel32 = None
if sys.platform == "win32":
    try:
        _kernel32 = ctypes.windll.kernel32
        _kernel32.GetCurrentThread.restype = ctypes.c_void_p
        _kernel32.SetThreadAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        _kernel32.SetThreadAffinityMask.restype = ctypes.c_void_p
    except Exception:  # pragma: no cover - 极端环境
        _kernel32 = None


def pin_current_thread(core_id: int) -> bool:
    """把当前线程绑定到指定逻辑核。

    MSR 是按核的,想读第 N 核的温度就必须让读 MSR 的线程跑在第 N 核上。
    """
    if _kernel32 is None:
        return False
    try:
        thread = _kernel32.GetCurrentThread()
        mask = 1 << core_id
        prev = _kernel32.SetThreadAffinityMask(thread, mask)
        return bool(prev)
    except Exception:
        return False


def unpin_current_thread() -> None:
    """恢复线程亲和性(交还系统调度)。"""
    if _kernel32 is None:
        return
    try:
        thread = _kernel32.GetCurrentThread()
        _kernel32.SetThreadAffinityMask(thread, 0xFFFFFFFFFFFFFFFF)
    except Exception:
        pass
