"""格式化与终端控制小工具。

两个容易踩的坑:
1. 中文/日文是双宽字符,`len()` 和 `ljust()` 会算错,导致表格错位。
   这里统一用 `dwidth()` 按 East Asian Width 计算显示宽度。
2. Windows 传统控制台默认不解析 ANSI 转义序列,必须显式打开 VT 模式。
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from typing import Iterable, Iterator, Optional, Sequence

# ANSI 转义序列。上色之后字符串里会混进这些字符,
# 算宽度时必须忽略它们,否则表格的右边框会全部错位。
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# ---------------------------------------------------------------- 终端能力


def enable_vt() -> bool:
    """尝试为当前控制台开启 ANSI 转义支持。返回是否成功。

    失败不致命:降级为无色模式,靠调用方的 --no-color 语义处理。
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle is None or handle == -1:
            return False
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(
            kernel32.SetConsoleMode(
                handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
        )
    except Exception:
        return False


def supports_color(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


# ---------------------------------------------------------------- 宽度计算


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if ch in ("", "﻿"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def dwidth(text: str) -> int:
    """字符串在等宽终端里的显示宽度(忽略 ANSI 转义序列)。"""
    return sum(char_width(c) for c in _ANSI_RE.sub("", text))


def _ansi_tokens(text: str) -> Iterator[str]:
    """把字符串拆成"转义序列"和"普通文本"两类片段。"""
    pos = 0
    for match in _ANSI_RE.finditer(text):
        if match.start() > pos:
            yield text[pos : match.start()]
        yield match.group(0)
        pos = match.end()
    if pos < len(text):
        yield text[pos:]


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    """按显示宽度截断,超长时补省略号。保留已有的颜色设置。"""
    if dwidth(text) <= width:
        return text
    if width <= 0:
        return ""
    target = width - dwidth(ellipsis)
    out: list[str] = []
    used = 0
    colored = False
    for token in _ansi_tokens(text):
        if token.startswith("\x1b"):
            colored = True
            out.append(token)
            continue
        for ch in token:
            w = char_width(ch)
            if used + w > target:
                if colored:
                    out.append("\033[0m")
                return "".join(out) + ellipsis
            out.append(ch)
            used += w
    return "".join(out) + ellipsis


def pad(text: str, width: int, align: str = "left") -> str:
    """按显示宽度补齐。"""
    text = truncate(text, width)
    space = width - dwidth(text)
    if space <= 0:
        return text
    if align == "right":
        return " " * space + text
    if align == "center":
        left = space // 2
        return " " * left + text + " " * (space - left)
    return text + " " * space


# ---------------------------------------------------------------- 颜色


class Style:
    """ANSI 样式集合。`enabled` 为 False 时所有方法原样返回。"""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self.enabled or not text:
            return text
        return f"\033[{code}m{text}\033[0m"

    def reset(self, t: str) -> str:
        return self._wrap("0", t)

    def bold(self, t: str) -> str:
        return self._wrap("1", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)

    def green(self, t: str) -> str:
        return self._wrap("32", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)

    def blue(self, t: str) -> str:
        return self._wrap("34", t)

    def magenta(self, t: str) -> str:
        return self._wrap("35", t)

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)

    def gray(self, t: str) -> str:
        return self._wrap("90", t)

    def bright_red(self, t: str) -> str:
        return self._wrap("91", t)

    def bright_green(self, t: str) -> str:
        return self._wrap("92", t)

    def bright_yellow(self, t: str) -> str:
        return self._wrap("93", t)

    def bright_blue(self, t: str) -> str:
        return self._wrap("94", t)

    def bright_magenta(self, t: str) -> str:
        return self._wrap("95", t)

    def bright_cyan(self, t: str) -> str:
        return self._wrap("96", t)


# ---------------------------------------------------------------- 图形元素

BLOCK_FULL = "█"
BLOCK_EMPTY = "░"
SPARK_CHARS = "▁▂▃▄▅▆▇█"


def bar(percent: float, width: int = 20, style: Optional[Style] = None) -> str:
    """水平进度条。

    percent 会被裁剪到 [0, 100]。filled 为空时返回等宽的空格,
    这样进度为 0 的行也能保持列对齐。
    """
    percent = max(0.0, min(100.0, percent))
    filled = int(round(percent / 100.0 * width))
    filled = max(0, min(width, filled))
    bar_text = BLOCK_FULL * filled + BLOCK_EMPTY * (width - filled)
    if style is None:
        return bar_text
    return style.bold(style._wrap("38;5;39", bar_text))


def usage_color(percent: float, style: Style) -> str:
    """按占用率上色:绿 -> 黄 -> 红。"""
    if percent >= 90:
        return style.bright_red
    if percent >= 75:
        return style.bright_yellow
    if percent >= 50:
        return style.yellow
    return style.bright_green


def temp_color(temp_c: float, style: Style) -> str:
    """按温度上色。阈值按消费级 CPU/SSD 常识设定。"""
    if temp_c >= 85:
        return style.bright_red
    if temp_c >= 75:
        return style.bright_yellow
    if temp_c >= 60:
        return style.yellow
    return style.bright_green


def sparkline(values: Sequence[float], width: int = 40) -> str:
    """把一串数值画成迷你曲线图(横向)。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return " " * width
    if len(vals) > width:
        vals = vals[-width:]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        mid = SPARK_CHARS[len(SPARK_CHARS) // 2]
        return mid * len(vals) + " " * (width - len(vals))
    scale = (len(SPARK_CHARS) - 1) / (hi - lo)
    out = "".join(SPARK_CHARS[int((v - lo) * scale)] for v in vals)
    return out + " " * (width - len(out))


# ---------------------------------------------------------------- 数值格式化


def fmt_bytes(n: Optional[float], precision: int = 1) -> str:
    if n is None:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(n)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.{precision}f} {units[idx]}"


def fmt_rate(n: Optional[float]) -> str:
    """速率:自动选单位,固定 1 位小数。"""
    if n is None:
        return "-"
    return fmt_bytes(n) + "/s"


def fmt_percent(n: Optional[float], width: int = 0) -> str:
    if n is None:
        return "-".rjust(width)
    return f"{n:.0f}%".rjust(width)


def fmt_temp(c: Optional[float]) -> str:
    if c is None:
        return "--.-°C"
    return f"{c:.1f}°C"


def fmt_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "-"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}天{hours}小时"
    if hours:
        return f"{hours}小时{minutes}分"
    return f"{minutes}分"


def fmt_human_time(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def join_nonempty(parts: Iterable[str], sep: str = "  ") -> str:
    return sep.join(p for p in parts if p)
