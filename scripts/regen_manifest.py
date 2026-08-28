# -*- coding: utf-8 -*-
"""重新生成 docs/MANIFEST.txt(git 已跟踪文件清单 + 首 16 位 sha256)。

用法: python scripts/regen_manifest.py
说明: 目录结构调整 / 版本发布前运行一次,保证 MANIFEST 与实际入库文件一致。
"""
import hashlib
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "MANIFEST.txt")


def sha16(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True)
    paths = [p for p in out.stdout.splitlines() if p]
    total = 0
    lines = []
    for p in paths:
        full = os.path.join(ROOT, p)
        if not os.path.isfile(full):
            continue
        size = os.path.getsize(full)
        total += size
        lines.append(f"{sha16(full)}  {size:>10}  {p}")
    header = [
        "# MANIFEST - mysql-console 当前代码清单(main, 目录结构化后)",
        f"# regenerated: {time.strftime('%Y-%m-%d')}",
        f"# files: {len(lines)} | raw size: {total/1024:.1f} KB",
        "# verify: sha256(first 16 hex chars) per file",
        "# 说明: 由 scripts/regen_manifest.py 按 git ls-files 重新生成(结构: src/ 代码, docs/ 文档, tests/ 分型)。",
        "#",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(header + lines) + "\n")
    print(f"已生成 {OUT}: {len(lines)} 个文件, {total/1024:.1f} KB")


if __name__ == "__main__":
    main()