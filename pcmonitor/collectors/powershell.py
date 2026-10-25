"""PowerShell 调用helper。

Windows 上很多硬件信息(CPU/主板 ACPI 热区、硬盘 SMART 健康度)没有
Python 原生 API,走 WMI/CIM 查询最直接。这里统一封装进程调用、超时和
输出解析,避免每个采集器各写一遍。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Optional

_CREATE_NO_WINDOW = 0x08000000


def run_powershell(script: str, timeout: float = 15.0) -> Optional[str]:
    """执行一段 PowerShell 脚本,返回 stdout;失败返回 None。

    用 -NoProfile 跳过用户 profile 里的自定义函数,避免被环境干扰。
    CREATE_NO_WINDOW 防止每次查询都闪一个黑框。
    """
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = _CREATE_NO_WINDOW

    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def run_powershell_json(script: str, timeout: float = 15.0) -> Any:
    """执行脚本并把 stdout 当 JSON 解析。

    失败、空输出都返回 None。脚本需要自己输出 ConvertTo-Json 的结果。
    """
    out = run_powershell(script, timeout=timeout)
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def as_list(value: Any) -> list:
    """ConvertTo-Json 对单个元素会输出对象而不是数组,这里统一成列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
