# -*- coding: utf-8 -*-
"""系统库管理：建库建表 + 旧文件数据迁移 + 全量模式存储后端（Phase 1）。"""
import json
import os
import time
import uuid

import pymysql

from config_store import encrypt, decrypt

# 系统库默认名
DEFAULT_SYS_DB = "_mysql_console"


def _ssh_full_val(name, v):
    """SSH 字段落库值:布尔/整数规范化,文本转字符串。"""
    if name == "ssh_enabled":
        return 1 if v else 0
    if name in ("ssh_port", "ssh_bind_port"):
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    return str(v or "")

# 建表 SQL
_CREATE_TABLES = [
    """CREATE TABLE IF NOT EXISTS mc_config (
        config_key   VARCHAR(64) PRIMARY KEY,
        config_value TEXT,
        updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mc_connection (
        id          VARCHAR(16) PRIMARY KEY,
        name        VARCHAR(128) NOT NULL,
        host        VARCHAR(128) NOT NULL,
        port        INT NOT NULL DEFAULT 3306,
        username    VARCHAR(64) NOT NULL,
        password    TEXT NOT NULL,
        note        TEXT,
        ssh_enabled TINYINT DEFAULT 0,
        ssh_host    VARCHAR(128) DEFAULT '',
        ssh_port    INT DEFAULT 22,
        ssh_user    VARCHAR(64) DEFAULT '',
        ssh_key     VARCHAR(512) DEFAULT '',   -- 私钥路径;用 VARCHAR 而非 TEXT:MySQL 5.7/<8.0.13 不允许 TEXT DEFAULT
        ssh_bind_host VARCHAR(128) DEFAULT '',
        ssh_bind_port INT DEFAULT 0,
        remote_os   VARCHAR(16) DEFAULT '',
        is_active   TINYINT DEFAULT 0,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mc_schedule (
        id              VARCHAR(16) PRIMARY KEY,
        name            VARCHAR(128) NOT NULL,
        enabled         TINYINT DEFAULT 0,
        cron_expr       VARCHAR(64),
        scope           VARCHAR(16),
        dbs             TEXT,
        backup_dir      TEXT,
        keep_days       INT DEFAULT 7,
        gzip            TINYINT DEFAULT 1,
        conn_id         VARCHAR(16),
        schedule_type   VARCHAR(16),
        extra           TEXT,
        last_run        DATETIME,
        last_status     VARCHAR(16),
        native_registered TINYINT DEFAULT 0,
        created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mc_backup_history (
        id          VARCHAR(16) PRIMARY KEY,
        type        VARCHAR(16),
        target      VARCHAR(128),
        object      TEXT,
        file_path   TEXT,
        file_size   BIGINT,
        duration_ms INT,
        result      VARCHAR(16),
        error_msg   TEXT,
        operator    VARCHAR(64),
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mc_operation_log (
        id          BIGINT AUTO_INCREMENT PRIMARY KEY,
        level       VARCHAR(8),
        message     TEXT,
        operator    VARCHAR(64),
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_created (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mc_admin (
        id              INT PRIMARY KEY DEFAULT 1,
        username        VARCHAR(64) NOT NULL,
        password_hash   VARCHAR(255) NOT NULL,
        login_fail_count INT DEFAULT 0,
        locked_until    DATETIME,
        last_login      DATETIME,
        updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def _connect_server(conn_cfg, db=None):
    """连接 MySQL 服务器（不指定库或指定库）。"""
    return pymysql.connect(
        host=conn_cfg.get("host", "127.0.0.1"),
        port=int(conn_cfg.get("port", 3306)),
        user=conn_cfg.get("user", "root"),
        password=conn_cfg.get("password", ""),
        database=db,
        connect_timeout=10,
        charset="utf8mb4",
    )


def init_system_db(conn_cfg, db_name=DEFAULT_SYS_DB):
    """创建系统库和所有表。返回 (ok, error_msg)。"""
    conn = _connect_server(conn_cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE DATABASE IF NOT EXISTS `%s` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci" % db_name
            )
        conn.commit()
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()

    conn = _connect_server(conn_cfg, db=db_name)
    try:
        with conn.cursor() as cur:
            for sql in _CREATE_TABLES:
                cur.execute(sql)
            _migrate_connection_columns(cur)
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


# mc_connection 表新增列的历史迁移(对已存在的旧库幂等补齐)
_CONN_MIGRATE = [
    ("ssh_enabled", "TINYINT DEFAULT 0"),
    ("ssh_host", "VARCHAR(128) DEFAULT ''"),
    ("ssh_port", "INT DEFAULT 22"),
    ("ssh_user", "VARCHAR(64) DEFAULT ''"),
    ("ssh_key", "VARCHAR(512) DEFAULT ''"),   # 私钥路径;VARCHAR 兼容 MySQL 5.7/<8.0.13(TEXT 不能带 DEFAULT)
    ("ssh_bind_host", "VARCHAR(128) DEFAULT ''"),
    ("ssh_bind_port", "INT DEFAULT 0"),
    ("backup_dir", "VARCHAR(512) DEFAULT ''"),
    ("remote_backup_dir", "VARCHAR(512) DEFAULT ''"),
    ("remote_os", "VARCHAR(16) DEFAULT ''"),
    ("db_version", "VARCHAR(16) DEFAULT ''"),  # 数据库版本族: ''/auto/5.7/8.x(备份按此选内置工具)
]


def _migrate_connection_columns(cur):
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mc_connection'")
    cols = {r[0] for r in cur.fetchall()}
    for name, ddl in _CONN_MIGRATE:
        if name not in cols:
            cur.execute("ALTER TABLE mc_connection ADD COLUMN %s %s" % (name, ddl))


# mc_connection 列迁移进程内一次性开关(与 _ensure_extra_col 同模式)。
_conn_cols_ready = False


def _ensure_conn_cols(conn):
    """旧系统库自动补 mc_connection 缺失列(运行期幂等)。

    背景:列迁移(ssh_* / backup_dir / remote_os)只在 init_system_db(建库)时执行,
    旧版建的系统库缺这些列,全量模式编辑连接保存会报 `Unknown column '...'`(1054)。
    这里在连接表操作前兜底补齐。

    健壮性:逐列尝试 ALTER,单列失败不阻断其余;**未全部补齐则不置 ready**,下次访问自动重试
    (此前一列失败即置 ready,导致永不重试——曾因 MySQL 5.7 不支持 TEXT DEFAULT 卡在 ssh_key)。
    失败原因打印到 stderr 便于定位。
    """
    global _conn_cols_ready
    if _conn_cols_ready:
        return
    pending = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mc_connection'")
        cols = {r[0] for r in cur.fetchall()}
        for name, ddl in _CONN_MIGRATE:
            if name not in cols:
                pending.append((name, ddl))
    if not pending:
        _conn_cols_ready = True
        return
    failed = []
    for name, ddl in pending:
        try:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE mc_connection ADD COLUMN %s %s" % (name, ddl))
            conn.commit()
        except Exception as e:
            failed.append("%s: %s" % (name, e))
    if not failed:
        _conn_cols_ready = True
    else:
        import sys as _sys
        print("[system_db] 补 mc_connection 列失败,下次访问将重试: %s"
              % "; ".join(failed), file=_sys.stderr)


def get_sys_conn(conn_cfg, db_name=DEFAULT_SYS_DB):
    """获取系统库连接。"""
    return _connect_server(conn_cfg, db=db_name)


def is_system_db_ready(conn_cfg, db_name=DEFAULT_SYS_DB):
    """检查系统库是否已初始化。"""
    try:
        conn = _connect_server(conn_cfg, db=db_name)
        conn.close()
        return True
    except Exception:
        return False


def import_from_file(conn_cfg, db_name=DEFAULT_SYS_DB, source="local"):
    """把轻量模式本地数据(SQLite, 见 local_store)迁移到系统库。返回 (ok, error_msg, counts)。
    source 保留参数以兼容;统一从本地 SQLite 读取。"""
    import local_store
    counts = {"connections": 0, "settings": 0, "schedules": 0, "history": 0, "logs": 0}
    conn = _connect_server(conn_cfg, db=db_name)
    try:
        with conn.cursor() as cur:
            for c in local_store.list_connections():
                raw_pwd = c.get("password", "")
                plain = decrypt(raw_pwd) if raw_pwd else ""
                cur.execute(
                    """INSERT IGNORE INTO mc_connection
                       (id, name, host, port, username, password, note, is_active)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (c.get("id", uuid.uuid4().hex[:12]), c.get("name", "未命名"),
                     c.get("host", "127.0.0.1"), int(c.get("port", 3306)),
                     c.get("user", "root"), encrypt(plain),
                     c.get("note", ""), 1 if c.get("active") else 0),
                )
                counts["connections"] += 1
            settings = local_store.get_settings()
            for k, v in settings.items():
                if k in ("admin_username", "admin_password_hash"):
                    continue
                try:
                    val = json.loads(v) if isinstance(v, str) else v
                except Exception:
                    val = v
                cur.execute(
                    """INSERT INTO mc_config (config_key, config_value)
                       VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)""",
                    (k, json.dumps(val, ensure_ascii=False)),
                )
                counts["settings"] += 1
        conn.commit()
        return True, "", counts
    except Exception as e:
        return False, str(e), counts
    finally:
        conn.close()


