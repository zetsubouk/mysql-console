# -*- coding: utf-8 -*-
"""瘦版向导:后台下载 MySQL 客户端 tools(双版本 5.7+8.x),立即返回,轮询 status。

从 handlers.Handler._handle_setup_download_tools 拆出(2026-09-02),逻辑零改动:
- 下载逻辑全部收敛在本模块,Handler 只保留共享状态(_dl_state/_dl_lock)与 HTTP 外壳;
- 状态由调用方传入的 dict + threading.Lock 承载,便于在多个请求间共享轮询。
"""
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import urllib.request
import zipfile

import paths

# 平台官方客户端下载源:官方 + 国内镜像(阿里云/清华),逐 URL 尝试。
_OFFICIAL = {
    "win64": {
        "5.7": [
            "https://dev.mysql.com/get/Downloads/MySQL-5.7/mysql-5.7.44-winx64.zip",
            "https://mirrors.aliyun.com/mysql/MySQL-5.7/mysql-5.7.44-winx64.zip",
            "https://mirrors.tuna.tsinghua.edu.cn/mysql/MySQL-5.7/mysql-5.7.44-winx64.zip",
            "https://cdn.mysql.com/archives/mysql-5.7/mysql-5.7.44-winx64.zip",
        ],
        "8.0": [
            "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-winx64.zip",
            "https://mirrors.aliyun.com/mysql/MySQL-8.0/mysql-8.0.36-winx64.zip",
            "https://mirrors.tuna.tsinghua.edu.cn/mysql/MySQL-8.0/mysql-8.0.36-winx64.zip",
            "https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-winx64.zip",
        ],
    },
    "linux": {
        "5.7": [
            "https://dev.mysql.com/get/Downloads/MySQL-5.7/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz",
            "https://mirrors.aliyun.com/mysql/MySQL-5.7/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz",
            "https://mirrors.tuna.tsinghua.edu.cn/mysql/MySQL-5.7/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz",
            "https://cdn.mysql.com/archives/mysql-5.7/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz",
        ],
        "8.0": [
            "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-linux-glibc2.12-x86_64.tar.gz",
            "https://mirrors.aliyun.com/mysql/MySQL-8.0/mysql-8.0.36-linux-glibc2.12-x86_64.tar.gz",
            "https://mirrors.tuna.tsinghua.edu.cn/mysql/MySQL-8.0/mysql-8.0.36-linux-glibc2.12-x86_64.tar.gz",
            "https://cdn.mysql.com/archives/mysql-8.0/mysql-8.0.36-linux-glibc2.12-x86_64.tar.gz",
        ],
    },
}


def bundled_tools_present():
    """是否已内置 MySQL 客户端(发布包 tools/ 或已下载成功)。失败视为未内置。"""
    try:
        import env_probe
        return bool(env_probe.bundled_tools_summary())
    except Exception:
        return False


def _load_sha_map():
    """读取 scripts/official_sha256.json 的 sha256 清单(跳过 _ 前缀元数据键)。"""
    try:
        import json
        cand = os.path.join(paths.APP_ROOT, "scripts", "official_sha256.json")
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8") as f:
                d = json.load(f)
            return {k: v.strip() for k, v in d.items()
                    if isinstance(v, str) and v.strip() and not k.startswith("_")}
    except Exception:
        pass
    return {}


def _verify_tmp(tmp_p, url, sha_map):
    """校验下载临时文件:大小下限 + 可选 SHA256 + zip/tar 完整性。返回 (ok, reason)。"""
    try:
        if os.path.getsize(tmp_p) < 5 * 1024 * 1024:
            return False, "下载文件过小"
        fname = os.path.basename(url.split("?")[0])
        want = sha_map.get(fname)
        if want:
            h = hashlib.sha256()
            with open(tmp_p, "rb") as fh:
                for ch in iter(lambda: fh.read(1 << 20), b""):
                    h.update(ch)
            if h.hexdigest().lower() != want.lower():
                return False, "SHA256 不匹配 期望 %s..." % want[:12]
        if tmp_p.endswith(".zip") or url.endswith(".zip"):
            with zipfile.ZipFile(tmp_p) as zz:
                bad = zz.testzip()
                if bad:
                    return False, "zip 损坏: %s" % bad
        else:
            with tarfile.open(tmp_p, "r:gz") as tt:
                tt.getmembers()
        return True, ""
    except Exception as e:
        return False, str(e)[:200]


