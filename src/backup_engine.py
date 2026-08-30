# -*- coding: utf-8 -*-
"""备份/还原引擎:调用 mysqldump / mysql 官方 CLI。
- 支持异步任务(任务管理器)+ 进度回调(备份按表、还原按字节)
- 备份输出到指定目录,支持 gzip 流式压缩
- 历史记录 + 操作日志
"""
import gzip
import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid

import pymysql

import local_store
from config_store import get_settings, _is_full_mode, _get_backend
import env_probe

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
    """动态解析 MySQL 客户端目录(设置值 -> PATH -> 常见目录)。"""
    cfg = get_settings().get("mysql_bin", "")
    return env_probe.find_tool("mysqldump", cfg) \
        or env_probe.find_tool("mysql", cfg) \
        or ""

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
def _cli_args(conn_cfg, tool):
    """构造客户端命令行参数。tool 形如 "mysqldump.exe" / "mysql.exe"。"""
    cfg_bin = (get_settings().get("mysql_bin") or "").strip().strip('"')
    if cfg_bin and os.path.isfile(cfg_bin):
        exe = cfg_bin  # 用户直接配置了完整可执行文件路径
    else:
        path = env_probe.find_tool(tool.rsplit(".", 1)[0], cfg_bin)
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
    """客户端与服务器大版本不一致时返回警告文本,否则空串。"""
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
        rec["exists"] = os.path.exists(rec["path"])
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
        for raw in iter(proc.stderr.readline, b""):
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
        try:
            if gzip_:
                fout = gzip.open(out_path, "wb", compresslevel=6)
            else:
                fout = open(out_path, "wb")
            with fout:
                while True:
                    chunk = proc.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    fout.write(chunk)
                    done_bytes += len(chunk)
                    if total_size:
                        pct = min(100.0, round(done_bytes / total_size * 100, 1))
                        cb(percent=pct,
                           message=f"已导出 {_fmt_size(done_bytes)} / 约{_fmt_size(total_size)} ({pct}%)")
        finally:
            proc.stdout.close()

    st_thread = threading.Thread(target=_read_stderr, daemon=True)
    out_thread = threading.Thread(target=_write_stdout, daemon=True)
    st_thread.start()
    out_thread.start()
    # 先等子进程退出(期间两个读线程持续排空管道,避免缓冲死锁),再收尾线程
    rc = proc.wait()
    out_thread.join()
    st_thread.join()
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    return rc, size, err_lines


def run_backup(conn_cfg, dbs, backup_dir=None, gzip_=True, extra_opts=None, progress_cb=None):
    """同步执行备份(内部支持进度回调)。
    dbs 为空=全部数据库(自动枚举用户库);单库=单文件;多库=每库独立文件打包成 zip。
    extra_opts: None=用 settings 默认(backup_opts),否则为当次 token 列表(可为空)。"""
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
    record.setdefault("target", conn_cfg.get("host", ""))
    record.setdefault("object", ",".join(dbs) if dbs else "* 全部库 *")
    record.setdefault("file_path", out_path)
    record.setdefault("file_size", size)
    record.setdefault("duration_ms", int(elapsed * 1000))
    record.setdefault("operator", "")
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


def run_restore(conn_cfg, target_db, file_path, extra_opts=None, progress_cb=None):
    """同步执行还原(字节进度回调)。extra_opts: None=用 settings 默认(restore_opts)。
    支持单文件(.sql/.sql.gz)与多库打包包(.zip,成员逐个还原)。"""
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
    record.setdefault("target", target_db or "(自带)")
    record.setdefault("object", target_db or "(文件自带建库)")
    record.setdefault("file_path", file_path)
    record.setdefault("file_size", total)
    record.setdefault("duration_ms", int(elapsed * 1000))
    record.setdefault("operator", "")
    _save_history(record)
    _log("还原", f"目标={target_db or '(自带)'} 文件={file_path} ({elapsed}s)", ok=ok)
    cb(phase="完成", percent=100.0 if ok else 0,
       detail=f"结果: {'成功' if ok else '失败'}")
    return record


def start_restore_task(conn_cfg, target_db, file_path, extra_opts=None):
    tid = _new_task("restore", "还原数据库")

    def worker():
        try:
            record = run_restore(conn_cfg, target_db, file_path, extra_opts=extra_opts,
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
