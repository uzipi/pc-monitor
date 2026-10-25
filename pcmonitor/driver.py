"""PawnIO 驱动的检测、安装与提权。

程序自带官方安装器 `PawnIO_setup.exe`(上游在二进制里明确写着
"This installer can be redistributed unmodified."),所以用户在新机器上
不用再去 pawnio.eu 下载:第一次运行时检测驱动,没装就用内置安装器提权装上,
装完直接跑。

有两件事必须记住,否则会踩坑:

1. **官方安装器靠自己的进程名判断角色**。反汇编可以看到它调用的是
   `wcsstr(argv[0], "uninstall.exe")` —— 路径里只要出现 "uninstall.exe",
   它就认为自己是卸载器,于是 `-install` 会被自己抵消掉,弹一个
   "Please specify either -install or -uninstall" 就退出。
   所以内置副本必须叫 `PawnIO_setup.exe`,而且**存放路径里也不能出现
   "uninstall.exe"**。
2. **驱动装好之后仍然要管理员权限**。PawnIO 暴露的是 MSR / I/O 端口这类
   ring0 能力,设备对象只放行管理员;普通进程打开会拿到
   0x80070005(ACCESS_DENIED)。所以非管理员启动时需要自我提权。
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from . import resources

# 内置安装器的文件名。改名会让官方安装器把自己当成卸载器(见模块 docstring)。
INSTALLER_NAME = "PawnIO_setup.exe"

# 官方安装器判定"自己是卸载器"用的关键字
_UNINSTALLER_MARK = "uninstall.exe"

_SERVICE_KEY = r"SYSTEM\CurrentControlSet\Services\PawnIO"
#: 设备节点注册表项(ROOT\PAWNIO\0000 那一支)
_DEVICE_KEY = r"SYSTEM\CurrentControlSet\Enum\ROOT\PAWNIO"

#: pawnio_open 失败时代表"没权限"的 HRESULT
ACCESS_DENIED_HRESULT = 0x80070005

_STILL_ACTIVE = 259

_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_NOASYNC = 0x00000100
_SEE_MASK_FLAG_NO_UI = 0x00000400
_SW_HIDE = 0


@dataclass
class InstallResult:
    """一次驱动安装的结果。"""

    ok: bool
    message: str
    code: int = 0


# ---------------------------------------------------------------- 检测


def installer_path() -> Optional[Path]:
    """内置安装器的路径(打包后在 PyInstaller 解压目录里)。"""
    path = resources.vendor_dir() / INSTALLER_NAME
    return path if path.is_file() else None


def _registry_key_exists(path: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path):
            return True
    except OSError:
        return False


def is_installed() -> bool:
    """驱动是否已经装好。

    装好之后系统里会留下三样东西:
    * `Services\\PawnIO` —— 驱动服务项;
    * `Enum\\ROOT\\PAWNIO` —— 设备节点(ROOT\\PAWNIO\\0000);
    * `Uninstall\\PawnIO` —— "应用和功能"里的卸载项。

    前两个齐了才算装好。只剩服务项的情况是**卸载到一半**:设备节点已经被
    摘掉(重启后驱动就没了),这时要重新装一次。
    """
    if sys.platform != "win32":
        return False
    try:
        if not _registry_key_exists(_SERVICE_KEY):
            return False
        return _registry_key_exists(_DEVICE_KEY)
    except Exception:
        return False


def is_elevated() -> bool:
    """当前进程是不是以管理员身份在跑。"""
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pawnio_lib_path() -> Optional[Path]:
    """PawnIOLib.dll 的位置:优先用随程序分发的副本,其次用系统装的。"""
    bundled = resources.vendor_dir() / "PawnIOLib.dll"
    if bundled.is_file():
        return bundled
    installed = Path(r"C:\Program Files\PawnIO\PawnIOLib.dll")
    if installed.is_file():
        return installed
    return None


def probe_access() -> Optional[int]:
    """试着打开一次 PawnIO 设备,返回 HRESULT。

    * 0 —— 打开成功,当前进程有权限用驱动。
    * 0x80070005 —— 设备在,但当前进程没有权限(需要管理员)。
    * 其它 HRESULT —— 别的错误。
    * None —— 压根没找到 PawnIOLib.dll,判断不了。
    """
    if sys.platform != "win32":
        return None
    lib_path = _pawnio_lib_path()
    if lib_path is None:
        return None
    try:
        lib = ctypes.CDLL(str(lib_path))
    except OSError:
        return None

    lib.pawnio_open.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    lib.pawnio_open.restype = ctypes.c_long
    lib.pawnio_close.argtypes = [ctypes.c_void_p]
    lib.pawnio_close.restype = ctypes.c_long

    handle = ctypes.c_void_p()
    try:
        hr = lib.pawnio_open(ctypes.byref(handle))
    except Exception:
        return None
    if hr == 0 and handle.value:
        try:
            lib.pawnio_close(handle)
        except Exception:
            pass
        return 0
    return int(hr) & 0xFFFFFFFF


# ---------------------------------------------------------------- 安装


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("fMask", ctypes.c_ulong),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hKeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def _run_elevated(
    exe: str,
    params: str = "",
    timeout: float = 180.0,
    directory: Optional[str] = None,
) -> int:
    """以管理员身份跑一个程序,等它结束并返回退出码。

    走 ShellExecuteExW 的 "runas" 动词,用户会看到 UAC 授权窗口。
    拿不到退出码时返回 -1。
    """
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = ctypes.c_int
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(_ShellExecuteInfo)
    info.fMask = (
        _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC | _SEE_MASK_FLAG_NO_UI
    )
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params or None
    info.lpDirectory = directory or None
    info.nShow = _SW_HIDE

    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())

    handle = info.hProcess
    try:
        kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(code.value)
    finally:
        if handle:
            kernel32.CloseHandle(handle)


def install(timeout: float = 180.0) -> InstallResult:
    """用内置安装器装 PawnIO 驱动(会弹 UAC)。

    官方安装器成功时返回 0,失败时返回一个 NTSTATUS 风格的错误码。
    """
    exe = installer_path()
    if exe is None:
        return InstallResult(False, f"找不到内置安装器 {INSTALLER_NAME}")

    # 安装器把"路径里含 uninstall.exe"当成卸载器,那样 -install 会被抵消
    if _UNINSTALLER_MARK in str(exe).lower():
        return InstallResult(
            False, f"内置安装器路径不能包含 {_UNINSTALLER_MARK}: {exe}"
        )

    try:
        code = _run_elevated(str(exe), "-install -silent", timeout)
    except OSError as exc:
        return InstallResult(False, f"启动安装器失败: {exc}")

    if code == 0:
        return InstallResult(True, "PawnIO 驱动安装成功", 0)
    if code == _STILL_ACTIVE:
        return InstallResult(False, "安装器超时未退出", code)
    return InstallResult(False, f"安装器返回错误码 0x{code:08X}", code)


# ---------------------------------------------------------------- 自我提权


def _quote(arg: str) -> str:
    if not arg or any(ch in arg for ch in ' \t"'):
        return '"' + arg.replace('"', r"\"") + '"'
    return arg


def self_command(argv: Optional[Sequence[str]] = None) -> tuple[str, str]:
    """重新启动当前程序需要的 (可执行文件, 参数串)。"""
    args = list(argv if argv is not None else sys.argv[1:])
    if getattr(sys, "frozen", False):
        return sys.executable, " ".join(_quote(a) for a in args)
    # 源码运行时是 `python -m pcmonitor`,重开一个解释器带上同样的参数
    return sys.executable, " ".join(["-m", "pcmonitor"] + [_quote(a) for a in args])


def elevate_self(argv: Optional[Sequence[str]] = None) -> bool:
    """以管理员身份重新启动当前程序。

    成功发起返回 True,调用方应该立刻退出,让提权后的那个实例接着跑。
    """
    if sys.platform != "win32" or is_elevated():
        return False
    exe, params = self_command(argv)
    try:
        _run_elevated(exe, params, timeout=0, directory=working_directory())
    except OSError:
        return False
    return True


def working_directory() -> Optional[str]:
    """重开进程时用的工作目录(源码运行时需要它来 import pcmonitor)。"""
    if getattr(sys, "frozen", False):
        return None
    return os.getcwd()