def _extract_subdir(sub, tmp_p, url):
    """把下载包里需要的 mysqldump/mysql(及 Windows 运行库 dll)提取到 sub 目录。"""
    if url.endswith(".zip"):
        with zipfile.ZipFile(tmp_p) as zf:
            for info in zf.infolist():
                if info.filename.endswith(("mysqldump.exe", "mysql.exe")):
                    name = os.path.basename(info.filename)
                    with zf.open(info) as src, open(os.path.join(sub, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                elif info.filename.endswith(".dll"):
                    name = os.path.basename(info.filename)
                    if name.lower() in ("libmysql.dll", "vcruntime140.dll", "msvcp140.dll"):
                        with zf.open(info) as src, open(os.path.join(sub, name), "wb") as dst:
                            shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(tmp_p, "r:gz") as tf:
            for m in tf.getmembers():
                if m.name.endswith(("bin/mysqldump", "bin/mysql")):
                    name = os.path.basename(m.name)
                    f = tf.extractfile(m)
                    if f:
                        with open(os.path.join(sub, name), "wb") as dst:
                            shutil.copyfileobj(f, dst)
                        os.chmod(os.path.join(sub, name), 0o755)


def _write_sha256sums(dst_base):
    """对 tools/ 下所有文件重写 SHA256SUMS 清单(幂等,失败静默)。"""
    try:
        lines = []
        for root, _, fs in os.walk(dst_base):
            for fn in fs:
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, dst_base).replace("\\", "/")
                if rel == "SHA256SUMS":
                    continue
                h = hashlib.sha256()
                with open(fp, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                lines.append("%s  %s" % (h.hexdigest(), rel))
        if lines:
            with open(os.path.join(dst_base, "SHA256SUMS"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(sorted(lines)) + "\n")
    except Exception:
        pass


def start_download(state, lock):
    """后台线程入口:按平台下载双版本 MySQL 客户端到 tools/,更新共享 state。

    state 约定:{"status","msg","ok_cnt","error"};调用方负责在入线程前加锁置 running。
    返回前由线程在后台更新终态(done/failed)。本函数立即返回。
    """
    def _worker():
        plat = "win64" if sys.platform == "win32" else "linux"
        sha_map = _load_sha_map()
        dst_base = os.path.join(paths.APP_ROOT, "tools")
        os.makedirs(dst_base, exist_ok=True)
        ok_cnt, last_err = 0, ""
        for ver, entry in _OFFICIAL.get(plat, {}).items():
            urls = entry if isinstance(entry, (list, tuple)) else [entry]
            sub = os.path.join(dst_base, "mysql-%s" % ver)
            if os.path.isdir(sub) and any(
                    os.path.isfile(os.path.join(sub, n))
                    for n in ("mysqldump", "mysqldump.exe", "mysql", "mysql.exe")):
                ok_cnt += 1
                continue
            with lock:
                state["msg"] = "下载 MySQL %s..." % ver
            got = False
            for url in urls:
                tmp = os.path.join(tempfile.gettempdir(),
                                   "mysql-%s-%s-%s.tmp" % (ver, plat, abs(hash(url)) % 10000))
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "mysql-console"})
                    with urllib.request.urlopen(req, timeout=30) as resp, open(tmp, "wb") as out:
                        shutil.copyfileobj(resp, out)
                    ok, reason = _verify_tmp(tmp, url, sha_map)
                    if not ok:
                        last_err = reason
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
                        continue
                    os.makedirs(sub, exist_ok=True)
                    _extract_subdir(sub, tmp, url)
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                    if any(os.path.isfile(os.path.join(sub, n))
                           for n in ("mysqldump", "mysqldump.exe", "mysql", "mysql.exe")):
                        ok_cnt += 1
                        got = True
                        break
                    last_err = "解压后缺少 mysqldump/mysql"
                    try:
                        shutil.rmtree(sub)
                    except Exception:
                        pass
                except Exception as e:
                    last_err = str(e)[:200]
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
                    continue
            if not got:
                continue
        _write_sha256sums(dst_base)
        with lock:
            if ok_cnt:
                state.update({"status": "done",
                              "msg": "已下载 %s/2 版本到 tools/" % ok_cnt,
                              "ok_cnt": ok_cnt, "error": ""})
            else:
                state.update({"status": "failed", "msg": "下载失败", "ok_cnt": 0,
                              "error": last_err or "网络不可达，可跳过或手动指定客户端目录"})
    threading.Thread(target=_worker, daemon=True).start()


def snapshot_status(state, lock, has_tools):
    """读取共享状态快照,并按是否已有客户端修正 status。返回新 dict(不污染原状态)。"""
    with lock:
        st = dict(state)
    st["has_tools"] = has_tools
    if has_tools and st["status"] in ("idle", "running"):
        st["status"] = "done"
    return st