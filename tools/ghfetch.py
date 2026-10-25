"""从 GitHub 网页版抓取源码(raw 域名被墙,只能走 HTML 页面里的 embeddedData)。"""

import json
import re
import sys
import urllib.request

BASE = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor"


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"}
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read().decode("utf-8", "ignore")


def blob_lines(path: str):
    url = f"{BASE}/blob/master/{path}"
    html = fetch(url)
    m = re.search(r'"rawLines":(\[.*?\]),"styling', html)
    if not m:
        m = re.search(r'"rawLines":(\[.*?\])', html)
    if not m:
        raise SystemExit(f"rawLines not found for {path} (len={len(html)})")
    return json.loads(m.group(1))


def tree_paths(subpath: str):
    url = f"{BASE}/tree/master/{subpath}"
    html = fetch(url)
    return sorted(set(re.findall(r'"path":"([^"]+)"', html)))


if __name__ == "__main__":
    if sys.argv[1] == "tree":
        for p in tree_paths(sys.argv[2]):
            print(p)
    else:
        path = sys.argv[1]
        pattern = sys.argv[2] if len(sys.argv) > 2 else None
        lines = blob_lines(path)
        for i, ln in enumerate(lines):
            if pattern is None or re.search(pattern, ln, re.I):
                print(f"{i+1:4}| {ln}")
