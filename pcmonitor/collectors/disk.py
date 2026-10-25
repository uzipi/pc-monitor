"""磁盘采集:分区容量使用率 + 每块物理盘的读写速率。

容量来自 psutil.disk_partitions/disk_usage,读写速率来自
psutil.disk_io_counters(perdisk=True) 的累计值做差。
"""

from __future__ import annotations

import psutil

from ..models import DiskIo, DiskSnapshot
from .base import DeltaCounter


class DiskCollector:
    def __init__(self) -> None:
        self._delta = DeltaCounter()

    def sample(self) -> list[DiskSnapshot]:
        disks: list[DiskSnapshot] = []
        seen: set[str] = set()

        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception:
            partitions = []

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
