# vendor/pawnio —— 第三方组件

本目录下的文件**不是本项目写的**,而是从上游项目原样取来的二进制。
本项目不修改它们,只通过 PawnIO 的官方 C API 调用。

## 文件与来源

| 文件 | 上游 | 版本 | 许可证 |
| --- | --- | --- | --- |
| `PawnIOLib.dll` | [namazso/PawnIO](https://github.com/namazso/PawnIO) 的 `PawnIOLib/` | 2.1.0 | GPL-2.0-or-later |
| `modules/*.bin` | [namazso/PawnIO.Modules](https://github.com/namazso/PawnIO.Modules) 的 Releases | 0.2.11 | LGPL-2.1-or-later |

- `PawnIOLib.dll` 与本机 `C:\Program Files\PawnIO\PawnIOLib.dll`
  (PawnIO 2.1.0 官方安装包)SHA-256 完全一致。
- `.bin` 是 Pawn 虚拟机的 AMX 字节码,`IntelMSR.bin` 导出
  `ioctl_read_msr` / `msr_read` / `cpuid` / `get_arch` 四个函数,
  本项目只用 `ioctl_read_msr`。
- 许可证全文:`LICENSE.PawnIO-GPL-2.0.txt`、
  `modules/LICENSE.PawnIO.Modules-LGPL-2.1.txt`。

## 为什么本项目整体采用 GPL-2.0-or-later

PawnIO 的驱动与用户态库是 GPL-2.0-or-later。上游的许可例外只覆盖
"**仅**通过设备 IOCTL 接口与驱动通信"的程序;而本项目使用了
**Pawn 接口**——把 `IntelMSR.bin` 加载进 Pawn 虚拟机并调用其中的函数。
按上游的说法,这类使用方必须采用与 GPL 兼容的许可证。
AMX 模块本身是 LGPL-2.1-or-later,同样与 GPL-2.0+ 兼容。

综合下来:本项目以 **GPL-2.0-or-later** 发布,并原样附带以上二进制文件。

## SHA-256 校验和

```
5af38af0b002a4f50e7e2ff94af6139162ac03677cc73811bf0ac11e0616c5e9  PawnIOLib.dll
ed41a0d0de082f4668a9caccb9681ebbad97a8ed3452c41918dda1eb454b77a9  modules/IntelMSR.bin
7218c633384bd019cce09e8df91f74996f980765ac44dde0f22b0b5318c20f03  modules/IsaBridgeEC.bin
c38fd116e7aff4d1fdb0a494e296be0a6708e5a22fc72f14587442fb7f8f7906  modules/LpcACPIEC.bin
277ed6ce6b5f647d5dae9b06c83ed39e904974b06f95598d49f4737237974776  modules/LpcCrOSEC.bin
3dcf8b2bc80ff642d97c4608511a818642b5bf315ff53df3df393d043e71d101  modules/LpcIO.bin
76b082a144027244fe7bbf3ca0e987e7dfa279aa87e0ccc2a6a918e10e2038e1  modules/SmbusI801.bin
385bd1b229faa44aefe2eb1f938ac629f6d897f1a4931dab5341b18b3bd432c1  modules/SmbusNCT6793.bin
3f8b44c93eb030d59fb68c6fdc6857c61313eab2b6aa0ed64595396d71bf3ea3  modules/SmbusPIIX4.bin
```

## 如何更新

1. 升级系统里的 PawnIO 驱动(安装包见 <https://pawnio.eu/>)。
2. 从 PawnIO.Modules 的 Releases 下载对应版本的模块包,解压出 `.bin` 覆盖
   `modules/` 下的同名文件。
3. 更新上面的 SHA-256(在本目录下执行 `sha256sum PawnIOLib.dll modules/*.bin`)。
4. 跑一遍 `pytest tests -q` 确认 MSR 读取仍然正常。

> 提示:AMX 模块必须与驱动版本配套。换了驱动却留着旧模块,
> `pawnio_load` 会失败,表现为 CPU 温度/功耗变成"不可用"。
