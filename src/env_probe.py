# -*- coding: utf-8 -*-
"""环境探测:MySQL 客户端动态定位与依赖检查。

目标:任何一台新主机上无需改代码即可找到 mysqldump / mysql:
  1) 用户在设置中指定的目录(或完整路径)
  2) 系统 PATH
  3) 各平台常见安装目录扫描
找不到时不抛异常,由调用方给出明确降级提示。

同时提供客户端/服务器版本解析,用于大版本兼容性警告
(如 8.x mysqldump 导出 5.7 数据的经典坑)。
"""
import glob
import os
import platform
import re
import shutil
import subprocess
import sys

IS_WIN = sys.platform == "win32"


def candidate_dirs():
    """按平台生成 MySQL 客户端候选目录列表。"""
    dirs = []
    if IS_WIN:
        pats = [
            r"C:\Program Files\MySQL\*\bin",
            r"C:\Program Files (x86)\MySQL\*\bin",
            r"C:\mysql*\bin", r"D:\mysql*\bin", r"E:\mysql*\bin",
            r"C:\phpstudy_pro\Extensions\MySQL*\bin",
            r"D:\phpstudy_pro\Extensions\MySQL*\bin",
            r"C:\xampp\mysql\bin", r"D:\xampp\mysql\bin",
            r"C:\wamp64\bin\mysql\*\bin",
            r"C:\ProgramData\chocolatey\bin",
        ]
        for p in pats:
            dirs.extend(glob.glob(p))
    else:
        dirs = ["/usr/bin", "/usr/local/bin", "/usr/local/mysql/bin",
                "/opt/mysql/bin", "/opt/lampp/bin"]
    return [d for d in dirs if os.path.isdir(d)]


# ---------------- 内置工具(打包随附) ----------------
# 远程备份场景:目标服务器 3306 不通或本地未装 MySQL 客户端时,
# 发布包可在部署根内置一套 MySQL 客户端工具(tools/),探测链兜底使用。
def bundled_tools_dir():
    """内置工具根目录: 部署根/tools(与 src/ 同级);不存在返回空串。"""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here) if os.path.basename(here) == "src" else here
    d = os.path.join(root, "tools")
    return d if os.path.isdir(d) else ""


def _bundled_candidate_dirs():
    """内置 tools/ 下的候选 bin 目录:
    tools[:dir=根]、tools/bin、tools/mysql-*(历史版本,按目录名版本排序)。"""
    base = bundled_tools_dir()
    if not base:
        return []
    dirs = [base, os.path.join(base, "bin")]
    try:
        subs = os.listdir(base)
    except OSError:
        subs = []
    for sub in subs:
        if not re.search(r"mysql", sub, re.I):
            continue
        p = os.path.join(base, sub)
        if os.path.isdir(p):
            dirs.append(p)
            b = os.path.join(p, "bin")
            if os.path.isdir(b):
                dirs.append(b)
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def _version_key(ver):
    if not ver:
        return (-1, -1, -1)
    return (ver.get("major", -1), ver.get("minor", -1), ver.get("patch", -1))


def _dir_version_text(path):
    """候选目录的版本文本:若为 bin 子目录取父目录名(如 mysql-8.0.42/bin)。"""
    base = os.path.basename(path)
    if base.lower() == "bin":
        base = os.path.basename(os.path.dirname(path))
    return base


def find_bundled_tool(exe):
    """在内置 tools/ 内定位 exe(带目录名版本排序,取最高)。
    exe 已含扩展名("mysql.exe" / "mysqldump");找不到返回 None。"""
    best, best_key = None, (-1, -1, -1)
    for d in _bundled_candidate_dirs():
        p = os.path.join(d, exe)
        if not os.path.isfile(p):
            continue
        key = _version_key(parse_version(_dir_version_text(d)))
        if key > best_key:
            best, best_key = os.path.abspath(p), key
    return best


def bundled_tools_summary():
    """列出内置 tools/ 下所有可用 bin 目录及其版本(供引导向导提示)。"""
    ext = ".exe" if IS_WIN else ""
    probes = ("mysqldump" + ext, "mysql" + ext)
    out = []
    for d in _bundled_candidate_dirs():
        if not any(os.path.isfile(os.path.join(d, e)) for e in probes):
            continue
        v = parse_version(_dir_version_text(d))
        ver = ""
        if v:
            ver = "%d.%d.%d" % (v["major"], v["minor"], v["patch"])
        out.append({"dir": os.path.abspath(d), "version": ver})
    return out


