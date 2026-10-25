"""磁盘采集:分区容量使用率 + 每块物理盘的读写速率。

容量来自 psutil.disk_partitions/disk_usage,读写速率来自
psutil.disk_io_counters(perdisk=True) 的累计值做差。

性能上有一处值得优化:`psutil.disk_partitions()` 要枚举所有卷,实测 2.1ms,
而分区列表几乎不变(热插拔的盘才会多一个)。所以列表缓存 30 秒,
只有 `disk_usage()`(0.04ms)每次都重新读。
"""

from __future__ import annotations

import time

import psutil

from ..models import DiskIo, DiskSnapshot
from .base import DeltaCounter

#: 分区列表的缓存时间(秒)。热插拔的盘最多晚这么久才出现。
_PARTITION_TTL = 30.0


class DiskCollector:
    def __init__(self) -> None:
        self._delta = DeltaCounter()
        self._partitions: list = []
        self._partitions_at = 0.0

    def _get_partitions(self) -> list:
        now = time.monotonic()
        if not self._partitions or now - self._partitions_at > _PARTITION_TTL:
            try:
                self._partitions = psutil.disk_partitions(all=False)
            except Exception:
                self._partitions = []
            self._partitions_at = now
        return self._partitions

    def sample(self) -> list[DiskSnapshot]:
        disks: list[DiskSnapshot] = []
        seen: set[str] = set()

        partitions = self._get_partitions()

        for part in partitions:
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue

            disk = DiskSnapshot(
                device=part.device,
                mountpoint=part.mountpoint,
                filesystem=part.fstype,
                total_bytes=int(usage.total),
                used_bytes=int(usage.used),
                percent=float(usage.percent),
            )
            disks.append(disk)
            seen.add(part.mountpoint.rstrip("\\").upper())

        # 把物理盘(整盘)也列出来,便于对上温度/健康度
        try:
            io_counters = psutil.disk_io_counters(perdisk=True)
        except Exception:
            io_counters = {}

        for device, c in io_counters.items():
            key = device.rstrip("\\").upper()
            if key in seen:
                continue
            disks.append(
                DiskSnapshot(
                    device=device,
                    mountpoint=device,
                    filesystem="",
                    total_bytes=0,
                    used_bytes=0,
                    percent=0.0,
                )
            )

        return disks

    def sample_io(self) -> list[DiskIo]:
        """每块物理盘的读写速率。"""
        result: list[DiskIo] = []
        try:
            counters = psutil.disk_io_counters(perdisk=True)
        except Exception:
            return result

        for device, c in counters.items():
            io = DiskIo(name=device)
            io.read_bytes_per_sec = self._delta.rate(
                f"{device}:read", float(c.read_bytes)
            )
            io.write_bytes_per_sec = self._delta.rate(
                f"{device}:write", float(c.write_bytes)
            )
            io.read_bytes_total = int(c.read_bytes)
            io.write_bytes_total = int(c.write_bytes)
            result.append(io)

        result.sort(key=lambda i: -(i.read_bytes_per_sec + i.write_bytes_per_sec))
        return result
