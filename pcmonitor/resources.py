"""资源路径解析。

打包成 exe 后,资源位于 PyInstaller 解压出来的临时目录(sys._MEIPASS);
源码运行时位于项目根目录。两边都要能找到 vendor/ 下的第三方 DLL。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def vendor_dir() -> Path:
    """第三方组件目录(PawnIO 客户端库与 AMX 模块)。"""
    return app_root() / "vendor" / "pawnio"


def module_dir() -> Path:
    """PawnIO AMX 模块目录。"""
    return vendor_dir() / "modules"


def lhm_dll() -> Optional[Path]:
    """兼容旧路径:LibreHardwareMonitorLib.dll 是否存在。"""
    candidate = app_root() / "vendor" / "librehardwaremonitor" / "LibreHardwareMonitorLib.dll"
    return candidate if candidate.is_file() else None


def data_dir() -> Path:
    """用户数据目录(历史记录、配置等)。"""
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Local" / "pc-monitor"
    else:
        base = Path.home() / ".local" / "share" / "pc-monitor"
    return base
