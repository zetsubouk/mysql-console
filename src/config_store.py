# -*- coding: utf-8 -*-
"""连接/配置存储：双模式适配器（统一存储方案 2026-08-27 用户确认）。

- 轻量模式: 全部存本地 SQLite(data/config.db, 见 local_store)。
- 全量模式: 配置/连接/日志/管理员等 **唯一来源 = MySQL 系统库**;
  本地只留“最小 bootstrap”(meta: run_mode/sys_db_name + 一条能连系统库的连接),
  供启动时连系统库。不再整份镜像连接列表 → 消除“删了又回来/残留”的双存储混乱。
"""
import json
import os
import uuid
import hashlib

from cryptography.fernet import Fernet

import local_store

DATA_DIR = local_store.DATA_DIR
KEY_PATH = os.path.join(DATA_DIR, ".secret.key")

DEFAULT_SETTINGS = {
    "backup_dir": "",
    "poll_interval": 5,
    "schedule_enabled": False,
    "schedule_cron": "0 2 * * *",
    "schedule_dbs": [],
    "schedule_keep": 7,
    "mysql_bin": "",
    "query_max_rows": 500,
    "ai_base_url": "",
    "ai_api_key_enc": "",
    "ai_model": "",
    "ai_enabled": False,
    "setup_done": False,
    "run_mode": "lite",
    "sys_db_name": "_mysql_console",
    "admin_username": "",
    "admin_password_hash": "",
    "alert_max_conn": 100,
        "alert_max_slow": 10,
        "alert_max_running": 20,
        "mysql_service_name": "",
        "update_check_interval": "weekly",
        "update_last_check": 0,
        "backup_opts": "",   # mysqldump 额外参数(shlex 拆分;空=内置默认)
        "restore_opts": "",  # mysql 还原额外参数
        "access_token": "",  # 0.0.0.0 暴露时的控制台访问令牌(Fernet 加密存储)
    }


def _load_key():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(KEY_PATH, "wb") as f:
        f.write(key)
    return key


_FERNET = Fernet(_load_key())


def encrypt(plain: str) -> str:
    return _FERNET.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _FERNET.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _hash_password(password: str, salt: bytes = None):
    if salt is None:
        salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
    return salt.hex() + "$" + h.hex()


def _verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, _ = stored.split("$", 1)
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    return _hash_password(password, salt) == stored


class SystemDbUnavailable(Exception):
    """全量模式下系统库不可达(认证/配置/连接读写需要系统库)。"""


# ---------------- 后端分派 + 最小 bootstrap ----------------
def _sys_db_name() -> str:
    return local_store.get_meta("sys_db_name") or "_mysql_console"


def _is_full_config() -> bool:
    return local_store.get_meta("run_mode") == "full"


def _set_bootstrap(conn):
    """把一条能连系统库的连接存为本地最小 bootstrap(全量模式用)。
    conn 的 password 应为明文;存库时加密。"""
    if not conn:
        local_store.set_meta("bootstrap", "null")
        return
    d = {
        "id": conn.get("id", ""), "name": conn.get("name", "未命名"),
        "host": conn.get("host", "127.0.0.1"), "port": int(conn.get("port", 3306)),
        "user": conn.get("user", "root"),
        "password_enc": encrypt(conn.get("password", "")),
        "note": conn.get("note", ""),
    }
    local_store.set_meta_json("bootstrap", d)


def _get_bootstrap_conn_cfg():
    b = local_store.get_meta_json("bootstrap")
    if not b:
        return None
    return {
        "id": b.get("id", ""), "name": b.get("name", "未命名"),
        "host": b.get("host", "127.0.0.1"), "port": int(b.get("port", 3306)),
        "user": b.get("user", "root"),
        "password": decrypt(b.get("password_enc", "")),
        "note": b.get("note", ""),
    }


def _system_db_usable() -> bool:
    if not _is_full_config():
        return False
    try:
        from system_db import is_system_db_ready
        bc = _get_bootstrap_conn_cfg()
        return bool(bc and is_system_db_ready(bc, _sys_db_name()))
    except Exception:
        return False


def _is_full_mode() -> bool:
    return _system_db_usable()


def _get_backend():
    from system_db import StorageBackend
    bc = _get_bootstrap_conn_cfg()
    if not bc:
        raise SystemDbUnavailable("无可用连接配置(需要先初始化系统库)")
    return StorageBackend(bc, db_name=_sys_db_name())


def _require_full():
    """全量模式读写系统库的唯一入口校验:不可达即抛,不回退陈旧本地数据。"""
    if not _system_db_usable():
        raise SystemDbUnavailable("系统库不可用,无法读取/写入数据")


