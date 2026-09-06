# -*- coding: utf-8 -*-
"""MySQL 数据库安装引导（检测 / 官网版本 / 参数建议 / 免安装版部署编排）。

背景（2026-09 新增需求）: 首次部署初始化时检测本机是否已有数据库——
  - 有: 向导直接跳过，展示检测结果；
  - 无: 询问用户是否由向导代装 MySQL Server（免安装版，可静默脚本化）；
  - 用户拒绝安装时走既有链路（env_probe 检测客户端驱动/工具 → 向导内一键下载或稍后配置）。
设计对齐项目既有约定:
  - 纯标准库；探测命令缺失/超时一律降级返回，绝不抛异常打断向导或 install 脚本；
  - 后台编排 + 共享 state + 前端轮询（同 tools_downloader 模式，后续阶段交付）；
  - 下载源多镜像 fallback（官方 → 阿里云 → 清华 → cdn，同 tools_downloader._OFFICIAL）。

分阶段交付（小步迭代，每步可测）:
  阶段1 detect_local_server(): 本机数据库三分量检测（OS 服务 / mysqld 二进制 / 3306 端口）
  阶段2 build_config_suggestions() / render_my_cnf(): 参数建议与 my.ini 渲染
  阶段3 fetch_official_versions(): 官网版本获取（缓存 + 兜底清单）
  阶段4/5 install_mysql_server(): Windows ZIP / Linux tar.xz 免安装版编排
"""
import glob
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import env_probe

IS_WIN = sys.platform == "win32"

# 端口问候特征: MySQL/MariaDB 握手包为明文，首包内含版本号(如 8.0.36)与认证插件名，
# 借此区分"3306 上跑的是数据库(Docker 映射等)"与"端口被其它程序占用"。
_GREETING_RE = re.compile(r"mariadb|mysql_native_password|caching_sha2_password", re.I)
_GREETING_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


# ================= ========== 阶段1: 本机数据库检测 ========== =================

def server_candidate_dirs():
    """按平台生成 MySQL 服务端(mysqld)候选目录。

    与 env_probe.candidate_dirs(客户端)的差别: 服务端通常随 Server/集成环境安装，
    Linux 下位于 /usr/sbin(apt/yum 默认)，额外覆盖 brew(mysql@x.x)/xampp 等路径。
    """
    dirs = []
    if IS_WIN:
        pats = [
            r"C:\Program Files\MySQL\MySQL Server *\bin",
            r"C:\Program Files (x86)\MySQL\MySQL Server *\bin",
            r"C:\Program Files\MySQL\*\bin",
            r"C:\Program Files (x86)\MySQL\*\bin",
            r"C:\mysql*\bin", r"D:\mysql*\bin", r"E:\mysql*\bin",
            r"C:\phpstudy_pro\Extensions\MySQL*\bin",
            r"D:\phpstudy_pro\Extensions\MySQL*\bin",
            r"C:\xampp\mysql\bin", r"D:\xampp\mysql\bin",
            r"C:\wamp64\bin\mysql\*\bin",
        ]
        for p in pats:
            dirs.extend(glob.glob(p))
    else:
        dirs = ["/usr/sbin", "/usr/local/mysql/bin", "/usr/local/mysql/support-files",
                "/usr/local/bin", "/opt/mysql/bin", "/opt/lampp/bin",
                "/usr/local/opt/mysql/bin", "/opt/homebrew/opt/mysql/bin"]
    # dict.fromkeys 去重且保序，避免同一目录重复执行探测
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def find_server():
    """定位本机 mysqld 服务端二进制。顺序: PATH → 常见目录扫描。找不到返回空串。

    mariadbd 一并查找: MariaDB 是本工具的被管对象之一(service_manager 同样匹配 maria)，
    其服务端二进制名为 mariadbd(通常有 mysqld 兼容软链，但软链不保证存在)。
    """
    for name in ("mysqld", "mariadbd"):
        hit = shutil.which(name)
        if hit:
            return hit
    for d in server_candidate_dirs():
        for name in ("mysqld", "mariadbd"):
            exe = os.path.join(d, name)
            if os.path.isfile(exe):
                return exe
            # Windows 下 CreateProcess 依赖 PATHEXT，扫描目录必须显式补 .exe
            if IS_WIN and os.path.isfile(exe + ".exe"):
                return exe + ".exe"
    return ""


def server_bin_version(path):
    """对 mysqld 绝对路径执行 --version 解析版本；失败返回 None（权限/损坏等）。"""
    if not path:
        return None
    try:
        p = subprocess.run([path, "--version"], capture_output=True, timeout=15)
        out = (p.stdout or b"").decode("utf-8", "replace") + \
              (p.stderr or b"").decode("utf-8", "replace")
        return env_probe.parse_version(out)
    except Exception:
        return None


