# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

单文件(onefile)模式:目标机器不需要装 Python。
vendor/pawnio 下的 PawnIOLib.dll 和 AMX 模块必须一起打进去,
否则读不到 MSR,CPU 温度/功耗会变成"不可用"。

PawnIO_setup.exe 是官方安装器(上游允许原样转发),打进去之后
用户在没装驱动的机器上首次运行就能自动装,不用再去 pawnio.eu 下载。
它必须以 PawnIO_setup.exe 这个名字存在:官方安装器会用
wcsstr(argv[0], "uninstall.exe") 判断自己是安装器还是卸载器,
名字(或路径)里出现 "uninstall.exe" 的话 -install 会被抵消掉。
"""

from pathlib import Path

project_root = Path(SPECPATH).resolve()
vendor = project_root / "vendor" / "pawnio"

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

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 用不到的大块头,砍掉能显著减小体积
        "numpy",
        "pandas",
        "matplotlib",
        "tkinter",
        "PIL",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pc-monitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # TUI 需要控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
