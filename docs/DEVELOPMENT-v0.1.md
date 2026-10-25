# pc-monitor 开发记录与技术总结

> **版本 v0.1** · 2026-10-25 · 对应提交 `8bdbf70` → `f692b26`
>
> 这份文档记录的是**怎么做的**和**为什么这么做**,包括走过的弯路。
> 面向的是以后接手这个项目的人(包括三个月后的我自己)。
> 怎么用、装什么、参数是什么,看 [README](../README.md)。

---

## 目录

1. [项目缘起](#1-项目缘起)
2. [整体架构](#2-整体架构)
3. [开发过程记录](#3-开发过程记录)
4. [技术详解](#4-技术详解)
5. [性能实测](#5-性能实测)
6. [走过的弯路](#6-走过的弯路)
7. [已知限制与后续](#7-已知限制与后续)
8. [测试策略](#8-测试策略)
9. [附录](#9-附录)

---

## 1. 项目缘起

### 1.1 要解决的问题

想要一个**在 Windows 终端里看硬件状态**的东西。需求很朴素:

- 一屏看完 CPU / 内存 / GPU / 温度 / 网速 / 磁盘 / 进程的占用;
- **温度要是真的**。Windows 自带的东西和大部分"温度监控"工具给的是
  ACPI 热区那个值 —— 开机 30°C,烤机 30 分钟还是 30°C,因为它根本不跟负载走;
- 发给别人就能用,**不能让人先装 Python、先装 .NET、先去 pawnio.eu 下驱动**。

### 1.2 硬约束

这几条是整个项目的设计前提,后面所有技术选择都是围绕它们转的:

| 约束 | 影响 |
| --- | --- |
| 目标机器是 Windows | 不能用 Linux 生态的现成库,一切 Win32 API 化 |
| 单文件 exe,目标机器不装 Python | PyInstaller onefile;体积和启动速度都要抠 |
| 不依赖 .NET Framework | **直接否掉了 LibreHardwareMonitor**(见 §6.1) |
| 拿不到就显示"不可用",绝不编数字 | 所有采集器都必须能优雅降级 |
| 运行时依赖尽量少 | 运行时只有 `psutil` 一个第三方依赖,其余全是标准库 |

### 1.3 v0.1 做完之后的样子

```
╭─ 概览 ─────────────────────────────────────────────────────────────────────────╮
│ CPU    █████░░░░░░░░░░░░░░  27.3% ▅                5300MHz  68°C  97W          │
│ 内存   ███████████░░░░░░░  58.9% ▅                9.3 GB/15.8 GB  提交 18.4 GB │
│ GPU    █████████████████░  95.0% ▅                70°C  179W  显存 5.4 GB/15.9 GB │
│ 网络  ↓ 25.2 KB/s   ↑ 6.4 KB/s   ▅               ▅                  活动网卡 1 张 │
│ 磁盘   读     0 B/s 写  2.0 KB/s ▅       ▅        42°C Healthy ST4000NM0035   │
╰──────────────────────────────────────────────────────────────────────────────╯
```

- 代码 4356 行(不含测试),测试 1252 行 / 102 个用例;
- 打包后 11.7 MB;
- 占整机 CPU 约 0.22%(§5)。

---

## 2. 整体架构

### 2.1 分层

```
                     ┌──────────────────────────────────────┐
   终端  ◀──────────  │  cli.py       模式选择 / 输出格式     │
                     ├──────────────────────────────────────┤
                     │  ui/dashboard.py + ui/theme.py        │
                     │         渲染成 ANSI 字符串            │
                     ├──────────────────────────────────────┤
                     │  monitor.py   把各采集器凑成一帧       │
                     ├──────────────────────────────────────┤
                     │  collectors/  八个采集器,互不依赖     │
                     │    cpu.py       psutil + MSR          │
                     │    memory.py    psutil + WMI(SMBIOS)  │
                     │    gpu.py       NVML / nvidia-smi     │
                     │    nvml.py      nvml.dll 的 ctypes 封装│
                     │    disk.py      psutil               │
                     │    network.py   psutil               │
                     │    process.py   psutil + GetPerformanceInfo│
                     │    thermal.py   WMI + ACPI           │
                     │    pawnio.py    PawnIOLib.dll 的 ctypes 封装│
                     │    powershell.py PowerShell 调用封装  │
                     │    base.py      增量计数器 + 后台刷新器│
                     └──────────────────────────────────────┘
```

### 2.2 数据流

采集器之间**没有依赖**,谁也不认识谁。每个采集器只管产出自己那块
`dataclass`(定义在 `models.py`),`Monitor.sample()` 按固定顺序调一遍,
拼成一颗 `Snapshot` 交给 `Dashboard` 渲染。

```
  采集器 ──► XxxSnapshot ──► Monitor.sample() ──► Snapshot ──► Dashboard.render()
              (models.py)                          (monitor.py)      (ui/)
                                                                   │
                                              ANSI 字符串 ◀────────┘
                                                   │
                                              终端 / --json / --csv
```

**为什么这么设计**:

- 采集器之间零耦合,想加一个"主板温度"就是新写一个采集器 + 在
  `models.py` 加一个 dataclass + 在 `monitor.py` 加一行,不用动别的;
- `Snapshot` 是**纯数据**,可以整体 `dataclasses.asdict()` 序列化成 JSON,
  所以 `--json` / `--csv` 模式一行代码都不用额外写;
- 采集器全部返回 `Optional`,`None` 一律表示"拿不到"。UI 层负责把
  `None` 渲染成"不可用"而不是 0。

### 2.3 三个贯穿全局的约定

**① 增量类指标必须有基线。**
CPU 占用率、网速、磁盘速率、RAPL 功耗,全都是"两次采样求差"。
第一帧必然是 0 或 `None`。所以 `Monitor.warmup()` 先空采一帧,
并且 `cli.py` 里还有 0.8 秒的 `_WARMUP_SECONDS` 静默期。

**② 慢查询一律不进采集路径。**
凡是"要拉起一个子进程"或"要进内核慢查"的东西,都放后台线程,
前台只读缓存。这是本项目最重要的一条性能约定(§4.9)。

**③ 拿不到就是拿不到。**
没有 MSR 权限就不显示温度,没有 SMART 就不显示硬盘健康度。
`models.py` 里所有字段都是 `Optional`,`ui/dashboard.py` 里所有取值
都有 `None` 分支。

---

## 3. 开发过程记录

整个项目是一个下午(2026-10-25 18:09 → 19:50)做出来的,6 个提交。
按时间顺序记录,包括当时遇到的问题和解决方式。

### 3.1 `8bdbf70` 第一版:能跑的仪表盘

**做了什么**

从零搭起整个骨架:9 个采集器、快照模型、仪表盘渲染、CLI、打包脚本。
第一版就有 MSR 温度、GPU、SMART、网速、进程榜。

**当时踩的坑**

*坑 1:Windows 控制台默认是 GBK(cp936)。*
画框线用的 `░`、温度符号 `°` 直接抛 `UnicodeEncodeError`,
打包成 exe 之后尤其明显(源码运行时 `PYTHONIOENCODING` 兜住了)。
解法在 `cli.py:setup_output_encoding()`:启动时把控制台代码页切到 65001,
退出时还原;同时给 stdout/stderr 强制 `reconfigure(encoding="utf-8")`。

*坑 2:中文是双宽字符,`len()` 和 `ljust()` 全都算错。*
表格右边框全部错位。解法是 `util.py` 里的 `dwidth()`:
按 East Asian Width 算显示宽度,`W`/`F` 算 2 列;
配套还有 `pad()` / `truncate()`,截断时保留已有的颜色设置
(用 `_ansi_tokens()` 把 ANSI 序列和普通文本分开处理)。

*坑 3:`PERFORMANCE_INFORMATION` 结构体大小不对。*
Windows SDK 头文件里只有 88 字节,但内核实际用的是 104 字节的版本
(多出 `PoolPaged` / `PoolNonpaged`)。`cb` 传 88 直接
`ERROR_BAD_LENGTH` 失败。而且内存类字段的单位是**页**不是字节。
见 §4.8。

### 3.2 `06c6af4` 慢查询搬到后台线程

**问题**:第一版把硬盘 SMART 和内存条信息直接放在采样线程里同步查。
这两个都要拉起一个 PowerShell 进程,单次 1.0~1.4 秒。
结果是界面**每 10 秒就整体卡住一秒多**,动画直接顿住。

**解法**:在 `collectors/base.py` 里加 `BackgroundRefresher` ——
一个守护线程定期刷新并写缓存,采样线程只做一次加锁读。

```python
class BackgroundRefresher:
    def get(self) -> tuple[Any, Optional[str]]:
        with self._lock:          # 采样线程只干这一件事
            return self._value, self._error
```

**顺带修的**:进程采集也在拖后腿。`process_iter(["status"])` 预取每个
进程的状态,200 多个进程要 200ms+;逐个进程调 `num_threads()` 更慢。
改成用一次 `GetPerformanceInfo` 拿进程数/线程数总量,进程榜从 511ms
降到 18ms。

**效果**:一帧从 1350ms 降到 80ms。

### 3.3 `231bc15` 内置 PawnIO 安装器

**问题**:第一版要求用户自己去 <https://pawnio.eu/> 下载驱动。
这对"发给别人就能用"是硬伤。

**解法**:把官方安装器打进 exe,首次运行自动检测 → 自动装 → 自动提权。
上游明确允许原样转发(二进制里就写着 "This installer can be
redistributed unmodified."),`PawnIO_setup.exe` 的 SHA-256 是
`a3a46226c5e2824f4cdd42be0eecbabfc672c86f7889710f5ab1e6ad385b47a0`。

**踩到的三个坑**(详见 §4.5):

1. 官方安装器靠**自己的进程名**判断角色(`wcsstr(argv[0], "uninstall.exe")`),
   内置副本必须改名,而且**存放路径里也不能出现 "uninstall.exe"**;
2. 驱动装好后**仍然要管理员权限**才能打开设备,得自我提权;
3. 检测不能只看服务项,卸载到一半会留下 `Services\PawnIO` 但设备节点
   已经被摘掉。

**"只弹一次 UAC"的设计**:没装驱动时,先把自己提权重开,让提权后的那个
实例去装驱动 —— 它已经是管理员了,装的时候不会再弹窗。
所以整个流程用户只点一次"是"。

### 3.4 `e9f7fb5` 概览 / 详细两种模式

**需求**:想要一个"一屏看完"的概览模式,和一个"什么都有"的详细模式,
启动时让用户选,默认概览。

**实现要点**:

- 提示用 `input()` 读,`parse_mode_choice()` 解析:
  空输入(直接回车)→ 概览;`1`/`2`/`overview`/`detail` 都认;
  认不出来就再问一次;
- **非交互场景不弹提示**:`--json` / `--csv`、stdin 不是终端、
  输出被重定向 —— 一律用默认模式,免得程序干等着没人回答;
- 概览模式**不采集进程**,直接顶上 `_NullProcessCollector`,
  省一次进程遍历;
- 概览模式加上了**每块硬盘的读写速率和温度**;
- 头部改成"按重要性塞,装不下就砍" —— 头部不是面板,不会被自动裁剪,
  加了模式名之后宽度会超,得自己算。

### 3.5 `071c919` 性能优化一:GPU 走 NVML

见 §4.6 和 §5。单帧从 78ms 降到 18ms。

### 3.6 `f692b26` 性能优化二:SMART 走 WMI

见 §4.7 和 §5。

---

## 4. 技术详解

### 4.1 CPU 温度:为什么必须读 MSR

Windows 上能拿到的"CPU 温度"只有两个来源:

| 来源 | 实际是什么 | 能不能用 |
| --- | --- | --- |
| ACPI 热区 `MSAcpi_ThermalZoneTemperature` | 主板 ACPI 区报的粗糙值 | ❌ 几乎所有主板上这个值**不随负载变化** |
| MSR `IA32_THERM_STATUS` / `IA32_PACKAGE_THERM_STATUS` | CPU 里的数字温度传感器 | ✅ 真实值 |

LHM(以及绝大多数硬件监控工具)读的是前者。区别有多大?本机实测:
ACPI 热区报 27.9°C,而同时 MSR 读出来的封装温度是 60°C,最热的核 67°C。
**差 30 多度**,而且 ACPI 那个值一晚上都不会变。

所以本项目**必须**读 MSR,也就必须有一个 ring0 驱动。

### 4.2 MSR 是按核隔离的 —— 绑核读

这是 MSR 读取最容易踩的坑之一。

`IA32_THERM_STATUS` 读的是**当前正在执行这条指令的那个逻辑核**的温度。
想在一次采样里拿到 20 个核的温度,就必须让 20 条读指令分别跑在 20 个核上。

**错误做法**:每次采样新建 20 个线程,各自 `SetThreadAffinityMask` 绑核再读。
线程创建开销大,而且绑核的设置/撤销也是开销。

**本项目的做法**:每个逻辑核起一个**常驻线程**,启动时绑好核,
之后靠 `threading.Event` 收发请求:

```python
class _CoreWorker(threading.Thread):
    def run(self) -> None:
        self.pinned = pin_current_thread(self.core_id)   # 绑死在第 N 核
        while True:
            self._request.wait()      # 等采样线程喊我
            self._request.clear()
            out = self._module.execute("ioctl_read_msr", (MSR_THERM_STATUS,), 1)
            self.value = out[0] if out else None
            self._done.set()
```

绑核用 `SetThreadAffinityMask(线程句柄, 1 << core_id)`。
注意 `PawnIO` 的执行器句柄**不是线程安全的**,所以
`PawnIoClient.execute()` 里面有一把锁把 20 个线程串行化。

### 4.3 温度的位域公式

`IA32_THERM_STATUS` (0x19C) 的解码有个经典陷阱:

```
bit 31      : 有效位(status & 0x80000000 必须为 1,否则读数无效)
bits 22:16  : delta(当前温度与 TjMax 的差值)
```

公式:

```
temp = TjMax - ((status >> 16) & 0x7F)
```

**不是**"低位是数字、bit15 是有效位"那种别的布局。
封装温度的 MSR 地址是 `0x1B1`(`IA32_PACKAGE_THERM_STATUS`),
**不是** `0x1B0`。

TjMax 从 `IA32_TEMPERATURE_TARGET` (0x1A2) 的 bits 23:16 取,
并做了合理性检查(只在 40~150 之间才认),否则退化成 100°C。

### 4.4 RAPL 功耗

功耗不是直接读出来的,是**算**出来的:

1. `MSR_RAPL_POWER_UNIT` (0x606) 的 bits 12:8 给出能量单位:
   `multiplier = 1 / (1 << bits)`;
2. `MSR_PKG_ENERGY_STATUS` (0x611) 是封装域的累计能耗计数;
   `MSR_PP0_ENERGY_STATUS` (0x639) 是核心域;
   `MSR_PP1_ENERGY_STATUS` (0x641) 是核显域;
3. 两次采样相减除以时间间隔:

```
power = multiplier * Δcounter / Δt
```

**32 位计数器会回绕**,所以 `Δcounter < 0` 时要加上 `1 << 32`。
另外两次采样间隔小于 50ms 就直接返回 `None`(噪声太大)。

### 4.5 PawnIO 驱动:三个坑

**为什么用 PawnIO 而不是 WinRing0**:
WinRing0 早被微软封了(直接发内核补丁,CE 之后的 Windows 不再接受未签名驱动)。
PawnIO 是它的替代方案,只开放受控的 ring0 能力,而且驱动是
**WHQL 签名**的,Secure Boot 开着也能装,不需要关 VBS。

**坑 1:官方安装器靠自己的文件名判断角色。**
反汇编 `PawnIO_setup.exe` 可以看到它调用 `wcsstr(argv[0], "uninstall.exe")` ——
路径里只要出现 "uninstall.exe",它就认为自己是卸载器,
于是 `-install` 会被自己抵消掉,弹一个
"Please specify either -install or -uninstall" 就退出。

所以内置副本必须叫 `PawnIO_setup.exe`(不能叫 `uninstall.exe`),
而且**存放路径里也不能出现 "uninstall.exe"**。
`driver.py:install()` 里有一条显式检查:

```python
if _UNINSTALLER_MARK in str(exe).lower():
    return InstallResult(False, f"内置安装器路径不能包含 {_UNINSTALLER_MARK}: {exe}")
```

**坑 2:装好之后仍然要管理员权限。**
PawnIO 暴露的是 MSR / I/O 端口这类 ring0 能力,设备对象只放行管理员;
普通进程打开会拿到 `0x80070005`(ACCESS_DENIED)。
所以 `probe_access()` 专门用来区分"驱动没装"和"驱动装了但我没权限",
后者要触发自我提权。

**坑 3:检测不能只看服务项。**
装好之后系统里留下三样东西:

- `Services\PawnIO` —— 驱动服务项;
- `Enum\ROOT\PAWNIO` —— 设备节点(`ROOT\PAWNIO\0000`);
- `Uninstall\PawnIO` —— "应用和功能"里的卸载项。

前两个齐了才算装好。**只剩服务项的情况是卸载到一半** ——
设备节点已经被摘掉(重启后驱动就没了),这时要重新装一次。

**提权实现**:`ShellExecuteExW` + `lpVerb="runas"`,配
`SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI`。
源码运行时(`python -m pcmonitor`)要重开一个解释器带上同样的参数,
并且**必须把工作目录设成当前目录**,否则 `import pcmonitor` 会失败。

### 4.6 GPU:NVML vs nvidia-smi

**第一版**用 `nvidia-smi --query-gpu=... --format=csv,noheader,nounits`,
一次拿到所有需要的字段,看着挺美。

**问题**:实测一次 **47ms**,占了整帧采样的六成。

**关键观察**:贵的是**进程启动 + NVML 初始化**,不是查询本身 ——
加不加字段耗时几乎一样(因为 `nvidia-smi` 每次启动都会重新初始化 NVML)。
所以"少查几个字段"这条路走不通。

**解法**:ctypes 直接调 `nvml.dll`(`%SystemRoot%\System32\nvml.dll`,
随显卡驱动一起装):

```python
lib = ctypes.WinDLL("nvml.dll")
# 逐个绑定函数,绑不上就记 None,那一项显示"不可用"
self._fn["init"]    = self._bind("nvmlInit_v2", [])
self._fn["count"]   = self._bind("nvmlDeviceGetCount_v2", [POINTER(c_uint)])
self._fn["handle"]  = self._bind("nvmlDeviceGetHandleByIndex_v2",
                                 [c_uint, POINTER(c_void_p)])
self._fn["util"]    = self._bind("nvmlDeviceGetUtilizationRates",
                                 [c_void_p, POINTER(_Utilization)])
```

**结果**:一次 **0.098ms**,快了 480 倍,整机还少了一个进程的创建/销毁。

**兼容处理**:本文用到的接口从 NVML 2.0 起就没变过,但个别老驱动缺某个
函数(比如 PCIe 链路信息在老驱动上叫 `nvmlDeviceGetPcieLinkGeneration`,
新驱动改名成 `nvmlDeviceGetCurrPcieLinkGeneration`)。
`nvml.py` 的 `_bind()` 绑定不上就返回 `None`,那一项显示"不可用",**不编数字**。

`nvidia-smi` 保留着当降级方案:NVML 初始化成功却没采到东西时,自动退回。

### 4.7 硬盘 SMART:WMI vs cmdlet

这是性能优化里最费劲的一处,前后试了五六种写法。

**第一版**(也是绝大多数工具的写法):

```powershell
Get-PhysicalDisk | Get-StorageReliabilityCounter
```

实测:**1270 ms 墙钟 / 900 ms CPU**。一个 PowerShell 进程自己就烧掉将近
一秒 CPU(纯 `powershell -Command exit` 只要 80ms 墙钟,剩下的全是这两个
cmdlet 的开销)。而且 —— **非管理员身份下直接报错**,型号、温度、健康度
一个都读不到。

**试过的其它写法**:

| 写法 | 墙钟 | 子进程 CPU | 非管理员 |
| --- | --- | --- | --- |
| `Get-PhysicalDisk` 单独 | 939 ms | 906 ms | ❌ |
| `Get-CimInstance MSFT_PhysicalDisk` 单独 | 270 ms | 250 ms | ✅ |
| `Get-CimInstance` + `Get-CimAssociatedInstance` | 600 ms | **250 ms** | ✅ 型号/健康度 |
| `Get-WmiObject` 查 `root/wmi` 的 FailurePredict 类 | 400 ms | 230 ms | ✅ 但只覆盖 SATA |
| `Get-CimInstance` + `Get-StorageReliabilityCounter` | 1200 ms | 810 ms | ✅ |

**最终方案**:

```powershell
foreach ($d in (Get-CimInstance -Namespace root/Microsoft/Windows/Storage
                              -ClassName MSFT_PhysicalDisk)) {
    $r = Get-CimAssociatedInstance -InputObject $d `
                                   -ResultClassName MSFT_StorageReliabilityCounter
    ...
}
```

- `MSFT_PhysicalDisk` 拿型号 / 总线类型 / 容量 / 健康度 —— **普通用户也能读**;
- `MSFT_StorageReliabilityCounter` 拿温度 / 通电时长 / 磨损 —— 要管理员。

CPU 降到 1/3,数值跟老路完全一致(本机四块盘 42/48/49/34°C 全对得上),
还顺带修好了非管理员读不到数据的问题。

**枚举值映射**(WMI 返回的是数字,得自己转):

| 枚举 | 值 | 映射 |
| --- | --- | --- |
| `MSFT_StorageBusType` | 0/3/7/11/14/16/17 | 未知 / ATA-IDE / USB / SATA / 虚拟磁盘 / 存储空间 / NVMe |
| `MSFT_PhysicalDisk.MediaType` | 0/3/4 | 未指定 / HDD / SSD |
| `MSFT_StorageEnums.StorageHealthStatus` | 0/1/2 | Healthy / Warning / Unhealthy |

> 顺带记一笔,免得以后再走弯路:Windows 上**没有便宜的原生 SMART 读取办法**。
> `IOCTL_STORAGE_QUERY_PROPERTY` 的 `StorageDeviceTemperatureProperty`
> 只有 NVMe / USB-SAT 驱动支持,SATA 盘直接 `ERROR_INVALID_FUNCTION`;
> ATA 直通(`IOCTL_SCSI_PASS_THROUGH_DIRECT`)和老式 IDE 寄存器直通
> (`SMART_RCV_DRIVE_DATA`)在 AHCI 驱动上同样不支持。
> SATA 盘的温度只能靠 WMI,没有更省的办法。详见 §6.2、§6.3。

### 4.8 内存提交量:`PERFORMANCE_INFORMATION` 的坑

Windows 的"提交量"(Commit)是判断内存压力的关键指标:
物理内存用完之前,提交量就已经先触顶,随后开始大量换页。

`K32GetPerformanceInfo` 能一次拿到提交量、提交上限、提交峰值、
物理内存总量、进程数、线程数。但有两个坑:

1. **结构体大小**:SDK 头文件里只有 88 字节,内核实际用 104 字节的版本
   (多出 `PoolPaged` / `PoolNonpaged`)。`cb` 传 88 直接
   `ERROR_BAD_LENGTH` 失败;
2. **单位是页不是字节**,要用结构体里的 `PageSize` 换算。

```python
class _PerformanceInformation(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("CommitTotal", ctypes.c_size_t),
        ...
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_uint32),
        ("ProcessCount", ctypes.c_uint32),
        ("ThreadCount", ctypes.c_uint32),
    ]
```

用 `kernel32` 的 `K32` 前缀版本而不是 `psapi.dll` 的转发层,行为一致。

### 4.9 慢查询为什么必须放后台

本项目里有三类东西慢:

| 东西 | 慢在哪 | 现在多贵 |
| --- | --- | --- |
| 硬盘 SMART | 拉起 PowerShell 进程 | 0.25s CPU |
| 内存条信息 | 拉起 PowerShell 进程 | 0.25s CPU(只查一次) |
| ACPI 热区 | 拉起 PowerShell 进程 | 0.22s CPU(120 秒一次) |

**只要一个查询会同步阻塞采样线程,界面就会顿。** 所以规矩是:

- 这些查询全部交给 `BackgroundRefresher`(守护线程 + 缓存 + 锁);
- 采样线程只做一次加锁读,`get()` 是 O(微秒) 的;
- `refresh_now()` 只在测试和首帧预热时用,那是故意同步的。

`BackgroundRefresher` 后来加了个 `stop_after_success` 参数:
内存条的型号/频率/容量运行期根本不会变,查成功一次就停,
失败才按间隔重试。少拉起一个 PowerShell 就少 0.25 秒 CPU。

### 4.10 终端渲染:双宽字符 + ANSI

`util.py` 是整个 UI 的地基,两个坑:

**双宽字符**:中文/日文在等宽终端里占 2 列,`len()` 和 `ljust()` 全算错。

```python
def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    if ch in ("", "﻿"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
```

**ANSI 转义序列**:上色之后字符串里混进了 `\033[38;5;39m` 这样的字符,
算宽度时必须剥掉,否则右边框全部错位。

```python
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

def dwidth(text: str) -> int:
    return sum(char_width(c) for c in _ANSI_RE.sub("", text))
```

截断(`truncate()`)更麻烦:要在保留颜色的前提下按显示宽度切。
做法是用 `_ansi_tokens()` 把字符串拆成"转义序列"和"普通文本"两类片段,
逐段处理,切的时候补一个 `\033[0m` 把颜色收尾。

**面板边框**:`theme.py` 的 `row()` 会先 `truncate()` 再 `pad()`,
所以内容再长也不会把面板撑破。但**头部不是面板**,不会被自动裁剪 ——
所以头部得自己算宽度(§3.4)。

### 4.11 打包:PyInstaller onefile

`pc-monitor.spec` 里要打进去的东西:

```python
binaries = []
dll = vendor / "PawnIOLib.dll"
if dll.is_file():
    binaries.append((str(dll), "vendor/pawnio"))

modules_dir = vendor / "modules"
if modules_dir.is_dir():
    for module in sorted(modules_dir.glob("*.bin")):
        binaries.append((str(module), "vendor/pawnio/modules"))

datas = []
installer = vendor / "PawnIO_setup.exe"
if installer.is_file():
    datas.append((str(installer), "vendor/pawnio"))
```

- `PawnIOLib.dll` 和 `IntelMSR.bin` **必须**一起打进去,否则读不到 MSR,
  CPU 温度/功耗会变成"不可用";
- `PawnIO_setup.exe` 走 `datas` 而不是 `binaries`(它是数据不是依赖),
  名字必须是 `PawnIO_setup.exe`(§4.5);
- `excludes` 砍掉 numpy / pandas / matplotlib / tkinter / PIL / pytest,
  能显著减小体积。

**打包后怎么找资源**:`resources.py` 统一处理两种情况:

```python
def app_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)      # onefile 解压出来的临时目录
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
```

**`scripts/build.bat` 必须是纯 ASCII**。cmd.exe 用控制台 ANSI 代码页读
`.bat` 文件,UTF-8 的中文注释会让解析器错位,后面的行会被静默改坏。
中文文档一律放 README。

### 4.12 自我提权与"只弹一次 UAC"

```python
def prepare_driver(args) -> bool:
    if args.no_msr:
        return True

    if not driver.is_installed():
        if args.no_driver_install:
            _warn("提示:未检测到 PawnIO 驱动,按 --no-driver-install 跳过安装,MSR 指标不可用。")
            return True
        if _can_elevate(args) and not driver.is_elevated():
            _info("首次运行需要安装 PawnIO 驱动,正在以管理员身份重新启动……")
            if driver.elevate_self():
                return False        # 已经重开了一个实例,这边马上退出
        _info("正在安装 PawnIO 驱动(会弹出 UAC 授权窗口,请点“是”)……")
        result = driver.install()
        if not result.ok:
            _warn(f"PawnIO 驱动安装失败:{result.message}")
            return True

    # 装好了,但当前进程打不开设备(设备只放行管理员)
    hr = driver.probe_access()
    if hr == driver.ACCESS_DENIED_HRESULT and not driver.is_elevated():
        if _can_elevate(args):
            if driver.elevate_self():
                return False
        _warn("提示:当前不是管理员身份,MSR 指标(CPU 温度/功耗)不可用。")
    return True
```

关键在于顺序:**先提权重开,再在提权后的实例里装**。
这样用户只点一次 UAC。`--json` / `--csv` 不自动提权
(输出可能要重定向到管道,重开进程会把这条流弄丢),只提示用户。

---

## 5. 性能实测

测量条件:20 逻辑核,2 秒刷新间隔,详细模式,Windows 10 19045。

### 5.1 单帧分解(优化后)

| 采集器 | 耗时 | 说明 |
| --- | --- | --- |
| cpu | 0.15 ms | psutil + 20 个绑核线程读 MSR |
| memory | 4.13 ms | virtual_memory + swap_memory + GetPerformanceInfo(带缓存) |
| disk | 0.28 ms | 分区列表走 30 秒缓存 |
| disk_io | 0.07 ms | psutil |
| network | 2.49 ms | `net_io_counters(pernic=True)`,一次系统调用 |
| gpu | 0.22 ms | **NVML**(原来是 nvidia-smi 的 47ms) |
| thermal_disks | 0.00 ms | 只读缓存 |
| process | 10.35 ms | 206 个进程,每帧一次遍历 |
| **采样合计** | **17.70 ms** | |
| **整帧 + 渲染** | **18.35 ms** | |

### 5.2 优化前后

| | 优化前 | 优化后 | 变化 |
| --- | --- | --- | --- |
| 单帧采样 + 渲染 | 78 ms | **18 ms** | 4.3× |
| 整个程序占单核 | ~18% | **~4.4%** | 4× |
| 折合整机 CPU | ~0.9% | **~0.22%** | 4× |

测量方法:连续跑 40 秒,累计主进程 `cpu_times()` 加上所有
PowerShell 子进程的 CPU,再除以墙钟时间。

### 5.3 各项优化单独贡献多少

| 优化 | 省下的 |
| --- | --- |
| `nvidia-smi` → NVML | 46.9 ms/帧(占整帧 61%) |
| 进程列表不预取 `username` | 7.4 ms/帧 |
| 分区列表缓存 30 秒 | 2.1 ms/帧 |
| `GetPerformanceInfo` 调 3 次 → 1 次 | 2.0 ms/帧 |
| SMART 查询方式换掉 | 每次 900 ms → 250 ms CPU |
| 内存条信息只查一次 | 省掉每分钟 0.25 s CPU |
| ACPI 热区 30s → 120s | 省掉 2/3 的 0.22 s CPU |

### 5.4 后台查询的调度

| 查询 | 间隔 | 每次 CPU | 平均占用单核 |
| --- | --- | --- | --- |
| 硬盘 SMART | 10 s | 0.25 s | 2.5% |
| ACPI 热区 | 120 s | 0.22 s | 0.2% |
| 内存条信息 | 只查一次 | 0.25 s | ≈0 |

---

## 6. 走过的弯路

这一节是整份文档里最值钱的 —— 知道哪些路走不通,能省下以后重复踩坑的时间。

### 6.1 LibreHardwareMonitor(放弃)

LHM 是 Windows 上最靠谱的硬件监控库,直接拿来用能省掉一半工作量。

**放弃原因**:

1. **要 .NET**。C# 库需要 pythonnet + .NET Framework 运行时,
   打包后体积大、启动慢;
2. **会静默失败**。在部分机器上会因程序集版本绑定失败而**枚举不到任何硬件**
   —— `Computer.Open()` 返回成功,硬件数为 0,**没有任何报错**。
   这种"看起来成功其实是空的"的失败模式比直接报错难查得多;
3. 它读的还是 ACPI 热区那种假温度(§4.1),拿不到 MSR 级别的真值;
4. PawnIO 反正要装(LHM 内部用的也正是 PawnIO),那不如直接调它的 C API。

**替代**:ctypes 直接调 `PawnIOLib.dll`,只有三个函数
(`pawnio_open` / `pawnio_load` / `pawnio_execute` / `pawnio_close`),
纯 Python,零运行时依赖。

### 6.2 原生 SMART:ATA 直通(放弃)

想用 `IOCTL_SCSI_PASS_THROUGH_DIRECT` 发一条 ATA `SMART READ DATA`
命令(0xB0,feature 0xD0),直接读 512 字节 SMART 数据 —— 这样连
PowerShell 都不用拉,几十微秒就完事。

**为什么不行**:本机 SATA 盘走 AHCI 驱动,AHCI 的 storport **不实现**
ATA 直通,`DeviceIoControl` 直接返回 `ERROR_INVALID_FUNCTION` (1)。
试过的四条路:

| 方式 | IOCTL | 结果 |
| --- | --- | --- |
| ATA-16 直通 | `IOCTL_SCSI_PASS_THROUGH_DIRECT` | `ERROR_INVALID_FUNCTION` |
| IDE 寄存器直通 | `SMART_RCV_DRIVE_DATA` | `ERROR_INVALID_FUNCTION` |
| 存储温度属性 | `IOCTL_STORAGE_QUERY_PROPERTY` / `StorageDeviceTemperatureProperty` | SATA:不支持;NVMe:支持 |
| 存储设备描述符 | `IOCTL_STORAGE_QUERY_PROPERTY` / `StorageDeviceProperty` | ✅ 支持(396 字节) |

**结论**:存储温度属性只有 NVMe / USB-SAT 驱动支持,SATA 盘拿不到。
**没有更省的办法,只能靠 WMI。** 这一条已经写进 README 的技术要点。

### 6.3 保留一个常驻 PowerShell 进程(放弃)

想法:既然慢在"每次拉起一个 PowerShell 进程",那就**常驻一个**,
让它自己循环查询,结果从 stdout 一行行读回来。这样每个查询省掉
约 80ms 的进程启动开销。

**为什么放弃**:实测进程启动只占 80ms,而 `Get-StorageReliabilityCounter`
本身要 600~900ms。**省下来的只是零头**。而且常驻进程要处理管道阻塞、
进程崩溃重启、编码,复杂度上去了收益却只有 10%。

后来发现真正的大头是 `Get-PhysicalDisk` 这个 cmdlet 本身
(它会去查 `MSFT_PhysicalDisk` + `MSFT_Disk` + `MSFT_Partition` 好几个类),
换成直接 `Get-CimInstance` 一个类就够 —— 这才是 §4.7 的由来。

### 6.4 每次采样新建绑核线程(放弃)

见 §4.2。一开始是"每次采样给每个核建一个线程、绑核、读、退出",
后来改成常驻线程 + Event 握手。理由是线程创建 + 绑核 + 解绑的开销
在 2 秒刷新间隔下虽然不大,但常驻版本代码也不复杂,没有理由不换。

### 6.5 `process_iter(["status"])` 和 `username` 预取(放弃)

- `["status"]` 会预取每个进程的状态,**200 多个进程要 200ms+**;
- 逐个进程调 `num_threads()` 更慢,改成一次 `GetPerformanceInfo` 拿总量;
- **`username` 预取**是最隐蔽的一个:它要开进程 token 查 SID,
  206 个进程实测 **7.4ms**,而界面上根本没显示用户名。
  预取一次直接省掉三分之一的时间。顺带把 `models.py` 里
  那个从来没被填过的 `ProcessStat.username` 字段也删了。

### 6.6 `Get-WmiObject` vs `Get-CimInstance`(结论:看情况)

- 查 `root/wmi` 的老式 `MSStorageDriver_FailurePredict*` 类,
  `Get-WmiObject` 确实比 `Get-CimInstance` 快一点(400ms vs 450ms);
- 但这类**只覆盖 SATA/IDE**,NVMe 和 USB 盘拿不到温度;
- 而 `Get-CimInstance MSFT_PhysicalDisk` + 关联类**四块盘全覆盖**。

所以选了后者。**覆盖度比那 50ms 重要。**

### 6.7 头部加模式名把宽度撑爆了(踩坑)

给头部加上"概览模式 / 详细模式"之后,`test_render_lines_fit_width[80]`
挂了 —— 实际宽度 91 > 80。

**原因**:面板内容会被 `theme.row()` 自动裁剪,但**头部不是面板**,
没人管它。

**解法**:把头部的各个部分按重要性排好,逐个往里塞,
塞到放不下就停:

```python
for part in optional:
    if dwidth(sep.join(parts + [part])) > t.width:
        break
    parts.append(part)
```

顺带加了"头部在 80 / 100 / 160 宽度下都不超"和"有颜色/无颜色两种情况"
的测试。

---

## 7. 已知限制与后续

### 7.1 已知限制

| 限制 | 原因 | 计划 |
| --- | --- | --- |
| 内存条温度、时序拿不到 | 读 SPD 需要 SMBus 工具芯片支持,PawnIO 的 SMBus 模块覆盖不了所有主板 | v0.2 试试 `IsaBridgeEC` / `SmbusI801` 模块 |
| 只支持 NVIDIA | NVML 是 NVIDIA 私有接口 | v0.2 可以接 AMD ADLX / 自行读 PCI 配置空间 |
| 硬盘温度要管理员权限 | `MSFT_StorageReliabilityCounter` 要管理员 | 默认自动提权;`--no-msr` 模式下不提权,这几项显示"不可用" |
| 核心电压只有 VID 推算值 | `IA32_PERF_STATUS` 的 VID 不是真实电压 | 需要读 VCCIN 传感器,PawnIO 覆盖不到 |
| ACPI 热区不随负载变化 | 主板 ACPI 区就是这么报的 | 已经标为"近似",只当兜底 |
| RAPL 功耗首次采样没有值 | 计数器要两次采样求差 | `warmup()` 已经处理 |

### 7.2 v0.2 计划

1. **内存条温度**:试 PawnIO 的 SMBus 模块读 SPD;
2. **AMD 支持**:CPU 温度走 `MSR_AMD_*`,GPU 走 ADLX;
3. **告警阈值可配置**:配置文件放 `%LOCALAPPDATA%\pc-monitor\config.json`;
4. **历史曲线**:把采样数据存到 SQLite,画一天的曲线;
5. **多显示器/高 DPI**:现在只按终端宽度适配。

---

## 8. 测试策略

102 个用例,1252 行。原则是**不测硬件,测逻辑**。

| 文件 | 用例 | 测什么 |
| --- | --- | --- |
| `test_util.py` | 13 | 双宽字符宽度、ANSI 感知截断/补齐、格式化函数 |
| `test_dashboard.py` | 19 | 面板渲染、各种宽度下不超宽、两种模式的内容差异 |
| `test_cli.py` | 23 | 参数解析、模式选择(空输入/数字/乱码/EOF)、非交互降级 |
| `test_driver.py` | 12 | 驱动检测、安装器校验和、"卸载器"名字的坑、提权 |
| `test_background.py` | 12 | 后台刷新器不阻塞前台、错误不致命、`stop_after_success` |
| `test_collectors.py` | 9 | 采集器的降级行为 |
| `test_gpu.py` | 4 | nvidia-smi 的 CSV 解析(N/A、显存单位换算) |
| `test_nvml.py` | 6 | NVML 缺失时的降级、NVML 优先于 nvidia-smi |
| `test_pawnio.py` | 4 | 绑核辅助函数 |

**两条值得说的**:

1. **不测真实硬件**。`test_nvml.py` 里有一个"本机有 N 卡就真采一次"的
   用例,但它对两种结果都放行 —— 重点是"不能抛异常";
2. **守护性能约定**。`test_background.py` 里有这么一条:

```python
def test_smart_script_avoids_the_slow_storage_cmdlets():
    """守护这条性能约定:别再退回 `Get-PhysicalDisk` 那条 0.9 秒 CPU 的老路。"""
    script = thermal._PHYSICAL_DISK_SCRIPT
    assert "Get-PhysicalDisk" not in script
    assert "Get-StorageReliabilityCounter" not in script
```

   性能优化最怕的就是"过半年有人手一痒改回去"。用一条测试把决定钉住。

**`test_gpu.py` 的教训**:加了 NVML 之后,原来那些测 nvidia-smi 解析的用例
全挂了 —— 因为在有 N 卡的机器上根本轮不到假的 subprocess。
改法是给 `GpuCollector` 加个 `use_nvml=False` 参数,让降级路径可以被显式测试。
**降级路径必须能测**,不然它就等于没有。

---

## 9. 附录

### 9.1 关键常量速查

| 常量 | 值 | 出处 |
| --- | --- | --- |
| `IA32_PERF_STATUS` | 0x198 | `collectors/cpu.py` |
| `IA32_THERM_STATUS` | 0x19C | 同上 |
| `IA32_TEMPERATURE_TARGET` | 0x1A2 | 同上 |
| `IA32_PACKAGE_THERM_STATUS` | 0x1B1 | 同上 |
| `MSR_RAPL_POWER_UNIT` | 0x606 | 同上 |
| `MSR_PKG_ENERGY_STATUS` | 0x611 | 同上 |
| `MSR_PP0_ENERGY_STATUS` | 0x639 | 同上 |
| `MSR_PP1_ENERGY_STATUS` | 0x641 | 同上 |
| `IOCTL_STORAGE_QUERY_PROPERTY` | 0x2D1400 | (试过,见 §6.2) |
| `IOCTL_SCSI_PASS_THROUGH_DIRECT` | 0x4D1037 | (试过,见 §6.2) |
| `SMART_RCV_DRIVE_DATA` | 0x4D0817 | (试过,见 §6.2) |
| `ACCESS_DENIED_HRESULT` | 0x80070005 | `driver.py` |
| `PERFORMANCE_INFORMATION` 大小 | **104** 字节 | `collectors/memory.py` |
| `_PARTITION_TTL` | 30.0 s | `collectors/disk.py` |
| `_PERF_INFO_TTL` | 0.5 s | `collectors/memory.py` |
| `PawnIO_setup.exe` SHA-256 | `a3a46226...85b47a0` | `vendor/pawnio/README.md` |

### 9.2 关键命令

```bash
# 开发
scripts\setup.bat                # 建虚拟环境、装依赖、跑测试
python -m pytest tests -q        # 跑测试
python -m pyflakes pcmonitor tests

# 跑
python -m pcmonitor                        # 启动(会问显示模式)
python -m pcmonitor --mode detail          # 跳过提示
python -m pcmonitor --once --json          # 采一帧输出 JSON
python -m pcmonitor --no-msr               # 不装驱动,不要 MSR 指标

# 打包
scripts\build.bat                 # 产出 dist\pc-monitor.exe
```

### 9.3 参考

- [PawnIO](https://github.com/namazso/PawnIO) —— 内核驱动与 AMX 模块
- [Intel SDM](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html) —— MSR 定义
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) —— 解码规则的参考实现
- [NVIDIA ML SDK](https://developer.nvidia.com/nvidia-management-library-nvml) —— NVML 接口文档
- [MSFT_PhysicalDisk](https://learn.microsoft.com/windows/win32/wmi/root/microsoft/windows/storage/msft-physicaldisk) —— WMI 存储类

### 9.4 提交历史

```
8bdbf70  feat: Windows 硬件监控终端仪表盘
06c6af4  perf: 慢查询移到后台线程,消除界面卡顿
231bc15  feat: 内置 PawnIO 安装器,首次运行自动检测并安装驱动
e9f7fb5  feat: 概览/详细两种显示模式,启动时选择(默认概览)
071c919  perf: GPU 采集改用 NVML,采样路径瘦身,单帧 78ms -> 18ms
f692b26  perf: 硬盘 SMART 改用 WMI 查询,后台开销降到 1/3
```
