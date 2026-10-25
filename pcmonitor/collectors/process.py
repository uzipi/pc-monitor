"""进程采集:CPU / 内存占用 Top N。

psutil 的进程 CPU 占用是"距上次调用"的增量,所以必须保留 Process 对象
跨采样复用,每次都新建对象只会得到 0。
"""

from __future__ import annotations

import psutil

from ..models import ProcessStat, ProcessTop


class ProcessCollector:
    def __init__(self) -> None:
        self._cache: dict[int, psutil.Process] = {}
        # psutil 的进程 CPU 占用是"单核百分比",多线程进程会超过 100。
        # 除以逻辑核数才是任务管理器那种 0~100% 的口径。
        self._cpu_count = psutil.cpu_count(logical=True) or 1

    def sample(self, top: int = 10) -> ProcessTop:
        result = ProcessTop()
        stats: list[ProcessStat] = []
        alive: dict[int, psutil.Process] = {}

        for proc in psutil.process_iter(["pid", "name", "username", "status"]):
            try:
                pid = proc.info["pid"]
                alive[pid] = proc
                cached = self._cache.get(pid)
                if cached is None:
                    # 第一次见到这个进程,先 prime 一次,下个周期才有 CPU 数据
                    proc.cpu_percent(None)
                    self._cache[pid] = proc
                    cached = proc

                stat = ProcessStat(
                    pid=pid,
                    name=proc.info.get("name") or "?",
                    username=proc.info.get("username") or "",
                    status=proc.info.get("status") or "",
                )
                stat.cpu_percent = cached.cpu_percent(None) / self._cpu_count
                try:
                    mem = cached.memory_info()
                    stat.memory_bytes = mem.rss
                    stat.memory_percent = cached.memory_percent()
                    stat.threads = cached.num_threads()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                stats.append(stat)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        self._cache = alive
        result.total_processes = len(stats)
        result.total_threads = sum(s.threads for s in stats)
        # "System Idle Process" 是内核的空闲时间累加器,不是真实进程,
        # 把它排进 CPU 榜只会误导。
        ranked = [s for s in stats if s.name.lower() != "system idle process"]
        result.by_cpu = sorted(
            ranked, key=lambda s: s.cpu_percent, reverse=True
        )[:top]
        result.by_memory = sorted(
            ranked, key=lambda s: s.memory_bytes, reverse=True
        )[:top]
        return result