# ---- 统一任务模型 <-> mc_schedule 行 转换 ----
# mc_schedule 表结构是旧模型(cron_expr/schedule_type);为与轻量模式统一,
# freq/time/interval_hours/weekday/day_of_month/at_once 序列化为 JSON 存 extra 列,
# cron_expr 由统一模型生成(仅作人工排查与兜底);extra 为空的旧数据从 cron_expr 反解。
_EXTRA_FIELDS = ("freq", "time", "interval_hours", "weekday", "day_of_month", "at_once")

_extra_col_ready = False


def _ensure_extra_col(conn):
    """旧系统库自动补 mc_schedule.extra 列(进程内只执行一次)。"""
    global _extra_col_ready
    if _extra_col_ready:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mc_schedule' "
            "AND COLUMN_NAME = 'extra'")
        if cur.fetchone()[0] == 0:
            cur.execute("ALTER TABLE mc_schedule ADD COLUMN extra TEXT")
    conn.commit()
    _extra_col_ready = True


def _hm(cron_h, cron_m):
    try:
        return "%02d:%02d" % (int(cron_h), int(cron_m))
    except (TypeError, ValueError):
        return "00:00"


def _cron_to_extra(cron_expr):
    """旧数据(仅 cron_expr,extra 为空)反解统一模型字段。"""
    parts = (cron_expr or "").split()
    if len(parts) != 5:
        return {"freq": "daily", "time": "00:00"}
    m, h, dom, dow = parts[0], parts[1], parts[2], parts[4]
    if dom != "*" and dom.isdigit():
        return {"freq": "monthly", "time": _hm(h, m),
                "day_of_month": max(1, min(31, int(dom)))}
    if dow != "*" and dow.isdigit():
        return {"freq": "weekly", "time": _hm(h, m), "weekday": int(dow) % 7}
    if h.startswith("*/"):
        try:
            return {"freq": "hourly", "interval_hours": max(1, int(h[2:]))}
        except ValueError:
            return {"freq": "daily", "time": "00:00"}
    return {"freq": "daily", "time": _hm(h, m)}


