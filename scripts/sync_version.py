# -*- coding: utf-8 -*-
"""版本号单一来源同步工具。

单一权威: src/version.py 中的 __version__。本工具把该版本号同步到所有声明过
版本号的文件,保证处处一致,避免发版时只改一处导致漂移(pyproject/package.json/
README 徽章不再各自为政)。

用法:
  python scripts/sync_version.py               # 读取 version.py,同步其余文件(就地改写)
  python scripts/sync_version.py --check       # 仅校验一致性,不一致则退出码 1(不改写)
  python scripts/sync_version.py --set 3.8.0   # 先把 version.py 提升到指定版本,再同步全部

说明:
  - 幂等;相对路径均以「本脚本所在目录的上一级」即仓库根为基准。
  - 默认模式:凡是已修复的差异都会打印出来,修复后统一返回退出码 0。
  - --check 模式:任一文件不一致或缺失必要文件即返回 1,便于接入 CI 强制门禁。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_REGEX = re.compile(r'__version__\s*=\s*"([^"]+)"')
VERSION_FORMAT = re.compile(r"\d+\.\d+\.\d+")

# (相对路径, 匹配当前版本的正则, 重写为给定版本的正则)
# 重写正则利用捕获组 1=前缀、2=结尾,把中间旧版本替换为新版本。
TARGETS = [
    (os.path.join("pyproject.toml"),
     re.compile(r'^version\s*=\s*"([^"]+)"', re.M),
     re.compile(r'(^version\s*=\s*")[^"]+(")', re.M)),
    (os.path.join("package.json"),
     re.compile(r'"version"\s*:\s*"([^"]+)"'),
     re.compile(r'("version"\s*:\s*")[^"]+(")')),
    (os.path.join("README.md"),
     re.compile(r"badge/version-([\d.]+)-34d399"),
     re.compile(r"(badge/version-)[\d.]+(-34d399)")),
    (os.path.join("README.en.md"),
     re.compile(r"badge/version-([\d.]+)-34d399"),
     re.compile(r"(badge/version-)[\d.]+(-34d399)")),
]
# 缺失即视为错误的目标(README.en.md 属可选增量,缺失不报错)
MUST_EXIST = {"pyproject.toml", "package.json", "README.md"}


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def version_from_src():
    m = VERSION_REGEX.search(_read(os.path.join(ROOT, "src", "version.py")))
    if not m:
        sys.exit("无法从 src/version.py 解析版本号")
    return m.group(1)


def current_of(path, pat):
    m = pat.search(_read(path))
    return m.group(1) if m else None


def sync_target(path, version, write):
    """返回该文件的当前版本;write=True 时把不一致就地改为 version。"""
    read_pat, write_pat = path[1], path[2]
    text = _read(path[0])
    cur = read_pat.search(text)
    cur_v = cur.group(1) if cur else None
    if cur_v == version:
        return cur_v
    if write:
        new = write_pat.sub(lambda m: m.group(1) + version + m.group(2), text)
        _write(path[0], new)
    return cur_v


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--set":
        if len(argv) != 2:
            sys.exit("用法: --set 需紧跟目标版本号, 如 --set 3.8.0")
        new_v = argv[1]
        if not VERSION_FORMAT.fullmatch(new_v):
            sys.exit("版本号格式应为 X.Y.Z, 收到: %s" % new_v)
        vp = os.path.join(ROOT, "src", "version.py")
        text = _read(vp)
        if not VERSION_REGEX.search(text):
            sys.exit("src/version.py 缺少 __version__ 赋值, 拒绝改写")
        new = VERSION_REGEX.sub(lambda m: f'__version__ = "{new_v}"', text, count=1)
        _write(vp, new)
        print("[sync] src/version.py -> %s" % new_v)
        version = new_v
        check_only = False
    elif argv and argv[0] == "--check":
        version = version_from_src()
        check_only = True
    elif argv:
        sys.exit("未知参数: %s (支持 --check / --set <版本>)")
    else:
        version = version_from_src()
        check_only = False

    print("[sync] 单一版本来源: %s" % version)
    dirty = False
    for rel, _read_pat, _write_pat in TARGETS:
        abs_path = os.path.join(ROOT, rel)
        if not os.path.isfile(abs_path):
            if rel in MUST_EXIST:
                print("  [!!] 缺失必要文件: %s" % rel)
                dirty = True
            else:
                print("  [--] 文件不存在,跳过: %s" % rel)
            continue
        cur = sync_target((abs_path, _read_pat, _write_pat), version, write=not check_only)
        if cur is None:
            print("  [!!] %s: 找不到版本号声明" % rel)
            dirty = True
        elif cur != version:
            if check_only:
                print("  [XX] %s: %s -> 应为 %s" % (rel, cur, version))
            else:
                print("  [ok] %s: %s -> %s" % (rel, cur, version))
            dirty = True
        else:
            print("  [   ] %s: %s 一致" % (rel, version))

    if dirty and check_only:
        print("\n校验未通过: 存在版本号漂移,请先运行 python scripts/sync_version.py")
        return 1
    print("\n%s (版本号一致)" % ("校验通过" if check_only else "同步完成"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
