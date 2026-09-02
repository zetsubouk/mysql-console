# -*- coding: utf-8 -*-
"""备份/还原引擎:调用 mysqldump / mysql 官方 CLI。
- 支持异步任务(任务管理器)+ 进度回调(备份按表、还原按字节)
- 备份输出到指定目录,支持 gzip 流式压缩
- 历史记录 + 操作日志
"""
import contextlib
import gzip
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid

import pymysql

import local_store
from config_store import get_settings, _is_full_mode, _get_backend
import env_probe
import ssh_tunnel

import paths

# 数据根目录:默认部署根 data/(见 paths.py);环境变量 MC_DATA_DIR 可覆盖。
_DATA_ROOT = paths.DATA_DIR
HISTORY_PATH = os.path.join(_DATA_ROOT, "backup_history.json")
LOG_PATH = os.path.join(_DATA_ROOT, "logs", "operations.log")

# 默认备份目录(设置 backup_dir 为空时的兜底)
DEFAULT_BACKUP_DIR = os.path.join(_DATA_ROOT, "backups")


# ---------------- 参数管理(用户可见/可改的备份还原参数) ----------------
# 内置参数:未配置 backup_opts/restore_opts 时使用;同时作为前端预览来源。
BUILTIN_BACKUP_OPTS = ["--single-transaction", "--routines", "--triggers", "--events",
                       "--set-gtid-purged=OFF", "--default-character-set=utf8mb4", "--verbose"]
BUILTIN_RESTORE_OPTS = ["--default-character-set=utf8mb4"]

# 禁止用户通过额外参数覆盖的项:连接/输出目标由系统控制,
# 用户改了会绕过备份目录白名单与凭据管理(--password 有泄露到进程列表/日志的风险)。
_FORBIDDEN_BACKUP_OPTS = {"--host", "--port", "--user", "--password", "-h", "-P", "-u", "-p",
                          "--databases", "--all-databases", "-B", "-A", "--result-file", "-r"}
_FORBIDDEN_RESTORE_OPTS = {"--host", "--port", "--user", "--password", "-h", "-P", "-u", "-p",
                           "--database", "-o", "--one-database"}


def validate_extra_opts(kind, tokens):
    """校验用户额外参数;违规返回错误信息,合法返回空串。"""
    forbidden = _FORBIDDEN_BACKUP_OPTS if kind == "backup" else _FORBIDDEN_RESTORE_OPTS
    for t in tokens:
        base = t.split("=", 1)[0]
        if base in forbidden:
            return f"参数 {base} 由系统管理,不允许通过额外参数覆盖"
    return ""


def resolve_backup_opts(extra=None):
    """最终备份参数 token 列表。
    extra: None=用内置+settings 默认(backup_opts);否则为当次完整清单(整体替换,可为空)。"""
    if extra is None:
        raw = get_settings().get("backup_opts", "")
        return BUILTIN_BACKUP_OPTS + (shlex.split(raw) if raw and raw.strip() else [])
    err = validate_extra_opts("backup", extra)
    if err:
        raise ValueError(err)
    return list(extra)


def resolve_restore_opts(extra=None):
    """最终还原参数 token 列表。extra 语义同 resolve_backup_opts。"""
    if extra is None:
        raw = get_settings().get("restore_opts", "")
        return BUILTIN_RESTORE_OPTS + (shlex.split(raw) if raw and raw.strip() else [])
    err = validate_extra_opts("restore", extra)
    if err:
        raise ValueError(err)
    return list(extra)


def mysql_bin():
    """动态解析 MySQL 客户端目录(设置值 -> PATH -> 内置 tools -> 常见目录)。"""
    cfg = get_settings().get("mysql_bin", "")
    return env_probe.find_tool("mysqldump", cfg) \
        or env_probe.find_tool("mysql", cfg) \
        or ""


@contextlib.contextmanager
def _maybe_tunnel(conn_cfg):
    """若连接启用 SSH 隧道,起/停端口转发并改写 host/port 为本地端点。

    用法:with _maybe_tunnel(cfg) as eff: ...  # eff 为实际连接端点。
    未启用隧道时透传原配置,无任何开销。
    """
    info, eff = ssh_tunnel.start_tunnel(conn_cfg)
    try:
        yield eff
    finally:
        ssh_tunnel.stop_tunnel(info)


# ---------------- 存储位置(local / remote) ----------------
# 需求:备份文件只落“部署所在环境”。本地主机(localhost/127.0.0.1/::1)
# 落到客户端本地 backup_dir;远程数据库经 SSH 管道直写远程服务器目录,不落本地。
REMOTE_DEFAULT_DIR = "~/mysql-console-backups"   # 远程默认兜底目录(相对远程家目录)


def _is_local_host(host):
    return bool(host) and str(host).strip().lower() in (
        "localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1", "0.0.0.0")


def storage_of(conn_cfg):
    """判定备份/还原的存储位置:'local' 或 'remote'。"""
    if _is_local_host(conn_cfg.get("host")):
        return "local"
    return "remote"


def _ssh_config(conn_cfg):
    """提取备份要用到的 SSH 配置(存储宿主=该连接的 SSH。返回 dict 或抛错)。"""
    if not (conn_cfg.get("ssh_host") or "").strip():
        raise RuntimeError(
            "检测到远程数据库(非本地地址)。远程备份需要在该连接配置 SSH 宿主机"
            "(主机/端口/用户/私钥)。")
    return conn_cfg


def _remote_dir(conn_cfg):
    d = (conn_cfg.get("remote_backup_dir") or "").strip()
    return d or REMOTE_DEFAULT_DIR

# 全局任务锁:同一时间只允许一个备份/还原任务
_task_lock = threading.Lock()

# ---------------- 任务管理器 ----------------
TASKS = {}
_tasks_lock = threading.Lock()


def _new_task(kind, desc):
    tid = uuid.uuid4().hex[:12]
    with _tasks_lock:
        TASKS[tid] = {
            "id": tid, "kind": kind, "desc": desc, "status": "running",
            "phase": "准备中", "percent": 0, "current": "", "message": "",
            "elapsed": 0, "detail": [], "result": None, "error": "",
            "started": time.time(),
        }
    return tid


def _update_task(tid, **kw):
    with _tasks_lock:
        t = TASKS.get(tid)
        if not t:
            return
        t.update(kw)
        t["elapsed"] = round(time.time() - t.get("started", time.time()), 1)
        if kw.get("detail"):
            t["detail"] = t["detail"][-120:]