# ---------------- 迁移旧 config.json(首次) ----------------
def _migrate_legacy_json():
    p = os.path.join(DATA_DIR, "config.json")
    if not os.path.exists(p):
        return
    try:
        with open(p, encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return
    try:
        os.rename(p, p + ".legacy")
    except OSError:
        pass
    s = old.get("settings", {}) or {}
    if not local_store.get_meta("run_mode"):
        local_store.set_meta("run_mode", s.get("run_mode", "lite"))
        local_store.set_meta("sys_db_name", s.get("sys_db_name", "_mysql_console"))
        local_store.set_meta("setup_done", "1" if s.get("setup_done") else "0")
        if old.get("active_conn_id"):
            local_store.set_meta("active_conn_id", old["active_conn_id"])
    # 轻量连接/设置迁入
    for c in old.get("connections", []):
        cid = c.get("id", uuid.uuid4().hex[:12])
        try:
            local_store.save_connection({
                "name": c.get("name", "未命名"), "host": c.get("host", "127.0.0.1"),
                "port": int(c.get("port", 3306)), "user": c.get("user", "root"),
                "password": c.get("password", ""), "note": c.get("note", ""),
            }, cid)
        except Exception:
            pass
    for k, v in s.items():
        if k in DEFAULT_SETTINGS:
            try:
                local_store.save_settings({k: v})
            except Exception:
                pass
    # 全量模式:用旧文件层激活连接作为 bootstrap
    if local_store.get_meta("run_mode") == "full":
        conns = old.get("connections", [])
        act = next((c for c in conns if c.get("id") == old.get("active_conn_id")), None) or \
            (conns[0] if conns else None)
        if act:
            _set_bootstrap({
                "id": act.get("id"), "name": act.get("name"), "host": act.get("host", "127.0.0.1"),
                "port": int(act.get("port", 3306)), "user": act.get("user", "root"),
                "password": decrypt(act.get("password", "")), "note": act.get("note", ""),
            })


_migrate_legacy_json()


# ---------------- 连接(全量=系统库唯源 / 轻量=SQLite) ----------------
def list_connections():
    if _is_full_config():
        _require_full()
        return _get_backend().list_connections()
    return _clean_lite_rows()


def _clean_lite_rows():
    out = []
    for c in local_store.list_connections():
        d = dict(c)
        d["has_password"] = bool(d.get("password"))
        d.pop("password", None)
        d["active"] = bool(d.get("active"))
        out.append(d)
    return out


def get_connection(cid):
    if _is_full_config():
        _require_full()
        return _get_backend().get_connection(cid)
    return _clean_lite_row(local_store.get_connection(cid))


def _clean_lite_row(c):
    if not c:
        return None
    d = dict(c)
    d["password"] = decrypt(d.get("password", ""))
    d["active"] = bool(d.get("active"))
    return d


def _refresh_bootstrap_from_db():
    """全量:把系统库里 active 连接刷新为最小 bootstrap;若 active 被删则滚动到其他连接。"""
    try:
        if not _system_db_usable():
            return
        b = _get_backend()
        conns = b.list_connections()
        act = next((c for c in conns if c.get("active")), None) or \
            (conns[0] if conns else None)
        if act:
            full = b.get_connection(act["id"])
            _set_bootstrap({
                "id": act.get("id"), "name": act.get("name", "未命名"),
                "host": act.get("host", "127.0.0.1"), "port": int(act.get("port", 3306)),
                "user": act.get("username", "root"),
                "password": full.get("password", "") if full else "",
                "note": act.get("note", ""),
            })
        else:
            _set_bootstrap(None)
    except Exception:
        pass


def save_connection(payload, cid=None):
    if _is_full_config():
        _require_full()
        return _get_backend().save_connection(payload, cid)
    data = {
        "name": payload.get("name", "未命名"), "host": payload.get("host", "127.0.0.1"),
        "port": int(payload.get("port", 3306)), "user": payload.get("user", "root"),
        "password": encrypt(payload.get("password", "")), "note": payload.get("note", ""),
    }
    # SSH 隧道 + 备份目录字段透传(远程备份用;缺省关闭)
    for k, dv in (("ssh_enabled", False), ("ssh_host", ""), ("ssh_port", 22),
                  ("ssh_user", ""), ("ssh_key", ""), ("ssh_bind_host", ""),
                  ("ssh_bind_port", 0), ("backup_dir", ""), ("remote_backup_dir", "")):
        if payload.get(k) is not None:
            data[k] = payload.get(k)
        elif k not in data:
            data[k] = dv
    return local_store.save_connection(data, cid)


def delete_connection(cid):
    if _is_full_config():
        _require_full()
        b = _get_backend()
        b.delete_connection(cid)
        _refresh_bootstrap_from_db()
        return
    local_store.delete_connection(cid)


def get_active_conn_id():
    if _is_full_config():
        if _system_db_usable():
            for c in _get_backend().list_connections():
                if c.get("active"):
                    return c["id"]
        # 系统库不可达时返回本地记录(通常为空),避免启动即崩;不引入陈旧连接数据
        return local_store.get_meta("active_conn_id")
    return local_store.get_meta("active_conn_id")


def set_active_conn_id(cid):
    if _is_full_config():
        _require_full()
        _get_backend().set_active_conn(cid)
        _refresh_bootstrap_from_db()
        return
    local_store.set_active_conn(cid)
    local_store.set_meta("active_conn_id", cid)


def set_file_active_conn(cid):
    """仅落本地激活标识(不碰系统库)。保留给引导初始化 bootstrap 就位。"""
    if _is_full_config():
        return
    local_store.set_active_conn(cid)
    local_store.set_meta("active_conn_id", cid)


# ---------------- 设置(全量=系统库唯源 / 轻量=SQLite) ----------------
def _lite_settings():
    d = dict(DEFAULT_SETTINGS)
    for k, v in local_store.get_settings().items():
        try:
            d[k] = json.loads(v)
        except Exception:
            d[k] = v
    return d


def get_settings():
    if _is_full_config():
        usable = _system_db_usable()
        if usable:
            try:
                s = _get_backend().get_settings()
            except Exception:
                s = None
        else:
            s = None
        if s is None:
            return dict(DEFAULT_SETTINGS)  # 系统库不可达返回默认(非陈旧用户数据)
        # 运行模式/系统库名以本地 meta 为唯一权威,不依赖系统库存值(切全量后系统库
        # 仍可能残存 'lite', 避免前端据此误判回退轻量)
        s["run_mode"] = "full"
        s["sys_db_name"] = _sys_db_name()
        return s
    return _lite_settings()


def save_settings(patch: dict):
    if _is_full_config():
        if not _system_db_usable():
            raise SystemDbUnavailable("系统库不可用,无法保存设置")
        return _get_backend().save_settings(patch)
    for k, v in patch.items():
        if k in DEFAULT_SETTINGS:
            local_store.save_settings({k: v})
    return _lite_settings()


def get_access_token() -> str:
    """读取访问令牌明文(Fernet 解密后返回)。"""
    raw = get_settings().get("access_token", "")
    return decrypt(raw) if raw else ""


def set_access_token(token: str):
    """设置访问令牌(加密落库)。传空字符串表示清除。"""
    enc = encrypt(token) if token else ""
    save_settings({"access_token": enc})


def prepare_full(sys_db_name: str, conn_cfg):
    """引导初始化:把模式/库名/bootstrap 写入本地 meta(全量链路唯一权威)。"""
    local_store.set_meta("run_mode", "full")
    local_store.set_meta("sys_db_name", sys_db_name)
    local_store.set_meta("setup_done", "1")
    _set_bootstrap(conn_cfg)


def prepare_lite():
    local_store.set_meta("run_mode", "lite")
    local_store.set_meta("setup_done", "1")
    if _is_full_config():
        _set_bootstrap(None)


def reset_local():
    """重新引导 = 清空本地全部配置。"""
    local_store.reset_all()


# ---------------- 管理员(全量=系统库 / 轻量=SQLite,少有登录) ----------------
def get_admin_username() -> str:
    if _is_full_config():
        if _system_db_usable():
            try:
                a = _get_backend().get_admin()
                if a and a.get("username"):
                    return a["username"]
            except Exception:
                pass
        return ""
    return _lite_settings().get("admin_username", "")


def is_password_set() -> bool:
    if _is_full_config():
        if _system_db_usable():
            try:
                a = _get_backend().get_admin()
                return bool(a and a.get("password_hash"))
            except Exception:
                return True  # 系统库可达却读失败 → 保守保持登录页
        # 系统库不可达:管理员凭据(在系统库)已不可用,且无敏感数据可读。
        # 放行「重新运行引导」做重新初始化,避免卡死在登录页(死锁)。
        return False
    return bool(_lite_settings().get("admin_password_hash"))


def verify_admin(password: str) -> bool:
    if _is_full_config():
        try:
            a = _get_backend().get_admin()
        except Exception as e:
            raise SystemDbUnavailable(str(e))
        if not a:
            return False
        return _verify_password(password, a.get("password_hash", ""))
    return _verify_password(password, _lite_settings().get("admin_password_hash", ""))


def set_admin(username: str, password: str):
    if _is_full_config():
        b = _get_backend()
        a = b.get_admin()
        phash = _hash_password(password) if password else (a.get("password_hash", "") if a else "")
        b.set_admin(username, phash)
        return
    local_store.save_settings({"admin_username": username})
    if password:
        local_store.save_settings({"admin_password_hash": _hash_password(password)})


def set_admin_password(password: str):
    if _is_full_config():
        b = _get_backend()
        a = b.get_admin()
        if not a:
            raise ValueError("管理员账号不存在")
        b.set_admin(a["username"], _hash_password(password))
        return
    local_store.save_settings({"admin_password_hash": _hash_password(password)})


def update_admin_login_fail(count, locked_until):
    if _is_full_config():
        _get_backend().update_admin_login_fail(count, locked_until)


def update_admin_login_success():
    if _is_full_config():
        _get_backend().update_admin_login_success()


def get_admin_lock_status():
    if _is_full_config():
        # 系统库不可达时按未锁定处理(真实验证交给调用方的 verify_admin → 503),
        # 否则登录流程在锁状态读取处就会 500,破坏「系统库不可用 → 503」约定。
        try:
            a = _get_backend().get_admin()
        except Exception:
            return False, None
        if not a or not a.get("locked_until"):
            return False, None
        return True, a["locked_until"]
    return False, None


def switch_to_full_mode(sys_db_name: str, admin_user: str, admin_pass: str):
    """从轻量切换到全量。创建系统库 + 迁移本地数据 + 设管理员。"""
    from system_db import init_system_db, import_from_file, StorageBackend
    conn_cfg = _resolve_full_mode_conn_cfg()
    if not conn_cfg:
        raise ValueError("无可用连接配置,无法切换全量模式")
    ok, err = init_system_db(conn_cfg, sys_db_name)
    if not ok:
        raise RuntimeError(f"创建系统库失败: {err}")
    import_from_file(conn_cfg, sys_db_name, source="local")
    backend = StorageBackend(conn_cfg, db_name=sys_db_name)
    backend.set_admin(admin_user, _hash_password(admin_pass))
    # 先把模式/系统库名写进系统库配置(系统库存值若残存 'lite' 会误导其它读取)
    backend.save_settings({"run_mode": "full", "sys_db_name": sys_db_name})
    prepare_full(sys_db_name, conn_cfg)
    # 切换全量的连接凭据要固化为当前系统库的 bootstrap,避免重启后靠陈旧 bootstrap 连不上
    _set_bootstrap(conn_cfg)
    local_store.set_meta("setup_done", "1")
    # 清空轻量模式残留数据(连接/配置/调度/历史 JSON),仅保留最小 bootstrap meta
    local_store.clear_lite_data()
    return True


def _resolve_full_mode_conn_cfg():
    """解析连系统库的连接用配置(明文)。

    - 已配置 bootstrap(全量或引导期): 直接用。
    - 轻量模式: bootstrap 从不写,改从本地活动连接取,无活动则取第一个连接。
    """
    bc = None
    try:
        bc = _get_bootstrap_conn_cfg()
    except Exception:
        bc = None
    if bc:
        return bc
    # 轻量模式:本地连接表取活动连接(优先)或第一个连接
    if not _is_full_config():
        cid = local_store.get_meta("active_conn_id")
        row = local_store.get_connection(cid) if cid else None
        if not row:
            conns = local_store.list_connections()
            row = conns[0] if conns else None
        if row:
            return {
                "id": row.get("id", ""), "name": row.get("name", "未命名"),
                "host": row.get("host", "127.0.0.1"), "port": int(row.get("port", 3306)),
                "user": row.get("user", "root"),
                "password": decrypt(row.get("password", "")),
                "note": row.get("note", ""),
            }
    return None


# ---------------- 操作日志(仅全量模式) ----------------
def add_operation_log(level: str, message: str, operator: str = ""):
    if not _is_full_mode():
        return
    try:
        _get_backend().add_log(level, message[:2000], operator)
    except Exception:
        pass


def get_operation_logs(limit: int = 300):
    if not _is_full_mode():
        return []
    try:
        rows = _get_backend().list_logs(limit)
        rows.reverse()
        out = []
        for r in rows:
            ts = r.get("created_at")
            ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if hasattr(ts, "strftime") else str(ts or "")
            op = (" | 操作人:" + str(r["operator"])) if r.get("operator") else ""
            out.append(f"[{ts_s}] {r.get('level', '')} {r.get('message', '')}{op}")
        return out
    except Exception:
        return []