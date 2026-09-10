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
    check_only = "--check" in sys.argv[1:]
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True)
    # 排除清单自身:含自己则每次重生成都会改变自身哈希,--check 永不通过;
    # 排除依赖清单类文件:它们由 dependabot 自动修改(不会 regen),含入会使每个
    # 依赖升级 PR 的漂移门禁永久红(2026-09-10)
    exclude = {"docs/MANIFEST.txt", "package.json", "package-lock.json", "requirements.txt"}
    paths = [p for p in out.stdout.splitlines() if p and p not in exclude]
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
    if check_only:
        # CI 漂移门禁:比对除「regenerated 日期行」外的全部内容,不一致即退出 1
        from pathlib import Path
        if not os.path.isfile(OUT):
            print("校验未通过: %s 不存在,请运行 python scripts/regen_manifest.py" % OUT)
            return 1
        existing = [x for x in Path(OUT).read_text(encoding="utf-8").splitlines()
                    if not x.startswith("# regenerated:")]
        expected = [x for x in header + lines if not x.startswith("# regenerated:")]
        if existing != expected:
            old_set = set(existing)
            new_set = set(expected)
            added = [x.split("  ", 2)[-1] for x in new_set - old_set][:8]
            removed = [x.split("  ", 2)[-1] for x in old_set - new_set][:8]
            print("校验未通过: MANIFEST 与仓库不一致."
                  + (f" 缺少条目: {added}," if added else "")
                  + (f" 过期条目: {removed}," if removed else "")
                  + " 请运行 python scripts/regen_manifest.py 后提交")
            return 1
        print("校验通过: MANIFEST 与仓库一致 (%d 个文件)" % len(lines))
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(header + lines) + "\n")
    print(f"已生成 {OUT}: {len(lines)} 个文件, {total/1024:.1f} KB")


if __name__ == "__main__":
    sys.exit(main())