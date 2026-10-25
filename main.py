"""PyInstaller 打包入口。

用 `python -m pcmonitor` 也能跑,这个文件只是为了给打包器一个
不含包名歧义的入口点。
"""

from __future__ import annotations

import sys

from pcmonitor.cli import main

if __name__ == "__main__":
    sys.exit(main())