def get_task(tid):
    with _tasks_lock:
        t = TASKS.get(tid)
        return dict(t) if t else None


def _task_log(tid, line):
    with _tasks_lock:
        t = TASKS.get(tid)
        if t:
            t["detail"].append(line)
            t["detail"] = t["detail"][-120:]


# ---------------- 通用 ----------------
def _db_major(conn_cfg):
    """连接声明的数据库版本族: ''/auto→0(自动), '5.7'→5, '8.x'→8。"""
    v = (conn_cfg.get("db_version") or "").strip().lower()
    if v in ("5", "5.5", "5.6", "5.7"):
        return 5
    if v.startswith("8"):
        return 8
    return 0


def _cli_args(conn_cfg, tool):
    """构造客户端命令行参数。tool 形如 "mysqldump.exe" / "mysql.exe"。

    客户端选择:优先已声明的数据库版本族(连接 db_version)命中内置工具;
    自动模式按探测链(配置→内置→PATH→常见目录)取最高版本。
    """
    cfg_bin = (get_settings().get("mysql_bin") or "").strip().strip('"')
    if cfg_bin and os.path.isfile(cfg_bin):
        exe = cfg_bin  # 用户直接配置了完整可执行文件路径
    else:
        path = env_probe.find_tool_versioned(
            tool.rsplit(".", 1)[0], cfg_bin, want_major=_db_major(conn_cfg))
        if not path:
            raise FileNotFoundError(
                f"未找到 {tool},请在「连接管理 → 设置」中指定 MySQL 客户端目录"
                f"(mysqldump/mysql 所在的 bin 目录)")
        exe = path
    return [
        exe,
        f"--host={conn_cfg['host']}",
        f"--port={int(conn_cfg['port'])}",
        f"--user={conn_cfg.get('user', 'root')}",
        f"--password={conn_cfg.get('password', '')}",
    ]


def _version_warning(conn_cfg):
    """客户端与服务器大版本不一致时返回警告文本,否则空串。

    策略:仅自动模式提示。连接显式声明了 db_version(5.7/8.x)时,工具已按声明
    版本族匹配,此时再提示“不一致”属噪音,交由声明本身负责。
    """
    if _db_major(conn_cfg):
        return ""
    cv = env_probe.tool_version("mysqldump", get_settings().get("mysql_bin", ""))
    sv = env_probe.server_version(conn_cfg)
    if cv and sv and cv["major"] != sv["major"]:
        return (f"版本不一致: mysqldump {cv['major']}.{cv['minor']} 与服务器 "
                f"{sv['major']}.{sv['minor']},可能存在兼容性风险")
    return ""


def _log(action, detail, ok=True):
    # 2026-08-27 用户决策:操作日志只在全量模式有意义,轻量模式不记录。
    if not _is_full_mode():
        return
    try:
        backend = _get_backend()
        level = "OK" if ok else "FAIL"
        backend.add_log(level, f"{action} | {detail}")
    except Exception:
        pass


def _read_history():
    if _is_full_mode():
        try:
            backend = _get_backend()
            return backend.list_history()
        except Exception:
            return []
    # 轻量模式统一存 SQLite(config.db meta)。旧 backup_history.json 一次性迁移。
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                items = json.load(f)
            if local_store.get_meta_json("backup_history") is None:
                local_store.set_meta_json("backup_history", items)
            try:
                os.remove(HISTORY_PATH)
            except OSError:
                pass
        except Exception:
            pass
    return local_store.get_meta_json("backup_history") or []


def _write_history(items, limit=300):
    if _is_full_mode():
        # 全量模式：历史通过 add_history 单条追加，此处无需批量写
        return
    local_store.set_meta_json("backup_history", items[-limit:])


def _apply_record_columns(record, *, target, object, file_path, size, elapsed):
    """补齐全量模式系统库所需列(target/object/file_path/file_size/duration_ms/operator)。

    备份/还原各路径共用同一映射(轻量 meta 与全量 mc_backup_history 双后端读写一致的
    前端 shape 来源),收敛四处重复的 setdefault 块。size 为字节数,elapsed 为秒。
    """
    record.setdefault("target", target)
    record.setdefault("object", object)
    record.setdefault("file_path", file_path)
    record.setdefault("file_size", size)
    record.setdefault("duration_ms", int(elapsed * 1000))
    record.setdefault("operator", "")


def _save_history(record):
    """追加一条历史。全量模式 → 系统库 mc_backup_history;轻量 → 本地 meta 列表。"""
    if _is_full_mode():
        try:
            _get_backend().add_history(record)
        except Exception:
            pass
        return
    items = _read_history()
    items.append(record)
    _write_history(items)


def list_backups():
    items = _read_history()
    out = []
    for it in items:
        # 全量模式行字段(target/object/file_path/...) → 前端期望 shape(time/dbs/path/...)
        rec = {
            "id": it.get("id", ""),
            "type": it.get("type", "backup"),
            "time": it.get("time") or str(it.get("created_at") or ""),
            "host": it.get("host", ""),
            "dbs": it.get("dbs") or ([it["object"]] if it.get("object") else []),
            "path": it.get("path") or it.get("file_path", ""),
            "size": it.get("size") if it.get("size") is not None else (it.get("file_size") or 0),
            "elapsed": it.get("elapsed") if it.get("elapsed") is not None
                       else round((it.get("duration_ms") or 0) / 1000, 1),
            "result": it.get("result", "failed"),
            "warning": it.get("warning", ""),
            "error": it.get("error") or it.get("error_msg", ""),
        }
        rec["storage"] = it.get("storage", "local")
        rec["remote_dir"] = it.get("remote_dir", "")
        rec["files"] = it.get("files", [])
        rec["exists"] = os.path.exists(rec["path"]) if rec["storage"] == "local" else False
        rec["compressed"] = rec["path"].endswith((".gz", ".sql.gz", ".zip"))
        out.append(rec)
    return out


def delete_backup_record(record_id):
    if _is_full_mode():
        try:
            backend = _get_backend()
            backend.delete_history(record_id)
        except Exception:
            pass
        return True
    items = _read_history()
    _write_history([it for it in items if it.get("id") != record_id])
    return True


# ---------------- 备份文件浏览 ----------------
def _backup_dirs():
    """返回允许浏览/下载的备份文件根目录(配置目录 + 项目默认目录),去重。"""
    settings = get_settings()
    roots = []
    cfg = settings.get("backup_dir") or ""
    if cfg:
        roots.append(os.path.abspath(cfg))
    roots.append(DEFAULT_BACKUP_DIR)
    seen, out = set(), []
    for r in roots:
        rp = os.path.realpath(r)
        if rp in seen:
            continue
        seen.add(rp)
        out.append(rp)
    return out