def find_tool(tool, configured_bin=""):
    """定位客户端工具,返回绝对路径或 None。

    tool: "mysqldump" / "mysql"(不带扩展名,Windows 自动补 .exe)
    """
    exe = tool + (".exe" if IS_WIN else "")
    # 1) 用户配置:可以是目录,也可以是完整可执行文件路径
    cfg = (configured_bin or "").strip().strip('"')
    if cfg:
        if os.path.isfile(cfg) and os.path.basename(cfg).lower() == exe.lower():
            return os.path.abspath(cfg)
        p = os.path.join(cfg, exe)
        if os.path.isfile(p):
            return os.path.abspath(p)
    # 2) PATH
    w = shutil.which(exe) or shutil.which(tool)
    if w:
        return os.path.abspath(w)
    # 3) 内置 tools/ 目录(按目录版本排序取最高,远程备份兜底)
    bundled = find_bundled_tool(exe)
    if bundled:
        return bundled
    # 4) 常见目录扫描
    for d in candidate_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def parse_version(text):
    """从 `mysqldump Ver 8.0.42 ...` / `8.0.46` 文本提取主次版本。"""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return None
    return {"major": int(m.group(1)), "minor": int(m.group(2)),
            "patch": int(m.group(3) or 0),
            "text": (text or "").strip().splitlines()[0][:120]}


def tool_version(tool, configured_bin=""):
    """返回工具版本 dict(含 major/minor/text)或 None。"""
    path = find_tool(tool, configured_bin)
    if not path:
        return None
    try:
        p = subprocess.run([path, "--version"], capture_output=True, timeout=10)
        out = (p.stdout or b"").decode("utf-8", "replace") + \
              (p.stderr or b"").decode("utf-8", "replace")
        return parse_version(out)
    except Exception:
        return None


def server_version(conn_cfg):
    """连接目标服务器取版本(SELECT VERSION()),失败返回 None。"""
    try:
        import pymysql
        conn = pymysql.connect(host=conn_cfg.get("host", "127.0.0.1"),
                               port=int(conn_cfg.get("port", 3306)),
                               user=conn_cfg.get("user", "root"),
                               password=conn_cfg.get("password", ""),
                               connect_timeout=5, charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                return parse_version(cur.fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return None


def env_summary(configured_bin=""):
    """汇总环境信息,供 GET /api/setup/env 与引导向导第 1 步使用。"""
    pyv = sys.version_info
    deps = {}
    for mod in ("pymysql", "cryptography"):
        try:
            __import__(mod)
            deps[mod] = True
        except ImportError:
            deps[mod] = False
    dump_v = tool_version("mysqldump", configured_bin)
    cli_v = tool_version("mysql", configured_bin)
    dump_path = find_tool("mysqldump", configured_bin)
    cli_path = find_tool("mysql", configured_bin)
    bundled = bundled_tools_summary()

    def _dir_of(path):
        return os.path.dirname(path) if path else ""

    bundled_dir = bundled[0]["dir"] if bundled else ""

    def _src_hint(path):
        """工具来源提示:命中内置 tools/ 时标注,便于向导判断。"""
        if path and bundled_dir and os.path.samefile(os.path.dirname(path), bundled_dir):
            return f"内置工具({os.path.basename(bundled_dir)})"
        return ""

    items = [
        {"name": f"Python ({pyv.major}.{pyv.minor}.{pyv.micro})",
         "ok": pyv >= (3, 10), "tip": "需要 Python 3.10+"},
        {"name": "PyMySQL 依赖", "ok": deps["pymysql"],
         "tip": "pip install pymysql"},
        {"name": "cryptography 依赖(密码加密)", "ok": deps["cryptography"],
         "tip": "pip install cryptography"},
        {"name": "mysqldump 备份工具", "ok": bool(dump_path),
         "detail": (dump_v["text"] if dump_v else "") + (_src_hint(dump_path)),
         "tip": "未找到。可在下一步手动指定 MySQL 客户端目录(仅影响备份/还原)"},
        {"name": "mysql 还原客户端", "ok": bool(cli_path),
         "detail": (cli_v["text"] if cli_v else "") + (_src_hint(cli_path)),
         "tip": "未找到。可在下一步手动指定 MySQL 客户端目录(仅影响备份/还原)"},
    ]
    return {
        "os_desc": f"{platform.system()} {platform.release()}",
        "platform": sys.platform,
        "items": items,
        "mysql_bin_found": _dir_of(dump_path) or _dir_of(cli_path),
        "mysqldump_path": dump_path or "",
        "mysql_path": cli_path or "",
        "bundled_tools": bundled,
        "all_required_ok": all(i["ok"] for i in items[:3]),
    }


def probe_client(configured_bin):
    """验证用户填写的客户端目录/路径是否可用(实际执行 mysqldump --version)。"""
    cfg = (configured_bin or "").strip().strip('"')
    # 用户显式填了路径就不许静默回退 PATH——路径不存在直接报错(防拼写错误被掩盖)
    if cfg and not os.path.exists(cfg):
        return {"ok": False, "error": "路径不存在: %s" % cfg}
    path = find_tool("mysqldump", configured_bin)
    if not path:
        return {"ok": False,
                "error": "该目录下未找到 mysqldump,请确认为 MySQL 的 bin 目录"}
    v = tool_version("mysqldump", configured_bin)
    return {"ok": True, "path": path, "version": v["text"] if v else "",
            "dir": os.path.dirname(path)}
