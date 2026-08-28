# -*- coding: utf-8 -*-
"""发布包一键构建(替代手工 git archive)。

产物(输出到 <部署根>/dist/):
  mysql-console-X.Y.Z.zip        Windows 用户安装包
  mysql-console-X.Y.Z.tar.gz     Linux/macOS 安装包

发布包结构(与开发仓库同名目录布局一致,安装/自更新/文档路径零迁移):
  mysql-console-X.Y.Z/
  ├── README.md  LICENSE  requirements.txt
  ├── install.bat / install.sh / start.bat / start.sh / stop.bat / stop.sh
  ├── init.bat / init.sh / mysql-console.service        ← 自 scripts/ 复制到包根
  ├── src/                    全部 Python 源码 + static/(前端资源)
  └── docs/                   INSTALL/RELEASE/MIGRATION/DEVLOG/HANDOFF/PLAN/MANIFEST

打包规则:
  - 主体取自 git 已跟踪文件(git ls-files),天然排除 data/.venv/node_modules/__pycache__/dist;
  - 显式忽略:tests/、.github/、package.json、package-lock.json、.gitignore、scripts/_kill*.ps1;
  - 额外强制纳入(即使尚未 git add):LICENSE、src/paths.py、scripts/ 下的全部启动器(复制到包根)。

用法:
  python scripts/build_release.py [--tag vX.Y.Z]   # 默认取 src/version.py 的版本
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# 发布包根应直接放置的启动器/服务模板(从 scripts/ 复制到包根)
LAUNCHERS = [
    "install.bat", "install.sh", "start.bat", "start.sh",
    "stop.bat", "stop.sh", "init.bat", "init.sh",
    "mysql-console.service",
]

# 从 git 跟踪文件中选择的发布前缀;其余一律剔除
_INCLUDE_TRACKED_PREFIXES = ("src/", "docs/", "requirements.txt", "README.md")


def version_from_src():
    with open(os.path.join("src", "version.py"), encoding="utf-8") as f:
        text = f.read()
    m = __import__("re").search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        sys.exit("无法从 src/version.py 解析版本号")
    return m.group(1)


def git_tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True,
                         text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def collect_release_files(tracked):
    """返回 [(源路径, 包内相对路径)];路径一律用正斜杠表达包内结构。"""
    pairs = []
    for p in tracked:
        rel = p.replace("\\", "/")
        if rel.startswith(("src/", "docs/")) or rel in ("requirements.txt", "README.md"):
            pairs.append((p, rel))
    for name in LAUNCHERS:
        sp = os.path.join("scripts", name)
        if os.path.exists(sp):
            pairs.append((sp, name))          # 启动器 → 包根
    for extra, rel in (("LICENSE", "LICENSE"),
                       (os.path.join("src", "paths.py"), "src/paths.py")):
        if os.path.isfile(extra):
            pairs.append((extra, rel))
    return pairs


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stage_package(version, pairs):
    pkg_dir = "mysql-console-" + version
    stage = os.path.join("dist", "_stage", pkg_dir)
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage, exist_ok=True)
    for src, rel in pairs:
        dst = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return stage


def make_archives(version, stage):
    base = os.path.join("dist", "mysql-console-" + version)
    zip_path = base + ".zip"
    tgz_path = base + ".tar.gz"
    root_name = "mysql-console-" + version
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, fs in os.walk(stage):
            for f in fs:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, stage).replace("\\", "/")
                z.write(full, os.path.join(root_name, rel))
    with tarfile.open(tgz_path, "w:gz") as t:
        t.add(stage, arcname=root_name)
    return zip_path, tgz_path


def validate(zip_path, version):
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    prefix = "mysql-console-" + version + "/"
    need = [
        prefix + "src/server.py", prefix + "src/version.py",
        prefix + "src/paths.py", prefix + "src/static/index.html",
        prefix + "README.md", prefix + "LICENSE", prefix + "requirements.txt",
        prefix + "install.bat", prefix + "install.sh",
        prefix + "start.bat", prefix + "start.sh",
        prefix + "mysql-console.service",
    ]
    missing = [n for n in need if n not in names]
    if missing:
        sys.exit("校验失败,缺少: " + ", ".join(missing))
    bad_prefixes = ("tests/", ".github/", "data/", ".venv/", "node_modules/",
                    "package.json", "package-lock.json", "_pydeps/", "scripts/")
    bad = [n for n in names if any(n[len(prefix):].startswith(bp) for bp in bad_prefixes)]
    if bad:
        sys.exit("校验失败,含应剔除内容: " + ", ".join(bad[:5]))
    print("[OK] 校验通过: %d 个条目" % len(names))


def main():
    tag = None
    argv = sys.argv[1:]
    if argv and argv[0] == "--tag":
        tag = argv[1]
    version = tag.lstrip("v") if tag else version_from_src()
    tracked = git_tracked_files()
    pairs = collect_release_files(tracked)
    if not pairs:
        sys.exit("未收集到发布文件(git ls-files 为空?)")
    stage = stage_package(version, pairs)
    zip_path, tgz_path = make_archives(version, stage)
    validate(zip_path, version)
    print("  zip    : %s (%.1f MB, sha256 %s)" % (
        zip_path, os.path.getsize(zip_path) / 1048576, sha256(zip_path)[:16]))
    print("  tar.gz : %s (%.1f MB, sha256 %s)" % (
        tgz_path, os.path.getsize(tgz_path) / 1048576, sha256(tgz_path)[:16]))
    print("  条目数 : %d" % len(pairs))


if __name__ == "__main__":
    main()