def list_backup_files(limit=500):
    """列出所有备份文件(*.sql / *.sql.gz),按修改时间倒序。返回带 safe 标记的条目。"""
    seen = set()
    out = []
    for root in _backup_dirs():
        if not os.path.isdir(root):
            continue
        try:
            for fn in sorted(os.listdir(root)):
                if not fn.lower().endswith((".sql", ".sql.gz", ".zip")):
                    continue
                fp = os.path.join(root, fn)
                if not os.path.isfile(fp):
                    continue
                rp = os.path.realpath(fp)
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    st = os.stat(rp)
                    out.append({
                        "name": fn,
                        "path": rp,
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                        "compressed": fn.lower().endswith(".gz"),
                    })
                except OSError:
                    continue
        except OSError:
            continue
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


def resolve_backup_file(raw_path):
    """校验并解析可下载的备份文件绝对路径;非法返回 None(防任意文件读取)。"""
    if not raw_path:
        return None
    rp = os.path.realpath(raw_path)
    if not rp.lower().endswith((".sql", ".sql.gz", ".zip")):
        return None
    if not os.path.isfile(rp):
        return None
    # 必须位于允许的备份目录内
    for root in _backup_dirs():
        root_rp = os.path.realpath(root)
        try:
            if os.path.commonpath([root_rp, rp]) == root_rp:
                return rp
        except ValueError:
            continue
    return None


