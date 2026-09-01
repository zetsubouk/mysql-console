# -*- coding: utf-8 -*-
"""发布包一键构建(替代手工 git archive)。

产物(输出到 <部署根>/dist/):
  4 包矩阵（英文命名）:
    mysql-console-X.Y.Z-win64.zip        正常版 Windows（内置 MySQL tools 5.7+8.x）
    mysql-console-X.Y.Z-linux.tar.gz     正常版 Linux（内置 MySQL tools 5.7+8.x）
    mysql-console-X.Y.Z-slim-win64.zip   瘦版 Windows（无 tools，向导提示下载/跳过）
    mysql-console-X.Y.Z-slim-linux.tar.gz 瘦版 Linux（无 tools）
  --with-runtime(完整包,内置 Windows 嵌入式 Python + 预装依赖):
    仅正常版 win64 可叠加：mysql-console-X.Y.Z-win64.zip 内含 runtime/python
  --wheels-dir(瘦版附离线依赖):
    包内附带 wheels/ 目录,有 Python 无外网的机器可离线装依赖
  --tools-dir(附带内置 MySQL 客户端,远程备份兜底):
    正常版必须 via --tools-dir 或自动拉取官方双版本（5.7+8.x）到 tools/

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
  python scripts/build_release.py --platform win64 --variant slim|standard  # 4 包矩阵
    --variant slim      瘦版（无 tools，~600K，向导提示下载/跳过）
    --variant standard  正常版（内置 tools 5.7+8.x，需 --tools-dir 或自动拉取）
  python scripts/build_release.py --platform win64 --variant standard --tools-dir <dir>  # 正常版 Windows
  python scripts/build_release.py --platform linux --variant slim --tag v3.8.0            # 瘦版 Linux
  python scripts/build_release.py --platform win64 --variant standard --with-runtime      # 正常版+嵌入式 Python
          [--runtime-zip "path\\to\\python-embed-amd64.zip"]   # 本地嵌入式包,离线构建
          [--wheels-dir "path\\to\\wheels"]               # 本地轮子目录(缺省自动下载到 dist/_wheels)

  --platform 必选: win64|linux；--variant 必选: slim|standard（英文命名）
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

# 发布包根应直接放置的启动器（单仓库双目录；--platform 必选）。
# 新规则：安装包仅含 install（不含 start/stop/init），初始化完成后由服务端生成。
# ponytail: package_launchers 仅 install，runtime 生成 start/stop/init 解耦部署与运行
_PACKAGE_LAUNCHERS_WIN = ["install.bat", "_resolve_python.bat"]
_PACKAGE_LAUNCHERS_LINUX = ["install.sh"]
# 全量模板（用于生成，供校验与本地开发）
_LAUNCHERS_WIN = ["install.bat", "start.bat", "stop.bat", "init.bat",
                  "_resolve_python.bat"]
_LAUNCHERS_LINUX = ["install.sh", "start.sh", "stop.sh", "init.sh",
                    "mysql-console.service"]
# 生成用模板（初始化后生成 start/stop/init）
_GENERATED_WIN = ["start.bat", "stop.bat", "init.bat"]
_GENERATED_LINUX = ["start.sh", "stop.sh", "init.sh", "mysql-console.service"]
LAUNCHERS = _LAUNCHERS_WIN + _LAUNCHERS_LINUX
# 启动器来源目录（优先 platforms/<platform>/scripts，回退 scripts/ 以兼容旧布局）
_PLATFORM_SCRIPT_DIRS = {
    "win64": [os.path.join("platforms", "win64", "scripts"), "scripts"],
    "linux": [os.path.join("platforms", "linux", "scripts"), "scripts"],
}

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


def collect_release_files(tracked, platform=None, variant=None):
    """返回 [(源路径, 包内相对路径)];路径一律用正斜杠表达包内结构。
    platform: 'win64'/'linux' 必选；variant: 'slim'|'standard' 必选。"""
    pairs = []
    for p in tracked:
        rel = p.replace("\\", "/")
        if rel.startswith(("src/", "docs/")) or rel in ("requirements.txt", "README.md"):
            pairs.append((p, rel))
    if not platform:
        sys.exit("--platform 必选: win64 | linux")
    if not variant:
        sys.exit("--variant 必选: slim | standard（英文命名）")
    # 安装包仅含 install（start/stop/init 由初始化后生成）
    launchers = _PACKAGE_LAUNCHERS_WIN if platform == "win64" else _PACKAGE_LAUNCHERS_LINUX
    search_dirs = _PLATFORM_SCRIPT_DIRS[platform]
    for name in launchers:
        found = None
        for d in search_dirs:
            cand = os.path.join(d, name)
            if os.path.exists(cand):
                found = cand
                break
        if found:
            pairs.append((found, name))          # 启动器 → 包根
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


def make_archives(version, stage, full=False, platform=None, variant=None):
    # 英文命名：standard-win64 / standard-linux / slim-win64 / slim-linux
    if not variant:
        sys.exit("--variant 必选: slim | standard")
    suffix = "-slim" if variant == "slim" else ""
    if platform == "linux":
        base = os.path.join("dist", f"mysql-console-{version}{suffix}-linux")
        tgz_path = base + ".tar.gz"
        root_name = f"mysql-console-{version}"
        with tarfile.open(tgz_path, "w:gz") as t:
            t.add(stage, arcname=root_name)
        return None, tgz_path
    if platform == "win64":
        base = os.path.join("dist",
                            f"mysql-console-{version}{suffix}-win64" if not full
                            else f"mysql-console-{version}-win64")
        # full 已废 slim 区分，runtime 仅 standard-win64 可叠加
        if variant == "slim" and full:
            sys.exit("--with-runtime 仅 standard-win64 可用")
        root_name = f"mysql-console-{version}"
        zip_path = base + ".zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _dirs, fs in os.walk(stage):
                for f in fs:
                    full_p = os.path.join(root, f)
                    rel = os.path.relpath(full_p, stage).replace("\\", "/")
                    z.write(full_p, os.path.join(root_name, rel))
        return zip_path, None
    sys.exit("--platform 必选: win64 | linux")


def validate(zip_path, version, full=False, platform=None, variant=None):
    """校验产物内容(.zip / .tar.gz 按扩展名识别)。--platform/--variant 必选。"""
    if not platform or not variant:
        sys.exit("--platform/--variant 必选: win64|linux + slim|standard")
    if zip_path.lower().endswith(".tar.gz"):
        with tarfile.open(zip_path) as t:
            names = [n.rstrip("/") for n in t.getnames()]
        prefix = "mysql-console-" + version + "/"
        need = [prefix + "src/server.py", prefix + "src/version.py",
                prefix + "src/paths.py", prefix + "src/static/index.html",
                prefix + "README.md", prefix + "LICENSE", prefix + "requirements.txt"]
        need += [prefix + n for n in _PACKAGE_LAUNCHERS_LINUX]
        missing = [n for n in need if n not in names]
        if missing:
            sys.exit("校验失败,缺少: " + ", ".join(missing))
        print("[OK] 校验通过: %d 个条目" % len(names))
        return
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    prefix = "mysql-console-" + version + "/"
    need = [
        prefix + "src/server.py", prefix + "src/version.py",
        prefix + "src/paths.py", prefix + "src/static/index.html",
        prefix + "README.md", prefix + "LICENSE", prefix + "requirements.txt",
    ]
    need += [prefix + n for n in (
        _PACKAGE_LAUNCHERS_WIN if platform == "win64" else _PACKAGE_LAUNCHERS_LINUX)]
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


def stage_tools(stage, tools_dir, platform=None):
    if not tools_dir:
        return
    tools_dir = os.path.abspath(tools_dir)
    if not os.path.isdir(tools_dir):
        sys.exit("--tools-dir 不存在: %s" % tools_dir)
    win = (os.name == "nt") if platform is None else (platform == "win64")
    exe = {t: t + (".exe" if win else "") for t in _TOOL_REQUIRED}
    missing = [n for n in _TOOL_REQUIRED
               if not os.path.isfile(os.path.join(tools_dir, exe[n]))]
    if missing:
        sys.exit("--tools-dir 缺少必需客户端工具: %s (%s, 平台=%s)" %
                 (", ".join(missing), tools_dir,
                  "win64" if win else ("linux" if platform else "构建机")))
    dst = os.path.join(stage, "tools")
    shutil.copytree(tools_dir, dst, dirs_exist_ok=True)
    write_tools_manifest(dst)
    print("  内置工具就位: %s -> tools/ (%d 个文件)" %
          (tools_dir, sum(len(fs) for _, _, fs in os.walk(dst))))

# 正常版双版本自动拉取（5.7 + 8.x），tools_dir 为空时按平台从官方下载
OFFICIAL_TOOLS = {
    "win64": {
        "5.7": "https://dev.mysql.com/get/Downloads/MySQL-5.7/mysql-5.7.44-winx64.zip",
        "8.0": "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-winx64.zip",
    },
    "linux": {
        "5.7": "https://dev.mysql.com/get/Downloads/MySQL-5.7/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz",
        "8.0": "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-linux-glibc2.12-x86_64.tar.gz",
    },
}

def fetch_official_tools(stage, platform):
    """正常版自动拉取官方双版本客户端到 stage/tools/mysql-{5.7,8.0}，失败则提示手动 --tools-dir。"""
    import urllib.request, tempfile, tarfile, zipfile
    dst_base = os.path.join(stage, "tools")
    os.makedirs(dst_base, exist_ok=True)
    urls = OFFICIAL_TOOLS.get(platform, {})
    if not urls:
        return False
    for ver, url in urls.items():
        sub = os.path.join(dst_base, f"mysql-{ver}")
        if os.path.isdir(sub):
            print(f"  官方 tools 已存在跳过: {sub}")
            continue
        print(f"  拉取官方 MySQL {ver} ({platform}): {url}")
        try:
            tmp = os.path.join(tempfile.gettempdir(), f"mysql-{ver}-{platform}.tmp")
            # 下载
            req = urllib.request.Request(url, headers={"User-Agent": "mysql-console"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
                shutil.copyfileobj(resp, out)
            if os.path.getsize(tmp) < 10*1024*1024:
                print(f"  下载文件过小，疑似失败: {tmp}")
                os.remove(tmp)
                continue
            # 解压：win64 zip → 取 bin/mysqldump.exe,mysql.exe；linux tar.gz → bin/*
            os.makedirs(sub, exist_ok=True)
            if url.endswith(".zip"):
                with zipfile.ZipFile(tmp) as zf:
                    for info in zf.infolist():
                        if info.filename.endswith(("mysqldump.exe","mysql.exe")):
                            # 取顶层目录后的 bin 文件
                            name = os.path.basename(info.filename)
                            with zf.open(info) as src, open(os.path.join(sub, name), "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            # 同步 DLL 依赖（libmysql.dll 等）
                        elif info.filename.endswith(".dll"):
                            name = os.path.basename(info.filename)
                            if name.lower() in ("libmysql.dll","vcruntime140.dll","msvcp140.dll"):
                                with zf.open(info) as src, open(os.path.join(sub, name), "wb") as dst:
                                    shutil.copyfileobj(src, dst)
            else:
                with tarfile.open(tmp, "r:gz") as tf:
                    for m in tf.getmembers():
                        if m.name.endswith(("bin/mysqldump","bin/mysql")):
                            name = os.path.basename(m.name)
                            f = tf.extractfile(m)
                            if f:
                                with open(os.path.join(sub, name), "wb") as dst:
                                    shutil.copyfileobj(f, dst)
                                os.chmod(os.path.join(sub, name), 0o755)
            os.remove(tmp)
            # 版本校验：至少有一对工具
            has = any(os.path.isfile(os.path.join(sub, exe)) for exe in ("mysqldump.exe" if platform=="win64" else "mysqldump", "mysql.exe" if platform=="win64" else "mysql"))
            if has:
                print(f"  MySQL {ver} 就位: {sub}")
            else:
                print(f"  MySQL {ver} 解压后未找到工具: {sub}")
        except Exception as e:
            print(f"  拉取 {ver} 失败: {e} — 可改用 --tools-dir 手动指定")
            continue
    # 生成清单
    if os.path.isdir(dst_base):
        write_tools_manifest(dst_base)
        cnt = sum(len(fs) for _,_,fs in os.walk(dst_base))
        if cnt > 1:
            print(f"  双版本 tools 就位: {dst_base} ({cnt} 文件)")
            return True
    return False


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
    """确保本地有 Windows/py3.12 轮子目录:requirements 依赖 + pip-*.whl。

    缺任一项都用本机 pip download 补拉(已存在则跳过,断点续拉)。
    pip 轮子是嵌入式运行时的"离线安装器"(stage_runtime 用它自启动 pip),
    因此即使依赖齐全也必须存在 pip-*.whl;否则完整包构建会在 stage_runtime 失败。
    """
    dest = wheels_dir or os.path.join("dist", "_wheels")
    os.makedirs(dest, exist_ok=True)
    if not glob_site(dest, "pip-*.whl"):
        print("  pip download pip 轮子 -> %s ..." % dest)
        subprocess.run(
            [sys.executable, "-m", "pip", "download", "pip", "--no-deps",
             "--only-binary=:all:", "--platform", "win_amd64",
             "--python-version", "3.12", "--implementation", "cp",
             "-d", dest],
            check=True)
    # 依赖判断按 requirements 核心包(不能按任意 *.whl——pip 轮子也算 whl)
    if not (glob_site(dest, "pymysql-*.whl") and glob_site(dest, "cryptography-*.whl")):
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
    platform = None
    variant = None
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
        elif a == "--platform":
            platform = argv[i + 1]
            if platform not in ("win64", "linux"):
                sys.exit("--platform 仅支持 win64/linux,收到: %s" % platform)
            i += 2
        elif a == "--variant":
            variant = argv[i + 1]
            if variant not in ("slim", "standard"):
                sys.exit("--variant 仅支持 slim|standard,收到: %s" % variant)
            i += 2
        else:
            sys.exit("未知参数: %s (支持 --tag/--with-runtime/--runtime-zip/"
                     "--wheels-dir/--tools-dir/--platform/--variant)" % a)
    if with_runtime and platform == "linux":
        sys.exit("完整包(--with-runtime)内置 Windows 嵌入式 Python,不能与 --platform linux 同用")
    if not platform:
        sys.exit("--platform 必选: win64 | linux")
    if not variant:
        sys.exit("--variant 必选: slim | standard")
    if variant not in ("slim", "standard"):
        sys.exit("--variant 仅支持 slim|standard,收到: %s" % variant)
    if variant == "standard" and not tools_dir:
        # 正常版应含 tools，允许自动拉取（后续 T2 实现），此处仅提示
        print("  [INFO] 正常版未指定 --tools-dir，将尝试自动拉取官方双版本 tools（5.7+8.x）")
    if variant == "slim" and tools_dir:
        sys.exit("瘦版不应含 --tools-dir（瘦版无内置 tools，向导提示下载）")
    version = tag.lstrip("v") if tag else version_from_src()
    tracked = git_tracked_files()
    pairs = collect_release_files(tracked, platform, variant)
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
        stage_tools(stage, tools_dir, platform)
    elif variant == "standard":
        # 正常版未指定 --tools-dir，尝试自动拉取官方双版本
        if not fetch_official_tools(stage, platform):
            print("  [WARN] 自动拉取失败，请改用 --tools-dir 手动指定 MySQL 客户端目录")
    zip_path, tgz_path = make_archives(version, stage, full=with_runtime,
                                       platform=platform, variant=variant)
    validate(zip_path or tgz_path, version, full=with_runtime, platform=platform, variant=variant)

    if zip_path:
        print("  zip    : %s (%.1f MB, sha256 %s)" % (
            zip_path, os.path.getsize(zip_path) / 1048576,
            sha256(zip_path)[:16]))
    if tgz_path:
        print("  tar.gz : %s (%.1f MB, sha256 %s)" % (
            tgz_path, os.path.getsize(tgz_path) / 1048576,
            sha256(tgz_path)[:16]))
    print("  条目数 : %d" % len(pairs))
    if with_runtime:
        print("  说明   : 完整包已内置 Windows 嵌入式 Python %s + 预装依赖,用户端全程离线" %
              runtime_resolver.EMBED_PY_VERSION)


if __name__ == "__main__":
    main()