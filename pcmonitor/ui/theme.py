"""终端主题:颜色、边框、标题。

界面全部用等宽字符画,所以每一处宽度计算都必须走 `dwidth()`,
否则中文一多就会错位。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from ..util import Style, dwidth, pad

# 边框字符。用圆角更适合终端,但部分老终端字体渲染不佳,
# 所以保留一套直角边框备用。
BOX = {
    "tl": "╭",
    "tr": "╮",
    "bl": "╰",
    "br": "╯",
    "h": "─",
    "v": "│",
    "lt": "├",
    "rt": "┤",
}

# 终端一次绘制不完时不要把行拆散
MIN_WIDTH = 76
MAX_WIDTH = 160


def terminal_size(default: tuple[int, int] = (100, 40)) -> tuple[int, int]:
    try:
        size = shutil.get_terminal_size(fallback=default)
        return max(MIN_WIDTH, min(MAX_WIDTH, size.columns)), size.lines
    except Exception:
        return default


@dataclass
class Theme:
    """一份配色方案。`enabled=False` 时所有样式都退化成原样输出。"""

    style: Style
    width: int = 100

    # 语义色
    accent: str = "bright_cyan"
    good: str = "bright_green"
    warn: str = "yellow"
    hot: str = "bright_yellow"
    critical: str = "bright_red"
    muted: str = "gray"
    label: str = "cyan"
    down: str = "bright_green"
    up: str = "bright_blue"

    def paint(self, name: str, text: str) -> str:
        """按语义色名上色。"""
        fn = getattr(self.style, name, None)
        if fn is None:
            return text
        return fn(text)

    # ------------------------------------------------------------ 边框

    def top(self, title: str = "") -> str:
        line = BOX["tl"] + BOX["h"] * (self.width - 2) + BOX["tr"]
        if title:
            label = f" {title} "
            inner = self.width - 2
            # 标题占用的宽度里包含两侧的横线
            fill = inner - dwidth(label)
            line = (
                BOX["tl"]
                + BOX["h"]
                + self.paint(self.accent, self.style.bold(label))
                + BOX["h"] * max(0, fill - 1)
                + BOX["tr"]
            )
        return line

    def bottom(self) -> str:
        return BOX["bl"] + BOX["h"] * (self.width - 2) + BOX["br"]

    def divider(self) -> str:
        return BOX["lt"] + BOX["h"] * (self.width - 2) + BOX["rt"]

    def row(self, content: str = "") -> str:
        """一行内容,自动裁剪/补齐到边框宽度内。"""
        inner = self.width - 4
        body = pad(content, inner)
        return f"{BOX['v']} {body} {BOX['v']}"

    def kv(self, key: str, value: str, key_width: int = 10) -> str:
        """键值对,键右对齐,便于列对齐。"""
        return (
            self.paint(self.label, pad(key, key_width))
            + " "
            + value
        )

    def title_bar(self, text: str) -> str:
        return self.paint(self.accent, self.style.bold(text))