def _task_to_row(t):
    """统一任务模型 -> mc_schedule 行。"""
    extra = {k: t.get(k) for k in _EXTRA_FIELDS}
    tm = t.get("time") or "00:00"
    h, m = (tm.split(":") + ["0", "0"])[:2]
    freq = t.get("freq") or "daily"
    cron = None
    if freq == "hourly":
        cron = "0 */%s * * *" % t.get("interval_hours", 1)
    elif freq == "weekly":
        cron = "%s %s * * %s" % (m, h, t.get("weekday", 0))
    elif freq == "monthly":
        cron = "%s %s %s * *" % (m, h, t.get("day_of_month", 1))
    elif freq == "daily":
        cron = "%s %s * * *" % (m, h)
    return {
        "name": t.get("name") or "未命名",
        "enabled": 1 if t.get("enabled") else 0,
        "cron_expr": cron,
        "scope": "pick" if t.get("dbs") else "all",
        "dbs": json.dumps(t.get("dbs") or [], ensure_ascii=False),
        "backup_dir": t.get("backup_dir") or "",
        "keep_days": t.get("keep", 7),
        "gzip": 1,
        "conn_id": t.get("conn_id") or "",
        "schedule_type": t.get("engine") or "builtin",
        "native_registered": 1 if t.get("native_registered") else 0,
        "extra": json.dumps(extra, ensure_ascii=False),
    }


