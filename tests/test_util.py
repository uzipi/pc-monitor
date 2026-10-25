"""终端宽度/格式化相关的单元测试。

这里的用例大多来自实际踩过的坑:中文双宽、ANSI 转义序列、
温度/占用率配色阈值。
"""

from __future__ import annotations

from pcmonitor.util import (
    Style,
    dwidth,
    fmt_bytes,
    fmt_duration,
    fmt_rate,
    pad,
    sparkline,
    temp_color,
    truncate,
    usage_color,
)


def test_dwidth_counts_cjk_as_double_width():
    assert dwidth("abc") == 3
    assert dwidth("中文") == 4
    assert dwidth("中文abc") == 7


def test_dwidth_ignores_ansi_sequences():
    plain = dwidth("中文")
    colored = dwidth("\033[32m中文\033[0m")
    assert colored == plain


def test_truncate_respects_display_width():
    # 省略号本身占 1 列,所以双宽字符只能放下一个
    assert truncate("中文测试", 4) == "中…"
    assert dwidth(truncate("中文测试", 4)) <= 4
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 10) == "abc"


def test_truncate_keeps_ansi_intact():
    text = "\033[31mabcdef\033[0m"
    out = truncate(text, 4)
    assert dwidth(out) == 4
    assert out.endswith("…")
    assert "\033[31m" in out


def test_pad_fills_to_display_width():
    assert dwidth(pad("中文", 10)) == 10
    assert dwidth(pad("abcdef", 4)) == 4  # 超长时按宽度截断
    assert dwidth(pad("\033[32m中文\033[0m", 10)) == 10


def test_pad_right_and_center():
    assert pad("ab", 6, "right") == "    ab"
    assert pad("ab", 6, "center") == "  ab  "


def test_style_disabled_returns_plain_text():
    style = Style(enabled=False)
    assert style.red("abc") == "abc"


def test_style_enabled_wraps_with_codes():
    style = Style(enabled=True)
    out = style.red("abc")
    assert out.startswith("\033[31m")
    assert out.endswith("\033[0m")
    assert dwidth(out) == 3


def test_usage_and_temp_thresholds():
    style = Style(enabled=True)
    assert usage_color(10, style) != usage_color(95, style)
    assert temp_color(50, style) != temp_color(90, style)


def test_fmt_bytes():
    assert fmt_bytes(0) == "0 B"
    assert fmt_bytes(1024) == "1.0 KB"
    assert fmt_bytes(1024 ** 3) == "1.0 GB"
    assert fmt_bytes(None) == "-"


def test_fmt_rate_appends_per_second():
    assert fmt_rate(1024).endswith("/s")
    assert fmt_rate(None) == "-"


def test_fmt_duration():
    assert fmt_duration(0) == "-"
    assert fmt_duration(90) == "1分"
    assert fmt_duration(3700) == "1小时1分"
    assert fmt_duration(90000) == "1天1小时"


def test_sparkline_width_is_stable():
    values = [1.0, 5.0, 3.0, 9.0]
    assert dwidth(sparkline(values, width=10)) == 10
    assert dwidth(sparkline([], width=8)) == 8
    # 全部相同的值也要占满宽度
    assert dwidth(sparkline([2.0] * 5, width=6)) == 6
