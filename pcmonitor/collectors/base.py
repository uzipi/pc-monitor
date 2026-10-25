"""采集器共用的基础设施。"""

from __future__ import annotations

import time
from typing import Optional


class Timer:
    """单调时钟计时器,用于计算采样间隔。"""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def reset(self) -> None:
        self._start = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start


class DeltaCounter:
    """把"累计计数器"换算成"每秒速率"。

    psutil 的网络/磁盘 IO 接口返回的是开机以来的累计字节数,两次采样相减
    再除以间隔才是速率。这里记录上一次的值,并处理两类异常:

    * 首次采样没有前值 -> 返回 0
    * 计数器被重置(新值小于旧值) -> 返回 0,避免出现巨大的负数或尖峰
    """

    def __init__(self) -> None:
        self._prev: dict[str, tuple[float, float]] = {}

    def rate(
        self, key: str, total: float, elapsed: Optional[float] = None
    ) -> float:
        """返回 key 对应计数器的每秒增量。

        elapsed 为 None 时用内部单调时钟推算。
        """
        now = time.monotonic()
        prev = self._prev.get(key)
        self._prev[key] = (total, now)
        if prev is None:
            return 0.0
        prev_total, prev_time = prev
        dt = elapsed if elapsed is not None else (now - prev_time)
        if dt <= 0:
            return 0.0
        delta = total - prev_total
        if delta < 0:
            # 计数器回退,说明设备被重置或枚举结果变了
            return 0.0
        return delta / dt

    def reset(self) -> None:
        self._prev.clear()