def _row_to_task(r):
    """mc_schedule 行 -> 统一任务模型(与轻量模式 schedule_store 字段一致)。"""
    r = dict(r)
    try:
        dbs = json.loads(r.get("dbs") or "[]")
    except Exception:
        dbs = []
    try:
        ex = json.loads(r.get("extra") or "{}")
    except Exception:
        ex = {}
    if not isinstance(ex, dict) or not ex:
        ex = _cron_to_extra(r.get("cron_expr"))
    t = {
        "id": r.get("id", ""),
        "name": r.get("name", ""),
        "enabled": bool(r.get("enabled")),
        "engine": r.get("schedule_type") or "builtin",
        "dbs": dbs,
        "keep": r.get("keep_days") if r.get("keep_days") is not None else 7,
        "backup_dir": r.get("backup_dir") or "",
        "conn_id": r.get("conn_id") or "",
        "last_run": str(r.get("last_run")) if r.get("last_run") else "",
        "last_result": r.get("last_status") or "",
        "native_registered": bool(r.get("native_registered")),
    }
    for k in _EXTRA_FIELDS:
        if ex.get(k) is not None:
            t[k] = ex[k]
    t.setdefault("freq", "daily")
    t.setdefault("time", "00:00")
    t.setdefault("interval_hours", 1)
    t.setdefault("weekday", 0)
    t.setdefault("day_of_month", 1)
    t.setdefault("at_once", "")
    return t


