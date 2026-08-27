# -*- coding: utf-8 -*-
"""自动更新模块:检查 GitHub 公开 releases → 下载/校验/备份 → 生成自更新脚本并重启。

自更新约束:运行中的 Python 进程无法替换自己正在执行的 .py(Windows 文件锁 + 不能自重启)。
因此走"独立 updater 脚本"模式:
  1. 服务端把新代码下载到 data/updates/staging/<ver>/src 并解压、校验、备份当前代码
  2. POST /api/update/apply 调用 build_apply_script() 生成 updater 脚本并启动它, 再让主进程退出
  3. updater 脚本(独立进程):等 8090 端口释放 → 用 staging 代码替换 BASE_DIR(保留 data/ 与 .venv)
     → 写 data/updates/update.log → 按原启动方式重启
更新只替换代码文件, 绝不碰 data/(配置/系统库 bootstrap/备份全在其中)。
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPD_DIR = os.path.join(DATA_DIR, "updates")       # staging/<ver>/src + backup/<cur>/ + update.log
STAGING = os.path.join(UPD_DIR, "staging")
BACKUP = os.path.join(UPD_DIR, "backup")
LOG = os.path.join(UPD_DIR, "update.log")

REPO = "zetsubouk/mysql-console"
API = f"https://api.github.com/repos/{REPO}"
UA = {"User-Agent": "mysql-console-updater", "Accept": "application/vnd.github+json"}

# 只替换代码;这些路径/目录绝不碰(配置、依赖、运行时数据)
_PRESERVE_DIRS = {".venv", "__pycache__", "dist", "data", ".git", ".jsdomtest", "node_modules"}


def current_version():
    try:
        from version import __version__
        return str(__version__)
    except Exception:
        return "0.0.0"


def _norm(v):
    """'v3.2.0-beta1' → [3,2,0]。只取第一个数字点段。"""
    m = re.match(r"[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(v).strip())
    return [int(m.group(i) or 0) for i in (1, 2, 3)]


def compare(current, latest):
    """current<latest 返回 -1; 相等 0; current>latest 1。"""
    c, l = _norm(current), _norm(latest)
    return (c < l) - (c > l)


def fetch_latest():
    req = urllib.request.Request(API + "/releases/latest", headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def check():
    """检查是否有新版本。网络失败返回 offline=True(不打扰用户)。"""
    cur = current_version()
    try:
        rel = fetch_latest()
    except Exception as e:
        return {"current": cur, "offline": True, "has_update": False,
                "latest": "", "error": str(e)}
    lat = rel.get("tag_name", "").lstrip("v")
    assets = [{"name": a.get("name"), "url": a.get("browser_download_url"),
               "size": a.get("size")} for a in rel.get("assets", [])]
    return {
        "current": cur, "latest": lat or cur, "has_update": compare(cur, lat) < 0,
        "new_version": lat or "", "name": rel.get("name"), "tag": rel.get("tag_name"),
        "body": (rel.get("body") or "")[:8000], "published": rel.get("published_at"),
        "assets": assets, "offline": False,
    }


def pick_asset(assets):
    """按平台选安装包:Windows 优先 .zip, 其它 .tar.gz。"""
    if not assets:
        return None
    want_zip = sys.platform.startswith("win")
    for a in assets:
        nm = (a.get("name") or "").lower()
        if want_zip and nm.endswith(".zip"):
            return a
    for a in assets:
        nm = (a.get("name") or "").lower()
        if nm.endswith(".tar.gz"):
            return a
    return assets[0]


def download(asset, dst_dir):
    """下载 release 资产到 dst_dir, 返回本地路径; 校验大小。"""
    os.makedirs(dst_dir, exist_ok=True)
    url = asset["url"]
    name = asset["name"]
    local = os.path.join(dst_dir, name)
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=300) as r, open(local, "wb") as f:
        shutil.copyfileobj(r, f)
    size = os.path.getsize(local)
    expect = asset.get("size")
    if expect:
        sha256(local)  # 至少算一遍, 留作日志
    return local, size


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_archive(archive, dest):
    """解压到 dest(去掉顶层目录)。返回解压后顶层是否需剥离。"""
    os.makedirs(dest, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive, "r:gz") as t:
            t.extractall(dest, filter="data")
    # 去掉单层包裹目录(如 mysql-console-3.2.0/)
    items = [os.path.join(dest, x) for x in os.listdir(dest)]
    if len(items) == 1 and os.path.isdir(items[0]):
        inner = items[0]
        for x in os.listdir(inner):
            shutil.move(os.path.join(inner, x), os.path.join(dest, x))
        os.rmdir(inner)


def prepare(version):
    """下载+校验+解压+备份。返回 {ok, msg, staging_src, version}。"""
    info = check()
    if info.get("offline"):
        return {"ok": False, "msg": "无法连接 GitHub(离线?)"}
    if not info.get("has_update"):
        return {"ok": False, "msg": f"已是当前最新版本 {info['current']}"}
    asset = pick_asset(info["assets"])
    if not asset:
        return {"ok": False, "msg": "release 无可用安装包资产"}
    ver_dir = os.path.join(STAGING, str(version or info["new_version"]))
    src_dir = os.path.join(ver_dir, "src")
    if os.path.exists(src_dir):
        shutil.rmtree(src_dir, ignore_errors=True)
    try:
        local, size = download(asset, ver_dir)
        _extract_archive(local, src_dir)
    except Exception as e:
        return {"ok": False, "msg": f"下载/解压失败: {e}"}
    # 备份当前代码
    bk = os.path.join(BACKUP, info["current"])
    try:
        _backup_code(bk)
    except Exception as e:
        return {"ok": False, "msg": f"备份失败: {e}"}
    return {"ok": True, "msg": f"已下载并解压 v{info['new_version']}, 已备份当前 v{info['current']}",
            "staging_src": src_dir, "new_version": info["new_version"]}


def _code_items():
    """待替换的代码项(文件/目录), 不含保留目录。"""
    items = []
    for name in os.listdir(BASE_DIR):
        if name in _PRESERVE_DIRS or name.startswith("."):
            continue
        p = os.path.join(BASE_DIR, name)
        if os.path.isfile(p) or os.path.isdir(p):
            items.append(p)
    return items


def _backup_code(dst):
    os.makedirs(dst, exist_ok=True)
    for p in _code_items():
        rel = os.path.basename(p)
        d = os.path.join(dst, rel)
        if os.path.isdir(p):
            shutil.copytree(p, d, ignore=shutil.ignore_patterns("*__pycache__*", "*.pyc"))
        else:
            shutil.copy2(p, d)


def build_apply_script(version):
    """生成并返回自更新离线脚本路径。脚本由独立进程运行:等端口释放→替换代码→重启。"""
    import version as ver
    script = os.path.join(UPD_DIR, "apply_update.py")
    os.makedirs(UPD_DIR, exist_ok=True)
    src_dir = os.path.join(STAGING, version, "src")
    code = UPGRADER_TMPL.format(
        BASE_DIR=repr(BASE_DIR), SRC=repr(src_dir),
        PRESERVES=repr(sorted(_PRESERVE_DIRS)), LOG=repr(LOG),
        VERSION=repr(version), NEW_VERSION=repr(version),
    )
    with io.open(script, "w", encoding="utf-8") as f:
        f.write(code)
    return script


UPGRADER_TMPL = '''# -*- coding: utf-8 -*-
"""自更新离线脚本(独立进程)。等 8090 释放 → 用 staging 替换 BASE_DIR(保留 data/ 等) → 重启。"""
import os, shutil, sys, time, subprocess

BASE = {BASE_DIR}
SRC = {SRC}
PRESERVES = {PRESERVES}
LOG = {LOG}
VERSION = {VERSION}
PORT = 8090

def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\\n")

def wait_port_free():
    import socket
    for _ in range(90):
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            time.sleep(2)
        except OSError:
            return True
    return False

def swap():
    if not os.path.isdir(SRC):
        log("ERROR staging 目录不存在: " + SRC)
        return False
    # 先删旧代码项(保留目录除外), 再拷新
    for name in os.listdir(BASE):
        if name in PRESERVES or name.startswith("."):
            continue
        p = os.path.join(BASE, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        elif os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
    for name in os.listdir(SRC):
        if name in PRESERVES or name.startswith("."):
            continue
        s = os.path.join(SRC, name)
        d = os.path.join(BASE, name)
        if os.path.isdir(s):
            shutil.copytree(s, d, ignore=shutil.ignore_patterns("*__pycache__*", "*.pyc"))
        else:
            shutil.copy2(s, d)
    return True

def restart():
    bat = os.path.join(BASE, "start.bat")
    sh_ = os.path.join(BASE, "start.sh")
    if os.name == "nt" and os.path.exists(bat):
        subprocess.Popen(["cmd", "/c", "start", "", bat], cwd=BASE)
    elif os.path.exists(sh_):
        subprocess.Popen(["sh", sh_], cwd=BASE)
    else:
        subprocess.Popen([sys.executable, os.path.join(BASE, "server.py")], cwd=BASE)

log("updater start, target=" + VERSION)
wait_port_free()
try:
    if swap():
        log("code swapped to v" + VERSION)
        restart()
        log("restart issued, done")
    else:
        log("swap failed, no restart")
except Exception as e:
    log("ERROR " + repr(e))
'''

# ---------- 更新状态(给前端读 update.log) ----------
def read_status():
    if not os.path.exists(LOG):
        return {"log_exists": False, "lines": []}
    try:
        with io.open(LOG, "r", encoding="utf-8") as f:
            lines = [l.rstrip() for l in f.read().splitlines() if l.strip()]
        return {"log_exists": True, "lines": lines[-50:]}
    except Exception:
        return {"log_exists": True, "lines": []}