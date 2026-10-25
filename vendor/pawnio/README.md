# vendor/pawnio —— 第三方组件

本目录下的文件**不是本项目写的**,而是从上游项目原样取来的二进制。
本项目不修改它们,只通过 PawnIO 的官方 C API 调用。

唯一一处例外是 `PawnIO_setup.exe` 的**文件名**(内容一字未改),
原因见下面的说明。

## 文件与来源

| 文件 | 上游 | 版本 | 许可证 |
| --- | --- | --- | --- |
| `PawnIOLib.dll` | [namazso/PawnIO](https://github.com/namazso/PawnIO) 的 `PawnIOLib/` | 2.1.0 | GPL-2.0-or-later |
| `PawnIO_setup.exe` | [namazso/PawnIO](https://github.com/namazso/PawnIO) Releases 里的官方安装包 | 2.1.0 | GPL-2.0-or-later |
| `modules/*.bin` | [namazso/PawnIO.Modules](https://github.com/namazso/PawnIO.Modules) 的 Releases | 0.2.11 | LGPL-2.1-or-later |

- `PawnIOLib.dll` 与本机 `C:\Program Files\PawnIO\PawnIOLib.dll`
  (PawnIO 2.1.0 官方安装包)SHA-256 完全一致。
- `PawnIO_setup.exe` 是官方安装包(自带 WHQL 签名的驱动),
  上游明确写着 "This installer can be redistributed unmodified.",
  所以可以原样转发。改名成 `PawnIO_setup.exe` 的原因见下。
- `.bin` 是 Pawn 虚拟机的 AMX 字节码,`IntelMSR.bin` 导出
  `ioctl_read_msr` / `msr_read` / `cpuid` / `get_arch` 四个函数,
  本项目只用 `ioctl_read_msr`。
- 许可证全文:`LICENSE.PawnIO-GPL-2.0.txt`、
  `modules/LICENSE.PawnIO.Modules-LGPL-2.1.txt`。

## 为什么安装器要改名

官方安装包里同时有安装器和卸载器两个可执行文件,而它们**靠自己的
进程路径来判断角色**:反汇编可以看到它调用 `wcsstr(argv[0], "uninstall.exe")`。
路径里只要出现 "uninstall.exe",它就认为自己是卸载器,于是 `-install`
会被自己抵消掉,弹一句 "Please specify either -install or -uninstall"
就以 `0xC000000D` 退出。

所以本项目把安装器副本改名为 `PawnIO_setup.exe`(**内容一字未改**,
SHA-256 与原文件一致,见下),并用 `-install -silent` 调用它。
`pcmonitor/driver.py` 里还有一道运行时检查:如果内置安装器的路径里
出现 "uninstall.exe",直接拒绝安装并报错,避免源码运行时把项目放在
这种路径下时静默失败。

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
a3a46226c5e2824f4cdd42be0eecbabfc672c86f7889710f5ab1e6ad385b47a0  PawnIO_setup.exe
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

1. 从 [namazso/PawnIO](https://github.com/namazso/PawnIO) 的 Releases 下载
   新的官方安装包,解压出 `uninstall.exe`,**改名**成 `PawnIO_setup.exe`
   覆盖本目录下的同名文件(名字不能带 "uninstall.exe",原因见上)。
2. 升级系统里的 PawnIO 驱动:直接双击 `PawnIO_setup.exe` 即可。
3. 从 PawnIO.Modules 的 Releases 下载对应版本的模块包,解压出 `.bin` 覆盖
   `modules/` 下的同名文件。
4. 更新上面的 SHA-256(在本目录下执行 `sha256sum PawnIOLib.dll PawnIO_setup.exe modules/*.bin`),
   并同步改 `tests/test_driver.py` 里的 `_INSTALLER_SHA256` ——
   那个测试就是靠校验和发现安装器被改坏的。
5. 跑一遍 `pytest tests -q` 确认 MSR 读取仍然正常。

> 提示:AMX 模块必须与驱动版本配套。换了驱动却留着旧模块,
> `pawnio_load` 会失败,表现为 CPU 温度/功耗变成"不可用"。
