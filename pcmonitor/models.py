"""采样数据结构。

每个采集器负责产出自己那一块的快照,`Snapshot` 把它们聚合成一帧画面。
字段一律用 Optional 表达"拿不到"——温度传感器、SMART、频率在不同机器上
的可用性差异极大,拿不到必须能优雅降级,而不是抛异常。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CpuSnapshot:
    usage_percent: float = 0.0
    per_core_percent: list[float] = field(default_factory=list)
    freq_mhz: Optional[float] = None
    max_freq_mhz: Optional[float] = None
    logical_cores: int = 0
    # 以下来自 PawnIO 读 MSR(Intel 平台),拿不到时为 None
    package_temp_c: Optional[float] = None
    core_temps_c: list[Optional[float]] = field(default_factory=list)
    core_max_temp_c: Optional[float] = None
    core_avg_temp_c: Optional[float] = None
    tjmax_c: Optional[float] = None
    voltage_v: Optional[float] = None
    package_power_w: Optional[float] = None
    core_power_w: Optional[float] = None
    graphics_power_w: Optional[float] = None
    # 温度/功耗的数据来源标记:"MSR" 或 "不可用"
    sensor_source: str = ""


@dataclass
class MemorySnapshot:
    total_bytes: int = 0
    used_bytes: int = 0
    available_bytes: int = 0
    percent: float = 0.0
    swap_total_bytes: int = 0
    swap_used_bytes: int = 0
    # Windows 的提交量(Commit),比"已用物理内存"更早暴露内存泄漏
    committed_bytes: Optional[int] = None
    commit_limit_bytes: Optional[int] = None
    commit_peak_bytes: Optional[int] = None
    dimms: list[DimmInfo] = field(default_factory=list)


@dataclass
class DiskIo:
    name: str
    read_bytes_per_sec: float = 0.0
    write_bytes_per_sec: float = 0.0
    read_bytes_total: int = 0
    write_bytes_total: int = 0


@dataclass
class DiskSnapshot:
    device: str
    model: str = ""
    mountpoint: str = ""
    filesystem: str = ""
    total_bytes: int = 0
    used_bytes: int = 0
    percent: float = 0.0


@dataclass
class DimmInfo:
    """内存条信息。

    频率/厂商/型号来自 SMBIOS(Win32_PhysicalMemory)。
    温度与时序需要读 SPD,当前依赖链拿不到(见 README「已知限制」),
    因此保持 None 而不是填假数据。
    """

    locator: str = ""
    manufacturer: str = ""
    part_number: str = ""
    capacity_bytes: int = 0
    speed_mhz: Optional[int] = None
    configured_speed_mhz: Optional[int] = None
    smbios_type: Optional[str] = None
    form_factor: Optional[str] = None
    voltage_v: Optional[float] = None
    temp_c: Optional[float] = None
    timings: Optional[str] = None


@dataclass
class PhysicalDisk:
    """物理磁盘(SMART 视角),与分区是不同维度,不能混为一谈。

    分区显示容量占用,物理盘显示温度/健康度/通电时长——一块 4TB 盘
    可能有 3 个分区,温度却是整块的。
    """

    device_id: int = 0
    model: str = ""
    bus_type: str = ""
    media_type: str = ""
    size_bytes: int = 0
    temp_c: Optional[float] = None
    health: Optional[str] = None
    power_on_hours: Optional[int] = None
    wear_percent: Optional[float] = None


@dataclass
class NetworkIface:
    name: str
    rx_bytes_per_sec: float = 0.0
    tx_bytes_per_sec: float = 0.0
    rx_bytes_total: int = 0
    tx_bytes_total: int = 0
    rx_packets_per_sec: float = 0.0
    tx_packets_per_sec: float = 0.0
    # 是否是真实网卡。虚拟网卡(WSL/Hyper-V/VPN)常是物理网卡的桥接,
    # 全量汇总会把同一份流量算两遍。
    is_physical: bool = False


@dataclass
class NetworkSnapshot:
    interfaces: list[NetworkIface] = field(default_factory=list)
    rx_bytes_per_sec: float = 0.0
    tx_bytes_per_sec: float = 0.0
    # 虚拟网卡(WSL/Hyper-V/VPN)会重复计数同一份流量,单独汇总一份真实流量
    physical_rx_bytes_per_sec: float = 0.0
    physical_tx_bytes_per_sec: float = 0.0


@dataclass
class GpuSnapshot:
    name: str = ""
    index: int = 0
    usage_percent: Optional[float] = None
    temp_c: Optional[float] = None
    fan_percent: Optional[float] = None
    power_w: Optional[float] = None
    freq_mhz: Optional[float] = None
    mem_freq_mhz: Optional[float] = None
    mem_used_bytes: Optional[int] = None
    mem_total_bytes: Optional[int] = None
    mem_percent: Optional[float] = None
    utilization_gpu_percent: Optional[float] = None
    utilization_mem_percent: Optional[float] = None
    # PCIe 链路:当前协商到的代数/位宽,以及显卡本身支持的上限
    pcie_gen_current: Optional[int] = None
    pcie_gen_max: Optional[int] = None
    pcie_width_current: Optional[int] = None
    pcie_width_max: Optional[int] = None


@dataclass
class ThermalSnapshot:
    """所有温度/风扇的汇总,来源可能是 LHM、nvidia-smi、SMART 或 ACPI。"""

    cpu_package_c: Optional[float] = None
    cpu_cores: list[tuple[str, float]] = field(default_factory=list)
    cpu_fan_rpm: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    gpu_fan_rpm: Optional[float] = None
    motherboard_c: Optional[float] = None
    dimm: list[tuple[str, float]] = field(default_factory=list)
    nvme: list[tuple[str, float]] = field(default_factory=list)
    acpi_zones: list[tuple[str, float]] = field(default_factory=list)
    # 未能归类的传感器,原样保留,避免信息丢失
    extra_sensors: list[tuple[str, str, float]] = field(default_factory=list)
    # 每个传感器的来源,用于在界面上标注可信度(如 "LHM" / "SMART" / "近似")
    sources: dict[str, str] = field(default_factory=dict)
    # 采集过程中发生的降级/错误,展示给用户而不是静默吞掉
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProcessStat:
    pid: int
    name: str
    username: str = ""
    cpu_percent: float = 0.0
    memory_bytes: int = 0
    memory_percent: float = 0.0


@dataclass
class ProcessTop:
    by_cpu: list[ProcessStat] = field(default_factory=list)
    by_memory: list[ProcessStat] = field(default_factory=list)
    total_processes: int = 0
    total_threads: int = 0


@dataclass
class HostSnapshot:
    hostname: str = ""
    os_name: str = ""
    uptime_seconds: float = 0.0
    boot_time: Optional[float] = None


@dataclass
class Snapshot:
    timestamp: float = 0.0
    cpu: CpuSnapshot = field(default_factory=CpuSnapshot)
    memory: MemorySnapshot = field(default_factory=MemorySnapshot)
    disks: list[DiskSnapshot] = field(default_factory=list)
    physical_disks: list[PhysicalDisk] = field(default_factory=list)
    disk_io: list[DiskIo] = field(default_factory=list)
    network: NetworkSnapshot = field(default_factory=NetworkSnapshot)
    gpus: list[GpuSnapshot] = field(default_factory=list)
    thermal: ThermalSnapshot = field(default_factory=ThermalSnapshot)
    host: HostSnapshot = field(default_factory=HostSnapshot)
    processes: Optional[ProcessTop] = None
