# -*- coding: utf-8 -*-
"""Python 运行时解析与自带独立运行时管理(2026-08-29 新增,v3.7.0)。

解决目标机器"没有 Python / Python 版本过低导致装不上、起不来"的问题。

三级解析(顺序固定,所有入口必须一致,详见 scripts/install.bat):
  1) bundled  : <APP_ROOT>/runtime/python/python.exe
                (完整包内置,或此前下载的独立运行时)
  2) system   : py -3 / python / python3 实际执行探测(防 Windows 商店占位符),
                要求 >= 3.10;不满足时由调用方交互确认,绝不静默处理
  3) download : 下载官方嵌入式 Python(Windows embeddable)到 runtime/python,
                源顺序: python.org 官方 → 华为云镜像 → npmmirror 镜像

设计原则:
  - 绝不改动用户系统环境:不装系统 Python、不改 PATH、不写注册表、
    不向系统 Python 安装任何包;独立运行时只落在部署根 runtime/ 内,
    删除 runtime/ 目录即彻底移除;
  - venv 路线(.venv)基于用户已有 Python 创建,同样与系统环境隔离;
  - 纯标准库;subprocess/urllib 均可 mock,全部逻辑可离线单测;
  - 本模块与 scripts/install.bat、start.bat、init.bat 中的三级解析策略
    必须保持同步(bat 无法在无 Python 时调用本模块,故策略重复实现)。
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

IS_WIN = sys.platform == "win32"
MIN_PY = (3, 10)

# Windows 嵌入式 Python(amd64)版本与下载源(顺序即优先级)
EMBED_PY_VERSION = "3.12.10"
EMBED_MIN_BYTES = 8 * 1024 * 1024          # 合理下限,防半截文件/错误页

RUNTIME_DIR_NAME = "runtime"
CACHE_FILE_NAME = "resolved_python.txt"


def _embed_urls(ver=EMBED_PY_VERSION):
    """嵌入式 Python 下载源列表(官方优先,国内镜像兜底)。"""
    fn = "python-%s-embed-amd64.zip" % ver
    return [
        "https://www.python.org/ftp/python/%s/%s" % (ver, fn),
        "https://mirrors.huaweicloud.com/python/%s/%s" % (ver, fn),
        "https://registry.npmmirror.com/-/binary/python/%s/%s" % (ver, fn),
    ]


def embed_zip_name(ver=EMBED_PY_VERSION):
    return "python-%s-embed-amd64.zip" % ver


# ---------------- 解析 ----------------

def runtime_dir(root):
    return os.path.join(root, RUNTIME_DIR_NAME)


def bundled_runtime(root):
    """内置独立运行时解释器路径;不存在返回 None。"""
    if IS_WIN:
        p = os.path.join(root, RUNTIME_DIR_NAME, "python", "python.exe")
    else:
        p = os.path.join(root, RUNTIME_DIR_NAME, "python", "bin", "python3")
    return p if os.path.isfile(p) else None


def venv_runtime(root):
    """项目 venv 解释器路径;不存在返回 None。"""
    if IS_WIN:
        p = os.path.join(root, ".venv", "Scripts", "python.exe")
    else:
        p = os.path.join(root, ".venv", "bin", "python")
    return p if os.path.isfile(p) else None


def version_satisfies(ver):
    return bool(ver) and ver[:2] >= MIN_PY


def _probe_one(cmd, timeout=15):
    """实际执行解释器取版本(防商店占位符/损坏安装)。返回 (major,minor,micro) 或 None。"""
    try:
        p = subprocess.run(
            list(cmd) + ["-c",
                         "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, timeout=timeout)
        out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
        if p.returncode != 0 or not m:
            return None
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def system_candidates():
    """探测用命令列表(顺序固定)。"""
    if IS_WIN:
        return [["py", "-3"], ["python"], ["python3"]]
    return [["python3"], ["python"]]


def probe_system_pythons(timeout=15):
    """逐个实际执行候选命令,返回 [{cmd, version, satisfies}]。"""
    out = []
    for cmd in system_candidates():
        ver = _probe_one(cmd, timeout=timeout)
        out.append({"cmd": cmd, "version": ver,
                    "satisfies": version_satisfies(ver)})
    return out


def resolve(root, timeout=15):
    """三级解析快照(不做下载、不交互),供脚本/测试/未来 API 使用。

    返回:
      {source: bundled|system|none, exe, cmd, version, version_text,
       satisfies, candidates}
    source=bundled/none 时 exe 为绝对路径(cmd=None);
    source=system 时 cmd 为命令列表(exe=None);
    source=none 时 candidates 里保留全部探测明细(含版本不满足者),
    供调用方生成"检测到 x.y 不满足"类提示。
    """
    exe = bundled_runtime(root)
    if exe:
        ver = _probe_one([exe], timeout=timeout)
        return {"source": "bundled", "exe": exe, "cmd": None,
                "version": ver,
                "version_text": ".".join(map(str, ver)) if ver else "",
                "satisfies": True, "candidates": []}
    cands = probe_system_pythons(timeout=timeout)
    for c in cands:
        if c["satisfies"]:
            return {"source": "system", "exe": None, "cmd": c["cmd"],
                    "version": c["version"],
                    "version_text": ".".join(map(str, c["version"])),
                    "satisfies": True, "candidates": cands}
    return {"source": "none", "exe": None, "cmd": None, "version": None,
            "version_text": "", "satisfies": False, "candidates": cands}


# ---------------- 运行时缓存 ----------------

def _cache_path(root):
    return os.path.join(root, RUNTIME_DIR_NAME, CACHE_FILE_NAME)


def write_runtime_cache(root, exe):
    """记录已就绪的独立运行时/venv 解释器绝对路径(启动脚本兜底读取)。"""
    os.makedirs(os.path.dirname(_cache_path(root)), exist_ok=True)
    with open(_cache_path(root), "w", encoding="ascii") as f:
        f.write(os.path.abspath(exe) + "\n")


def read_runtime_cache(root):
    """读缓存;路径已失效返回 None。仅信任绝对路径。"""
    try:
        with open(_cache_path(root), encoding="ascii") as f:
            line = (f.readline() or "").strip()
    except OSError:
        return None
    if not line or not os.path.isabs(line) or not os.path.isfile(line):
        return None
    return line


# ---------------- 嵌入式运行时安装 ----------------

def patch_embedded_ppth(runtime_python_dir):
    """解注 python3XX._pth 的 import site(启用 site-packages)。

    幂等:已解注返回 True;找不到 ._pth 返回 False。
    """
    pths = glob.glob(os.path.join(runtime_python_dir, "python3*._pth"))
    if not pths:
        return False
    changed = False
    for p in pths:
        with open(p, encoding="ascii") as f:
            text = f.read()
        if re.search(r"^#import\s+site", text, flags=re.M):
            text = re.sub(r"^#import\s+site", "import site", text, flags=re.M)
            with open(p, "w", encoding="ascii", newline="") as f:
                f.write(text)
            changed = True
        elif re.search(r"^import\s+site", text, flags=re.M):
            changed = True                    # 已解注
    return changed


def _safe_extract(zf, dest):
    """防路径穿越解压(跳过绝对路径/.. 条目)。"""
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            continue
        target = os.path.join(dest, *name.split("/"))
        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _download(url, dest, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "mysql-console"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, \
            open(dest, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


def download_embedded(root, log=print, timeout=60, zip_source=None):
    """下载并就位嵌入式 Python 到 runtime/python。

    zip_source: 本地 zip 路径(离线构建/手动下载兜底),跳过网络下载。
    返回 {"ok": bool, "exe": str, "source": str, "error": str}
    """
    rtdir = runtime_dir(root)
    pydir = os.path.join(rtdir, "python")
    exe = bundled_runtime(root)
    if exe:
        return {"ok": True, "exe": exe, "source": "existing", "error": ""}
    os.makedirs(pydir, exist_ok=True)
    tmp_zip = os.path.join(rtdir, "_embed.zip")
    source = "local"
    try:
        if zip_source:
            if not os.path.isfile(zip_source):
                return {"ok": False, "exe": "", "source": "local",
                        "error": "本地包不存在: %s" % zip_source}
            shutil.copyfile(zip_source, tmp_zip)
        else:
            urls = _embed_urls()
            last_err = ""
            for url in urls:
                try:
                    log("  下载: %s" % url)
                    if os.path.exists(tmp_zip):
                        os.remove(tmp_zip)
                    _download(url, tmp_zip, timeout=timeout)
                    if os.path.getsize(tmp_zip) < EMBED_MIN_BYTES:
                        last_err = "文件过小(%d bytes),疑似错误页" % os.path.getsize(tmp_zip)
                        continue
                    source = url.split("/")[2]
                    break
                except Exception as e:                     # noqa: BLE001
                    last_err = "%s: %s" % (type(e).__name__, e)
                    continue
            else:
                return {"ok": False, "exe": "", "source": "download",
                        "error": "全部下载源失败(%s)" % last_err}
        if os.path.getsize(tmp_zip) < EMBED_MIN_BYTES:
            return {"ok": False, "exe": "", "source": source,
                    "error": "压缩包不完整(%d bytes)" % os.path.getsize(tmp_zip)}
        with zipfile.ZipFile(tmp_zip) as zf:
            _safe_extract(zf, pydir)
        if not patch_embedded_ppth(pydir):
            return {"ok": False, "exe": "", "source": source,
                    "error": "未找到 python3XX._pth,包内容异常"}
        exe = bundled_runtime(root)
        if not exe:
            return {"ok": False, "exe": "", "source": source,
                    "error": "解压后未找到 python.exe,包内容异常"}
        return {"ok": True, "exe": exe, "source": source, "error": ""}
    finally:
        if os.path.exists(tmp_zip):
            try:
                os.remove(tmp_zip)
            except OSError:
                pass


# ---------------- 依赖安装(pip wheel 内嵌启动) ----------------

def find_pip_wheel(wheels_dir):
    """wheels/ 目录中的 pip 轮子;用于无 pip 的嵌入式运行时离线装依赖。"""
    hits = sorted(glob.glob(os.path.join(wheels_dir, "pip-*.whl")))
    return hits[0] if hits else None


def install_deps_offline(python_cmd, wheels_dir, requirements, target=None,
                         log=print):
    """离线装依赖:用 pip 轮子自启动,--no-index 只吃本地 wheels/。

    python_cmd: 解释器命令列表(如 ["C:/.../python.exe"]);
    target:     传入时安装到该目录(嵌入式运行时用),否则装进解释器默认环境。
    返回 {"ok": bool, "cmd": str, "output": str}
    """
    pip_whl = find_pip_wheel(wheels_dir)
    if not pip_whl:
        return {"ok": False, "cmd": "", "output": "wheels/ 中未找到 pip-*.whl"}
    cmd = list(python_cmd) + [os.path.join(pip_whl, "pip"), "install",
                              "--no-index", "--find-links", wheels_dir,
                              "-r", requirements]
    if target:
        cmd += ["--target", target]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=600)
        out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")
        return {"ok": p.returncode == 0, "cmd": " ".join(cmd), "output": out[-2000:]}
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "cmd": " ".join(cmd),
                "output": "%s: %s" % (type(e).__name__, e)}


def install_deps_online(python_cmd, requirements, mirror=None, log=print):
    """在线装依赖(venv 内有 pip 时);mirror 可选国内镜像(如清华源)。"""
    cmd = list(python_cmd) + ["-m", "pip", "install", "-r", requirements]
    if mirror:
        cmd += ["-i", mirror]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=600)
        out = ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")
        return {"ok": p.returncode == 0, "cmd": " ".join(cmd), "output": out[-2000:]}
    except Exception as e:                                 # noqa: BLE001
        return {"ok": False, "cmd": " ".join(cmd),
                "output": "%s: %s" % (type(e).__name__, e)}


PYPI_MIRROR_TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple"


def deps_ok(python_cmd, timeout=30):
    """依赖是否已装齐(import 试探)。"""
    try:
        p = subprocess.run(
            list(python_cmd) + ["-c", "import pymysql, cryptography"],
            capture_output=True, timeout=timeout)
        return p.returncode == 0
    except Exception:
        return False
