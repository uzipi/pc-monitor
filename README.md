# pc-monitor

Windows 硬件监控仪表盘 —— 终端 TUI,单文件 exe,目标机器不需要装 Python。

CPU / 内存 / GPU 的占用与温度、磁盘 SMART、网速、进程榜,一屏看完。

```
╭─ 概览 ─────────────────────────────────────────────────────────────────────────╮
│ CPU    █████░░░░░░░░░░░░░░  27.3% ▅                5300MHz  68°C  97W          │
│ 内存   ███████████░░░░░░░  58.9% ▅                9.3 GB/15.8 GB  提交 18.4 GB │
│ GPU    █████████████████░  95.0% ▅                70°C  179W  显存 5.4 GB/15.9 GB │
│ 网络  ↓ 25.2 KB/s   ↑ 6.4 KB/s   ▅               ▅                  活动网卡 1 张 │
╰──────────────────────────────────────────────────────────────────────────────╯
```

## 特点

- **CPU 温度是真的**:直接读 Intel MSR(封装温度、每核温度、电压、RAPL 功耗),
  不是 ACPI 热区那种"永远不变"的假值。
- **不依赖 .NET**:绕过 LibreHardwareMonitor 的 C# 库,直接用 ctypes 调
  PawnIO 官方 C API,纯 Python 实现,打包体积小、启动快。
- **驱动不用用户操心**:官方安装器已经打进 exe,第一次运行自动检测、
  自动装、自动提权,用户不用再去 pawnio.eu 下载。
- **两种显示模式**:启动时选一次 —— 概览模式一屏看完(含硬盘 IO 与温度),
  详细模式把每核温度、SMART、网卡、进程榜全摆出来。
- **不卡界面**:SMART、SMBIOS 这类要拉起 PowerShell 的查询(单次 1 秒多)
  全部放到后台线程,采样线程只读缓存,刷新节奏稳定在 ~80ms 一帧。
- **降级诚实**:拿不到的指标显示"不可用",绝不编造数字;每个传感器都标注来源。
- **零重型依赖**:运行时只依赖 `psutil`,其余全是标准库。

## 两种显示模式

启动时会问一次,**直接回车就是 1(概览模式)**:

```
选择显示模式:
  1 概览模式  CPU / 内存 / GPU / 网络 / 磁盘,一屏看完
  2 详细模式  全部面板:每核温度、硬盘 SMART、网卡、进程榜
请输入编号 [1]:
```

**1 · 概览模式**(默认)—— 一屏放得下 CPU / 内存 / GPU / 网络,外加**每块硬盘的
读写速率和温度**:

```
╭─ 概览 ───────────────────────────────────────────────────────────────────────────╮
│ CPU    ███░░░░░░░░░░░░░░░  14.0% ▅                5300MHz  57°C  64W                 │
│ 内存   ███████████████░░░  82.4% ▅                13.0 GB/15.8 GB  提交 29.6 GB/43.6 GB│
│ GPU    ██████████████████ 100.0% ▅                68°C  172W  显存 11.7 GB/15.9 GB 73% │
│ 网络  ↓ 13.6 KB/s   ↑ 3.7 KB/s   ▅               ▅                  活动网卡 1 张      │
│ 磁盘   读     0 B/s 写  1.9 KB/s ▅       ▅        42°C Healthy ST4000NM0035          │
│        读 163.6 KB/s 写 662.2 KB/s ▅       ▅        51°C Healthy ZHITAI TiPlus710   │
╰────────────────────────────────────────────────────────────────────────────────────╯
```

概览模式不画进程榜,也就不会去遍历进程,更省资源。

**2 · 详细模式** —— 本页开头那张图:每核温度网格、硬盘 SMART 细节、
每张网卡的流量、进程 CPU/内存榜。

命令行也可以直接指定,不弹提示:

```bash
pc-monitor --mode overview   # 概览
pc-monitor --mode detail     # 详细
```

## 监控指标