def _safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _prefetch_tables(conn_cfg, dbs):
    """预查备份涉及的表:名称与大小(数据+索引),用于表级进度。"""
    conn = pymysql.connect(host=conn_cfg["host"], port=int(conn_cfg["port"]),
                           user=conn_cfg.get("user", "root"), password=conn_cfg.get("password", ""),
                           connect_timeout=5, charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            if dbs:
                ph = ",".join(["%s"] * len(dbs))
                cur.execute(f"""
                    SELECT TABLE_SCHEMA, TABLE_NAME, IFNULL(DATA_LENGTH,0)+IFNULL(INDEX_LENGTH,0)
                    FROM information_schema.tables
                    WHERE TABLE_SCHEMA IN ({ph}) AND TABLE_TYPE='BASE TABLE'
                    ORDER BY TABLE_SCHEMA, TABLE_NAME""", list(dbs))
            else:
                cur.execute("""
                    SELECT TABLE_SCHEMA, TABLE_NAME, IFNULL(DATA_LENGTH,0)+IFNULL(INDEX_LENGTH,0)
                    FROM information_schema.tables
                    WHERE TABLE_TYPE='BASE TABLE'
                      AND TABLE_SCHEMA NOT IN ('information_schema','performance_schema','mysql','sys')
                    ORDER BY TABLE_SCHEMA, TABLE_NAME""")
            return [{"db": r[0], "name": r[1], "size": int(r[2] or 0)} for r in cur.fetchall()]
    finally:
        conn.close()


# ---------------- 备份 ----------------
def _dump_to_file(conn_cfg, dbs, out_path, gzip_, opts, tables, cb):
    """执行一次 mysqldump 流式写入 out_path。dbs=[] 表示 --all-databases。
    返回 (rc, size, err_lines)。percent 相对本次 dump(0-100)。"""
    args = _cli_args(conn_cfg, "mysqldump.exe") + opts
    if dbs:
        args += ["--databases"] + list(dbs)
    else:
        args += ["--all-databases"]

    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    table_order = [t["name"] for t in tables]
    total_size = sum(t["size"] for t in tables)
    current_table = ""
    done_bytes = 0
    err_lines = []

    def _read_stderr():
        """解析 stderr:表切换事件 + 真正的错误行(进度百分比由 stdout 字节驱动)。"""
        nonlocal current_table
        serr = getattr(proc, "stderr", None)
        if serr is None:
            return
        for raw in iter(serr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if (line.startswith("mysqldump: [ERROR]") or line.startswith("[ERROR]")
                    or (line.startswith("mysqldump:") and "error" in line.lower())):
                err_lines.append(line)
            m = re.search(r"for table\s+'?([\w$]+)'?", line, re.I)
            if m:
                tname = m.group(1)
                if tname != current_table:
                    current_table = tname
                    idx = table_order.index(tname) + 1 if tname in table_order else len(table_order) + 1
                    cb(current=tname, message=f"正在备份表 {tname} ({idx}/{len(table_order)})",
                       detail=f"[表] {tname}")

    def _write_stdout():
        """流式写文件,并按导出字节数平滑更新进度。"""
        nonlocal done_bytes
        sout = getattr(proc, "stdout", None)
        if sout is None:
            return
        fout = None
        try:
            fout = gzip.open(out_path, "wb", compresslevel=6) if gzip_ else open(out_path, "wb")
            try:
                while True:
                    chunk = sout.read(1024 * 1024)
                    if not chunk:
                        break
                    fout.write(chunk)
                    done_bytes += len(chunk)
                    if total_size:
                        pct = min(100.0, round(done_bytes / total_size * 100, 1))
                        cb(percent=pct,
                           message=f"已导出 {_fmt_size(done_bytes)} / 约{_fmt_size(total_size)} ({pct}%)")
            finally:
                try:
                    fout.close()
                except Exception:
                    pass
        finally:
            try:
                sout.close()
            except Exception:
                pass
            serr2 = getattr(proc, "stderr", None)
            if serr2 is not None:
                try:
                    serr2.close()
                except Exception:
                    pass

    st_thread = threading.Thread(target=_read_stderr, daemon=True)
    out_thread = threading.Thread(target=_write_stdout, daemon=True)
    st_thread.start()
    out_thread.start()
    try:
        rc = proc.wait()
    finally:
        out_thread.join(timeout=10)
        st_thread.join(timeout=10)
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    return rc, size, err_lines


# ---------------- 远程备份(SSH 管道直写远程,不落本地) ----------------
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _dump_to_remote(storage_cfg, db_endpoint, db, remote_path, gzip_, opts, tables, cb):
    """本地 mysqldump 经 SSH 管道直写远程文件。db=None 表示 --all-databases。

    进度保持与本地一致:百分比的“已导出字节”按 mysqldump 原始输出字节
    (表数据总量分母)计算;gzip 仅在客户端流式压缩用于省带宽,不落本地盘。
    返回 (ssh_rc, dump_rc, remote_size, err_lines)。
    """
    args = _cli_args(db_endpoint, "mysqldump.exe") + opts
    if db is None:
        args += ["--all-databases"]
    else:
        args += ["--databases", db]
    sshcfg = _ssh_config(storage_cfg)
    pre = ssh_tunnel.ssh_prefix(sshcfg)
    rdir = shlex.quote(os.path.dirname(remote_path))
    rpath = shlex.quote(remote_path)
    remote_cmd = "mkdir -p %s && cat > %s" % (rdir, rpath)
    ssh_proc = subprocess.Popen(pre + [remote_cmd], stdin=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=_NO_WINDOW,
                                start_new_session=(sys.platform != "win32"))
    dump_proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    total_size = sum(t["size"] for t in tables)
    current_table = ""
    done = 0
    err_lines = []
    ssh_err = []

    def _read_stderr():
        nonlocal current_table
        serr = getattr(dump_proc, "stderr", None)
        if serr is None:
            return
        for raw in iter(serr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if (line.startswith("mysqldump: [ERROR]") or line.startswith("[ERROR]")
                    or (line.startswith("mysqldump:") and "error" in line.lower())):
                err_lines.append(line)
            m = re.search(r"for table\s+'?([\w$]+)'?", line, re.I)
            if m:
                tname = m.group(1)
                if tname != current_table:
                    current_table = tname
                    idx = tables and next((i for i, t in enumerate(tables)
                                           if t["name"] == tname), -1) + 1 or 0
                    cb(current=tname, message=f"正在备份表 {tname} ({idx}/{len(tables)})",
                       detail=f"[表] {tname}")

    def _drain_ssh_err():
        serr2 = getattr(ssh_proc, "stderr", None)
        if serr2 is None:
            return
        for raw in iter(serr2.readline, b""):
            if raw:
                ssh_err.append(raw.decode("utf-8", "replace").strip())

    _t1 = threading.Thread(target=_read_stderr, daemon=True)
    _t2 = threading.Thread(target=_drain_ssh_err, daemon=True)
    _t1.start()
    _t2.start()

    out = None
    dump_out = getattr(dump_proc, "stdout", None)
    ssh_in = getattr(ssh_proc, "stdin", None)
    try:
        if ssh_in is None or dump_out is None:
            raise RuntimeError("备份管道未就绪(子进程 stdout/stdin 缺失)")
        out = gzip.GzipFile(filename="", mode="wb", fileobj=ssh_in) if gzip_ else ssh_in
        while True:
            chunk = dump_out.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total_size:
                pct = min(100.0, round(done / total_size * 100, 1))
                cb(percent=pct, message=f"已导出 {_fmt_size(done)} / 约{_fmt_size(total_size)} ({pct}%)")
    finally:
        try:
            if gzip_ and out is not None:
                out.close()
            elif ssh_in is not None:
                ssh_in.close()
        except Exception:
            pass
        try:
            if dump_out is not None:
                dump_out.close()
        except Exception:
            pass
        try:
            serr_d = getattr(dump_proc, "stderr", None)
            if serr_d is not None:
                serr_d.close()
        except Exception:
            pass
        try:
            serr_s = getattr(ssh_proc, "stderr", None)
            if serr_s is not None:
                serr_s.close()
        except Exception:
            pass

    ssh_rc = ssh_proc.wait()
    dump_rc = dump_proc.wait()
    _t1.join(timeout=10)
    _t2.join(timeout=10)
    # 取远程文件大小必须传“命令”而非路径(remote_file_size 把第二参数当远端命令执行);
    # 之前传 remote_path 会执行失败 → size=-1 → 备份被误判失败。
    size = ssh_tunnel.remote_file_size(sshcfg, _remote_size_cmd(remote_path))
    errs = err_lines + ssh_err[-6:]
    return ssh_rc, dump_rc, size or 0, errs


def _remote_backup(storage_cfg, db_endpoint, dbs, gzip_, extra_opts, progress_cb):
    """远程备份编排:逐库经 SSH 直写远程,产出持久不动本地。dbs 空=全库单文件。"""
    start = time.time()
    try:
        warn = _version_warning(db_endpoint)
    except Exception:
        warn = ""
    sshcfg = _ssh_config(storage_cfg)
    remote_dir = _remote_dir(storage_cfg)
    opts = resolve_backup_opts(extra_opts)

    # Windows 远程服务器:远端命令是 Unix 语法,须走 Git Bash(路线A)。
    # remote_os 配置为 windows 时,先探测 Git Bash 环境,未就绪直接给可读错误而非管道报错。
    if (storage_cfg.get("remote_os") or "").strip().lower() == "windows":
        env = ssh_tunnel.probe_remote_env(sshcfg)
        if env.get("os") == "windows" and not env.get("git_bash"):
            raise RuntimeError(
                "远程服务器为 Windows,但未检测到 Git Bash 环境。请在远程 Windows 安装 "
                "Git for Windows,并把 OpenSSH 默认 shell 改为 Git Bash"
                "(连接表单→远程服务器配置指引 有完整步骤与验证命令)。")

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    if not dbs:
        try:
            dbs = sorted({t["db"] for t in _prefetch_tables(db_endpoint, [])})
        except Exception:
            dbs = []
    try:
        tables = _prefetch_tables(db_endpoint, dbs)
    except Exception:
        tables = []
    multi = len(dbs) > 1
    init_msg = f"共 {len(tables)} 张表" if tables else "开始备份(远程存储)"
    if multi:
        init_msg = f"{len(dbs)} 个库逐库备份(远程) | {init_msg}"
    if warn:
        init_msg += f" | ⚠ {warn}"
    cb(phase="备份中", percent=0, current="", message=init_msg,
       detail=f"远程目标: {remote_dir} @ {sshcfg.get('ssh_host', '')} "
              f"({', '.join(dbs) if dbs else '全部数据库'})")

    ts = time.strftime("%Y%m%d_%H%M%S")
    ext = ".sql.gz" if gzip_ else ".sql"
    parts, err_parts, ok_all = [], [], True
    targets = [None] if not dbs else dbs
    for i, db in enumerate(targets):
        fname = (("all_databases" if db is None else _safe_filename(db)) + "_" + ts + ext)
        rpath = os.path.join(remote_dir, fname).replace("\\", "/")
        db_tables = (tables if db is None else [t for t in tables if t["db"] == db])
        n = max(len(targets), 1)

        def _wrap(**kw):
            kw2 = dict(kw)
            if "percent" in kw2:
                kw2["percent"] = round((i + kw2["percent"] / 100.0) / n * 100, 1)
            kw2["message"] = f"[{db or '全部库'}] {kw2.get('message', '')}"
            cb(**kw2)

        ssh_rc, dump_rc, size, errs = _dump_to_remote(
            storage_cfg, db_endpoint, db, rpath, gzip_, opts, db_tables, _wrap)
        if dump_rc != 0 or ssh_rc != 0 or size <= 0:
            ok_all = False
            err_parts.append(f"{db or '全部库'}: " + ("; ".join(errs[-5:]) if errs
                                                      else f"ssh/mysqldump 退出码 ssh={ssh_rc} dump={dump_rc}"))
        else:
            parts.append(rpath)

    size = 0
    elapsed = round(time.time() - start, 1)
    err_text = "\n".join(err_parts)[:800]
    ok = ok_all and bool(parts)
    out_path = parts[0] if parts else os.path.join(remote_dir, "未生成")
    record = {
        "id": uuid.uuid4().hex[:12], "type": "backup",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": f"{storage_cfg.get('host', '')}:{storage_cfg.get('port', '')}",
        "dbs": dbs or ["* 全部库 *"], "path": out_path, "files": parts, "size": size,
        "elapsed": elapsed, "result": "success" if ok else "failed",
        "warning": warn, "error": err_text,
        "storage": "remote", "remote_dir": remote_dir,
    }
    _apply_record_columns(record,
        target=storage_cfg.get("host", ""),
        object=",".join(dbs) if dbs else "* 全部库 *",
        file_path=os.path.join("ssh://", sshcfg.get("ssh_host", ""), remote_dir),
        size=size, elapsed=elapsed)
    _save_history(record)
    _log("备份(远程)", f"{record['dbs']} -> {remote_dir}@ {sshcfg.get('ssh_host', '')} "
                       f"({elapsed}s)", ok=ok)
    cb(phase="完成", percent=100.0 if ok else 0, current="",
       message=f"远程备份{'成功' if ok else '失败'}: {remote_dir}@ {sshcfg.get('ssh_host', '')}",
       detail=f"结果: {'成功' if ok else '失败'}({elapsed}s) 文件: {', '.join(parts)}")
    return record


def run_backup(conn_cfg, dbs, backup_dir=None, gzip_=True, extra_opts=None, progress_cb=None):
    """同步执行备份(内部支持进度回调)。SSH 隧道启用时自动起/停转发。"""
    with _maybe_tunnel(conn_cfg) as eff:
        return _run_backup(conn_cfg, eff, dbs, backup_dir, gzip_, extra_opts, progress_cb)


def _run_backup(storage_cfg, conn_cfg, dbs, backup_dir=None, gzip_=True, extra_opts=None, progress_cb=None):
    """同步执行备份。
    storage_cfg:原始连接配置(判断本地/远程 + SSH);conn_cfg:实际 DB 端点(隧道本地化后)。
    dbs 为空=全部数据库;单库=单文件;多库=每库独立文件(本地打包 zip / 远程各自落远程文件)。
    extra_opts: None=用 settings 默认(backup_opts),否则为当次 token 列表(可为空)。"""
    if storage_of(storage_cfg) == "remote":
        return _remote_backup(storage_cfg, conn_cfg, dbs, gzip_, extra_opts, progress_cb)
    start = time.time()
    settings = get_settings()
    try:
        warn = _version_warning(conn_cfg)
    except Exception:
        warn = ""
    backup_dir = backup_dir or settings.get("backup_dir") or DEFAULT_BACKUP_DIR
    os.makedirs(backup_dir, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    ext = ".sql.gz" if gzip_ else ".sql"
    opts = resolve_backup_opts(extra_opts)

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    # 全库模式:枚举用户库,逐库拆分(系统库排除,与 _prefetch_tables 过滤一致)
    if not dbs:
        try:
            dbs = sorted({t["db"] for t in _prefetch_tables(conn_cfg, [])})
        except Exception:
            dbs = []
    multi = len(dbs) > 1

    try:
        tables = _prefetch_tables(conn_cfg, dbs)
    except Exception:
        tables = []
    init_msg = f"共 {len(tables)} 张表" if tables else "开始备份"
    if multi:
        init_msg = f"{len(dbs)} 个库逐库备份,打包 zip | {init_msg}"
    if warn:
        init_msg += f" | ⚠ {warn}"
    cb(phase="备份中", percent=0, current="",
       message=init_msg,
       detail=f"目标: {conn_cfg.get('host', '')}:{conn_cfg.get('port', '')} "
              f"{', '.join(dbs) if dbs else '全部数据库'}({len(tables)} 张表)")

    if multi:
        # 需求:每个库独立 .sql(.gz) 文件,最后打包 zip
        import zipfile
        # ZIP_STORED:成员已是 .gz 压缩流,二次压缩无收益
        parts, err_parts, ok_all = [], [], True
        for i, db in enumerate(dbs):
            p = os.path.join(backup_dir, f"{_safe_filename(db)}_{ts}{ext}")
            db_tables = [t for t in tables if t["db"] == db]

            def _wrap(**kw):
                kw2 = dict(kw)
                if "percent" in kw2:
                    kw2["percent"] = round((i + kw2["percent"] / 100.0) / len(dbs) * 100, 1)
                kw2["message"] = f"[{db}] {kw2.get('message', '')}"
                cb(**kw2)

            rc, size1, errs = _dump_to_file(conn_cfg, [db], p, gzip_, opts, db_tables, _wrap)
            if rc != 0 or size1 == 0:
                ok_all = False
                err_parts.append(f"{db}: " + ("; ".join(errs[-5:]) if errs else f"mysqldump 退出码 {rc}"))
            else:
                parts.append(p)
        cb(phase="备份中", percent=99, message="打包 zip ...", current="")
        out_path = os.path.join(backup_dir, f"databases_{ts}.zip")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as zf:
            for p in parts:
                zf.write(p, os.path.basename(p))
        for p in parts:  # 散件已入 zip,删掉避免双倍占用
            try:
                os.remove(p)
            except OSError:
                pass
        size = os.path.getsize(out_path)
        ok = ok_all and size > 0
        elapsed = round(time.time() - start, 1)
        err_text = "\n".join(err_parts)[:800]
    else:
        name_part = _safe_filename(dbs[0]) if dbs else "all_databases"
        out_path = os.path.join(backup_dir, f"{name_part}_{ts}{ext}")
        rc, size, err_lines = _dump_to_file(conn_cfg, dbs, out_path, gzip_, opts, tables, cb)
        ok = rc == 0 and size > 0
        elapsed = round(time.time() - start, 1)
        err_text = "\n".join(err_lines[-8:])[:800] if err_lines else ("mysqldump 退出码非 0" if not ok else "")

    record = {
        "id": uuid.uuid4().hex[:12], "type": "backup",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": f"{conn_cfg.get('host', '')}:{conn_cfg.get('port', '')}",
        "dbs": dbs or ["* 全部库 *"], "path": out_path, "size": size,
        "elapsed": elapsed, "result": "success" if ok else "failed",
        "warning": warn, "error": err_text,
    }
    # 全量模式表列(target/object/file_path/...)来自 record 的映射字段
    _apply_record_columns(record,
        target=conn_cfg.get("host", ""),
        object=",".join(dbs) if dbs else "* 全部库 *",
        file_path=out_path, size=size, elapsed=elapsed)
    _save_history(record)
    _log("备份", f"{record['dbs']} -> {out_path} ({size}B, {elapsed}s)", ok=ok)
    cb(phase="完成", percent=100.0 if ok else 0.0, current="",
       message=f"备份{'成功' if ok else '失败'}: {out_path}",
       detail=f"结果: {'成功' if ok else '失败'}({size}B, {elapsed}s)")
    return record


def start_backup_task(conn_cfg, dbs, backup_dir=None, gzip_=True, extra_opts=None):
    tid = _new_task("backup", "备份数据库")

    def worker():
        try:
            record = run_backup(conn_cfg, dbs, backup_dir, gzip_, extra_opts=extra_opts,
                                progress_cb=lambda **kw: _update_task(tid, **kw))
            _update_task(tid, status="done", phase="完成", percent=100.0 if record["result"] == "success" else _get_percent(tid), result=record,
                         message=f"备份{'成功' if record['result'] == 'success' else '失败'}: {record['path']}",
                         error=record.get("error", ""))
        except Exception as e:
            _update_task(tid, status="failed", phase="失败", message=str(e), error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return tid


def _get_percent(tid):
    t = get_task(tid)
    return t.get("percent", 0) if t else 0


# ---------------- 还原 ----------------
def _dump_contains_create_db(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    try:
        with opener(path, "rb") as f:
            head = f.read(256 * 1024).decode("utf-8", "replace")
        return bool(re.search(r"CREATE DATABASE|^USE `", head, re.M | re.I))
    except Exception:
        return True


def _gz_uncompressed_size(path):
    """读取 gzip 尾部 ISIZE 字段(gzip 流解压后的大小, mod 2^32)作为还原进度分母。
    通过 f.seek 定位倒数 4 字节读取,无需整文件预先扫描。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            if end < 18:  # 至少头部+尾部
                return 0
            f.seek(end - 4)
            b = f.read(4)
        return int.from_bytes(b, "little") if len(b) == 4 else 0
    except Exception:
        return 0


def _ensure_database(conn_cfg, db_name):
    """确保目标数据库存在,不存在则创建(utf8mb4)。返回 (ok, error_msg)。"""
    import pymysql
    try:
        conn = pymysql.connect(
            host=conn_cfg["host"], port=int(conn_cfg["port"]),
            user=conn_cfg.get("user", "root"), password=conn_cfg.get("password", ""),
            connect_timeout=10, charset="utf8mb4")
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE DATABASE IF NOT EXISTS `%s` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci" % db_name)
            conn.commit()
            return True, ""
        finally:
            conn.close()
    except Exception as e:
        return False, str(e)


# ---------------- 远程还原(从远程 SSH 读流 -> mysql,不落本地) ----------------
def _remote_copy_cmd(path):
    gz = str(path).endswith(".gz")
    base = "gzip -dc %s" if gz else "cat %s"
    return base % shlex.quote(path)


def _remote_size_cmd(path):
    gz = str(path).endswith(".gz")
    base = "gzip -dc %s | wc -c" if gz else "wc -c < %s"
    return base % shlex.quote(path)


def _remote_contains_create_db(storage_cfg, path):
    sshcfg = _ssh_config(storage_cfg)
    cmd = ("gzip -dc %s | head -c 262144" if str(path).endswith(".gz")
           else "head -c 262144 %s") % shlex.quote(path)
    try:
        pre = ssh_tunnel.ssh_prefix(sshcfg)
        p = subprocess.run(pre + [cmd], capture_output=True, timeout=30)
        head = (p.stdout or b"")[:262144].decode("utf-8", "replace")
    except Exception:
        return True
    return bool(re.search(r"CREATE DATABASE|^USE `", head, re.M | re.I))


# ---------------- 远程备份文件列表(还原时选择远程文件) ----------------
def _remote_list_cmd(remote_dir):
    """构造列远程备份文件的命令:目录存在标记 + GNU find 输出 name/size/mtime。

    find 的 -printf 是 GNU 扩展,Linux 原生与 Windows Git Bash 均可用(路线A);
    文件按 `文件名\\t大小\\tYYYY-MM-DD HH:MM` 每行一条,便于解析。
    """
    d = shlex.quote(remote_dir)
    return ("test -d %s && echo __OK__ || echo __NO_DIR__; "
            "find %s -maxdepth 1 -type f \\( -name '*.sql' -o -name '*.sql.gz' \\) "
            "-printf '%%f\\t%%s\\t%%TY-%%Tm-%%Td %%TH:%%TM\\n' 2>/dev/null" % (d, d))


def _parse_remote_ls(text, remote_dir):
    """解析 find 输出为文件列表(非法行忽略,按文件名倒序)。"""
    files = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, size, mtime = parts[0], parts[1], parts[2]
        if not name or not size.isdigit():
            continue
        files.append({
            "name": name,
            "size": int(size),
            "mtime": mtime,
            "path": os.path.join(remote_dir, name).replace("\\", "/"),
            "compressed": name.lower().endswith(".gz"),
        })
    files.sort(key=lambda x: x["name"], reverse=True)
    return files


def list_remote_files(storage_cfg, remote_dir=None):
    """列出远程服务器备份目录下的 .sql/.sql.gz 文件。返回 (remote_dir, files)。

    远端命令为 Unix 语法,要求 Linux 或已配 Git Bash 的 Windows(路线A);
    未配 Git Bash 的 Windows 直接报错引导配置,与备份前校验一致。
    """
    sshcfg = _ssh_config(storage_cfg)
    remote_dir = (remote_dir or _remote_dir(storage_cfg)).strip() or REMOTE_DEFAULT_DIR
    env = ssh_tunnel.probe_remote_env(sshcfg)
    if env.get("os") == "windows" and not env.get("git_bash"):
        raise RuntimeError(
            "远程服务器为 Windows,但未检测到 Git Bash 环境,无法列出远程备份文件。"
            "请先在远程 Windows 配置 Git Bash(连接表单→远程服务器配置指引)。")
    out = ssh_tunnel.ssh_run(sshcfg, _remote_list_cmd(remote_dir), timeout=20)
    lines = (out or "").splitlines()
    if not lines:
        raise RuntimeError("无法获取远程目录状态(SSH 无输出): %s" % remote_dir)
    if lines[0].strip() == "__NO_DIR__":
        raise RuntimeError("远程备份目录不存在或不可访问: %s" % remote_dir)
    return remote_dir, _parse_remote_ls("\n".join(lines[1:]), remote_dir)


def _remote_restore(storage_cfg, conn_cfg, target_db, file_path, extra_opts, progress_cb):
    """从远程服务器经 SSH 流式还原到目标库。file_path 为远程文件路径。"""
    start = time.time()
    try:
        warn = _version_warning(conn_cfg)
    except Exception:
        warn = ""
    sshcfg = _ssh_config(storage_cfg)

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    if str(file_path).lower().endswith(".zip"):
        return {"result": "failed", "error": "远程还原暂不支持 zip 包,请还原单个 .sql/.sql.gz 文件"}
    total = ssh_tunnel.remote_file_size(sshcfg, _remote_size_cmd(file_path))
    if total <= 0:
        return {"result": "failed", "error": f"无法获取远程文件大小(可能不存在或权限不足): {file_path}"}
    contains_db = _remote_contains_create_db(storage_cfg, file_path)

    args = _cli_args(conn_cfg, "mysql.exe") + resolve_restore_opts(extra_opts)
    if target_db and not contains_db:
        ok, err = _ensure_database(conn_cfg, target_db)
        if not ok:
            return {"result": "failed", "error": f"创建数据库失败: {err}"}
        args += [target_db]
    cb(phase="还原中", percent=0,
       message=f"从远程恢复: {file_path} | 约{_fmt_size(total)}"
               + (f" | ⚠ {warn}" if warn else ""))

    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    stderr_parts = []

    def _drain():
        try:
            for line in iter(proc.stderr.readline, b""):
                if line:
                    stderr_parts.append(line.decode("utf-8", "replace").strip())
        except Exception:
            pass

    threading.Thread(target=_drain, daemon=True).start()
    src = ssh_tunnel.read_remote_stream(sshcfg, _remote_copy_cmd(file_path))
    if not src:
        return {"result": "failed", "error": "无法建立远程读取 SSH 通道"}

    done, write_err = 0, ""
    try:
        while True:
            chunk = src.stdout.read(1024 * 1024)
            if not chunk:
                break
            try:
                proc.stdin.write(chunk)
            except Exception as e:
                write_err = str(e)
                break
            done += len(chunk)
            pct = min(100.0, round(done / total * 100, 1))
            cb(percent=pct,
               message=f"已还原 {_fmt_size(done)} / 约{_fmt_size(total)} ({pct}%)",
               detail=f"[远程还原] {_fmt_size(done)}/{_fmt_size(total)}")
    except Exception as e:
        write_err = f"读取远程还原文件失败: {e}"
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            src.stdout.close()
        except Exception:
            pass
    rc = proc.wait()
    err = "\n".join(stderr_parts).strip()
    elapsed = round(time.time() - start, 1)

    if write_err:
        ok = False
        reason = f"{write_err} | mysql 输出: {err[:800]}" if err else \
                 (f"{write_err} | mysql 未输出错误信息,请检查目标库状态与 "
                  f"max_allowed_packet(超大语句易触发 Lost connection)")
    else:
        ok = rc == 0
        reason = err[:800] if not ok else ""

    record = {
        "id": uuid.uuid4().hex[:12], "type": "restore",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": f"{storage_cfg.get('host', '')}:{storage_cfg.get('port', '')}",
        "dbs": [target_db or "(文件自带建库)"], "path": file_path,
        "size": total, "elapsed": elapsed, "result": "success" if ok else "failed",
        "warning": warn, "error": reason, "storage": "remote",
        "remote_dir": os.path.dirname(file_path) or "",
    }
    _apply_record_columns(record,
        target=target_db or "(自带)",
        object=target_db or "(文件自带建库)",
        file_path=os.path.join("ssh://", sshcfg.get("ssh_host", ""), file_path),
        size=total, elapsed=elapsed)
    _save_history(record)
    _log("还原(远程)", f"目标={target_db or '(自带)'} 远程={file_path} ({elapsed}s)", ok=ok)
    cb(phase="完成", percent=100.0 if ok else 0,
       detail=f"结果: {'成功' if ok else '失败'}")
    return record


def run_restore(conn_cfg, target_db, file_path, extra_opts=None, progress_cb=None, storage="local"):
    """同步执行还原(字节进度回调)。SSH 隧道/远程存储自动处理。
    storage: 'local'=本地文件;'remote'=文件在远程服务器(file_path 为远程路径)。"""
    with _maybe_tunnel(conn_cfg) as eff:
        return _run_restore(conn_cfg, eff, target_db, file_path, extra_opts, progress_cb, storage)


def _run_restore(storage_cfg, conn_cfg, target_db, file_path, extra_opts=None, progress_cb=None, storage="local"):
    """同步执行还原(字节进度回调)。extra_opts: None=用 settings 默认(restore_opts)。
    支持单文件(.sql/.sql.gz)与多库打包包(.zip,成员逐个还原)。"""
    if storage == "remote":
        return _remote_restore(storage_cfg, conn_cfg, target_db, file_path, extra_opts, progress_cb)
    if not os.path.exists(file_path):
        return {"result": "failed", "error": f"文件不存在: {file_path}"}
    start = time.time()

    # zip 包(多库备份产物):解到临时目录后逐成员还原
    if str(file_path).lower().endswith(".zip"):
        import zipfile
        import tempfile
        try:
            with zipfile.ZipFile(file_path) as zf:
                members = [n for n in zf.namelist()
                           if n.lower().endswith((".sql", ".sql.gz")) and not n.startswith(("__MACOSX", "."))]
                if not members:
                    return {"result": "failed", "error": "zip 内没有 .sql/.sql.gz 文件"}
                tmpdir = tempfile.mkdtemp(prefix="mc_restore_")
                paths = []
                for n in members:
                    # 防路径穿越:成员名只取文件名
                    dest = os.path.join(tmpdir, os.path.basename(n))
                    with zf.open(n) as src, open(dest, "wb") as out:
                        out.write(src.read())
                    paths.append(dest)
        except zipfile.BadZipFile:
            return {"result": "failed", "error": "zip 文件损坏或不是有效的 zip"}
        results, errors = [], []
        for i, p in enumerate(paths):
            r = run_restore(conn_cfg, target_db, p, extra_opts=extra_opts,
                            progress_cb=lambda **kw: progress_cb and progress_cb(
                                **{**kw, "message": f"[{os.path.basename(p)}] {kw.get('message', '')}",
                                   "percent": round((i + kw.get("percent", 0) / 100.0) / len(paths) * 100, 1)}))
            results.append(r)
            if r.get("result") != "success":
                errors.append(f"{os.path.basename(p)}: {r.get('error', '未知错误')}")
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
        ok = not errors
        # ponytail: 成员级 run_restore 已各自落库,外层只做汇总回调,不重复落库
        if progress_cb:
            progress_cb(phase="完成", percent=100.0 if ok else 0,
                        message=f"zip 还原{'成功' if ok else '部分失败'}: {len(paths) - len(errors)}/{len(paths)} 个文件")
        return {"result": "success" if ok else "failed",
                "error": "; ".join(errors)[:800] if not ok else ""}

    try:
        warn = _version_warning(conn_cfg)
    except Exception:
        warn = ""

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    # 指定了目标库但备份文件不含建库语句时,先确保库存在
    if target_db and not _dump_contains_create_db(file_path):
        cb(phase="还原中", percent=0, message=f"检查目标数据库 {target_db} ...")
        ok, err = _ensure_database(conn_cfg, target_db)
        if not ok:
            return {"result": "failed", "error": f"创建数据库失败: {err}"}

    start = time.time()
    total = os.path.getsize(file_path)   # 压缩包/文件本体大小(记录与初始展示用)
    contains_db = _dump_contains_create_db(file_path)
    args = _cli_args(conn_cfg, "mysql.exe")
    args += resolve_restore_opts(extra_opts)
    if target_db and not contains_db:
        args += [target_db]
    # 还原进度分母: .gz 用「解压后大小」(ISIZE)与已完成解压字节比对——
    # 否则 1G 数据用 149MB 压缩包做分母,播到压缩包大小就到 100%,实际才还原六分之一。
    prog_total = total
    if str(file_path).endswith(".gz"):
        isize = _gz_uncompressed_size(file_path)
        if isize > total:
            prog_total = isize
    cb(phase="还原中", percent=0,
       message=f"目标库: {target_db or '(文件自带建库)'} | 备份包大小: {_fmt_size(total)}"
               + (f" | ⚠ {warn}" if warn else ""))

    opener = gzip.open if str(file_path).endswith(".gz") else open
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    # 后台线程实时排空 stderr:既避免错误/告警写满管道导致 mysql 阻塞(还原假死),
    # 也保证失败时能拿到 mysql 自身的真实报错。
    stderr_parts = []

    def _drain_stderr():
        try:
            for line in iter(proc.stderr.readline, b""):
                if line:
                    stderr_parts.append(line.decode("utf-8", "replace").strip())
        except Exception:
            pass

    threading.Thread(target=_drain_stderr, daemon=True).start()
    done = 0
    write_err = ""      # 写 stdin 抛出的异常(通常 = mysql 提前退出导致 Broken pipe)
    try:
        with opener(file_path, "rb") as fin:
            while True:
                chunk = fin.read(1024 * 1024)
                if not chunk:
                    break
                try:
                    proc.stdin.write(chunk)
                except Exception as e:
                    write_err = str(e)   # 管道断开:mysql 已停止消费 stdin(大概率已退出)
                    break
                done += len(chunk)
                pct = min(100.0, round(done / prog_total * 100, 1) if prog_total else 100.0)
                cb(percent=pct, message=f"已还原 {_fmt_size(done)} / 约{_fmt_size(prog_total)} ({pct}%)",
                   detail=f"[还原] {_fmt_size(done)}/{_fmt_size(prog_total)}")
    except Exception as e:
        write_err = f"读取还原文件失败: {e}"
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
    rc = proc.wait()
    err = "\n".join(stderr_parts).strip()
    elapsed = round(time.time() - start, 1)

    # 写 stdin 失败时,mysql 的真实原因在其自身 stderr 中(常见:连接中断 / 超过
    # max_allowed_packet / 权限不足)。把两者拼起来,避免旧逻辑只报 "还原中断",丢掉根因。
    if write_err:
        ok = False
        if err:
            reason = f"{write_err} | mysql 输出: {err[:800]}"
        else:
            reason = (f"{write_err} | mysql 未输出错误信息,请检查目标库状态与 "
                      f"max_allowed_packet(超大语句易触发 Lost connection)")
    else:
        ok = rc == 0
        reason = err[:800] if not ok else ""

    record = {
        "id": uuid.uuid4().hex[:12], "type": "restore",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "host": f"{conn_cfg.get('host', '')}:{conn_cfg.get('port', '')}",
        "dbs": [target_db or "(文件自带建库)"], "path": file_path,
        "size": total, "elapsed": elapsed,
        "result": "success" if ok else "failed",
        "warning": warn, "error": reason,
    }
    _apply_record_columns(record,
        target=target_db or "(自带)",
        object=target_db or "(文件自带建库)",
        file_path=file_path, size=total, elapsed=elapsed)
    _save_history(record)
    _log("还原", f"目标={target_db or '(自带)'} 文件={file_path} ({elapsed}s)", ok=ok)
    cb(phase="完成", percent=100.0 if ok else 0,
       detail=f"结果: {'成功' if ok else '失败'}")
    return record


def start_restore_task(conn_cfg, target_db, file_path, extra_opts=None, storage="local"):
    tid = _new_task("restore", "还原数据库")

    def worker():
        try:
            record = run_restore(conn_cfg, target_db, file_path, extra_opts=extra_opts, storage=storage,
                                 progress_cb=lambda **kw: _update_task(tid, **kw))
            _update_task(tid, status="done", phase="完成",
                         percent=100.0 if record["result"] == "success" else 0,
                         result=record,
                         message=f"还原{'成功' if record['result'] == 'success' else '失败'}: {file_path}",
                         error=record.get("error", ""))
        except Exception as e:
            _update_task(tid, status="failed", phase="失败", message=str(e), error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return tid


def _fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"