def probe_port(host="127.0.0.1", port=3306, timeout=1.5):
    """TCP 连通探测 + 问候语特征识别。返回 (端口是否开放, 特征)。

    特征: "mysql"=握手含 MySQL/MariaDB 特征; "unknown"=开放但不像数据库; ""=未开放。
    只读不写: 仅连接并读取服务端首包，不发送任何数据，对真实数据库零影响。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            try:
                data = s.recv(128)
            except Exception:
                data = b""
            text = data.decode("utf-8", "replace")
            if _GREETING_RE.search(text) or _GREETING_VERSION_RE.search(text):
                return True, "mysql"
            return True, "unknown"
    except Exception:
        return False, ""


def aggregate_detection(service_name, service_state, mysqld_path, version,
                        port=3306, port_open=False, port_kind=""):
    """纯聚合函数: 由三分量探测结果合成结论（独立出来便于离线单测）。

    installed 判定: 服务 / 二进制 / 端口 MySQL 握手任一命中即视为"有数据库"——
    Docker 映射等场景没有 OS 服务与可见二进制，但 3306 确实有库可连，向导应放行。
    端口开放但非 MySQL 不算（可能是其它应用占用，不能误导用户去连）。
    """
    port_is_mysql = port_open and port_kind == "mysql"
    installed = bool(service_name) or bool(mysqld_path) or port_is_mysql
    hint = ""
    if port_open:
        hint = "mysql" if port_is_mysql else "unknown"

    parts = []
    if service_name:
        parts.append(f"OS 服务 {service_name}({service_state or '状态未知'})")
    if mysqld_path:
        vtxt = (version or {}).get("text", "版本未知").splitlines()[0]
        parts.append(f"服务端 {mysqld_path}({vtxt})")
    if port_is_mysql:
        parts.append(f"端口 {port} 有 MySQL 握手特征(Docker/便携部署?)")

    if installed:
        summary = "检测到本机数据库: " + "; ".join(parts)
    elif port_open:
        summary = f"未发现本机 MySQL/MariaDB 安装；端口 {port} 被其它程序占用"
    else:
        summary = "未检测到本机 MySQL/MariaDB 数据库"
    return {
        "installed": installed,
        "service_name": service_name or "",
        "service_state": service_state or "",
        "mysqld_path": mysqld_path or "",
        "version": version,
        "port": port,
        "port_open": bool(port_open),
        "port_hint": hint,
        "summary": summary,
    }


def detect_local_server(host="127.0.0.1", port=3306):
    """检测本机是否已有 MySQL/MariaDB 数据库（向导第 1 步 / install 脚本挂钩共用）。

    任一分量命中即视为有: 1) OS 服务; 2) mysqld 二进制; 3) 3306 端口 MySQL 握手。
    每个分量独立容错——sc/systemctl 缺失、--version 超时都不影响其余分量与最终结论。
    """
    service_name, service_state = "", ""
    try:
        import service_manager
        service_name = service_manager.detect_service_name() or ""
        if service_name:
            service_state = service_manager.service_status(service_name).get("os_status", "")
    except Exception:
        pass
    mysqld, version = "", None
    try:
        mysqld = find_server()
        if mysqld:
            version = server_bin_version(mysqld)
    except Exception:
        mysqld, version = "", None
    try:
        port_open, port_kind = probe_port(host, port)
    except Exception:
        port_open, port_kind = False, ""
    return aggregate_detection(service_name, service_state, mysqld, version,
                               port, port_open, port_kind)


# ================= ========== 阶段2: 参数建议引擎(纯函数,零 I/O) ========== =================

_GB = 1024 ** 3
_MB = 1024 ** 2


def _human_mem(n):
    """字节数 → my.ini 可接受的档位化文本。

    档位化的原因: 避免建议值形如 536870913 这种原始字节数,对齐常见硬件档位更可读。
    规则: 整数 G(如恰好 1G/4G)按 G 展示;≥2G 向下取整 G;其余对齐 128M。
    """
    if n >= _GB and n % _GB == 0:
        return "%dG" % (n // _GB)
    if n >= 2 * _GB:
        return "%dG" % (n // _GB)
    return "%dM" % (max(n // (128 * _MB), 1) * 128)


def _to_bytes(text):
    """'512M'/'1G'/'3306' → 字节数。解析失败返回 None(渲染旧版 redo 参数时折半用)。"""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([KMG]?)B?\s*$", str(text), re.I)
    if not m:
        return None
    mul = {"": 1, "K": 1024, "M": _MB, "G": _GB}[m.group(2).upper()]
    return int(float(m.group(1)) * mul)


def suggest_buffer_pool(mem_total_bytes, co_exist=False):
    """innodb_buffer_pool_size 建议: 专用机给大值,与其它服务共存时收敛到 25%。

    内存未知/异常时保守返回 MySQL 默认 128M——建议引擎的原则是宁小勿爆,
    用户可在向导中手动改大。
    """
    if not mem_total_bytes or mem_total_bytes <= 0:
        return "128M"
    if mem_total_bytes <= 2 * _GB:
        return "128M"          # 小内存机: 128M 已占 ~6%,再大会挤压系统
    if co_exist or mem_total_bytes <= 4 * _GB:
        share = 0.25
    elif mem_total_bytes <= 16 * _GB:
        share = 0.50
    else:
        share = 0.60
    raw = min(int(mem_total_bytes * share), 32 * _GB)  # >32G 单实例收益递减,封顶
    return _human_mem(raw)


def suggest_max_connections(mem_total_bytes):
    """max_connections 分档: 每连接常驻约 256K~10M 内存,按内存档位给安全上限。"""
    if not mem_total_bytes or mem_total_bytes <= 0:
        return 100
    if mem_total_bytes <= 2 * _GB:
        return 50
    if mem_total_bytes <= 4 * _GB:
        return 100
    if mem_total_bytes <= 8 * _GB:
        return 200
    return 300


def default_collation(version_major):
    """排序规则默认值随大版本: utf8mb4_0900_ai_ci 仅 8.0+ 存在,5.7 会启动报错。"""
    return "utf8mb4_0900_ai_ci" if (version_major or 8) >= 8 else "utf8mb4_unicode_ci"


def build_config_suggestions(mem_total_bytes=None, cpu_cores=None,
                             co_exist=False, version_major=8):
    """汇总建议值(向导预填起点,全部允许用户覆盖)。

    version_major 决定排序规则默认值(8.0 与 5.7 的 utf8mb4 排序规则集合不同);
    redo 容量统一建议 1G,渲染时再按具体小版本折算成新旧参数名。
    """
    return {
        "character_set_server": "utf8mb4",
        "collation_server": default_collation(version_major),
        "innodb_buffer_pool_size": suggest_buffer_pool(mem_total_bytes, co_exist),
        "max_connections": suggest_max_connections(mem_total_bytes),
        "innodb_redo_log_capacity": "1G",
        "max_allowed_packet": "64M",
        "port": 3306,
        "mem_total_bytes": mem_total_bytes,
        "cpu_cores": cpu_cores,
        "notes": {
            "innodb_buffer_pool_size":
                "内存≤2G 保守 128M；2-4G 取 25%；4-16G 取 50%；>16G 取 60%(封顶 32G)。"
                "勾选「本机还跑其它服务」按 25% 收敛",
            "max_connections": "按内存档位 50/100/200/300,防连接数耗尽内存",
            "character_set_server": "utf8mb4 完整支持 Emoji 与生僻字,业界默认",
            "collation_server": "8.0+ 默认 utf8mb4_0900_ai_ci；5.7 用 utf8mb4_unicode_ci",
            "innodb_redo_log_capacity": "1G 兼顾写入吞吐与崩溃恢复时长",
        },
    }


def render_my_cnf(cfg, version=(8, 0, 36)):
    """渲染 my.ini/my.cnf 文本(两平台内容一致,路径分隔符由调用方按平台传入)。

    版本差异必须在 --initialize 前写对(8.0 初始化后不可再改):
    - 8.0.30+ 用 innodb_redo_log_capacity；旧版(8.0.29- 与 5.7)用 innodb_log_file_size
      + innodb_log_files_in_group=2,容量折半(旧参数是"每组单文件大小")
    - collation_server 留空/"auto" 时按大版本给默认值
    """
    v = tuple(version or (8, 0, 36)) + (0, 0, 0)
    major, minor, patch = v[0], v[1], v[2]

    def g(key, default=""):
        return str(cfg.get(key, default) if cfg.get(key) is not None else default)

    collation = g("collation_server").strip()
    if collation.lower() in ("", "auto"):
        collation = default_collation(major)

    lines = [
        "[mysqld]",
        "# 由 MySQL Console 安装引导生成,可按需修改",
        "basedir=" + os.path.normpath(g("basedir")),
        "datadir=" + os.path.normpath(g("datadir")),
        "port=%d" % int(g("port", "3306") or 3306),
        "character_set_server=" + (g("character_set_server") or "utf8mb4"),
        "collation_server=" + collation,
        "innodb_buffer_pool_size=" + (g("innodb_buffer_pool_size") or "128M"),
        "max_connections=%d" % int(g("max_connections", "100") or 100),
        "max_allowed_packet=" + (g("max_allowed_packet") or "64M"),
    ]
    cap = g("innodb_redo_log_capacity") or "1G"
    if (major, minor, patch) >= (8, 0, 30):
        lines.append("innodb_redo_log_capacity=" + cap)
    else:
        half = max((_to_bytes(cap) or _GB) // 2, 128 * _MB)
        lines.append("innodb_log_file_size=" + _human_mem(half))
        lines.append("innodb_log_files_in_group=2")
    lctn = cfg.get("lower_case_table_names")
    if lctn is not None:
        # 8.0 只在初始化时读取此值,之后改 my.ini 无效——必须在 initialize 前写对
        lines.append("lower_case_table_names=%d" % int(lctn))
    # X 协议端口(33060)常被忽略却常引发启动失败,免安装引导场景直接关闭
    lines.append("mysqlx=OFF")
    return "\n".join(lines) + "\n"


def validate_install_paths(basedir, datadir, is_win=None):
    """安装路径预检。返回 (errors, warnings): errors 非空时安装编排必须中止。"""
    is_win = IS_WIN if is_win is None else is_win
    errors, warnings = [], []
    basedir = (basedir or "").strip().strip('"')
    datadir = (datadir or "").strip().strip('"')
    if not basedir:
        errors.append("安装路径不能为空")
    if not datadir:
        errors.append("数据存储路径不能为空")
    for name, p in (("安装路径", basedir), ("数据存储路径", datadir)):
        if not p:
            continue
        if is_win and not re.match(r"^[A-Za-z]:\\", p):
            errors.append(f"{name}需为盘符开头的 Windows 绝对路径(如 D:\\mysql): {p}")
        if not is_win and not p.startswith("/"):
            errors.append(f"{name}需为绝对路径(如 /opt/mysql): {p}")
        # MySQL 5.7 的服务端对非 ASCII 路径兼容差,提前给出可读提示
        if is_win and re.search(r"[^\x00-\x7f]", p):
            warnings.append(f"{name}含非 ASCII 字符,建议使用纯英文路径: {p}")
    if datadir and os.path.isdir(datadir):
        try:
            if os.listdir(datadir):
                warnings.append(f"数据存储路径非空({datadir}),初始化前将要求确认")
        except Exception:
            warnings.append(f"数据存储路径不可访问({datadir}),请检查权限")
    return errors, warnings


# ================= ========== 阶段3: 官网版本获取(缓存 + 多级兜底) ========== =================
# 版本枚举策略(2026-09 实测验证):
#   1) endoflife.date 官方数据聚合 API(纯 JSON,含 LTS 标记,同步 Oracle release);
#   2) 枚举出的每个版本在 Oracle 官方 CDN(cdn.mysql.com)做 HEAD 验证,
#      只列出直链确实存在的版本——dev.mysql.com 下载页是 JS 渲染无法纯 HTML 解析,
#      且官方会把 EOL 版本从 CDN 下架,盲目列版本会给出死链;
#   3) 全部失败时回退到内置清单(下列 URL 均经实测 200)。
# 下载镜像回退(阿里云/清华/dev.get 跳转)在阶段 4/5 的下载编排里做,此处只管"有哪些版本"。
# 注意: 清华镜像站已改为 JS 渲染、阿里云 mysql 目录同步停更于 8.0.28,均不可作为枚举源。

_EOL_API = "https://endoflife.date/api/mysql.json"
_GLIBC_CANDIDATES = ("glibc2.28", "glibc2.17")   # 8.x 双标签并存,9.x 起仅 2.28
_MAX_ENUMERATED = 6                              # 向导下拉不需要全部历史版本
_VERSIONS_CACHE_TTL = 24 * 3600

_FALLBACK_VERSIONS = [
    {"version": "9.7.2", "lts": True,
     "win_url": "https://cdn.mysql.com/Downloads/MySQL-9.7/mysql-9.7.2-winx64.zip",
     "linux_url": "https://cdn.mysql.com/Downloads/MySQL-9.7/mysql-9.7.2-linux-glibc2.28-x86_64.tar.xz"},
    {"version": "8.0.45", "lts": False,
     "win_url": "https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-8.0.45-winx64.zip",
     "linux_url": "https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-8.0.45-linux-glibc2.28-x86_64.tar.xz"},
]


def _versions_cache_path():
    import paths
    return os.path.join(paths.DATA_DIR, "mysql_versions_cache.json")


def _cdn_urls(version):
    """按版本号拼 Oracle 官方 CDN 直链: (win zip, [linux tar.xz 候选])。"""
    majmin = ".".join(version.split(".")[:2])
    base = "https://cdn.mysql.com/Downloads/MySQL-%s/mysql-%s" % (majmin, version)
    return base + "-winx64.zip", \
        [base + "-linux-%s-x86_64.tar.xz" % g for g in _GLIBC_CANDIDATES]


def _probe_url(url, timeout=8):
    """HEAD 探测直链是否真的存在(官方会把 EOL 版本从 CDN 下架)。任何异常视为不存在。"""
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "mysql-console"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:
        return False


def _fetch_official_versions(timeout=8):
    """枚举官方在架版本: endoflife API 取版本号 → cdn 逐个 HEAD 验证。

    win 直链是版本"在架"的判据(不存在即被下架,整个版本跳过);
    linux 直链按 glibc 标签候选探测,都缺失时留空(仍可列出,仅提示不支持 linux)。
    """
    import json as _json
    import urllib.request
    req = urllib.request.Request(_EOL_API, headers={"User-Agent": "mysql-console"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = _json.loads(r.read().decode("utf-8"))
    out = []
    for entry in data:
        if len(out) >= _MAX_ENUMERATED:
            break
        ver = str(entry.get("latest") or "").strip()
        if not re.match(r"^\d+\.\d+\.\d+$", ver):
            continue
        win_url, linux_urls = _cdn_urls(ver)
        if not _probe_url(win_url, timeout):
            continue
        linux_url = ""
        for u in linux_urls:
            if _probe_url(u, timeout):
                linux_url = u
                break
        out.append({"version": ver, "lts": bool(entry.get("lts")),
                    "release_date": entry.get("latestReleaseDate") or "",
                    "win_url": win_url, "linux_url": linux_url,
                    "source": "official-api"})
    if not out:
        raise RuntimeError("官方版本枚举为空")
    return _sort_versions(out)


def _sort_versions(versions):
    """LTS 优先,其余按版本号新→旧(向导下拉的展示顺序)。"""
    def key(v):
        t = tuple(int(x) for x in str(v.get("version", "0")).split(".")[:3])
        return (0 if v.get("lts") else 1, -t[0], -t[1], -t[2])
    return sorted(versions, key=key)


def _load_versions_cache():
    """读版本缓存,命中 TTL 返回条目,过期/损坏/不存在返回 None(静默降级重新拉取)。"""
    p = _versions_cache_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            entry = json_loads(f.read())
        if time.time() - float(entry.get("ts", 0)) <= _VERSIONS_CACHE_TTL \
                and entry.get("versions"):
            return entry
    except Exception:
        pass
    return None


def json_loads(text):
    """局部 json 解析(独立小函数,便于测试桩替换;标准库转发)。"""
    import json as _json
    return _json.loads(text)


def _save_versions_cache(versions):
    """原子写版本缓存(tmp + rename,防半截文件)。失败静默——缓存不是关键路径。"""
    import tempfile
    try:
        p = _versions_cache_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json_dumps({"ts": time.time(), "versions": versions}))
            os.replace(tmp, p)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
    except Exception:
        pass


def json_dumps(obj):
    """局部 json 序列化(标准库转发,ensure_ascii=False 保中文可读)。"""
    import json as _json
    return _json.dumps(obj, ensure_ascii=False)


def list_official_versions(force=False):
    """对外入口: MySQL Server 可安装版本列表。

    优先级: 24h 内缓存 → 官方 API + CDN 验证 → 内置兜底清单。
    永不抛异常、永不返回空列表——向导下拉框任何情况下都有的选。
    """
    import time as _time
    if not force:
        cache = _load_versions_cache()
        if cache:
            return {"source": "cache", "cached_at": cache.get("ts", 0),
                    "versions": cache["versions"]}
    try:
        versions = _fetch_official_versions()
        _save_versions_cache(versions)
        return {"source": "official-api", "cached_at": _time.time(),
                "versions": versions}
    except Exception:
        versions = [dict(v, source="builtin") for v in _FALLBACK_VERSIONS]
        return {"source": "builtin", "cached_at": 0,
                "versions": _sort_versions(versions)}


# ============ ========== 阶段4/5: 免安装版部署编排(Windows ZIP / Linux tar.xz) ========== ============
# 流水线: 校验 → 下载(多镜像) → 解压(防 zip-slip) → 生成 my.ini → --initialize-insecure
#         → 进程方式启动 → 设 root 密码并回连验证 → 可选注册系统服务(开机自启,默认勾选)。
# 设计约束: 后台线程 + 共享 state + 前端轮询(同 tools_downloader);
#          失败信息必须可读可行动;破坏性操作(非空数据目录)必须显式 force 才放行。

_INSTALL_MIN_BYTES = 50 * _MB      # 安装包完整性下限(小于此必是错误页/半截文件)
_CREATE_NO_WINDOW = 0x08000000     # Windows: 防止 mysqld 弹出控制台窗口


class _InstallAbort(Exception):
    """编排中受控失败: 携带面向用户的中文原因,直接写入 state.error。"""


def snapshot_install(state, lock):
    """安装状态快照(供 /api/setup/install-mysql/status 轮询)。"""
    with lock:
        return dict(state)


def _report(state, lock, cfg, percent=None, phase=None, msg=None):
    """统一进度上报;CLI 模式附带打印(供 --install-json 真机验证)。"""
    kw = {}
    if percent is not None:
        kw["percent"] = percent
    if phase is not None:
        kw["phase"] = phase
    if msg is not None:
        kw["msg"] = msg
    with lock:
        state.update(kw)
        state["ts"] = time.time()
    if cfg.get("cli"):
        try:
            print("[%3d%%][%s] %s" % (state.get("percent", 0),
                                      kw.get("phase") or state.get("phase", ""),
                                      msg or ""), flush=True)
        except Exception:
            pass


def _cmd_tail(result, limit=600):
    """子进程输出尾部(初始化/服务命令失败时给用户可行动的信息)。"""
    out = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
    return out.strip()[-limit:]


def _validate_install_cfg(cfg):
    """安装前置校验(下载前快速失败)。返回 (version, basedir, datadir, port, password)。"""
    ver = str(cfg.get("version") or "").strip()
    if not re.match(r"^\d+\.\d+\.\d+$", ver):
        raise _InstallAbort("版本号缺失或格式不正确(应为 x.y.z)")
    basedir = str(cfg.get("basedir") or "").strip().strip('"')
    datadir = str(cfg.get("datadir") or "").strip().strip('"')
    errors, _ = validate_install_paths(basedir, datadir)
    if errors:
        raise _InstallAbort("；".join(errors))
    if os.path.normpath(basedir).lower() == os.path.normpath(datadir).lower():
        raise _InstallAbort("数据存储路径不能与安装路径相同")
    password = str(cfg.get("root_password") or "")
    if len(password) < 6:
        raise _InstallAbort("root 密码至少 6 位")
    try:
        port = int(cfg.get("port") or 3306)
    except (TypeError, ValueError):
        raise _InstallAbort("端口必须是数字")
    if not (1 <= port <= 65535):
        raise _InstallAbort("端口超出范围(1-65535)")
    # 破坏性操作双确认: 非空目录必须显式 force(API 层由前端确认框触发),绝不静默覆盖
    if os.path.isdir(datadir) and os.listdir(datadir) and not cfg.get("force"):
        raise _InstallAbort(f"数据存储路径非空({datadir})。确认其中的数据可以被清空后,请在向导中勾选「我确认覆盖」再重试")
    if os.path.isdir(basedir) and os.listdir(basedir) and not cfg.get("force"):
        raise _InstallAbort(f"安装路径已存在且非空({basedir})。请更换路径,或在确认可覆盖后勾选「我确认覆盖」")
    open_kind = probe_port("127.0.0.1", port, timeout=1.0)[1]
    if open_kind:
        raise _InstallAbort(f"端口 {port} 已被占用(可能有数据库正在运行)。请更换端口或先停止占用程序")
    return ver, basedir, datadir, port, password


def _download_mirrors(ver, is_win):
    """按平台生成多镜像下载列表(cdn 官方 → dev.get 跳转 → 阿里云)。
    返回 (urls, 归档扩展名)。linux 需先解析 glibc 标签。"""
    majmin = ".".join(ver.split(".")[:2])
    if is_win:
        fname = "mysql-%s-winx64.zip" % ver
        return ([_CDN_FMT.format(majmin=majmin, fname=fname),
                 "https://dev.mysql.com/get/Downloads/MySQL-%s/%s" % (majmin, fname),
                 "https://mirrors.aliyun.com/mysql/MySQL-%s/%s" % (majmin, fname)], ".zip")
    linux_url = _resolve_linux_url(ver)
    if not linux_url:
        raise _InstallAbort("未找到该版本的 Linux 二进制(glibc2.28/2.17 均不在架),请更换版本")
    fname = linux_url.rsplit("/", 1)[-1]
    return ([linux_url,
             "https://dev.mysql.com/get/Downloads/MySQL-%s/%s" % (majmin, fname),
             "https://mirrors.aliyun.com/mysql/MySQL-%s/%s" % (majmin, fname)],
            ".tar.xz" if fname.endswith(".tar.xz") else ".tar.gz")


_CDN_FMT = "https://cdn.mysql.com/Downloads/MySQL-{majmin}/{fname}"


def _resolve_linux_url(ver):
    """解析 linux tar.xz 直链(版本不同 glibc 标签不同,只能探测确定)。"""
    majmin = ".".join(ver.split(".")[:2])
    for g in _GLIBC_CANDIDATES:
        u = _CDN_FMT.format(majmin=majmin, fname="mysql-%s-linux-%s-x86_64.tar.xz" % (ver, g))
        if _probe_url(u, timeout=10):
            return u
    return ""


def _download_package(urls, dest, state, lock, cfg):
    """多镜像流式下载。单源失败(含内容过小=错误页)自动换下一源;全部失败才中止。"""
    import urllib.request
    last_err = None
    for url in urls:
        try:
            host = re.sub(r"^https?://", "", url).split("/")[0]
            _report(state, lock, cfg, percent=5, phase="download", msg="下载源: " + host)
            req = urllib.request.Request(url, headers={"User-Agent": "mysql-console"})
            done = 0
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                next_report = 0
                while True:
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if done >= next_report:
                        next_report = done + 32 * _MB
                        pct = int(5 + 60 * done / total) if total else None
                        _report(state, lock, cfg, percent=pct, phase="download",
                                msg="已下载 %dMB%s" % (done // _MB,
                                                       "/%dMB" % (total // _MB) if total else ""))
            if done < _INSTALL_MIN_BYTES:
                raise IOError("下载内容仅 %dB,疑似错误页" % done)
            return dest
        except Exception as e:
            last_err = e
            try:
                os.unlink(dest)
            except OSError:
                pass
    raise _InstallAbort("所有下载源均失败: %s" % last_err)


def _safe_zip_extract(archive, staging):
    """zip 解压(逐条校验防 zip-slip: 绝对路径/.. 穿越直接中止)。"""
    import zipfile
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise _InstallAbort("压缩包内发现不安全路径,已中止: %s" % info.filename)
        z.extractall(staging)


def _safe_tar_extract(archive, staging):
    """tar.xz 解压: 逐成员预校验 + 官方 data 过滤器双保险。

    链接规则: MySQL 官方包 lib/ 内合法存在相对符号链接(如 libmysqlclient.so),
    故允许"目标仍落在解压目录内"的链接;绝对路径或逃逸出目录的链接一律中止。
    """
    import tarfile
    staging_real = os.path.realpath(staging)

    def _inside(p):
        rp = os.path.realpath(p)
        return rp == staging_real or rp.startswith(staging_real + os.sep)

    with tarfile.open(archive, "r:*") as t:
        for m in t.getmembers():
            name = m.name.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise _InstallAbort("压缩包内发现不安全条目,已中止: %s" % m.name)
            if m.issym() or m.islnk():
                link = (m.linkname or "").replace("\\", "/")
                # 硬链接目标是相对包根的;符号链接目标是相对成员所在目录的
                base = "" if m.islnk() else os.path.dirname(name)
                if link.startswith("/") or not _inside(
                        os.path.normpath(os.path.join(staging, base, link))):
                    raise _InstallAbort(
                        "压缩包内发现指向外部的链接,已中止: %s -> %s" % (m.name, m.linkname))
        try:
            t.extractall(staging, filter="data")
        except TypeError:
            t.extractall(staging)   # 3.12 以下无过滤器,预校验已兜底


def _wait_port(port, timeout_s, probe_gap=1.0):
    """轮询等待端口就绪(mysqld 启动通常 5-30s,首次初始化后较慢)。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if probe_port("127.0.0.1", port, timeout=1.5)[0]:
            return True
        time.sleep(probe_gap)
    return False