class StorageBackend:
    """全量模式存储后端：所有配置/连接/任务/历史/日志/管理员 均走系统库。"""

    def __init__(self, conn_cfg, db_name=DEFAULT_SYS_DB):
        self.conn_cfg = conn_cfg
        self.db_name = db_name

    def _conn(self):
        conn = _connect_server(self.conn_cfg, db=self.db_name)
        _ensure_conn_cols(conn)
        return conn

    # ---- 配置 ----
    def get_settings(self):
        from config_store import DEFAULT_SETTINGS
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT config_key, config_value FROM mc_config")
                settings = dict(DEFAULT_SETTINGS)
                for k, v in cur.fetchall():
                    try:
                        settings[k] = json.loads(v)
                    except Exception:
                        settings[k] = v
                return settings
        finally:
            conn.close()

    def save_settings(self, patch):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for k, v in patch.items():
                    cur.execute(
                        """INSERT INTO mc_config (config_key, config_value)
                           VALUES (%s, %s)
                           ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)""",
                        (k, json.dumps(v, ensure_ascii=False)),
                    )
            conn.commit()
        finally:
            conn.close()
        return self.get_settings()

    # ---- 连接 ----
    def list_connections(self):
        conn = self._conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_connection ORDER BY created_at")
                rows = cur.fetchall()
            out = []
            for r in rows:
                item = dict(r)
                item["has_password"] = bool(item.get("password"))
                item.pop("password", None)
                item["active"] = bool(item.get("is_active"))
                if "user" not in item:                    # 系统库列为 username,统一为 user(全链路)
                    item["user"] = item.get("username", "")
                out.append(item)
            return out
        finally:
            conn.close()

    def get_connection(self, cid):
        conn = self._conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_connection WHERE id = %s", (cid,))
                r = cur.fetchone()
            if not r:
                return None
            r = dict(r)
            r["password"] = decrypt(r.get("password", ""))
            if "user" not in r:                          # 系统库列为 username,统一为 user
                r["user"] = r.get("username", "")
            return r
        finally:
            conn.close()

    def save_connection(self, payload, cid=None):
        conn = self._conn()
        try:
            if cid:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM mc_connection WHERE id = %s", (cid,))
                    if not cur.fetchone():
                        raise KeyError("连接不存在")
                    sets = []
                    vals = []
                    if payload.get("password"):
                        sets.append("password = %s")
                        vals.append(encrypt(payload["password"]))
                    for f in ("host", "port", "name", "note"):
                        if f in payload:
                            sets.append(f"{f} = %s")
                            vals.append(payload[f])
                    if "user" in payload:
                        sets.append("username = %s")
                        vals.append(payload["user"])
                    for f in _CONN_MIGRATE:
                        name = f[0]
                        if name in payload:
                            sets.append(f"{name} = %s")
                            vals.append(_ssh_full_val(name, payload.get(name)))
                    if sets:
                        vals.append(cid)
                        cur.execute(
                            "UPDATE mc_connection SET " + ", ".join(sets) + " WHERE id = %s",
                            vals,
                        )
                    conn.commit()
                return cid
            else:
                new_id = uuid.uuid4().hex[:12]
                with conn.cursor() as cur:
                    ssh_cols = [f[0] for f in _CONN_MIGRATE if f[0] in payload]
                    sql_cols = ("id, name, host, port, username, password, note"
                                + "".join(", " + c for c in ssh_cols))
                    ph = ", ".join(["%s"] * (7 + len(ssh_cols)))
                    base_vals = (new_id, payload.get("name", "未命名"),
                                 payload.get("host", "127.0.0.1"),
                                 int(payload.get("port", 3306)),
                                 payload.get("user", "root"),
                                 encrypt(payload.get("password", "")),
                                 payload.get("note", ""))
                    ssh_vals = tuple(_ssh_full_val(n, payload.get(n)) for n in ssh_cols)
                    cur.execute(
                        "INSERT INTO mc_connection (%s) VALUES (%s)" % (sql_cols, ph),
                        base_vals + ssh_vals,
                    )
                    conn.commit()
                return new_id
        finally:
            conn.close()

    def delete_connection(self, cid):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mc_connection WHERE id = %s", (cid,))
            conn.commit()
        finally:
            conn.close()

    def set_active_conn(self, cid):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE mc_connection SET is_active = 0")
                if cid:
                    cur.execute("UPDATE mc_connection SET is_active = 1 WHERE id = %s", (cid,))
            conn.commit()
        finally:
            conn.close()

    # ---- 定时任务 ----
    def list_schedules(self):
        conn = self._conn()
        try:
            _ensure_extra_col(conn)
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_schedule ORDER BY created_at")
                rows = cur.fetchall()
            return [_row_to_task(r) for r in rows]
        finally:
            conn.close()

    def get_schedule(self, sid):
        conn = self._conn()
        try:
            _ensure_extra_col(conn)
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_schedule WHERE id = %s", (sid,))
                r = cur.fetchone()
            return _row_to_task(r) if r else None
        finally:
            conn.close()

    def save_schedule(self, payload, sid=None):
        """接收统一任务模型(schedule_store._default_task 字段集),落库为表列。"""
        conn = self._conn()
        try:
            _ensure_extra_col(conn)
            row = _task_to_row(payload)
            cols = ("name", "enabled", "cron_expr", "scope", "dbs", "backup_dir",
                    "keep_days", "gzip", "conn_id", "schedule_type",
                    "native_registered", "extra")
            if sid:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM mc_schedule WHERE id = %s", (sid,))
                    if not cur.fetchone():
                        raise KeyError("任务不存在")
                    sets, vals = [], []
                    for f in cols:
                        sets.append(f"{f} = %s")
                        vals.append(row[f])
                    vals.append(sid)
                    cur.execute(
                        "UPDATE mc_schedule SET " + ", ".join(sets) + " WHERE id = %s",
                        vals,
                    )
                conn.commit()
                return sid
            new_id = (payload.get("id") or uuid.uuid4().hex[:12])[:16]
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO mc_schedule
                       (id, name, enabled, cron_expr, scope, dbs, backup_dir,
                        keep_days, gzip, conn_id, schedule_type, native_registered, extra)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (new_id, *(row[f] for f in cols)),
                )
            conn.commit()
            return new_id
        finally:
            conn.close()

    def delete_schedule(self, sid):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mc_schedule WHERE id = %s", (sid,))
            conn.commit()
        finally:
            conn.close()

    def update_schedule_status(self, sid, result):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE mc_schedule SET last_run = %s, last_status = %s
                       WHERE id = %s""",
                    (time.strftime("%Y-%m-%d %H:%M:%S"), result, sid),
                )
            conn.commit()
        finally:
            conn.close()

    # ---- 备份历史 ----
    def list_history(self):
        conn = self._conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_backup_history ORDER BY created_at DESC LIMIT 300")
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def add_history(self, record):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO mc_backup_history
                       (id, type, target, object, file_path, file_size,
                        duration_ms, result, error_msg, operator)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (record.get("id", uuid.uuid4().hex[:12]),
                     record.get("type"), record.get("target"), record.get("object"),
                     record.get("file_path"), record.get("file_size"),
                     record.get("duration_ms"), record.get("result"),
                     record.get("error_msg"), record.get("operator")),
                )
            conn.commit()
        finally:
            conn.close()

    def delete_history(self, rid):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mc_backup_history WHERE id = %s", (rid,))
            conn.commit()
        finally:
            conn.close()

    # ---- 操作日志 ----
    def add_log(self, level, message, operator=""):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO mc_operation_log (level, message, operator)
                       VALUES (%s, %s, %s)""",
                    (level, message[:2000], operator),
                )
            conn.commit()
        finally:
            conn.close()

    def list_logs(self, limit=300):
        conn = self._conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM mc_operation_log ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ---- 管理员 ----
    def get_admin(self):
        conn = self._conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_admin WHERE id = 1")
                r = cur.fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def set_admin(self, username, password_hash):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO mc_admin (id, username, password_hash)
                       VALUES (1, %s, %s)
                       ON DUPLICATE KEY UPDATE username = VALUES(username),
                       password_hash = VALUES(password_hash)""",
                    (username, password_hash),
                )
            conn.commit()
        finally:
            conn.close()

    def update_admin_login_fail(self, count, locked_until):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE mc_admin SET login_fail_count = %s, locked_until = %s
                       WHERE id = 1""",
                    (count, locked_until),
                )
            conn.commit()
        finally:
            conn.close()

    def update_admin_login_success(self):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE mc_admin SET login_fail_count = 0, locked_until = NULL,
                       last_login = %s WHERE id = 1""",
                    (time.strftime("%Y-%m-%d %H:%M:%S"),),
                )
            conn.commit()
        finally:
            conn.close()
