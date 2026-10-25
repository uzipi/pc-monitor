"""采集器共用的基础设施。"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional


class Timer:
    """单调时钟计时器,用于计算采样间隔。"""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def reset(self) -> None:
        self._start = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start


class BackgroundRefresher:
    """后台线程定期刷新数据,前台只读缓存。

    硬盘 SMART、内存条信息都要过一遍 PowerShell,单次 1 秒多。
    放在采集路径里会让界面每 10 秒卡一下,放到后台线程里就完全无感了。
    """

    def __init__(
        self,
        refresh: Callable[[], Any],
        interval: float = 10.0,
        name: str = "refresher",
    ) -> None:
        self._refresh = refresh
        self._interval = interval
        self._name = name
        self._lock = threading.Lock()
        self._value: Any = None
        self._error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=self._name
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                value = self._refresh()
            except Exception as exc:  # 后台线程不能因为一次失败就退出
                with self._lock:
                    self._error = str(exc)
            else:
                with self._lock:
                    self._value = value
                    self._error = None
            self._ready.set()
            self._stop.wait(self._interval)

    def wait_ready(self, timeout: float = 3.0) -> bool:
        """等第一轮刷新结束。返回 True 表示已有结果。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready.wait(timeout=0.05):
                value, _ = self.get()
                return value is not None
            if self._stop.is_set():
                return False
        return False

    def get(self) -> tuple[Any, Optional[str]]:
        """返回 (最新值, 错误信息)。"""
        with self._lock:
            return self._value, self._error

    def refresh_now(self) -> None:
        """强制同步刷新一次(测试用)。"""
        try:
            value = self._refresh()
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
        else:
            with self._lock:
                self._value = value
                self._error = None


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