| 分类 | 指标 | 数据来源 | 备注 |
| --- | --- | --- | --- |
| CPU | 总占用率 / 每核占用率 | psutil | 与任务管理器同口径 |
| CPU | 封装温度、每核温度、最高/平均 | MSR `IA32_PACKAGE_THERM_STATUS` / `IA32_THERM_STATUS` | 需要 PawnIO |
| CPU | 频率、电压 | MSR `IA32_PERF_STATUS` | |
| CPU | 封装功耗、核心功耗、核显功耗 | MSR RAPL `0x611/0x639/0x641` | 首次采样后才有值 |
| CPU | TjMax 降频上限 | MSR `IA32_TEMPERATURE_TARGET` | |
| 内存 | 已用/可用/占用率 | psutil | |
| 内存 | 提交量 / 提交上限 / 提交峰值 | `K32GetPerformanceInfo` | Windows 内存压力先兆 |
| 内存 | 页面文件用量 | psutil | |
| 内存条 | 厂商/型号/容量/频率/电压/形态 | SMBIOS `Win32_PhysicalMemory` | |
| 内存条 | 温度、时序 | — | **当前拿不到,见「已知限制」** |
| GPU | 占用率、显存控制器占用、频率、显存频率 | `nvidia-smi` | 仅 NVIDIA |
| GPU | 温度、功耗、风扇 | `nvidia-smi` | |
| GPU | 显存用量 / 占用率 | `nvidia-smi` | |
| GPU | PCIe 链路代数与位宽(当前/上限) | `nvidia-smi` | |
| 磁盘 | 每块物理盘的温度、健康度、通电时长、磨损 | `Get-StorageReliabilityCounter` | SMART,不需要管理员权限 |
| 磁盘 | 每块物理盘的读写速率 | psutil | |
| 磁盘 | 各分区容量占用 | psutil | |
| 网络 | 各网卡上下行速率 | psutil | 物理网卡单独汇总,避免虚拟网卡重复计数 |
| 进程 | CPU / 内存占用 Top N | psutil | 归一化到 0~100% |
| 主机 | 运行时间、系统版本 | psutil | |

## 安装

### 1. 运行

直接下载 `dist/pc-monitor.exe` 双击,或在命令行里跑:

```
pc-monitor.exe
```

或者用源码跑:

```bash
pip install -r requirements.txt
python -m pcmonitor
```

### 2. PawnIO 驱动(读 CPU 温度/功耗的前提)

