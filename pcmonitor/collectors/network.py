"""网络采集:各网卡上下行速率。

Windows 上同时存在物理网卡和一堆虚拟网卡(WSL、Hyper-V、VPN、VirtualBox)。
虚拟网卡常常是物理网卡的桥接,直接汇总会把同一份流量重复计算,
所以额外维护一份"仅物理网卡"的汇总。
"""

from __future__ import annotations

import psutil

from ..models import NetworkIface, NetworkSnapshot
from .base import DeltaCounter

# 明确属于虚拟/回环的网卡名前缀
_VIRTUAL_PREFIXES = (
    "loopback",
    "pseudo",
    "teredo",
    "isatap",
    "vmware",
    "virtualbox",
    "vethernet",
    "hyper-v",
    "wsl",
    "tailscale",
    "zerotier",
    "npcap",
    "bluetooth",
    "docker",
    "vpn",
)

# 明确属于物理网卡的网卡名前缀(中英文都覆盖)
_PHYSICAL_PREFIXES = (
    "以太网",
    "ethernet",
    "wi-fi",
    "wifi",
    "wlan",
    "wireless",
    "无线",
)


def _is_physical(name: str) -> bool:
    lowered = name.lower()
    if lowered.startswith(_VIRTUAL_PREFIXES):
        return False
    if lowered.startswith(_PHYSICAL_PREFIXES):
        return True
    # 没把握的按虚拟处理:宁可少算,也不要重复计数
    return False


class NetworkCollector:
    def __init__(self) -> None:
        self._delta = DeltaCounter()

    def sample(self) -> NetworkSnapshot:
        snap = NetworkSnapshot()
        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception:
            counters = {}

        for name, c in counters.items():
            iface = NetworkIface(name=name)
            iface.rx_bytes_per_sec = self._delta.rate(
                f"{name}:rx", float(c.bytes_recv)
            )
            iface.tx_bytes_per_sec = self._delta.rate(
                f"{name}:tx", float(c.bytes_sent)
            )
            iface.rx_bytes_total = int(c.bytes_recv)
            iface.tx_bytes_total = int(c.bytes_sent)
            iface.rx_packets_per_sec = self._delta.rate(
                f"{name}:rxp", float(c.packets_recv)
            )
            iface.tx_packets_per_sec = self._delta.rate(
                f"{name}:txp", float(c.packets_sent)
            )
            snap.interfaces.append(iface)

            snap.rx_bytes_per_sec += iface.rx_bytes_per_sec
            snap.tx_bytes_per_sec += iface.tx_bytes_per_sec
            if _is_physical(name):
                snap.physical_rx_bytes_per_sec += iface.rx_bytes_per_sec
                snap.physical_tx_bytes_per_sec += iface.tx_bytes_per_sec

        # 按速率排序,活跃的网卡排在前面
        snap.interfaces.sort(
            key=lambda i: -(i.rx_bytes_per_sec + i.tx_bytes_per_sec)
        )
        return snap
