"""命令行入口。

三种输出模式:
* 默认:终端仪表盘,全屏刷新。
* `--json`:每帧输出一个 JSON 对象,方便接入别的工具。
* `--csv`:输出 CSV,方便丢进 Excel 或做时序分析。
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import sys
import time
from typing import Any, Optional

from . import __version__, driver
from .models import Snapshot
from .monitor import Monitor
from .ui import Dashboard, Theme, terminal_size
from .util import Style, enable_vt, supports_color

# ANSI 控制序列
_CLEAR_SCREEN = "\033[2J\033[H"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"

# 预热后等一小会儿,让第一批"增量类"指标(占用率/速率/功率)有可用窗口
_WARMUP_SECONDS = 0.8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pc-monitor",
        description="Windows 硬件监控仪表盘(CPU/内存/GPU/温度/网速/磁盘/进程)",
    )
    parser.add_argument(
        "--interval", type=float, default=2.0, help="刷新间隔(秒),默认 2"
    )
    parser.add_argument("--once", action="store_true", help="只采样一次并输出")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--csv", action="store_true", help="以 CSV 输出")
    parser.add_argument("--no-color", action="store_true", help="禁用颜色")
    parser.add_argument(
        "--no-msr", action="store_true", help="禁用 PawnIO/MSR 读取(不需要装驱动)"
    )
    parser.add_argument(
        "--no-driver-install",
        action="store_true",
        help="即使没装 PawnIO 驱动也不自动安装(默认首次运行会自动装,会弹 UAC)",
    )
    parser.add_argument(
        "--no-process", action="store_true", help="不采集进程列表(更快)"
    )
    parser.add_argument("--top", type=int, default=10, help="进程 Top N,默认 10")
    parser.add_argument("--width", type=int, default=0, help="强制界面宽度")
    parser.add_argument(
        "--out", default="", help="把输出写入文件而不是标准输出"
    )
    parser.add_argument("--version", action="version", version=f"pc-monitor {__version__}")
    return parser


# ---------------------------------------------------------------- 输出格式


def snapshot_to_dict(snap: Snapshot) -> dict[str, Any]:
    """把快照转成可 JSON 序列化的字典。"""
    return dataclasses.asdict(snap)


def _csv_row(snap: Snapshot) -> list[Any]:
    cpu = snap.cpu
    mem = snap.memory
    gpu = snap.gpus[0] if snap.gpus else None
    net = snap.network
    disk = snap.physical_disks[0] if snap.physical_disks else None
    return [
        snap.timestamp,
        cpu.usage_percent,
        cpu.freq_mhz,
        cpu.package_temp_c,
        cpu.core_max_temp_c,
        cpu.core_avg_temp_c,
        cpu.voltage_v,
        cpu.package_power_w,
        cpu.core_power_w,
        mem.percent,
        mem.used_bytes,
        mem.total_bytes,
        mem.committed_bytes,
        mem.commit_limit_bytes,
        gpu.usage_percent if gpu else None,
        gpu.temp_c if gpu else None,
        gpu.power_w if gpu else None,
        gpu.mem_percent if gpu else None,
        net.physical_rx_bytes_per_sec,
        net.physical_tx_bytes_per_sec,
        disk.temp_c if disk else None,
        disk.health if disk else None,
    ]


_CSV_HEADER = [
    "timestamp",
    "cpu_percent",
    "cpu_freq_mhz",
    "cpu_package_temp_c",
    "cpu_core_max_temp_c",
    "cpu_core_avg_temp_c",
    "cpu_voltage_v",
    "cpu_package_power_w",
    "cpu_core_power_w",
    "mem_percent",
    "mem_used_bytes",
    "mem_total_bytes",
    "mem_committed_bytes",
    "mem_commit_limit_bytes",
    "gpu_percent",
    "gpu_temp_c",
    "gpu_power_w",
    "gpu_mem_percent",
    "net_rx_bytes_per_sec",
    "net_tx_bytes_per_sec",
    "disk_temp_c",
    "disk_health",
]


# ---------------------------------------------------------------- 驱动


def _info(message: str) -> None:
    print(message)


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _can_elevate(args: argparse.Namespace) -> bool:
    """要不要为了读 MSR 自我提权。

    `--json` / `--csv` 的输出是往标准输出里流的(可能被重定向到管道或文件),
    重新起一个进程会把这条流弄丢,所以这两种模式不自动提权,只提示用户。
    """
    return not (args.json or args.csv)


def prepare_driver(args: argparse.Namespace) -> bool:
    """确保 PawnIO 驱动可用。

    返回 False 表示已经用管理员身份重新启动过了,调用方应该马上退出,
    让提权后的那个实例接着跑。

    流程:
    1. 没装驱动 -> 用内置的官方安装器提权装上(会弹 UAC)。
    2. 装好了但当前进程没权限 -> 以管理员身份重新启动自己。

    为了只弹一次 UAC,没装驱动时优先"先把自己提权重开",让提权后的实例
    去装驱动(它已经是管理员了,装的时候不会再弹窗)。
    """
    if args.no_msr:
        return True

    if not driver.is_installed():
        if args.no_driver_install:
            _warn("提示:未检测到 PawnIO 驱动,按 --no-driver-install 跳过安装,MSR 指标不可用。")
            return True
        if _can_elevate(args) and not driver.is_elevated():
            _info("首次运行需要安装 PawnIO 驱动,正在以管理员身份重新启动……")
            if driver.elevate_self():
                return False
        _info("正在安装 PawnIO 驱动(会弹出 UAC 授权窗口,请点“是”)……")
        result = driver.install()
        if result.ok:
            _info("PawnIO 驱动安装完成。")
        else:
            _warn(f"PawnIO 驱动安装失败:{result.message}")
            _warn("可以手动到 https://pawnio.eu/ 下载安装,其余指标不受影响。")
            return True

    hr = driver.probe_access()
    if hr == driver.ACCESS_DENIED_HRESULT and not driver.is_elevated():
        if _can_elevate(args):
            _info("读取 MSR 需要管理员权限,正在以管理员身份重新启动……")
            if driver.elevate_self():
                return False
        _warn("提示:当前不是管理员身份,MSR 指标(CPU 温度/功耗)不可用。")
    return True


# ---------------------------------------------------------------- 主循环


def _open_out(args: argparse.Namespace, **kwargs):
    """返回 (文件对象, 是否需要关闭)。写标准输出时不能替用户关掉它。"""
    if args.out:
        return open(args.out, "w", encoding="utf-8", **kwargs), True
    return sys.stdout, False


def setup_output_encoding() -> int:
    """把输出编码统一成 UTF-8,并把控制台代码页切到 65001。

    中文版 Windows 的控制台默认是 GBK(cp936),画框线用的
    "░"、温度符号 "°" 直接会抛 UnicodeEncodeError,
    打包成 exe 之后尤其明显(源码运行时 PYTHONIOENCODING 兜住了)。
    返回原来的代码页,退出时还原。
    """
    old_cp = 0
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            old_cp = int(kernel32.GetConsoleOutputCP())
            kernel32.SetConsoleOutputCP(65001)
        except Exception:
            old_cp = 0

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    return old_cp


def restore_output_encoding(old_cp: int) -> None:
    if old_cp and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(old_cp)
        except Exception:
            pass


def run_tui(monitor: Monitor, args: argparse.Namespace) -> int:
    color = not args.no_color and supports_color()
    style = Style(enabled=color)
    width = args.width or terminal_size()[0]
    theme = Theme(style=style, width=width)
    dashboard = Dashboard(theme, top=args.top)

    out, need_close = _open_out(args)
    try:
        time.sleep(_WARMUP_SECONDS)
        while True:
            snap = monitor.sample()
            warnings = monitor.warnings()
            width_now = args.width or terminal_size()[0]
            if width_now != theme.width:
                theme.width = width_now
            lines = dashboard.render(snap, warnings)
            if need_close:
                out.write("\n".join(lines) + "\n")
                out.flush()
            else:
                sys.stdout.write(_CLEAR_SCREEN)
                sys.stdout.write("\n".join(lines))
                sys.stdout.write("\n")
                sys.stdout.flush()

            if args.once:
                break
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        if not need_close:
            sys.stdout.write(_SHOW_CURSOR)
            sys.stdout.flush()
        if need_close:
            out.close()
    return 0


def run_json(monitor: Monitor, args: argparse.Namespace) -> int:
    out, need_close = _open_out(args)
    try:
        time.sleep(_WARMUP_SECONDS)
        while True:
            snap = monitor.sample()
            payload = snapshot_to_dict(snap)
            payload["warnings"] = monitor.warnings()
            out.write(json.dumps(payload, ensure_ascii=False) + "\n")
            out.flush()
            if args.once:
                break
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        if need_close:
            out.close()
    return 0


def run_csv(monitor: Monitor, args: argparse.Namespace) -> int:
    out, need_close = _open_out(args, newline="")
    writer = csv.writer(out)
    writer.writerow(_CSV_HEADER)
    try:
        time.sleep(_WARMUP_SECONDS)
        while True:
            snap = monitor.sample()
            writer.writerow(_csv_row(snap))
            out.flush()
            if args.once:
                break
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        if need_close:
            out.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    old_cp = setup_output_encoding()
    if not args.no_color and sys.stdout.isatty():
        enable_vt()

    if not prepare_driver(args):
        # 已经以管理员身份重开了一个实例,这边直接收工
        restore_output_encoding(old_cp)
        return 0

    monitor = Monitor(enable_msr=not args.no_msr, top=args.top)
    if args.no_process:
        monitor.process = _NullProcessCollector()
    monitor.warmup()

    try:
        if args.json:
            return run_json(monitor, args)
        if args.csv:
            return run_csv(monitor, args)
        return run_tui(monitor, args)
    finally:
        monitor.close()
        restore_output_encoding(old_cp)


class _NullProcessCollector:
    """`--no-process` 时顶掉真正的采集器,省掉遍历进程的开销。"""

    def sample(self, top: int = 10):  # noqa: ANN001 - 返回 None 表示无数据
        return None