**不用再去 <https://pawnio.eu/> 下载**。程序自带官方安装器
(`PawnIO_setup.exe`,上游在二进制里明确写了 "This installer can be
redistributed unmodified."),exe 里已经打进去了。

第一次运行时会:

1. 检测驱动装没装(查注册表 `Services\PawnIO` + `Enum\ROOT\PAWNIO`);
2. 没装的话,以管理员身份重新启动自己(弹一次 UAC,点"是");
3. 在提权后的那个实例里静默安装驱动 —— 全程只弹**一次** UAC;
4. 装完直接进主界面,CPU 温度/电压/功耗立刻就有值。

想手动装也可以,双击 `vendor/pawnio/PawnIO_setup.exe` 即可,或者去
pawnio.eu 下载官方安装包。装完**不需要**重启。

驱动是按 `Manual` 启动的,程序第一次访问时会由服务自动拉起。

> PawnIO 是微软封掉 WinRing0 之后的替代方案,只开放受控的 ring0 能力。
> 它的驱动是 **WHQL 签名**的,Secure Boot 开着也能装,不需要关 VBS。

不装驱动也能用:`--no-msr` 会跳过所有 MSR 读取,
其余指标(占用率、显存、SMART、网速、进程)照常工作。

## 命令行参数

| 参数 | 说明 |
| --- | --- |
| `--interval 2.0` | 刷新间隔(秒) |
| `--once` | 只采样一次并输出 |
| `--json` | 每帧输出一个 JSON 对象 |
| `--csv` | 输出 CSV(适合丢进 Excel 做时序分析) |
| `--no-color` | 关闭颜色 |
| `--no-msr` | 不读 MSR(不需要装 PawnIO 驱动) |
| `--no-driver-install` | 没装驱动也不自动装(默认首次运行会自动装) |
| `--mode overview` | 显示模式:概览(默认)/ 详细;不给就启动时问 |
| `--no-process` | 不采集进程列表(更快) |
| `--top 10` | 进程榜取前 N 名 |
| `--width 100` | 强制界面宽度 |
| `--out 文件名` | 输出到文件而不是标准输出 |

示例:

```bash
pc-monitor --json --interval 5 > 监控.jsonl
pc-monitor --csv --once > 快照.csv
```

## 已知限制

- **内存条温度**:DDR4 模组本身没有温度传感器(DDR5 才有),所以这个指标
  在本机的 DDR4 上物理上就不存在。要在 DDR5 上实现,需要读 DDR5 模组
  自带的热传感器(SPD Hub 的温度寄存器)。
- **内存条时序**(如 `CL16-18-18-40`):时序只存在于内存条的 SPD EEPROM 里,
  读它需要走 Intel IMC 的 SPD 通道。PawnIO 提供了 `SmbusIntelSkylakeIMC`
  模块,但它只覆盖 Skylake 服务器平台,消费级桌面平台(Coffee Lake / Comet Lake
  等)的 SPD 走的是 CPU 侧 IMC 寄存器,当前没有可用的现成实现。
  **目前显示为"不可用",而不是填一个假数字。**
- **主板温度、CPU/机箱风扇转速**:需要 SuperIO 芯片(NCT67xx 等)的
  SIO 通道,当前未实现。
- **ACPI 热区温度**不随负载变化,只作兜底参考。界面上单列一行标注为"近似",
  不会跟真实的 MSR 温度混在一起显示。
- GPU 指标依赖 `nvidia-smi`,AMD/Intel 核显暂不支持。

## 技术要点

### 为什么不用 LibreHardwareMonitor

LHM 的 C# 库需要 pythonnet + .NET Framework 运行时:打包后体积大、启动慢,
而且在部分机器上会因程序集版本绑定失败而**静默枚举不到任何硬件**
(`Computer.Open()` 返回成功,硬件数为 0,没有任何报错)。

LHM 0.9.6 内部用的正是 PawnIO,所以本项目绕开 .NET,直接调 PawnIO 的
C API:`pawnio_open` / `pawnio_load` / `pawnio_execute` / `pawnio_close`,
三个 ctypes 调用搞定,没有任何运行时依赖。

### 读 MSR 的两个坑

1. **MSR 是按核隔离的**。想拿到第 N 核的温度,读 MSR 的线程必须跑在第 N 核上。
   本项目为每个逻辑核起一个常驻线程,用 `SetThreadAffinityMask` 绑核,
   收到请求再读,而不是每次采样新建 20 个线程。
2. **温度的位域容易搞错**。`IA32_THERM_STATUS` 的 delta 在 bits 22:16,
   有效性标志是 **bit31**,不是"低位是数字、bit15 是有效位"。
   公式:`temp = TjMax - ((status >> 16) & 0x7F)`,且要求 `status & 0x80000000`。
   封装温度的 MSR 地址是 `0x1B1`,不是 `0x1B0`。

### 装驱动的三个坑

1. **官方安装器靠自己的文件名判断角色**。反汇编可以看到它调用的是
   `wcsstr(argv[0], "uninstall.exe")` —— 路径里只要出现 "uninstall.exe",
   它就认为自己是卸载器,于是 `-install` 会被自己抵消掉,弹一句
   "Please specify either -install or -uninstall" 就以 `0xC000000D` 退出。
   所以内置副本必须叫 `PawnIO_setup.exe`,而且**存放路径里也不能出现
   "uninstall.exe"`(`driver.py` 里还留了一道检查,防止源码运行时把项目
   放在这种路径下)。
2. **驱动装好之后仍然要管理员权限**。PawnIO 暴露的是 MSR / I/O 端口这类
   ring0 能力,设备对象只放行管理员;普通进程打开会拿到
   `0x80070005`(ACCESS_DENIED)。所以非管理员启动时程序要自我提权。
   而 `--json` / `--csv` 的输出是往标准输出里流的(可能被重定向到管道),
   重开进程会把这条流弄丢,所以这两种模式只提示、不自动提权。
3. **检测不能只看服务项**。卸载到一半会留下 `Services\PawnIO`,但设备节点
   已经被摘掉,这时驱动其实已经不能用了,得重新装。所以 `is_installed()`
   要求服务项和 `Enum\ROOT\PAWNIO` **同时**存在。

### `GetPerformanceInfo` 的坑

Windows SDK 头文件里的 `PERFORMANCE_INFORMATION` 只有 88 字节,
但内核实际使用的是 104 字节的版本(多出 `PoolPaged` / `PoolNonpaged`),
`cb` 传 88 会直接 `ERROR_BAD_LENGTH` 失败。而且内存类字段的单位是
**页**而不是字节,要用 `PageSize` 换算。

### 慢查询为什么不能放在采集路径里

硬盘 SMART(`Get-StorageReliabilityCounter`)和内存条信息
(`Get-CimInstance Win32_PhysicalMemory`)都要拉起一个 PowerShell 进程,
实测单次 1.0~1.4 秒。早期版本直接在采样线程里同步调用,
结果就是界面每 10 秒就整体卡住一秒多,动画直接顿住。

现在这两个查询都交给 `collectors/base.py` 里的 `BackgroundRefresher`:
守护线程定期刷新并写入缓存,采样线程只做一次加锁读。
实测一帧从 1350ms 降到 80ms,而且再也不会出现尖峰。

进程采集也踩过同样的坑:`process_iter(["status"])` 和逐个进程的
`num_threads()` 各要 200ms+,改用一次 `GetPerformanceInfo` 拿总量后,
进程榜从 511ms 降到 18ms。

## 目录结构

```
pcmonitor/
  cli.py              命令行入口(--json/--csv/TUI)
  driver.py           PawnIO 驱动的检测、安装、提权
  monitor.py          把各采集器汇总成一帧 Snapshot
  models.py           所有快照数据结构
  resources.py        打包后/源码运行时的资源路径
  util.py             宽度计算、颜色、格式化
  collectors/
    base.py           增量计数器 + 后台刷新器
    cpu.py            CPU + MSR 读取与解码
    memory.py         内存、提交量、内存条信息
    gpu.py            nvidia-smi 解析
    disk.py           分区容量 + 物理盘读写速率
    network.py        各网卡上下行速率
    process.py        进程 Top N
    thermal.py        SMART / ACPI 温度汇总
    pawnio.py         PawnIO 客户端(ctypes)+ 线程绑核
    powershell.py     PowerShell 调用封装
  ui/
    theme.py          配色、边框
    dashboard.py      面板渲染
vendor/pawnio/        第三方二进制(见该目录下的 README)
tests/                pytest 用例
tools/ghfetch.py      GitHub 抓取小工具(raw 域名不通时的备用)
```

## 开发

```bash
scripts\setup.bat     # 建虚拟环境、装依赖、跑测试
python -m pytest tests -q
scripts\build.bat     # 打包成 dist\pc-monitor.exe
```

## 许可证

本项目采用 **GPL-2.0-or-later**(全文见 [LICENSE](LICENSE))。

原因见 [`vendor/pawnio/README.md`](vendor/pawnio/README.md):
项目会分发 PawnIO 的用户态库(GPL-2.0-or-later),并使用 Pawn 接口
调用其 AMX 模块(LGPL-2.1-or-later),因此整体必须采用与 GPL 兼容的许可证。

## 参考

- [PawnIO](https://github.com/namazso/PawnIO) —— 通用内核驱动
- [PawnIO.Modules](https://github.com/namazso/PawnIO.Modules) —— 硬件访问模块
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) —— MSR 解码规则的参考实现
- [Intel® 64 and IA-32 Architectures Software Developer's Manual](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html) —— MSR 定义
