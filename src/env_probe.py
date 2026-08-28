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
    # 3) 常见目录扫描
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

    def _dir_of(path):
        return os.path.dirname(path) if path else ""

    items = [
        {"name": f"Python ({pyv.major}.{pyv.minor}.{pyv.micro})",
         "ok": pyv >= (3, 10), "tip": "需要 Python 3.10+"},
        {"name": "PyMySQL 依赖", "ok": deps["pymysql"],
         "tip": "pip install pymysql"},
        {"name": "cryptography 依赖(密码加密)", "ok": deps["cryptography"],
         "tip": "pip install cryptography"},
        {"name": "mysqldump 备份工具", "ok": bool(dump_path),
         "detail": dump_v["text"] if dump_v else "",
         "tip": "未找到。可在下一步手动指定 MySQL 客户端目录(仅影响备份/还原)"},
        {"name": "mysql 还原客户端", "ok": bool(cli_path),
         "detail": cli_v["text"] if cli_v else "",
         "tip": "未找到。可在下一步手动指定 MySQL 客户端目录(仅影响备份/还原)"},
    ]
    return {
        "os_desc": f"{platform.system()} {platform.release()}",
        "platform": sys.platform,
        "items": items,
        "mysql_bin_found": _dir_of(dump_path) or _dir_of(cli_path),
        "mysqldump_path": dump_path or "",
        "mysql_path": cli_path or "",
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
