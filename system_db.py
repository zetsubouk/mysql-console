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
        conn.commit()
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


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


class StorageBackend:
    """全量模式存储后端：所有配置/连接/任务/历史/日志/管理员 均走系统库。"""

    def __init__(self, conn_cfg, db_name=DEFAULT_SYS_DB):
        self.conn_cfg = conn_cfg
        self.db_name = db_name

    def _conn(self):
        return _connect_server(self.conn_cfg, db=self.db_name)

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
                    cur.execute(
                        """INSERT INTO mc_connection
                           (id, name, host, port, username, password, note)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        (new_id, payload.get("name", "未命名"),
                         payload.get("host", "127.0.0.1"),
                         int(payload.get("port", 3306)),
                         payload.get("user", "root"),
                         encrypt(payload.get("password", "")),
                         payload.get("note", "")),
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
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_schedule ORDER BY created_at")
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_schedule(self, sid):
        conn = self._conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute("SELECT * FROM mc_schedule WHERE id = %s", (sid,))
                r = cur.fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def save_schedule(self, payload, sid=None):
        conn = self._conn()
        try:
            fields = ("name", "enabled", "cron_expr", "scope", "dbs", "backup_dir",
                      "keep_days", "gzip", "conn_id", "schedule_type")
            if sid:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM mc_schedule WHERE id = %s", (sid,))
                    if not cur.fetchone():
                        raise KeyError("任务不存在")
                    sets, vals = [], []
                    for f in fields:
                        if f in payload:
                            sets.append(f"{f} = %s")
                            vals.append(payload[f])
                    if sets:
                        vals.append(sid)
                        cur.execute(
                            "UPDATE mc_schedule SET " + ", ".join(sets) + " WHERE id = %s",
                            vals,
                        )
                    conn.commit()
                return sid
            else:
                new_id = uuid.uuid4().hex[:12]
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO mc_schedule
                           (id, name, enabled, cron_expr, scope, dbs, backup_dir,
                            keep_days, gzip, conn_id, schedule_type)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (new_id, *(payload.get(f) for f in fields)),
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
