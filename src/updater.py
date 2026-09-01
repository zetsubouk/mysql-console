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

import paths

# 自更新替换目标 = 本文件所在目录(src/):新布局下全部代码与 static/ 都位于 src/,
# 顶层 data/.venv/node_modules 等保留目录天然不动 —— 替换范围自动正确(旧平铺布局同样成立)。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = paths.DATA_DIR
UPD_DIR = os.path.join(DATA_DIR, "updates")       # staging/<ver>/src + backup/<cur>/ + update.log
STAGING = os.path.join(UPD_DIR, "staging")
BACKUP = os.path.join(UPD_DIR, "backup")
LOG = os.path.join(UPD_DIR, "update.log")

REPO = "zetsubouk/mysql-console"
API = f"https://api.github.com/repos/{REPO}"
UA = {"User-Agent": "mysql-console-updater", "Accept": "application/vnd.github+json"}

def _latest_cache_path():
    return os.path.join(UPD_DIR, "latest_release.json")

def _save_latest_cache(rel):
    try:
        os.makedirs(UPD_DIR, exist_ok=True)
        payload = {
            "tag_name": rel.get("tag_name", ""),
            "name": rel.get("name", ""),
            "body": (rel.get("body") or "")[:8000],
            "published_at": rel.get("published_at", ""),
            "assets": rel.get("assets", []),
        }
        with io.open(_latest_cache_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass

def _load_latest_cache():
    try:
        p = _latest_cache_path()
        if not os.path.isfile(p):
            return None
        with io.open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None

def _load_bundled():
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bundled_release.json")
        if not os.path.isfile(p):
            return None
        with io.open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None

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
    return (c > l) - (c < l)


def fetch_latest():
    req = urllib.request.Request(API + "/releases/latest", headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _build_check_result(cur, rel, offline=False):
    lat = (rel.get("tag_name", "") or "").lstrip("v")
    assets = [{"name": a.get("name"), "url": a.get("browser_download_url"),
               "size": a.get("size")} for a in rel.get("assets", [])]
    return {
        "current": cur, "latest": lat or cur, "has_update": compare(cur, lat) < 0,
        "new_version": lat or "", "name": rel.get("name"), "tag": rel.get("tag_name"),
        "body": (rel.get("body") or "")[:8000], "published": rel.get("published_at"),
        "assets": assets, "offline": bool(offline),
    }

def check():
    """检查是否有新版本。网络失败返回 offline=True，并回落到本地缓存/随包 bundled 的最新发版信息。"""
    cur = current_version()
    try:
        rel = fetch_latest()
        _save_latest_cache(rel)
        return _build_check_result(cur, rel, offline=False)
    except Exception as e:
        cached = _load_latest_cache() or _load_bundled()
        err = str(e)
        if cached:
            if "CERTIFICATE_VERIFY_FAILED" in err or "SSL" in err:
                alt = None
                try:
                    import ssl as _ssl
                    ctx = _ssl._create_unverified_context()
                    req2 = urllib.request.Request(API + "/releases/latest", headers=UA)
                    with urllib.request.urlopen(req2, timeout=15, context=ctx) as r2:
                        alt = json.loads(r2.read().decode("utf-8"))
                    _save_latest_cache(alt)
                    return _build_check_result(cur, alt, offline=False)
                except Exception:
                    pass
            r = _build_check_result(cur, cached, offline=True)
            r["error"] = err
            return r
        return {"current": cur, "offline": True, "has_update": False,
                "latest": "", "error": err, "body": ""}


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
    """下载 release 资产到 dst_dir, 返回本地路径; 强校验大小与摘要。"""
    os.makedirs(dst_dir, exist_ok=True)
    url = asset["url"]
    name = asset["name"]
    local = os.path.join(dst_dir, name)
    tmp = local + ".part"
    expect = asset.get("size")
    try:
        expect = int(expect) if expect is not None else None
    except Exception:
        expect = None
    req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
    try:
        with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        size = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if size == 0:
            raise ValueError("下载文件为空,可能网络中断")
        if expect is not None and expect != 0 and size != expect:
            raise ValueError(f"下载完整性校验失败: 文件大小不符 期望 {expect} 实际 {size}(可能网络中断或被劫持)")
        digest = (asset.get("digest") or asset.get("sha256") or "").strip()
        if digest:
            want = digest.split(":", 1)[-1].strip().lower()
            got = sha256(tmp).lower()
            if got != want:
                raise ValueError(f"下载完整性校验失败: SHA256 不匹配 期望 {want[:12]}... 实际 {got[:12]}...")
        else:
            sha256(tmp)
        if os.path.exists(local):
            try:
                os.remove(local)
            except OSError:
                pass
        os.rename(tmp, local)
        return local, size
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


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


def _normalize_staging_src(src_dir):
    """新发布包格式:包内代码位于顶层 src/ 子目录。
    解压后若 src_dir 下存在 src/ 子目录,将其内容提升到 src_dir 顶层,
    并丢弃包内其它顶层项(docs/README/LICENSE/requirements.txt/scripts 等)。
    旧平铺格式(无 src/ 子目录)时为空操作,天然向后兼容。"""
    items = [os.path.join(src_dir, x) for x in os.listdir(src_dir)]
    src_items = [p for p in items if os.path.basename(p) == "src" and os.path.isdir(p)]
    if not src_items:
        return
    inner = src_items[0]
    for x in os.listdir(inner):
        shutil.move(os.path.join(inner, x), os.path.join(src_dir, x))
    os.rmdir(inner)
    for p in items:
        if os.path.basename(p) == "src":
            continue
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.remove(p)
            except OSError:
                pass


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
        _normalize_staging_src(src_dir)
    except Exception as e:
        try:
            shutil.rmtree(ver_dir, ignore_errors=True)
        except Exception:
            pass
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