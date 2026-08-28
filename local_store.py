# -*- coding: utf-8 -*-
"""本地 SQLite 存储层(data/config.db)。

统一存储方案(2026-08-27 用户确认):
- 轻量模式: 配置/连接/调度/历史 等全部存这里(config.db)。
- 全量模式: 连接/配置/日志等唯一来源是 MySQL 系统库;这里只保留
  "最小 bootstrap"(meta: run_mode/sys_db_name/active_conn_id + 一条能连系统库的连接)
  供服务启动时连系统库 —— 不再整份镜像连接列表(消除双存储的"删了又回来"/残留)。
"""
import os
import sqlite3
import json
import uuid
from contextlib import contextmanager

# 数据目录:默认项目 data/;可用环境变量 MC_DATA_DIR 覆盖(测试隔离/可移植部署用)。
DATA_DIR = os.environ.get("MC_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "config.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS connections (
    id        TEXT PRIMARY KEY,
    name      TEXT,
    host      TEXT,
    port      INTEGER,
    user      TEXT,
    password  TEXT,          -- Fernet 加密
    note      TEXT,
    is_active INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    return c


@contextmanager
def _db(commit=False):
    """连接上下文:用完关闭(Windows 下 WAL 连接不关会锁住文件导致 reset 删不掉)。"""
    c = _connect()
    try:
        yield c
        if commit:
            c.commit()
    finally:
        c.close()


# ---------------- meta ----------------
def get_meta(key, default=None):
    with _db() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_meta(key, value):
    with _db(commit=True) as c:
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_meta_json(key, default=None):
    v = get_meta(key)
    if v is None:
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


def set_meta_json(key, obj):
    set_meta(key, json.dumps(obj, ensure_ascii=False))


# ---------------- connections(仅轻量模式使用) ----------------
def list_connections():
    with _db() as c:
        rows = c.execute("SELECT * FROM connections ORDER BY rowid").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["active"] = bool(d.pop("is_active"))
        out.append(d)
    return out


def get_connection(cid):
    with _db() as c:
        r = c.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["active"] = bool(d.pop("is_active"))
    return d


def save_connection(payload, cid=None):
    with _db(commit=True) as c:
        if cid:
            cur = c.execute("SELECT id FROM connections WHERE id=?", (cid,))
            if not cur.fetchone():
                raise KeyError("连接不存在")
            sets, vals = [], []
            if payload.get("password"):
                sets.append("password=?")
                vals.append(payload["password"])
            for f in ("name", "host", "port", "user", "note"):
                if f in payload:
                    sets.append(f"{f}=?")
                    vals.append(payload[f])
            if sets:
                vals.append(cid)
                c.execute("UPDATE connections SET " + ", ".join(sets) + " WHERE id=?", vals)
            return cid
        new_id = uuid.uuid4().hex[:12]
        c.execute(
            "INSERT INTO connections(id,name,host,port,user,password,note) "
            "VALUES(?,?,?,?,?,?,?)",
            (new_id, payload.get("name", "未命名"), payload.get("host", "127.0.0.1"),
             int(payload.get("port", 3306)), payload.get("user", "root"),
             payload.get("password", ""), payload.get("note", "")),
        )
        return new_id


def delete_connection(cid):
    with _db(commit=True) as c:
        c.execute("DELETE FROM connections WHERE id=?", (cid,))


def set_active_conn(cid):
    with _db(commit=True) as c:
        c.execute("UPDATE connections SET is_active=0")
        if cid:
            c.execute("UPDATE connections SET is_active=1 WHERE id=?", (cid,))


def get_active_conn():
    with _db() as c:
        r = c.execute("SELECT * FROM connections WHERE is_active=1 LIMIT 1").fetchone()
    return dict(r) if r else None


# ---------------- settings(仅轻量模式使用) ----------------
def get_settings():
    with _db() as c:
        rows = c.execute("SELECT key,value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def save_settings(patch: dict):
    with _db(commit=True) as c:
        for k, v in patch.items():
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, json.dumps(v, ensure_ascii=False)))
        rows = c.execute("SELECT key,value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------------- 清理(重新引导=彻底重装) ----------------
def reset_all():
    """清空本地 config.db 全部配置(重新引导时调用)。 WAL 模式下 .db/.db-wal/.db-shm 都要删。"""
    for suffix in ("", "-wal", "-shm"):
        p = DB_PATH + suffix
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    _connect()  # 重建空库


def clear_lite_data():
    """清空轻量模式的连接与配置数据(切换全量后调用),仅保留 meta(最小 bootstrap)。

    切全量后,本地 SQLite 只应保留能连系统库的 meta(run_mode/sys_db_name/bootstrap/
    active_conn_id/setup_done)供启动时 bootstrap;连接列表/配置均由系统库唯源。
    避免轻量数据残留造成"双来源"。
    """
    with _db(commit=True) as c:
        c.execute("DELETE FROM connections")
        c.execute("DELETE FROM settings")
        # 清除调度/历史等批量数据 meta(全量下以系统库为准),保留核心 bootstrap meta
        for k in ("schedules", "backup_history", "admin_username", "admin_password_hash"):
            c.execute("DELETE FROM meta WHERE key=?", (k,))