def _wait_port_closed(port, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not probe_port("127.0.0.1", port, timeout=1.0)[0]:
            return True
        time.sleep(0.5)
    return False


def _set_root_password(port, password):
    """设置 root 密码并回连验证(initialize-insecure 后 root 无密码,窗口期极短)。"""
    import pymysql
    conn = pymysql.connect(host="127.0.0.1", port=int(port), user="root",
                           password="", connect_timeout=5, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            # ALTER USER 自 5.7.6 起支持,覆盖所有可安装版本,无需按版本分支
            cur.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (password,))
        conn.commit()
    finally:
        conn.close()
    conn = pymysql.connect(host="127.0.0.1", port=int(port), user="root",
                           password=password, connect_timeout=5, charset="utf8mb4",
                           database="mysql")
    conn.close()


def _win_is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _register_service(is_win, cfg, state, lock, basedir, ini, mysqld, proc, port, ver, datadir):
    """可选步骤: 注册系统服务实现开机自启(需求确认项,默认勾选)。
    权限不足时优雅降级为进程方式并给出可读提示。返回 warnings 列表。"""
    warnings = []
    svc = str(cfg.get("service_name") or "").strip() \
        or "MySQLConsole_" + "_".join(ver.split(".")[:2])
    # 先停进程实例,避免服务启动时端口冲突
    try:
        proc.terminate()
        proc.wait(timeout=20)
    except Exception:
        pass
    _wait_port_closed(port, 30)
    if is_win:
        if not _win_is_admin():
            return ["未以管理员身份运行,无法注册 Windows 服务;数据库保持进程方式(重启后需手动启动 mysqld)"]
        _report(state, lock, cfg, percent=96, phase="service", msg="注册并启动 Windows 服务: " + svc)
        binpath = '"%s" --defaults-file="%s" %s' % (mysqld, ini, svc)
        for args, what in ((["sc", "create", svc, "binPath=", binpath, "start=", "auto"],
                            "注册"), (["sc", "start", svc], "启动")):
            r = subprocess.run(args, capture_output=True, timeout=60)
            if r.returncode != 0:
                return ["Windows 服务%s失败(sc %s): %s" % (what, what, _cmd_tail(r, 300))]
        if not _wait_port(port, 60):
            return ["服务 %s 已注册但端口未就绪,请到 Windows 服务管理器检查" % svc]
        warnings.append("已注册 Windows 服务 %s 并设为开机自启" % svc)
        return warnings
    # Linux: systemd(需 root;服务以数据目录属主运行,避免以 root 跑 mysqld)
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        return ["注册 systemd 服务需要 root 权限;数据库保持进程方式(重启后需手动启动)"]
    _report(state, lock, cfg, percent=96, phase="service", msg="注册 systemd 服务: mysql-console-db")
    import pwd as _pwd
    try:
        run_user = _pwd.getpwuid(os.stat(datadir).st_uid).pw_name
    except Exception:
        run_user = "root"
    unit = ("\n[Unit]\nDescription=MySQL Server (managed by MySQL Console)\n"
            "After=network.target\n\n[Service]\nType=simple\n"
            "User=%s\nExecStart=%s --defaults-file=%s\nRestart=on-failure\n"
            "LimitNOFILE=65535\n\n[Install]\nWantedBy=multi-user.target\n"
            % (run_user, mysqld, ini))
    unit_path = "/etc/systemd/system/mysql-console-db.service"
    try:
        with open(unit_path, "w", encoding="utf-8") as f:
            f.write(unit)
        for args in (["systemctl", "daemon-reload"],
                     ["systemctl", "enable", "--now", "mysql-console-db"]):
            r = subprocess.run(args, capture_output=True, timeout=60)
            if r.returncode != 0:
                return ["systemd 注册失败: %s" % _cmd_tail(r, 300)]
    except Exception as e:
        return ["systemd 注册失败: %s" % e]
    if not _wait_port(port, 60):
        return ["服务已注册但端口未就绪,请执行 journalctl -u mysql-console-db 查看"]
    warnings.append("已注册 systemd 服务 mysql-console-db 并设为开机自启")
    return warnings


def _install_worker(cfg, state, lock):
    """安装编排主流程(后台线程内运行)。任何失败都以可读信息落入 state。"""
    try:
        ver, basedir, datadir, port, password = _validate_install_cfg(cfg)
        is_win = IS_WIN
        version_tuple = tuple(int(x) for x in ver.split("."))
        _report(state, lock, cfg, percent=2, phase="prepare", msg="校验通过,准备下载 MySQL " + ver)

        # 1) 下载(多镜像;url_override 仅 CLI 调试通道,API handler 不透传该键)
        override = str(cfg.get("url_override") or "").strip()
        if override:
            urls, ext = [override], ".tar.xz" if ".tar." in override else ".zip"
        else:
            urls, ext = _download_mirrors(ver, is_win)
        archive = os.path.join(tempfile.gettempdir(),
                               "mc-mysql-%s-%d%s" % (ver, os.getpid(), ext))
        try:
            _download_package(urls, archive, state, lock, cfg)

            # 2) 解压到同级 staging(同卷保证 rename 原子),再就位 basedir
            _report(state, lock, cfg, percent=70, phase="extract", msg="解压中(约 1-3 分钟)...")
            staging = basedir + ".staging"
            shutil.rmtree(staging, ignore_errors=True)
            os.makedirs(os.path.dirname(basedir) or os.sep, exist_ok=True)
            os.makedirs(staging, exist_ok=True)
            (_safe_zip_extract if ext == ".zip" else _safe_tar_extract)(archive, staging)
            try:
                os.unlink(archive)
            except OSError:
                pass
            entries = os.listdir(staging)
            if len(entries) != 1 or not os.path.isdir(os.path.join(staging, entries[0])):
                raise _InstallAbort("压缩包顶层结构异常(应只含一个 mysql-* 目录),文件可能不完整")
            if os.path.exists(basedir):
                if os.listdir(basedir):
                    raise _InstallAbort("安装路径在解压阶段出现非空内容,已中止")
                os.rmdir(basedir)
            shutil.move(os.path.join(staging, entries[0]), basedir)
            shutil.rmtree(staging, ignore_errors=True)

            # 3) 生成配置(lower_case_table_names 必须在 initialize 前写对,8.0 之后不可改)
            _report(state, lock, cfg, percent=78, phase="configure", msg="生成配置文件")
            os.makedirs(datadir, exist_ok=True)
            ini = os.path.join(basedir, "my.ini" if is_win else "my.cnf")
            rcfg = {k: cfg.get(k) for k in
                    ("character_set_server", "collation_server", "innodb_buffer_pool_size",
                     "max_connections", "innodb_redo_log_capacity", "max_allowed_packet",
                     "lower_case_table_names")}
            rcfg.update({"basedir": basedir, "datadir": datadir, "port": port,
                         "lower_case_table_names":
                             rcfg.get("lower_case_table_names") if is_win else None})
            with open(ini, "w", encoding="utf-8") as f:
                f.write(render_my_cnf(rcfg, version_tuple))

            # 4) 初始化数据目录(无密码 root,稍后立即设密码)
            mysqld = os.path.join(basedir, "bin", "mysqld" + (".exe" if is_win else ""))
            if not os.path.isfile(mysqld):
                raise _InstallAbort("bin/mysqld 不存在,安装包可能不完整")
            _report(state, lock, cfg, percent=82, phase="initialize",
                    msg="初始化数据目录(--initialize-insecure)...")
            cmd = [mysqld, "--defaults-file=" + ini, "--initialize-insecure"]
            if is_win:
                cmd.append("--console")   # 让初始化日志走 stderr,失败时能给出原因
            r = subprocess.run(cmd, capture_output=True, timeout=900)
            if r.returncode != 0:
                raise _InstallAbort("数据目录初始化失败: %s" % _cmd_tail(r))

            # 5) 进程方式启动并等端口就绪
            _report(state, lock, cfg, percent=88, phase="start", msg="启动 mysqld...")
            popen_kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if is_win:
                popen_kw["creationflags"] = _CREATE_NO_WINDOW
            else:
                popen_kw["start_new_session"] = True   # 脱离父进程会话,控制台退出不影响
            args = [mysqld, "--defaults-file=" + ini]
            if not is_win and hasattr(os, "geteuid") and os.geteuid() == 0:
                import pwd as _pwd
                args += ["--user", _pwd.getpwuid(os.geteuid()).pw_name]
            proc = subprocess.Popen(args, **popen_kw)
            if not _wait_port(port, 90):
                raise _InstallAbort("启动后 90 秒端口仍未就绪,请查看数据目录下 *.err 日志")

            # 6) 设密码 + 回连验证
            _report(state, lock, cfg, percent=94, phase="finalize", msg="设置 root 密码并验证连接...")
            _set_root_password(port, password)

            warnings = []
            if cfg.get("install_service"):
                warnings += _register_service(is_win, cfg, state, lock, basedir, ini,
                                              mysqld, proc, port, ver, datadir)
            else:
                warnings.append("未注册系统服务: 重启计算机后数据库不会自动启动")
            with lock:
                state.update({"status": "done", "percent": 100, "phase": "done",
                              "msg": "MySQL %s 安装完成" % ver, "warnings": warnings,
                              "conn": {"host": "127.0.0.1", "port": port},
                              "ts": time.time()})
            if cfg.get("cli"):
                for w in warnings:
                    print("提示: " + w, flush=True)
        finally:
            try:
                os.unlink(archive)
            except OSError:
                pass
    except _InstallAbort as e:
        with lock:
            state.update({"status": "failed", "error": str(e), "msg": "安装失败",
                          "ts": time.time()})
    except Exception as e:   # 意外异常也要给前端可读状态,绝不让轮询端悬空
        with lock:
            state.update({"status": "failed",
                          "error": "%s: %s" % (type(e).__name__, e),
                          "msg": "安装失败(意外错误)", "ts": time.time()})


def start_install(cfg, state, lock):
    """启动后台安装线程(同 tools_downloader.start_download 模式)。"""
    th = threading.Thread(target=_install_worker, args=(dict(cfg or {}), state, lock),
                          daemon=True)
    th.start()
    return th


def format_install_report(state):
    """CLI 安装结果报告(--install-json 用)。"""
    lines = ["== MySQL 安装结果 ==",
             "状态: %s" % state.get("status"),
             "阶段: %s (%s%%)" % (state.get("phase"), state.get("percent", 0)),
             "信息: %s" % state.get("msg", "")]
    if state.get("error"):
        lines.append("错误: %s" % state["error"])
    for w in state.get("warnings") or []:
        lines.append("提示: %s" % w)
    if state.get("conn"):
        lines.append("连接: 127.0.0.1:%d" % state["conn"].get("port", 3306))
    return "\n".join(lines)


# ================= ========== CLI 入口（install 脚本挂钩） ========== =================

def format_detect_report(res):
    """把检测结果渲染成 CLI 可读文本（install 脚本第 5 步输出用）。"""
    lines = ["== 本机数据库检测 ==",
             f"结论: {res.get('summary', '')}"]
    if res.get("service_name"):
        lines.append(f"  OS 服务: {res['service_name']} ({res.get('service_state') or '状态未知'})")
    if res.get("mysqld_path"):
        vt = (res.get("version") or {}).get("text", "版本未知")
        lines.append(f"  服务端: {res['mysqld_path']} ({vt})")
    if res.get("port_open"):
        tag = "（MySQL 握手特征）" if res.get("port_hint") == "mysql" else "（非 MySQL 程序）"
        lines.append(f"  端口 {res.get('port', 3306)}: 开放{tag}")
    else:
        lines.append(f"  端口 {res.get('port', 3306)}: 未开放")
    if not res.get("installed"):
        lines.append("  提示: 启动服务后，首次向导可引导安装本机数据库或配置远程连接。")
    return "\n".join(lines)


def main(argv=None):
    """CLI 入口:
    --detect-only            快速检测本机数据库(install 脚本第 5 步用)
    --install-json <file>    按 JSON 配置执行完整安装(CLI 调试/真机验证通道,
                             支持 url_override 指定本地包;Web 走 API,不透传该键)

    退出码: 0=成功(检测到/安装完成), 1=失败/未检测到, 2=参数错误。
    检测/安装自身异常只打印不抛出——install 必须能继续装完。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--detect-only" in argv:
        try:
            res = detect_local_server()
        except Exception as e:
            print(f"检测失败（不影响安装继续）: {e}")
            return 1
        print(format_detect_report(res))
        return 0 if res.get("installed") else 1
    if "--install-json" in argv:
        i = argv.index("--install-json")
        if i + 1 >= len(argv):
            print("缺少参数: --install-json <配置文件路径>")
            return 2
        try:
            with open(argv[i + 1], "r", encoding="utf-8") as f:
                cfg = json_loads(f.read())
        except Exception as e:
            print(f"读取配置失败: {e}")
            return 1
        cfg["cli"] = True
        state = {"status": "idle", "phase": "", "percent": 0, "msg": "", "error": ""}
        lock = threading.Lock()
        start_install(cfg, state, lock).join()
        print(format_install_report(state))
        return 0 if state.get("status") == "done" else 1
    print("用法: python src/mysql_installer.py --detect-only\n"
          "      python src/mysql_installer.py --install-json <配置文件>")
    return 2


if __name__ == "__main__":
    sys.exit(main())
