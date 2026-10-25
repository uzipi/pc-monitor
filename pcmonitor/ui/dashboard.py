"""把一帧 Snapshot 渲染成终端面板。

设计原则:
* 所有"拿不到"的指标都显示成 `-` 或 `不可用`,绝不编造数字。
* 宽表在窄终端里也不会串行——所有行都按显示宽度裁剪。
* 采样频率不高,一帧渲染控制在几毫秒内,不影响被监控的机器。
"""

from __future__ import annotations

from collections import deque
from typing import Iterable, Optional

from ..models import Snapshot
from ..util import (
    bar,
    dwidth,
    fmt_bytes,
    fmt_duration,
    fmt_human_time,
    fmt_rate,
    pad,
    sparkline,
    usage_color,
)
from .theme import Theme

# 概览里的迷你曲线保留多少个采样点
_HISTORY = 120
_SPARK_WIDTH = 16
# 磁盘摘要的曲线更短,给"读/写/温度"这些文字留地方
_DISK_SPARK_WIDTH = 8

#: 显示模式
MODE_OVERVIEW = "overview"
MODE_DETAIL = "detail"
#: 不问用户时的默认模式
DEFAULT_MODE = MODE_OVERVIEW


class Dashboard:
    """有状态的渲染器:保存历史值用于画迷你曲线。

    两种模式:
    * `MODE_OVERVIEW` —— 概览模式,只画概览面板(CPU/内存/GPU/网络/磁盘摘要)
      和提示行,一屏看完;
    * `MODE_DETAIL` —— 详细模式,画全部面板(每核温度、硬盘 SMART、网卡、进程榜)。
    """

    def __init__(
        self, theme: Theme, top: int = 10, mode: str = MODE_DETAIL
    ) -> None:
        self.theme = theme
        self.top = top
        self.mode = mode
        self._history: dict[str, deque] = {
            key: deque(maxlen=_HISTORY)
            for key in ("cpu", "mem", "gpu", "net_rx", "net_tx", "cpu_temp")
        }

    # ------------------------------------------------------------ 入口

    def render(self, snap: Snapshot, warnings: Optional[Iterable[str]] = None) -> list[str]:
        self._record(snap)
        lines: list[str] = []
        lines.extend(self._header(snap))
        lines.extend(self._overview(snap, include_disk=self.mode == MODE_OVERVIEW))
        if self.mode != MODE_OVERVIEW:
            lines.extend(self._cpu_panel(snap))
            lines.extend(self._gpu_panel(snap))
            lines.extend(self._memory_panel(snap))
            lines.extend(self._disk_panel(snap))
            lines.extend(self._network_panel(snap))
            lines.extend(self._process_panel(snap))
        lines.extend(self._footer(snap, warnings or []))
        return lines

    # ------------------------------------------------------------ 历史

    def _record(self, snap: Snapshot) -> None:
        self._history["cpu"].append(snap.cpu.usage_percent)
        self._history["mem"].append(snap.memory.percent)
        if snap.gpus and snap.gpus[0].usage_percent is not None:
            self._history["gpu"].append(snap.gpus[0].usage_percent)
        else:
            self._history["gpu"].append(0.0)
        self._history["net_rx"].append(snap.network.physical_rx_bytes_per_sec)
        self._history["net_tx"].append(snap.network.physical_tx_bytes_per_sec)
        if snap.cpu.package_temp_c is not None:
            self._history["cpu_temp"].append(snap.cpu.package_temp_c)
        # 磁盘的曲线按盘分开存,概览里每块盘各画各的
        for key, _model, _temp, _health, io in self._disk_entries(snap):
            read_key = f"disk_read:{key}"
            write_key = f"disk_write:{key}"
            if read_key not in self._history:
                self._history[read_key] = deque(maxlen=_HISTORY)
            if write_key not in self._history:
                self._history[write_key] = deque(maxlen=_HISTORY)
            self._history[read_key].append(io.read_bytes_per_sec if io else 0.0)
            self._history[write_key].append(io.write_bytes_per_sec if io else 0.0)

    def _spark(self, key: str, width: int = _SPARK_WIDTH) -> str:
        return sparkline(list(self._history.get(key, ())), width=width)

    # ------------------------------------------------------------ 各区块

    def _header(self, snap: Snapshot) -> list[str]:
        t = self.theme
        host = snap.host
        sep = "  ·  "
        mode_label = "概览模式" if self.mode == MODE_OVERVIEW else "详细模式"

        # 头部按重要性排序,窄终端下装不下的先砍掉(最后砍系统版本),
        # 保证整行永远不超过终端宽度 —— 头部不是面板,不会被自动裁剪。
        parts = [t.title_bar("PC Monitor")]
        optional = [
            t.paint(t.accent, mode_label),
            t.paint(t.accent, fmt_human_time(snap.timestamp)),
            t.paint(t.muted, host.hostname),
        ]
        if host.uptime_seconds:
            optional.append(
                t.paint(t.muted, f"运行 {fmt_duration(host.uptime_seconds)}")
            )
        optional.append(t.paint(t.muted, host.os_name))

        for part in optional:
            if dwidth(sep.join(parts + [part])) > t.width:
                break
            parts.append(part)

        line = t.paint(t.muted, sep).join(parts)
        return [line, t.paint(t.muted, "─" * t.width)]

    def _overview(self, snap: Snapshot, include_disk: bool = False) -> list[str]:
        t = self.theme
        bar_width = 18
        body: list[str] = []

        # ---- CPU
        cpu = snap.cpu
        usage = cpu.usage_percent
        body.append(
            self._meter(
                "CPU",
                usage,
                bar_width,
                self._spark("cpu"),
                [
                    f"{cpu.freq_mhz:.0f}MHz" if cpu.freq_mhz else "-",
                    fmt_temp_short(cpu.package_temp_c),
                    f"{cpu.package_power_w:.0f}W" if cpu.package_power_w else "-",
                ],
            )
        )

        # ---- 内存
        mem = snap.memory
        mem_extra = [
            f"{fmt_bytes(mem.used_bytes)}/{fmt_bytes(mem.total_bytes)}",
        ]
        if mem.committed_bytes is not None and mem.commit_limit_bytes:
            mem_extra.append(
                f"提交 {fmt_bytes(mem.committed_bytes)}/{fmt_bytes(mem.commit_limit_bytes)}"
            )
        body.append(
            self._meter("内存", mem.percent, bar_width, self._spark("mem"), mem_extra)
        )

        # ---- GPU
        if snap.gpus:
            gpu = snap.gpus[0]
            gpu_extra = []
            if gpu.temp_c is not None:
                gpu_extra.append(fmt_temp_short(gpu.temp_c))
            if gpu.power_w is not None:
                gpu_extra.append(f"{gpu.power_w:.0f}W")
            if gpu.mem_percent is not None:
                gpu_extra.append(
                    f"显存 {fmt_bytes(gpu.mem_used_bytes)}/{fmt_bytes(gpu.mem_total_bytes)}"
                    f" {gpu.mem_percent:.0f}%"
                )
            body.append(
                self._meter(
                    "GPU",
                    gpu.usage_percent or 0.0,
                    bar_width,
                    self._spark("gpu"),
                    gpu_extra,
                )
            )
        else:
            body.append(
                self._meter("GPU", 0.0, bar_width, self._spark("gpu"), ["不可用"])
            )

        # ---- 网络(没有"占用率"这种指标,直接显示速率和曲线)
        net = snap.network
        active = sum(
            1
            for i in net.interfaces
            if i.rx_bytes_per_sec > 0 or i.tx_bytes_per_sec > 0
        )
        body.append(
            t.paint(t.label, pad("网络", 6))
            + t.paint(t.down, f"↓ {fmt_rate(net.physical_rx_bytes_per_sec)}")
            + "   "
            + t.paint(t.up, f"↑ {fmt_rate(net.physical_tx_bytes_per_sec)}")
            + "   "
            + t.paint(t.muted, self._spark("net_rx"))
            + t.paint(t.muted, self._spark("net_tx"))
            + t.paint(t.muted, f"   活动网卡 {active} 张")
        )

        # ---- 磁盘(概览模式下才画,详细模式有单独的磁盘面板)
        if include_disk:
            body.extend(self._disk_summary(snap))
        return self._panel("概览", body)

    # ------------------------------------------------------------ 磁盘摘要

    def _disk_entries(
        self, snap: Snapshot
    ) -> list[tuple[str, str, Optional[float], Optional[str], object]]:
        """物理盘 + 对应的读写速率。

        拿不到 SMART(没装驱动 / 老机器不支持)时,退而求其次:
        只按 IO 设备列出速率,温度显示"-"。
        """
        io_map = {io.name: io for io in snap.disk_io}
        entries: list[tuple[str, str, Optional[float], Optional[str], object]] = []
        for disk in snap.physical_disks:
            io = self._match_io(io_map, disk)
            model = disk.model or disk.bus_type or "磁盘"
            entries.append(
                (str(disk.device_id), model, disk.temp_c, disk.health, io)
            )
        if not entries:
            for io in snap.disk_io[:2]:
                entries.append((io.name, io.name, None, None, io))
        return entries

    def _disk_summary(self, snap: Snapshot) -> list[str]:
        """概览模式下的硬盘摘要:每块盘一行,读/写速率 + 温度。

        列宽是照着概览里其它行(标签 + 进度条 + 百分比 + 曲线)算的:
        速率占 25 列,曲线正好从第 33 列开始,跟 CPU/内存/GPU 的曲线对齐。
        """
        t = self.theme
        entries = self._disk_entries(snap)
        if not entries:
            return [t.paint(t.muted, "未检测到磁盘")]

        lines: list[str] = []
        for index, (key, model, temp, health, io) in enumerate(entries):
            read_rate = io.read_bytes_per_sec if io is not None else 0.0
            write_rate = io.write_bytes_per_sec if io is not None else 0.0

            cells = [
                # 第一行写"磁盘",后面的留空,几块盘的内容才能对齐
                t.paint(t.label, pad("磁盘" if index == 0 else "", 6)),
                # 速率右对齐到固定宽度,不然曲线会跟着数字长度乱跑
                t.paint(t.down, f"读 {fmt_rate(read_rate):>9}")
                + " "
                + t.paint(t.up, f"写 {fmt_rate(write_rate):>9}"),
                t.paint(
                    t.muted,
                    self._spark(f"disk_read:{key}", _DISK_SPARK_WIDTH)
                    + self._spark(f"disk_write:{key}", _DISK_SPARK_WIDTH),
                ),
            ]
            if temp is not None:
                cells.append(t.paint(temp_color_name(temp), f"{temp:.0f}°C"))
            else:
                cells.append(t.paint(t.muted, "温度 -"))
            if health:
                healthy = health.lower() in ("healthy", "正常", "ok")
                cells.append(t.paint(t.good if healthy else t.critical, health))
            cells.append(t.paint(t.muted, model[:16]))
            lines.append(" ".join(c for c in cells if c))
        return lines

    def _meter(
        self,
        label: str,
        percent: float,
        bar_width: int,
        spark: str,
        extras: list[str],
    ) -> str:
        """一条"标签 + 进度条 + 百分比 + 曲线 + 附加指标"的行。"""
        t = self.theme
        painter = usage_color(percent, t.style)
        bar_text = bar(percent, bar_width, t.style)
        pct_text = painter(f"{percent:5.1f}%")

        cells = [
            t.paint(t.label, pad(label, 6)),
            bar_text,
            pct_text,
            t.paint(t.muted, spark),
        ]
        if extras:
            cells.append(t.paint(t.muted, "  ".join(extras)))
        return " ".join(c for c in cells if c)

    def _wrap_cells(self, cells: list[str], sep: str = "  ") -> list[str]:
        """把若干个片段按可用宽度折行,避免长内容被截断。"""
        t = self.theme
        limit = t.width - 4
        lines: list[str] = []
        current: list[str] = []
        used = 0
        for cell in cells:
            w = dwidth(cell)
            extra = dwidth(sep) if current else 0
            if current and used + extra + w > limit:
                lines.append(sep.join(current))
                current = [cell]
                used = w
            else:
                current.append(cell)
                used += extra + w
        if current:
            lines.append(sep.join(current))
        return lines

    def _cpu_panel(self, snap: Snapshot) -> list[str]:
        t = self.theme
        cpu = snap.cpu
        body: list[str] = []

        source = cpu.sensor_source or "不可用"
        temps = []
        if cpu.package_temp_c is not None:
            temps.append(
                t.paint(
                    t.label, "封装温度 "
                )
                + t.paint(temp_color_name(cpu.package_temp_c), f"{cpu.package_temp_c:.1f}°C")
            )
        if cpu.core_max_temp_c is not None:
            temps.append(
                t.paint(t.label, "最高核温 ")
                + t.paint(temp_color_name(cpu.core_max_temp_c), f"{cpu.core_max_temp_c:.1f}°C")
            )
        if cpu.core_avg_temp_c is not None:
            temps.append(
                t.paint(t.label, "平均 ")
                + f"{cpu.core_avg_temp_c:.1f}°C"
            )
        if cpu.tjmax_c:
            temps.append(t.paint(t.label, "TjMax ") + f"{cpu.tjmax_c:.0f}°C")
        if cpu.voltage_v:
            temps.append(t.paint(t.label, "电压 ") + f"{cpu.voltage_v:.3f}V")
        if cpu.package_power_w is not None:
            temps.append(
                t.paint(t.label, "封装功耗 ")
                + t.paint(t.accent, f"{cpu.package_power_w:.1f}W")
            )
        if cpu.core_power_w is not None:
            temps.append(t.paint(t.label, "核心功耗 ") + f"{cpu.core_power_w:.1f}W")
        if cpu.graphics_power_w is not None:
            temps.append(t.paint(t.label, "核显功耗 ") + f"{cpu.graphics_power_w:.1f}W")
        temps.append(t.paint(t.muted, f"来源 {source}"))
        body.extend(self._wrap_cells(temps))

        # ACPI 热区只是兜底参考:主板上这个值基本不随负载变化,
        # 所以单独一行标"近似",不跟真实 MSR 温度混在一起。
        for zone_name, zone_temp in snap.thermal.acpi_zones:
            body.append(
                t.paint(t.muted, f"ACPI 热区 {_short_zone(zone_name)}(近似) ")
                + t.paint(temp_color_name(zone_temp), f"{zone_temp:.1f}°C")
            )

        # 每核网格:编号 + 使用率 + 温度
        cores = cpu.per_core_percent
        temps_list = cpu.core_temps_c
        if cores:
            cell_w = 16
            cols = max(1, (t.width - 4) // cell_w)
            for start in range(0, len(cores), cols):
                cells = []
                for i in range(start, min(start + cols, len(cores))):
                    pct = cores[i]
                    temp = temps_list[i] if i < len(temps_list) else None
                    cell = usage_color(pct, t.style)(f"C{i:<2d} ")
                    cell += usage_color(pct, t.style)(f"{pct:3.0f}%")
                    if temp is not None:
                        cell += t.paint(temp_color_name(temp), f" {temp:3.0f}°")
                    cells.append(pad(cell, cell_w - 1))
                body.append("".join(cells).rstrip())
        return self._panel("CPU", body)

    def _gpu_panel(self, snap: Snapshot) -> list[str]:
        t = self.theme
        if not snap.gpus:
            return self._panel("GPU", [t.paint(t.muted, "未检测到 NVIDIA 显卡")])

        t = self.theme
        body: list[str] = []
        for gpu in snap.gpus:
            head = t.paint(t.accent, gpu.name) + t.paint(t.muted, f"  #{gpu.index}")
            body.append(head)

            cells: list[str] = []
            if gpu.usage_percent is not None:
                cells.append(t.paint(t.label, "占用 ") + f"{gpu.usage_percent:.0f}%")
            if gpu.utilization_mem_percent is not None:
                cells.append(t.paint(t.label, "显存控制器 ") + f"{gpu.utilization_mem_percent:.0f}%")
            if gpu.temp_c is not None:
                cells.append(
                    t.paint(t.label, "温度 ")
                    + t.paint(temp_color_name(gpu.temp_c), f"{gpu.temp_c:.0f}°C")
                )
            if gpu.power_w is not None:
                cells.append(t.paint(t.label, "功耗 ") + f"{gpu.power_w:.0f}W")
            if gpu.fan_percent is not None:
                cells.append(t.paint(t.label, "风扇 ") + f"{gpu.fan_percent:.0f}%")
            if gpu.freq_mhz:
                cells.append(t.paint(t.label, "核心频率 ") + f"{gpu.freq_mhz:.0f}MHz")
            if gpu.mem_freq_mhz:
                cells.append(t.paint(t.label, "显存频率 ") + f"{gpu.mem_freq_mhz:.0f}MHz")
            if gpu.mem_used_bytes is not None and gpu.mem_total_bytes:
                cells.append(
                    t.paint(t.label, "显存 ")
                    + f"{fmt_bytes(gpu.mem_used_bytes)}/{fmt_bytes(gpu.mem_total_bytes)}"
                    + f" ({gpu.mem_percent or 0:.0f}%)"
                )
            if gpu.pcie_gen_current:
                pcie = f"PCIe x{gpu.pcie_width_current or '?'} Gen{gpu.pcie_gen_current}"
                if gpu.pcie_gen_max and gpu.pcie_gen_max != gpu.pcie_gen_current:
                    pcie += f"(上限 Gen{gpu.pcie_gen_max} x{gpu.pcie_width_max or '?'})"
                cells.append(t.paint(t.label, "链路 ") + pcie)
            body.extend(self._wrap_cells(cells))
        return self._panel("GPU", body)

    def _memory_panel(self, snap: Snapshot) -> list[str]:
        t = self.theme
        mem = snap.memory
        body: list[str] = []

        cells = [
            t.paint(t.label, "已用 ")
            + f"{fmt_bytes(mem.used_bytes)}/{fmt_bytes(mem.total_bytes)}"
            + f" ({mem.percent:.0f}%)",
            t.paint(t.label, "可用 ") + fmt_bytes(mem.available_bytes),
        ]
        if mem.committed_bytes is not None and mem.commit_limit_bytes:
            ratio = mem.committed_bytes / mem.commit_limit_bytes * 100
            cells.append(
                t.paint(t.label, "提交 ")
                + f"{fmt_bytes(mem.committed_bytes)}/{fmt_bytes(mem.commit_limit_bytes)}"
                + f" ({ratio:.0f}%)"
            )
        if mem.commit_peak_bytes:
            cells.append(t.paint(t.label, "提交峰值 ") + fmt_bytes(mem.commit_peak_bytes))
        if mem.swap_total_bytes:
            cells.append(
                t.paint(t.label, "页面文件 ")
                + f"{fmt_bytes(mem.swap_used_bytes)}/{fmt_bytes(mem.swap_total_bytes)}"
            )
        body.extend(self._wrap_cells(cells))

        for dimm in mem.dimms:
            head = [
                t.paint(t.accent, dimm.locator or "DIMM"),
                t.paint(t.label, "容量 ") + fmt_bytes(dimm.capacity_bytes),
            ]
            if dimm.smbios_type:
                head.append(t.paint(t.label, "类型 ") + dimm.smbios_type)
            if dimm.form_factor:
                head.append(t.paint(t.label, "形态 ") + dimm.form_factor)
            if dimm.configured_speed_mhz:
                head.append(
                    t.paint(t.label, "频率 ")
                    + f"{dimm.configured_speed_mhz}MHz"
                    + (f" (标称 {dimm.speed_mhz})" if dimm.speed_mhz else "")
                )
            if dimm.voltage_v:
                head.append(t.paint(t.label, "电压 ") + f"{dimm.voltage_v:.3f}V")
            body.append("  ".join(head))

            detail = [
                t.paint(t.label, "厂商 ") + (dimm.manufacturer or "-"),
                t.paint(t.label, "型号 ") + (dimm.part_number or "-"),
                # 温度与时序需要读 SPD,当前拿不到,明确标出来而不是留空
                t.paint(t.label, "温度 ")
                + (
                    f"{dimm.temp_c:.1f}°C"
                    if dimm.temp_c is not None
                    else t.paint(t.muted, "不可用")
                ),
                t.paint(t.label, "时序 ")
                + (dimm.timings or t.paint(t.muted, "不可用")),
            ]
            body.extend(self._wrap_cells(detail, sep="  "))
        return self._panel("内存", body)

    def _disk_panel(self, snap: Snapshot) -> list[str]:
        t = self.theme
        body: list[str] = []

        io_map = {io.name: io for io in snap.disk_io}
        for disk in snap.physical_disks:
            cells = [
                t.paint(t.accent, (disk.model or "未知磁盘")[:28]),
                t.paint(t.label, "类型 ") + (disk.bus_type or "-"),
                t.paint(t.label, "容量 ") + fmt_bytes(disk.size_bytes),
            ]
            if disk.temp_c is not None:
                cells.append(
                    t.paint(t.label, "温度 ")
                    + t.paint(temp_color_name(disk.temp_c), f"{disk.temp_c:.0f}°C")
                )
            if disk.health:
                healthy = disk.health.lower() in ("healthy", "正常", "ok")
                cells.append(
                    t.paint(t.label, "健康 ")
                    + t.paint(t.good if healthy else t.critical, disk.health)
                )
            if disk.power_on_hours is not None:
                cells.append(
                    t.paint(t.label, "通电 ") + f"{disk.power_on_hours}h"
                )
            if disk.wear_percent is not None:
                cells.append(t.paint(t.label, "磨损 ") + f"{disk.wear_percent:.0f}%")
            body.extend(self._wrap_cells(cells))

            io = self._match_io(io_map, disk)
            if io is not None:
                body.append(
                    t.paint(t.muted, "    IO ")
                    + f"读 {fmt_rate(io.read_bytes_per_sec)}"
                    + f"  写 {fmt_rate(io.write_bytes_per_sec)}"
                )

        # 分区容量
        parts = [d for d in snap.disks if d.total_bytes > 0]
        if parts:
            body.append("")
            for disk in parts:
                body.append(
                    t.paint(t.accent, (disk.mountpoint or disk.device)[:24])
                    + "  "
                    + bar(disk.percent, 16, t.style)
                    + usage_color(disk.percent, t.style)(f" {disk.percent:5.1f}%")
                    + t.paint(t.muted, f"  {fmt_bytes(disk.used_bytes)}/{fmt_bytes(disk.total_bytes)}")
                    + t.paint(t.muted, f"  {disk.filesystem}")
                )
        return self._panel("磁盘", body)

    @staticmethod
    def _match_io(io_map: dict, disk) -> object:
        """物理盘和 psutil 的 I/O 名字按设备号对应(PhysicalDriveN)。"""
        target = f"PHYSICALDRIVE{disk.device_id}"
        for name, io in io_map.items():
            if name.rstrip("\\").upper() == target:
                return io
        return None

    def _network_panel(self, snap: Snapshot) -> list[str]:
        t = self.theme
        net = snap.network
        body: list[str] = []

        body.append(
            t.paint(t.label, "合计(物理网卡)")
            + f"  ↓ {fmt_rate(net.physical_rx_bytes_per_sec)}"
            + f"  ↑ {fmt_rate(net.physical_tx_bytes_per_sec)}"
            + t.paint(t.muted, "    含虚拟网卡: ")
            + f"↓ {fmt_rate(net.rx_bytes_per_sec)}"
            + f"  ↑ {fmt_rate(net.tx_bytes_per_sec)}"
        )

        for iface in net.interfaces:
            # 物理网卡一律列出(哪怕当前没流量),虚拟网卡只在有流量时出现,
            # 否则一屏几十行都是蓝牙/WSL 之类的虚拟适配器。
            if not iface.is_physical and (
                iface.rx_bytes_per_sec <= 0 and iface.tx_bytes_per_sec <= 0
            ):
                continue
            tag = t.paint(t.accent, pad(iface.name, 22))
            if not iface.is_physical:
                tag += t.paint(t.muted, "(虚拟)")
            body.append(
                tag
                + t.paint(t.down, f"↓ {fmt_rate(iface.rx_bytes_per_sec):>12}")
                + t.paint(t.up, f"  ↑ {fmt_rate(iface.tx_bytes_per_sec):>12}")
            )
        return self._panel("网络", body)

    def _process_panel(self, snap: Snapshot) -> list[str]:
        t = self.theme
        procs = snap.processes
        if procs is None:
            return self._panel("进程", [t.paint(t.muted, "无数据")])

        body: list[str] = []
        body.append(
            t.paint(t.label, "  PID   名称")
            + " " * 22
            + t.paint(t.label, "CPU%")
            + "  "
            + t.paint(t.label, "内存")
        )
        for stat in procs.by_cpu[: self.top]:
            body.append(
                t.paint(t.muted, f"{stat.pid:>6d} ")
                + pad(stat.name, 24)
                + usage_color(stat.cpu_percent, t.style)(f"{stat.cpu_percent:5.1f}")
                + "  "
                + fmt_bytes(stat.memory_bytes)
            )
        body.append("")
        body.append(t.paint(t.label, "  内存占用 Top"))
        for stat in procs.by_memory[: max(3, self.top // 2)]:
            body.append(
                t.paint(t.muted, f"{stat.pid:>6d} ")
                + pad(stat.name, 24)
                + f"{stat.memory_percent:5.1f}%"
                + "  "
                + fmt_bytes(stat.memory_bytes)
            )
        body.append(
            t.paint(
                t.muted,
                f"共 {procs.total_processes} 个进程 / {procs.total_threads} 个线程",
            )
        )
        return self._panel("进程", body)

    def _footer(self, snap: Snapshot, warnings: list[str]) -> list[str]:
        t = self.theme
        body: list[str] = []
        for note in warnings:
            body.append(t.paint(t.warn, "! " + note))
        body.append(
            t.paint(
                t.muted,
                "按 Ctrl+C 退出  ·  数据来源:MSR(温度/功耗) / nvidia-smi(GPU) / "
                "SMART(硬盘) / psutil(负载)",
            )
        )
        return self._panel("提示", body)

    # ------------------------------------------------------------ 工具

    def _panel(self, title: str, body: list[str]) -> list[str]:
        t = self.theme
        lines = [t.top(title)]
        for line in body:
            lines.append(t.row(line))
        lines.append(t.bottom())
        lines.append("")
        return lines


def fmt_temp_short(temp: Optional[float]) -> str:
    return f"{temp:.0f}°C" if temp is not None else "-"


def _short_zone(name: str) -> str:
    """ACPI 热区名字形如 ACPI\\ThermalZone\\TZ00_0,只留最后一段。"""
    return name.rsplit("\\", 1)[-1] or name


def temp_color_name(temp: float) -> str:
    """把温度映射到主题里的语义色名。"""
    if temp >= 85:
        return "critical"
    if temp >= 75:
        return "hot"
    if temp >= 60:
        return "warn"
    return "good"
