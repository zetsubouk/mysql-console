# -*- coding: utf-8 -*-
"""发布包一键构建(替代手工 git archive)。

产物(输出到 <部署根>/dist/):
  默认(精简包):
    mysql-console-X.Y.Z.zip        Windows 用户安装包
    mysql-console-X.Y.Z.tar.gz     Linux/macOS 安装包
  --with-runtime(完整包,内置 Windows 嵌入式 Python + 预装依赖):
    mysql-console-X.Y.Z-full-win64.zip   安装/启动全程离线,约 16MB
  --wheels-dir(精简包附离线依赖):
    包内附带 wheels/ 目录,有 Python 无外网的机器可离线装依赖
  --tools-dir(附带内置 MySQL 客户端,远程备份兜底):
    包内附带 tools/ 目录 + SHA256SUMS 清单,Windows 客户机免装客户端即可做远程备份

发布包结构(与开发仓库同名目录布局一致,安装/自更新/文档路径零迁移):
  mysql-console-X.Y.Z/
  ├── README.md  LICENSE  requirements.txt
  ├── install.bat / install.sh / start.bat / start.sh / stop.bat / stop.sh
  ├── init.bat / init.sh / _resolve_python.bat / mysql-console.service
  │                                ← 自 scripts/ 复制到包根
  ├── runtime/python/...           ← 仅 --with-runtime(嵌入式 Python + site-packages)
  ├── wheels/...                   ← 仅 --wheels-dir(离线依赖轮子)
  ├── tools/...                    ← 仅 --tools-dir(内置 MySQL 客户端 + SHA256SUMS)
  ├── src/                    全部 Python 源码 + static/(前端资源)
  └── docs/                   INSTALL/RELEASE/MIGRATION/DEVLOG/HANDOFF/PLAN/MANIFEST

打包规则:
  - 主体取自 git 已跟踪文件(git ls-files),天然排除 data/.venv/node_modules/__pycache__/dist;
  - 显式忽略:tests/、.github/、package.json、package-lock.json、.gitignore、scripts/_kill*.ps1;
  - 额外强制纳入(即使尚未 git add):LICENSE、src/paths.py、scripts/ 下的全部启动器(复制到包根)。

用法:
  python scripts/build_release.py [--tag vX.Y.Z]          # 精简包
  python scripts/build_release.py --with-runtime          # 完整包(需联网或本地缓存)
          [--runtime-zip "path\\to\\python-embed-amd64.zip"]   # 本地嵌入式包,离线构建
          [--wheels-dir "path\\to\\wheels"]               # 本地轮子目录(缺省自动下载到 dist/_wheels)
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

sys.path.insert(0, os.path.join(ROOT, "src"))
import runtime_resolver          # noqa: E402

# 发布包根应直接放置的启动器/服务模板(从 scripts/ 复制到包根)
LAUNCHERS = [
    "install.bat", "install.sh", "start.bat", "start.sh",
    "stop.bat", "stop.sh", "init.bat", "init.sh",
    "_resolve_python.bat", "mysql-console.service",
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


def make_archives(version, stage, full=False):
    if full:
        # 完整包:内置 Windows 嵌入式运行时,只出 zip(无 Linux/macOS 形态)
        base = os.path.join("dist", "mysql-console-%s-full-win64" % version)
        root_name = "mysql-console-" + version
        zip_path = base + ".zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, fs in os.walk(stage):
                for f in fs:
                    full_p = os.path.join(root, f)
                    rel = os.path.relpath(full_p, stage).replace("\\", "/")
                    z.write(full_p, os.path.join(root_name, rel))
        return zip_path, None
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


def validate(zip_path, version, full=False):
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    prefix = "mysql-console-" + version + "/"
    need = [
        prefix + "src/server.py", prefix + "src/version.py",
        prefix + "src/paths.py", prefix + "src/static/index.html",
        prefix + "README.md", prefix + "LICENSE", prefix + "requirements.txt",
        prefix + "install.bat", prefix + "install.sh",
        prefix + "start.bat", prefix + "start.sh",
        prefix + "mysql-console.service", prefix + "_resolve_python.bat",
    ]
    if full:
        need += [prefix + "runtime/python/python.exe",
                 prefix + "runtime/python/Lib/site-packages/pymysql/__init__.py",
                 prefix + "runtime/python/Lib/site-packages/cryptography/__init__.py"]
    missing = [n for n in need if n not in names]
    if missing:
        sys.exit("校验失败,缺少: " + ", ".join(missing))
    bad_prefixes = ("tests/", ".github/", "data/", ".venv/", "node_modules/",
                    "package.json", "package-lock.json", "_pydeps/", "scripts/")
    if not full:
        bad_prefixes += ("runtime/",)
    bad = [n for n in names if any(n[len(prefix):].startswith(bp) for bp in bad_prefixes)]
    if bad:
        sys.exit("校验失败,含应剔除内容: " + ", ".join(bad[:5]))
    print("[OK] 校验通过: %d 个条目" % len(names))


# ---------------- 内置工具:入库与 sha256 清单 ----------------
# 远程备份需求:客户机(尤其 Windows)未装 MySQL 客户端时,
# 发布包可附带 tools/ 目录兜底。本函数整目录拷入库 + 生成 SHA256SUMS。
# tools_dir 是构建机上的本地目录(含 mysqldump/mysql 及其依赖 DLL),不进 git。
_TOOL_REQUIRED = ("mysqldump", "mysql")


def stage_tools(stage, tools_dir):
    if not tools_dir:
        return
    tools_dir = os.path.abspath(tools_dir)
    if not os.path.isdir(tools_dir):
        sys.exit("--tools-dir 不存在: %s" % tools_dir)
    exe = {t: t + (".exe" if os.name == "nt" else "") for t in _TOOL_REQUIRED}
    missing = [n for n in _TOOL_REQUIRED
               if not os.path.isfile(os.path.join(tools_dir, exe[n]))]
    if missing:
        sys.exit("--tools-dir 缺少必需客户端工具: %s (%s)" %
                 (", ".join(missing), tools_dir))
    dst = os.path.join(stage, "tools")
    shutil.copytree(tools_dir, dst, dirs_exist_ok=True)
    write_tools_manifest(dst)
    print("  内置工具就位: %s -> tools/ (%d 个文件)" %
          (tools_dir, sum(len(fs) for _, _, fs in os.walk(dst))))


def write_tools_manifest(tools_dir):
    """生成 tools/SHA256SUMS,格式 `hexsha256  relative/path`(未来启动时可选校验)。"""
    lines = []
    for root, _dirs, fs in os.walk(tools_dir):
        for f in fs:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, tools_dir).replace("\\", "/")
            lines.append("%s  %s" % (sha256(p), rel))
    with open(os.path.join(tools_dir, "SHA256SUMS"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(lines)) + "\n")


def verify_tools_manifest(pkg_tools_dir):
    """部署端校验清单(工具可选,存在清单才验)。返回 (ok, 问题列表)。"""
    sums_path = os.path.join(pkg_tools_dir, "SHA256SUMS")
    if not os.path.isfile(sums_path):
        return True, []
    problems = []
    with open(sums_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) != 2:
                problems.append("清单行格式异常: %s" % line)
                continue
            want, rel = parts
            p = os.path.join(pkg_tools_dir, rel)
            if not os.path.isfile(p):
                problems.append("清单文件缺失: %s" % rel)
                continue
            if sha256(p) != want:
                problems.append("校验失败: %s" % rel)
    return not problems, problems


def ensure_wheels(wheels_dir):
    """确保本地有 Windows/py3.12 轮子目录;缺则用本机 pip download 拉取。"""
    if wheels_dir and glob_site(wheels_dir, "*.whl"):
        return wheels_dir
    dest = wheels_dir or os.path.join("dist", "_wheels")
    os.makedirs(dest, exist_ok=True)
    if not glob_site(dest, "*.whl"):
        print("  pip download 轮子 -> %s ..." % dest)
        subprocess.run(
            [sys.executable, "-m", "pip", "download",
             "-r", "requirements.txt", "-d", dest,
             "--only-binary=:all:", "--platform", "win_amd64",
             "--python-version", "3.12", "--implementation", "cp"],
            check=True)
    return dest


def glob_site(d, pat):
    import glob as _g
    return _g.glob(os.path.join(d, pat))


def stage_runtime(stage, runtime_zip, wheels_dir):
    """把嵌入式 Python + 预装依赖放进 stage/runtime/python。"""
    zip_src = runtime_zip
    if not zip_src:
        cache = os.path.join("dist", "_runtime_cache")
        os.makedirs(cache, exist_ok=True)
        name = runtime_resolver.embed_zip_name()
        zip_src = os.path.join(cache, name)
        if not os.path.isfile(zip_src):
            urls = runtime_resolver._embed_urls()
            last = ""
            for url in urls:
                try:
                    print("  下载嵌入式 Python: %s" % url)
                    runtime_resolver._download(url, zip_src)
                    if os.path.getsize(zip_src) >= runtime_resolver.EMBED_MIN_BYTES:
                        break
                    last = "文件过小"
                except Exception as e:                     # noqa: BLE001
                    last = str(e)
            else:
                sys.exit("嵌入式 Python 下载失败(%s);可用 --runtime-zip 指定本地包" % last)
    rt = os.path.join(stage, "runtime", "python")
    os.makedirs(rt, exist_ok=True)
    with zipfile.ZipFile(zip_src) as zf:
        runtime_resolver._safe_extract(zf, rt)
    if not runtime_resolver.patch_embedded_ppth(rt):
        sys.exit("嵌入式包内未找到 python3XX._pth,包内容异常")
    wd = ensure_wheels(wheels_dir)
    pip_whl = runtime_resolver.find_pip_wheel(wd)
    if not pip_whl:
        sys.exit("wheels 目录缺少 pip-*.whl: %s" % wd)
    site_pkg = os.path.join(rt, "Lib", "site-packages")
    os.makedirs(site_pkg, exist_ok=True)
    print("  预装依赖到 runtime/python/Lib/site-packages ...")
    r = subprocess.run(
        [sys.executable, os.path.join(pip_whl, "pip"), "install",
         "--no-index", "--find-links", wd,
         "-r", "requirements.txt", "--target", site_pkg, "--upgrade"],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("依赖预装失败:\n%s" % (r.stderr or r.stdout)[-2000:])
    print("  runtime 就位: %s (依赖 %s)" % (
        rt, ", ".join(sorted({f.split("-")[0] for f in os.listdir(wd)
                              if f.endswith(".whl")}))))


def main():
    tag = None
    with_runtime = False
    runtime_zip = None
    wheels_dir = None
    tools_dir = None
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--tag":
            tag = argv[i + 1]
            i += 2
        elif a == "--with-runtime":
            with_runtime = True
            i += 1
        elif a == "--runtime-zip":
            runtime_zip = argv[i + 1]
            i += 2
        elif a == "--wheels-dir":
            wheels_dir = argv[i + 1]
            i += 2
        elif a == "--tools-dir":
            tools_dir = argv[i + 1]
            i += 2
        else:
            sys.exit("未知参数: %s (支持 --tag/--with-runtime/--runtime-zip/--wheels-dir/--tools-dir)" % a)
    version = tag.lstrip("v") if tag else version_from_src()
    tracked = git_tracked_files()
    pairs = collect_release_files(tracked)
    if not pairs:
        sys.exit("未收集到发布文件(git ls-files 为空?)")
    stage = stage_package(version, pairs)
    if with_runtime:
        stage_runtime(stage, runtime_zip, wheels_dir)
    elif wheels_dir:
        # 精简包附离线依赖: wheels/ 随包(有 Python 无外网的机器可离线装)
        wd = ensure_wheels(wheels_dir)
        dst = os.path.join(stage, "wheels")
        shutil.copytree(wd, dst, dirs_exist_ok=True)
    if tools_dir:
        stage_tools(stage, tools_dir)
    zip_path, tgz_path = make_archives(version, stage, full=with_runtime)
    validate(zip_path, version, full=with_runtime)

    print("  zip    : %s (%.1f MB, sha256 %s)" % (
        zip_path, os.path.getsize(zip_path) / 1048576, sha256(zip_path)[:16]))
    if tgz_path:
        print("  tar.gz : %s (%.1f MB, sha256 %s)" % (
            tgz_path, os.path.getsize(tgz_path) / 1048576, sha256(tgz_path)[:16]))
    print("  条目数 : %d" % len(pairs))
    if with_runtime:
        print("  说明   : 完整包已内置 Windows 嵌入式 Python %s + 预装依赖,用户端全程离线" %
              runtime_resolver.EMBED_PY_VERSION)


if __name__ == "__main__":
    main()