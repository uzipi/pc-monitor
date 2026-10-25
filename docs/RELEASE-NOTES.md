# pc-monitor v0.1

Windows 硬件监控终端仪表盘。**单文件 exe**,目标机器不需要装 Python、
不需要装 .NET,也不需要手动去下载驱动 —— 官方 PawnIO 安装器已经打进 exe,
首次运行自动检测、自动安装、自动提权。

## 下载

| 文件 | 说明 |
| --- | --- |
| `pc-monitor.exe` | 主程序,单文件,直接双击运行 |
| `pc-monitor.exe.sha256` | 校验和,用来确认下载没损坏 |

**校验:**

```cmd
certutil -hashfile pc-monitor.exe SHA256
```

把输出的哈希和 `.sha256` 文件里的对一下就行。

| | |
| --- | --- |
| 适用系统 | Windows 10 / 11(64 位) |
| 运行时依赖 | 无 |
| 体积 | 约 11 MB |
| 许可证 | GPL-2.0-or-later |

## 首次运行会弹一次 UAC

程序要读 MSR 才能拿到**真实的** CPU 温度(封装温度、每核温度、电压、RAPL 功耗),
这需要 ring0 权限。首次运行会:

1. 检测 PawnIO 驱动装没装;
2. 没装的话,提权重开自己;
3. 在提权后的实例里静默装驱动 —— 全程**只弹一次** UAC,点"是"就行。

驱动是 WHQL 签名的,Secure Boot 开着也能装,不用关 VBS。

不想装驱动也可以加 `--no-msr`,其余指标(占用率、显存、SMART、网速、进程)照常工作。

## 两种显示模式

启动时会问一次,**直接回车就是概览模式**:

```
选择显示模式:
  1 概览模式  CPU / 内存 / GPU / 网络 / 磁盘,一屏看完
  2 详细模式  全部面板:每核温度、硬盘 SMART、网卡、进程榜
请输入编号 [1]:
```

命令行也能直接指定,跳过提问:`--mode overview` / `--mode detail`。

## 一点说明

- **CPU 温度是真的**。直接读 Intel MSR,不是 ACPI 热区那种"永远不变"的假值
  (本机实测两者能差 30 多度)。
- **硬盘温度需要管理员权限**。型号、总线类型、健康度普通用户就能读到,
  但温度、通电时长、磨损来自存储可靠性计数器,要管理员。
- **GPU 指标只支持 NVIDIA**。走 NVML,拿不到才退回 `nvidia-smi`。
- **内存条温度和时序拿不到**。DDR4 模组本身没有温度传感器,时序要读 SPD
  EEPROM,当前没有可用的实现 —— 界面上显示"不可用",不编数字。

## 常用参数

```cmd
pc-monitor.exe --interval 2.0
pc-monitor.exe --json --interval 5
pc-monitor.exe --csv --once
pc-monitor.exe --no-msr
pc-monitor.exe --top 10
```

分别是:改刷新间隔(秒)、每帧输出一行 JSON(方便接采集)、输出一帧 CSV、
不读 MSR(不需要装驱动)、进程榜取前 N 名。

完整参数见 [README](https://github.com/uzipi/pc-monitor#命令行参数)。

## 文档

- [README](https://github.com/uzipi/pc-monitor) —— 用法、指标表、性能占用、已知限制
- [开发记录与技术总结 v0.1](https://github.com/uzipi/pc-monitor/blob/main/docs/DEVELOPMENT-v0.1.md)
  —— 实现细节、踩过的坑、性能实测数据、走过的弯路
