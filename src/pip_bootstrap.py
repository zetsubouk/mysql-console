# -*- coding: utf-8 -*-
"""嵌入式私有运行时的 pip 引导(2026-08-30 新增,v3.7.0)。

场景:install.bat 三级解析走到"下载嵌入式 Python"后,该运行时自带无 pip,
且包内没有 wheels/ 离线轮子目录(开发仓库/精简包)→ 由本脚本在线引导 pip。
引导成功后 install.bat 即可用 `python -m pip install -r requirements.txt` 装依赖。

策略(逐个尝试,任一成功即返回 0):
  1) get-pip.py 官方源(bootstrap.pypa.io,Fastly CDN)
  2) get-pip.py 阿里云镜像
  3) 清华 PyPI 镜像 simple 索引解析最新 pip 轮子 → 下载 → pip 轮子自启动安装自身

纯标准库;由嵌入式运行时自身执行(sys.executable 即目标解释器),
前置条件:runtime/python 的 python3XX._pth 已解注 import site。
"""
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

GET_PIP_URLS = [
    "https://bootstrap.pypa.io/get-pip.py",
    "https://mirrors.aliyun.com/pypi/get-pip.py",
]
TUNA_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple/pip/"
UA = {"User-Agent": "mysql-console-pip-bootstrap"}


def _download(url, dest, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp, \
            open(dest, "wb") as f:
        f.write(resp.read())


def _fetch_text(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_pip_wheel_urls(html):
    """从 PEP 503 simple 索引 HTML 提取 pip 的 py3-none-any 轮子 URL。"""
    if not html:
        return []
    urls = re.findall(r'href=["\']([^"\']+\.whl)#?[^"\']*["\']', html, flags=re.I)
    out = []
    for u in urls:
        base = os.path.basename(u.split("#")[0])
        if re.match(r"pip-\d+(\.\d+)*-py3-none-any\.whl$", base):
            out.append(u.split("#")[0])
    return out


def pick_newest_pip_wheel(urls):
    """按版本号取最新轮子 URL;相对地址补全为绝对。"""
    best, best_key = None, None
    for u in urls:
        m = re.search(r"pip-(\d+(?:\.\d+)*)-py3-none-any\.whl$",
                      os.path.basename(u))
        if not m:
            continue
        key = tuple(int(x) for x in m.group(1).split("."))
        if best_key is None or key > best_key:
            best, best_key = u, key
    if best is None:
        return None
    return best if best.startswith("http") else \
        urllib.parse.urljoin(TUNA_PIP_INDEX, best)


def pip_ok(python_exe, timeout=30):
    """`python -m pip --version` 是否可用。"""
    try:
        p = subprocess.run([python_exe, "-m", "pip", "--version"],
                           capture_output=True, timeout=timeout)
        return p.returncode == 0
    except Exception:
        return False


def bootstrap(python_exe, log=print):
    """在线引导 pip;成功返回 True。"""
    if pip_ok(python_exe):
        log("  pip already present")
        return True
    tmp = tempfile.mkdtemp(prefix="mc_pipboot_")
    # 1/2) get-pip.py(官方源 → 阿里云镜像)
    for url in GET_PIP_URLS:
        gp = os.path.join(tmp, "get-pip.py")
        try:
            log("  下载 get-pip.py: %s" % url)
            _download(url, gp)
            r = subprocess.run([python_exe, gp, "--no-warn-script-location"],
                               capture_output=True, timeout=300)
            if r.returncode == 0 and pip_ok(python_exe):
                log("  get-pip.py 引导成功")
                return True
            log("  get-pip.py 执行失败(rc=%s)" % r.returncode)
        except Exception as e:                             # noqa: BLE001
            log("  get-pip.py 失败: %s: %s" % (type(e).__name__, e))
    # 3) 清华 simple 索引 → 最新 pip 轮子 → 轮子自启动安装自身
    try:
        log("  解析清华镜像 pip 索引: %s" % TUNA_PIP_INDEX)
        whl_url = pick_newest_pip_wheel(
            parse_pip_wheel_urls(_fetch_text(TUNA_PIP_INDEX)))
        if whl_url:
            whl = os.path.join(tmp, os.path.basename(whl_url))
            log("  下载 pip 轮子: %s" % whl_url)
            _download(whl_url, whl)
            r = subprocess.run(
                [python_exe, os.path.join(whl, "pip"), "install", whl,
                 "--no-warn-script-location"],
                capture_output=True, timeout=300)
            if r.returncode == 0 and pip_ok(python_exe):
                log("  pip 轮子引导成功")
                return True
            log("  pip 轮子安装失败(rc=%s)" % r.returncode)
        else:
            log("  索引中未解析到 pip 轮子")
    except Exception as e:                                 # noqa: BLE001
        log("  pip 轮子通道失败: %s: %s" % (type(e).__name__, e))
    return False


def main():
    ok = bootstrap(sys.executable, log=print)
    if not ok:
        print("[pip-bootstrap] FAILED: 全部引导源不可用,请检查网络或改用完整包/wheels")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
