"""查 GitHub API 的小工具。

这台机器上 Git Bash 的管道会把 UTF-8 当 GBK 解,中文 commit message
一进来 JSON 就解析失败。所以统一走"读字节 -> 显式按 UTF-8 解码"。

用法:
    python tools/ghapi.py <api 路径> [顶层字段]
例如:
    python tools/ghapi.py "repos/uzipi/pc-monitor/actions/runs" workflow_runs
"""

from __future__ import annotations

import json
import sys
import urllib.request

API = "https://api.github.com/"


def get(path: str) -> dict | list:
    req = urllib.request.Request(API + path, headers={"User-Agent": "pc-monitor-dev"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    data = get(sys.argv[1])
    if len(sys.argv) > 2:
        data = data.get(sys.argv[2], data)  # type: ignore[union-attr]
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
