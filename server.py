# -*- coding: utf-8 -*-
"""MySQL Console 主服务:HTTP API + 静态页面 + 定时备份调度。"""
import ctypes
import json
import re
import mimetypes
import os
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config_store
import local_store
import mysql_client
import backup_engine
import schedule_store
import native_scheduler
import env_probe

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST = "127.0.0.1"
PORT = 8090

# 客户端断开/重置导致的连接中止(WinError 10053/10054、BrokenPipe)。
# 监控轮询(每 5s 一次 /api/monitor,单次约 1s)期间浏览器刷新页面或发起新请求,
# 会中止旧的半成品连接——这是正常现象,不应作为"服务器错误"打印满屏堆栈。
_CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)

_lock = threading.Lock()
try:
    _current_conn_id = config_store.get_active_conn_id()  # 启动恢复激活连接;系统库未就绪时=None
    if not _current_conn_id:
        _current_conn_id = None
except Exception:
    _current_conn_id = None

# ---------- 登录认证（全量模式）----------
import secrets
import time as _time
import string

# session token -> (username, expire_ts)
_sessions = {}
SESSION_TIMEOUT = 8 * 3600  # 8 小时

# 找回密码验证码 -> (username, expire_ts)
_reset_codes = {}
RESET_CODE_TIMEOUT = 600  # 10 分钟

# 自动更新检查缓存(启动/定时后台填充, 前端徽标读取, 避免每次即时打 GitHub)
_update_cache = {"ts": 0.0, "result": None}

# 无需认证的路径
_AUTH_FREE_PATHS = {"/api/login", "/api/auth-status", "/api/health", "/api/request-reset-code", "/api/reset-password"}


def _is_auth_required(path):
    """全量模式且已设置密码时，需要认证。"""
    if not config_store.is_password_set():
        return False
    return path.startswith("/api/") and path not in _AUTH_FREE_PATHS


def _check_auth(handler):
    """检查请求是否已认证。返回 True 表示通过。"""
    token = handler.headers.get("Authorization", "").strip()
    if token.startswith("Bearer "):
        token = token[7:]
    if not token:
        return False
    sess = _sessions.get(token)
    if not sess:
        return False
    if _time.time() > sess[1]:
        del _sessions[token]
        return False
    return True


def _clear_expired_sessions():
    now = _time.time()
    expired = [t for t, s in _sessions.items() if now > s[1]]
    for t in expired:
        del _sessions[t]
    expired_codes = [c for c, v in _reset_codes.items() if now > v[1]]
    for c in expired_codes:
        del _reset_codes[c]


def _generate_reset_code():
    """生成 6 位数字验证码，输出到终端，返回 code。"""
    code = ''.join(secrets.choice(string.digits) for _ in range(6))
    username = config_store.get_admin_username() or "admin"
    _reset_codes[code] = (username, _time.time() + RESET_CODE_TIMEOUT)
    # 输出到终端（服务端控制台）
    print()
    print("=" * 50)
    print("  [MySQL Console] 找回密码验证码")
    print(f"  用户名: {username}")
    print(f"  验证码: {code}")
    print(f"  有效期: 10 分钟")
    print("=" * 50)
    print()
    return code

# ---------- Windows 原生对话框(ctypes 直接调 Win32 API,不依赖 PowerShell) ----------
class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
        ("lpfnHook", wintypes.LPVOID), ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", wintypes.LPVOID), ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


class BROWSEINFOW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND), ("pidlRoot", wintypes.LPVOID),
        ("pszDisplayName", wintypes.LPWSTR), ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT), ("lpfn", wintypes.LPVOID),
        ("lParam", wintypes.LPARAM), ("iImage", wintypes.INT),
    ]


# Win32 API 绑定(必须显式声明 argtypes/restype,避免 64 位指针被截断)
_GetOpenFileNameW = ctypes.windll.comdlg32.GetOpenFileNameW
_GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
_GetOpenFileNameW.restype = wintypes.BOOL

_SHBrowseForFolderW = ctypes.windll.shell32.SHBrowseForFolderW
_SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
_SHBrowseForFolderW.restype = ctypes.c_void_p  # 返回 PIDL(64 位指针),必须声明,否则截断

_SHGetPathFromIDListW = ctypes.windll.shell32.SHGetPathFromIDListW
_SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
_SHGetPathFromIDListW.restype = wintypes.BOOL

_CoTaskMemFree = ctypes.windll.ole32.CoTaskMemFree
_CoTaskMemFree.argtypes = [ctypes.c_void_p]

_SetForegroundWindow = ctypes.windll.user32.SetForegroundWindow
_SetForegroundWindow.argtypes = [wintypes.HWND]
_SetForegroundWindow.restype = wintypes.BOOL

_AllowSetForegroundWindow = ctypes.windll.user32.AllowSetForegroundWindow
_AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
_AllowSetForegroundWindow.restype = wintypes.BOOL

_SystemParametersInfoW = ctypes.windll.user32.SystemParametersInfoW
_SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
_SystemParametersInfoW.restype = wintypes.BOOL

_SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2001
_SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2002
SPIF_SENDCHANGE = 0x0003


_saved_lock_timeout = None  # 前台锁定超时原值(单槽,无需 push/pop 配对)


def _make_topmost_owner(title):
    """创建一个临时置顶消息窗口作为对话框 owner,保证对话框弹到最前。"""
    global _saved_lock_timeout
    WS_EX_TOPMOST = 0x00000008
    WS_POPUP = 0x80000000
    hwnd = ctypes.windll.user32.CreateWindowExW(
        WS_EX_TOPMOST, "STATIC", title, WS_POPUP,
        -10000, -10000, 1, 1, None, None, None, None)
    if not hwnd:
        return None
    _SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                  SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    # 解除 Windows 前台锁定,允许非前台进程切换到前台(完成后恢复)
    _saved_lock_timeout = None
    try:
        timeout = wintypes.UINT(0)
        if _SystemParametersInfoW(
                SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(timeout), 0):
            _SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, None, SPIF_SENDCHANGE)
            _saved_lock_timeout = timeout.value
        _AllowSetForegroundWindow(ASFW_ANY)
        _SetForegroundWindow(hwnd)
    except Exception:
        pass
    return hwnd


ASFW_ANY = 0xFFFFFFFF
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

_SetWindowPos = ctypes.windll.user32.SetWindowPos
_SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, wintypes.UINT]
_SetWindowPos.restype = wintypes.BOOL


def _destroy_owner(hwnd):
    """销毁临时窗口并恢复前台锁定超时设置。"""
    global _saved_lock_timeout
    if not hwnd:
        return
    ctypes.windll.user32.DestroyWindow(hwnd)
    t = _saved_lock_timeout
    _saved_lock_timeout = None
    if t is not None:
        try:
            _SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0,
                                   wintypes.LPVOID(t), SPIF_SENDCHANGE)
        except Exception:
            pass


def _win_open_file(title, start_dir):
    hwnd = _make_topmost_owner(title)
    try:
        ofn = OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
        ofn.hwndOwner = hwnd
        buf = ctypes.create_unicode_buffer(4096)
        filter_str = "SQL 备份文件 (*.sql;*.sql.gz)\0*.sql;*.sql.gz\0所有文件 (*.*)\0*.*\0\0"
        ofn.lpstrFilter = filter_str
        ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
        ofn.nMaxFile = 4096
        ofn.lpstrTitle = title
        ofn.lpstrInitialDir = start_dir or None
        ofn.Flags = 0x00000800 | 0x00000004  # OFN_PATHMUSTEXIST | OFN_FILEMUSTEXIST
        if _GetOpenFileNameW(ctypes.byref(ofn)):
            return {"path": buf.value}
        return {"canceled": True}
    finally:
        _destroy_owner(hwnd)


def _win_open_dir(title, start_dir):
    hwnd = _make_topmost_owner(title)
    try:
        b = BROWSEINFOW()
        display = ctypes.create_unicode_buffer(260)
        b.hwndOwner = hwnd
        b.lpszTitle = title
        b.ulFlags = 0x00000001 | 0x00000040  # BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
        b.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        pidl = _SHBrowseForFolderW(ctypes.byref(b))
    finally:
        _destroy_owner(hwnd)
    if pidl:
        path_buf = ctypes.create_unicode_buffer(1024)
        if _SHGetPathFromIDListW(pidl, path_buf):
            try:
                _CoTaskMemFree(pidl)
            except Exception:
                pass
            return {"path": path_buf.value}
    return {"canceled": True}


def _get_conn():
    with _lock:
        cid = _current_conn_id
    if not cid:
        raise mysql_client.DbError("尚未选择数据库连接,请先在「连接管理」中激活一个连接")
    cfg = config_store.get_connection(cid)
    if not cfg:
        raise mysql_client.DbError("连接配置不存在,请重新选择")
    return mysql_client.connect(cfg)


def _set_active_conn(cid):
    """设置当前激活连接(内存 + 持久化)。"""
    global _current_conn_id
    with _lock:
        _current_conn_id = cid
    config_store.set_active_conn_id(cid)


def _h(conn):
    return conn


def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


# ---------------- 定时备份调度 ----------------

# 内置调度器:遍历所有 enabled 且 engine=builtin 的任务,按各自周期触发。
_last_fire = {}  # tid -> "YYYYMMDDHHMM" 防止同一分钟重复触发


def _task_backup_dir(task):
    return task.get("backup_dir") or config_store.get_settings().get("backup_dir") or None


def _prune_task_backups(task):
    """按任务的 keep 清理该任务备份目录下的旧备份文件与记录。"""
    keep = int(task.get("keep", 7))
    bdir = os.path.abspath(_task_backup_dir(task) or "")
    records = backup_engine.list_backups()
    mine = [r for r in records if r["type"] == "backup"
            and str(r.get("path", "")).startswith(bdir)]
    if len(mine) <= keep:
        return
    for r in mine[:-keep]:
        try:
            if os.path.exists(r["path"]):
                os.remove(r["path"])
            backup_engine.delete_backup_record(r["id"])
        except Exception:
            pass


def _update_loop():
    """按 settings.update_check_interval 定时检查新版本并缓存。每小时审视一次。"""
    while True:
        try:
            s = config_store.get_settings()
            ival = s.get("update_check_interval", "weekly")
            if ival != "off":
                hour_map = {"hourly": 1, "daily": 24, "weekly": 24 * 7}
                period = hour_map.get(ival, 24 * 7) * 3600
                try:
                    last = float(s.get("update_last_check", 0) or 0)
                except Exception:
                    last = 0
                if (not last) or time.time() - last >= period:
                    import updater
                    _update_cache["result"] = updater.check()
                    _update_cache["ts"] = time.time()
                    config_store.save_settings({"update_last_check": time.time()})
        except Exception:
            pass
        time.sleep(3600)

def scheduler_loop():
    while True:
        try:
            for task in schedule_store.list_tasks():
                if not task.get("enabled") or task.get("engine") != "builtin":
                    continue
                now = time.localtime()
                mark = time.strftime("%Y%m%d%H%M", now)
                due = False
                if task["freq"] == "hourly":
                    n = max(1, int(task.get("interval_hours", 1)))
                    last = task.get("last_run", "")
                    if not last:
                        due = now.tm_min == 0  # 从未跑过:整点触发
                    else:
                        try:
                            elapsed_min = (time.mktime(now) -
                                           time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))) / 60
                            due = elapsed_min >= n * 60 - 19  # 20s 轮询容差
                        except Exception:
                            due = False
                else:
                    # daily/weekly/monthly/once:同一分钟只触发一次
                    due = schedule_store.is_due(task, now) and _last_fire.get(task["id"]) != mark
                if not due:
                    continue
                _last_fire[task["id"]] = mark
                cfg = config_store.get_connection(task.get("conn_id")) \
                    if task.get("conn_id") else None
                if not cfg:
                    print(f"[scheduler] 任务 {task['name']} 绑定的连接不可用,跳过")
                    schedule_store.update_run_status(task["id"], "failed")
                    continue
                try:
                    record = backup_engine.run_backup(
                        cfg, task.get("dbs") or [], backup_dir=_task_backup_dir(task), gzip_=True)
                    ok = record.get("result") == "success"
                    schedule_store.update_run_status(task["id"], "success" if ok else "failed")
                    if ok:
                        _prune_task_backups(task)
                    print(f"[scheduler] 定时备份 [{task['name']}]: {record.get('result')}")
                except Exception as e:
                    schedule_store.update_run_status(task["id"], "failed")
                    print(f"[scheduler] 任务 {task['name']} 执行异常: {e}")
        except Exception as e:
            print(f"[scheduler] 异常: {e}")
        time.sleep(20)


# ---------------- HTTP 服务 ----------------

class Handler(BaseHTTPRequestHandler):
    server_version = "MySQLConsole/1.0"

    def log_message(self, fmt, *args):
        pass

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, _CLIENT_GONE):
            try:
                self.connection.close()
            except Exception:
                pass
            return
        super().handle_error(request, client_address)

    def _send_json(self, obj, code=200):
        # default=str: 系统库行含 created_at 等 datetime,不转换直接 json.dumps 会抛
        # 'datetime is not JSON serializable'(连接列表/日志 500)。全局兜底。
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except _CLIENT_GONE:
            pass  # 客户端已断开,无需(也无法)回写

    def _send_error(self, msg, code=400):
        self._send_json({"error": str(msg)}, code)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _auth_guard(self):
        """认证守卫。未认证时返回 401 并发送 JSON。返回 True 表示通过。"""
        path = self.path.split("?")[0]
        if not _is_auth_required(path):
            return True
        if _check_auth(self):
            return True
        self._send_json({"error": "未登录或登录已过期", "code": 401}, 401)
        return False

    def _current_user(self):
        """从请求 Authorization Bearer token 解析当前登录用户名(无则空)。"""
        token = self.headers.get("Authorization", "").strip()
        if token.startswith("Bearer "):
            token = token[7:]
        entry = _sessions.get(token)
        return entry[0] if entry else ""

    def _log_op(self, action, ok=True, detail="", operator=None):
        """记录操作日志:全量模式入库(operator=当前登录用户),轻量/异常静默。"""
        try:
            config_store.add_operation_log(
                "OK" if ok else "FAIL", f"{action} | {detail}",
                operator if operator is not None else self._current_user())
        except Exception:
            pass

    def _serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        fp = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not fp.startswith(STATIC_DIR) or not os.path.isfile(fp):
            self._send_error("Not Found", 404)
            return
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        with open(fp, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except _CLIENT_GONE:
            pass  # 客户端断开,放弃回写

    def _serve_download(self, fp):
        """流式发送备份文件下载(带附件头,分块写,防大文件占内存)。"""
        name = os.path.basename(fp)
        try:
            size = os.path.getsize(fp)
        except OSError:
            return self._send_error("无法读取文件", 404)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        disp = "attachment; filename=\"{}\"".format(name.replace('"', ""))
        self.send_header("Content-Disposition", disp)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            with open(fp, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except _CLIENT_GONE:
            pass  # 客户端中断下载,放弃回写

    # ---- 路由 ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?")[0]
        if not path.startswith("/api/"):
            self._serve_static(path)
            return
        # 认证守卫
        if not self._auth_guard():
            return
        try:
            self._route_get(path)
        except mysql_client.DbError as e:
            self._send_error(str(e))
        except Exception as e:
            self._send_error(f"服务器错误: {e}", 500)

    def _route_get(self, path):
        if path == "/api/health":
            return self._send_json({"ok": True})
        if path == "/api/auth-status":
            return self._send_json({
                "password_set": config_store.is_password_set(),
                "username": config_store.get_admin_username(),
            })
        if path == "/api/connections":
            try:
                return self._send_json(config_store.list_connections())
            except config_store.SystemDbUnavailable:
                return self._send_json([])  # 系统库不可达显示空(非陈旧残留)
        if path == "/api/overview":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.server_overview(conn))
            finally:
                _close(conn)
        if path == "/api/databases":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.database_list(conn))
            finally:
                _close(conn)
        if path == "/api/users":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.user_list(conn))
            finally:
                _close(conn)
        if path == "/api/processlist":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.process_list(conn))
            finally:
                _close(conn)
        if path == "/api/monitor":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.monitor_metrics(conn))
            finally:
                _close(conn)
        if path == "/api/dashboard/health":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.health_score(conn))
            finally:
                _close(conn)
        if path == "/api/dashboard/innodb":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.innodb_metrics(conn))
            finally:
                _close(conn)
        if path == "/api/dashboard/tablespace":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.tablespace_top(conn))
            finally:
                _close(conn)
        if path == "/api/dashboard/replication":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.replication_status(conn))
            finally:
                _close(conn)
        if path == "/api/alerts":
            conn = _get_conn()
            try:
                s = config_store.get_settings()
                return self._send_json(mysql_client.alerts(
                    conn,
                    max_conn=int(s.get("alert_max_conn", 100)),
                    max_slow=int(s.get("alert_max_slow", 10)),
                    max_running=int(s.get("alert_max_running", 20)),
                ))
            finally:
                _close(conn)
        if path == "/api/variables":
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.variables(conn))
            finally:
                _close(conn)
        if path == "/api/service/status":
            return self._send_json(self._service_status())
        if path.startswith("/api/users/") and path.endswith("/grants"):
            return self._handle_user_grants(path)
        if path == "/api/backups":
            return self._send_json(backup_engine.list_backups())
        if path == "/api/backup-files":
            return self._send_json(backup_engine.list_backup_files())
        if path == "/api/backup-files/download":
            # 从查询串取 file 参数(路径含反斜杠需原样保留,不能做过分割)
            qs = self.path.split("?", 1)
            raw = qs[1] if len(qs) > 1 else ""
            import urllib.parse as _up
            params = _up.parse_qs(raw)
            raw_path = (params.get("file") or [""])[0]
            rp = backup_engine.resolve_backup_file(raw_path)
            if not rp:
                return self._send_error("文件不存在或不在允许的备份目录内", 404)
            return self._serve_download(rp)
        if path.startswith("/api/task/"):
            tid = path.split("/")[-1]
            t = backup_engine.get_task(tid)
            if not t:
                return self._send_error("任务不存在", 404)
            return self._send_json(t)
        if path == "/api/logs":
            return self._send_json(self._read_logs())
        if path == "/api/settings":
            return self._send_json(config_store.get_settings())
        if path == "/api/setup/env":
            return self._send_json(env_probe.env_summary(
                config_store.get_settings().get("mysql_bin", "")))
        if path == "/api/schedules":
            tasks = schedule_store.list_tasks()
            for t in tasks:
                t["desc"] = schedule_store.describe(t)
            return self._send_json(tasks)
        if path == "/api/schedules/env":
            return self._send_json(native_scheduler.env_info())
        if path.startswith("/api/schedules/"):
            parts = path.split("/")
            if len(parts) == 5 and parts[4] == "native-status":
                t = schedule_store.get_task(parts[3])
                if not t:
                    return self._send_error("任务不存在", 404)
                return self._send_json(native_scheduler.status(t))
            self._send_error("未知接口", 404)
            return
        if path.startswith("/api/databases/"):
            name = path.split("/")[-1]
            conn = _get_conn()
            try:
                return self._send_json(mysql_client.database_detail(conn, name))
            finally:
                _close(conn)
        if path == "/api/version":
            from version import __version__
            return self._send_json({"version": __version__})
        if path == "/api/update/check":
            import updater
            r = updater.check()
            _update_cache["result"] = r; _update_cache["ts"] = time.time()
            return self._send_json(r)
        if path == "/api/update/badge":
            r = _update_cache.get("result")
            if (not r) or (not r.get("offline") and time.time() - _update_cache.get("ts", 0) > 6 * 3600):
                import updater
                r = updater.check()
                _update_cache["result"] = r; _update_cache["ts"] = time.time()
            return self._send_json(r)
        if path == "/api/update/status":
            import updater
            return self._send_json({"version": updater.current_version(), "log": updater.read_status()})
        self._send_error("未知接口", 404)

    def _read_logs(self):
        # 2026-08-27: 操作日志已在全量模式入库。全量→读系统库; 轻量模式不记录→空。
        return config_store.get_operation_logs(300)

    def do_POST(self):
        path = self.path.split("?")[0]
        # 认证守卫（登录/认证状态/健康检查除外）
        if not self._auth_guard():
            return
        try:
            self._route_post(path)
        except mysql_client.DbError as e:
            self._send_error(str(e))
        except Exception as e:
            self._send_error(f"服务器错误: {e}", 500)

    def _route_post(self, path):
        body = self._read_body()
        if path == "/api/login":
            return self._handle_login(body)
        if path == "/api/logout":
            return self._handle_logout()
        if path == "/api/change-password":
            return self._handle_change_password(body)
        if path == "/api/change-username":
            return self._handle_change_username(body)
        if path == "/api/switch-to-full-mode":
            return self._handle_switch_to_full_mode(body)
        if path == "/api/request-reset-code":
            return self._handle_request_reset_code()
        if path == "/api/reset-password":
            return self._handle_reset_password(body)
        if path == "/api/connections":
            is_update = bool(body.get("id"))
            cid = config_store.save_connection(body)
            self._log_op("修改连接" if is_update else "新增连接", True,
                         f"{body.get('name')}({body.get('host')}:{body.get('port')})")
            return self._send_json({"id": cid, "ok": True}, 201)
        if path == "/api/connections/test":
            cfg = {
                "host": body.get("host", "127.0.0.1"),
                "port": int(body.get("port", 3306)),
                "user": body.get("user", "root"),
                "password": body.get("password", ""),
            }
            try:
                result = mysql_client.test(cfg)
                return self._send_json(result)
            except mysql_client.DbError as e:
                return self._send_json({"ok": False, "error": str(e)})
        if path == "/api/setup/probe-client":
            r = env_probe.probe_client(body.get("path", ""))
            return self._send_json(r, 200 if r.get("ok") else 400)
        if path == "/api/setup/test-db":
            cfg = {
                "host": body.get("host", "127.0.0.1"),
                "port": int(body.get("port", 3306)),
                "user": body.get("user", "root"),
                "password": body.get("password", ""),
            }
            try:
                result = mysql_client.test(cfg)
                return self._send_json(result)
            except mysql_client.DbError as e:
                return self._send_json({"ok": False, "error": str(e)})
        if path == "/api/setup/db-check":
            cfg = {
                "host": body.get("host", "127.0.0.1"),
                "port": int(body.get("port", 3306)),
                "user": body.get("user", "root"),
                "password": body.get("password", ""),
            }
            target = (body.get("target_db") or "").strip()
            try:
                info = {"target_exists": False, "legacy_dbs": []}
                if target:
                    info["target_exists"] = mysql_client.db_exists(cfg, target)
                # 旧系统库 = 本地 meta 记录的系统库名(若与目标不同)
                if config_store._is_full_config():
                    cur_sys = config_store._sys_db_name()
                    if cur_sys and cur_sys != target:
                        info["legacy_dbs"].append({"name": cur_sys, "exists": mysql_client.db_exists(cfg, cur_sys)})
                return self._send_json(info)
            except mysql_client.DbError as e:
                return self._send_json({"ok": False, "error": str(e)})
        if path == "/api/setup/drop-db":
            cfg = {
                "host": body.get("host", "127.0.0.1"),
                "port": int(body.get("port", 3306)),
                "user": body.get("user", "root"),
                "password": body.get("password", ""),
            }
            db_name = (body.get("db_name") or "").strip()
            if not re.match(r"^[A-Za-z0-9_]+$", db_name):
                return self._send_error("非法的系统库名", 400)
            try:
                mysql_client.drop_db(cfg, db_name)
                self._log_op("删除系统库", True, db_name)
                return self._send_json({"ok": True})
            except mysql_client.DbError as e:
                self._log_op("删除系统库", False, f"{db_name} {e}")
                return self._send_json({"ok": False, "error": str(e)})
        if path == "/api/setup/finish":
            # 引导完成 = 保存设置 + 可选新建连接 + (重新引导时)彻底重装。
            patch = {"setup_done": True}
            if body.get("mysql_bin") is not None:
                patch["mysql_bin"] = str(body.get("mysql_bin") or "").strip()
            if body.get("backup_dir") is not None:
                patch["backup_dir"] = str(body.get("backup_dir") or "").strip()
            run_mode = body.get("run_mode", "lite")
            conn = body.get("conn")
            sys_db_name = body.get("sys_db_name", "_mysql_console").strip() if run_mode == "full" else ""
            cid = None
            # —— 重新引导 = 彻底重装:若本地已配置过,先清空旧系统库 + 重置本地,避免任何残留 ——
            if local_store.get_meta("setup_done") == "1":
                try:
                    if config_store._is_full_config():
                        from config_store import _get_bootstrap_conn_cfg as _gbc0
                        old_boot = _gbc0()
                        old_sys = config_store._sys_db_name()
                        if old_boot and old_sys:
                            mysql_client.drop_db(old_boot, old_sys)  # 尽力;库不在也无妨
                except Exception:
                    pass
                config_store.reset_local()  # 清空 config.db 全部配置
            # 构建本次初始化用的连接配置(明文,刚“测试连接成功”)
            from config_store import _hash_password
            conn_cfg = None
            if conn and (conn.get("host") or conn.get("name")):
                conn_cfg = {
                    "host": conn.get("host", "127.0.0.1"),
                    "port": int(conn.get("port", 3306)),
                    "user": conn.get("user", "root"),
                    "password": conn.get("password", ""),
                    "name": conn.get("name", ""),
                    "note": conn.get("note", ""),
                }
            if run_mode == "full":
                admin_user = body.get("admin_user", "admin").strip()
                admin_pass = body.get("admin_pass", "")
                patch["run_mode"] = "full"
                patch["sys_db_name"] = sys_db_name
                # 无刚输入的连接时回退本地已有 bootstrap
                if not conn_cfg:
                    from config_store import _get_bootstrap_conn_cfg as _gbc2
                    conn_cfg = _gbc2()
                if not conn_cfg:
                    return self._send_error("初始化系统库失败: 无可用连接配置")
                # 本地 meta/bootstrap 就位(run_mode/sys_db_name/bootstrap 唯一权威)
                config_store.prepare_full(sys_db_name, conn_cfg)
                from system_db import init_system_db, import_from_file, StorageBackend
                ok, err = init_system_db(conn_cfg, sys_db_name)
                if not ok:
                    return self._send_error(f"初始化系统库失败: {err}")
                import_from_file(conn_cfg, sys_db_name, source="local")
                db = StorageBackend(conn_cfg, db_name=sys_db_name)
                db.set_admin(admin_user, _hash_password(admin_pass) if admin_pass else "")
                # 把刚输入的那条连接写进系统库(唯一来源)
                if conn_cfg.get("name") or conn_cfg.get("host"):
                    cid = db.save_connection(conn_cfg)
                config_store.save_settings(patch)   # 写进新系统库
            else:
                # 轻量模式(全新初始化)
                config_store.prepare_lite()
                if conn and (conn.get("host") or conn.get("name")):
                    cid = config_store.save_connection(conn)
                config_store.save_settings(patch)
            # 激活连接(全量:走系统库; 轻量:本地激活)
            if cid:
                _set_active_conn(cid)
            self._log_op("初始化引导完成", True, f"run_mode={run_mode}" + (f" 系统库={sys_db_name}" if run_mode == "full" else ""))
            return self._send_json({"ok": True, "conn_id": cid})
        if path == "/api/connect":
            cid = body.get("id")
            cfg = config_store.get_connection(cid)
            if not cfg:
                return self._send_error("连接不存在")
            mysql_client.test(cfg)  # 验证可用
            _set_active_conn(cid)
            self._log_op("切换连接", True, f"{cfg.get('name')}({cfg.get('host')}:{cfg.get('port')})")
            return self._send_json({"ok": True, "name": cfg["name"]})
        if path == "/api/kill":
            conn = _get_conn()
            try:
                mysql_client.kill_connection(conn, body.get("pid"))
                return self._send_json({"ok": True})
            finally:
                _close(conn)
        if path == "/api/service/restart":
            return self._handle_service_restart()
        if path == "/api/users":
            return self._handle_user_create(body)
        if path == "/api/backup":
            cfg = config_store.get_connection(_current_conn_id)
            if not cfg:
                return self._send_error("请先激活连接")
            dbs = body.get("dbs") or []
            gzip_ = bool(body.get("gzip", True))
            backup_dir = body.get("backup_dir") or None
            tid = backup_engine.start_backup_task(cfg, dbs, backup_dir, gzip_)
            return self._send_json({"task_id": tid, "ok": True}, 202)
        if path == "/api/restore":
            cfg = config_store.get_connection(_current_conn_id)
            if not cfg:
                return self._send_error("请先激活连接")
            target_db = body.get("target_db") or ""
            file_path = body.get("file", "")
            if not file_path or not os.path.exists(file_path):
                return self._send_error("还原文件不存在,请重新选择")
            tid = backup_engine.start_restore_task(cfg, target_db, file_path)
            return self._send_json({"task_id": tid, "ok": True}, 202)
        if path == "/api/dialog":
            return self._send_json(self._native_dialog(body))
        if path == "/api/browse":
            return self._send_json(self._browse(body.get("path", "")))
        if path == "/api/schedule":
            s = config_store.get_settings()
            s = config_store.save_settings({
                "schedule_enabled": bool(body.get("enabled", s.get("schedule_enabled"))),
                "schedule_cron": body.get("cron", s.get("schedule_cron")),
                "schedule_dbs": body.get("dbs", s.get("schedule_dbs")),
                "schedule_keep": int(body.get("keep", s.get("schedule_keep", 7))),
                "schedule_conn_id": body.get("conn_id", s.get("schedule_conn_id")),
            })
            return self._send_json({"ok": True, "settings": s})
        if path == "/api/schedules":
            try:
                tid = schedule_store.save_task(body)
            except ValueError as e:
                return self._send_error(str(e))
            return self._send_json({"id": tid, "ok": True}, 201)
        if path == "/api/schedules/toggle":
            tid = body.get("id", "")
            ok = schedule_store.set_enabled(tid, bool(body.get("enabled")))
            if not ok:
                return self._send_error("任务不存在", 404)
            return self._send_json({"ok": True})
        if path == "/api/schedules/register":
            t = schedule_store.get_task(body.get("id", ""))
            if not t:
                return self._send_error("任务不存在", 404)
            r = native_scheduler.register(t)
            if r.get("ok"):
                schedule_store.set_native_registered(t["id"], True)
            return self._send_json(r, 200 if r.get("ok") else 400)
        if path == "/api/schedules/unregister":
            t = schedule_store.get_task(body.get("id", ""))
            if not t:
                return self._send_error("任务不存在", 404)
            r = native_scheduler.unregister(t)
            if r.get("ok"):
                schedule_store.set_native_registered(t["id"], False)
            return self._send_json(r, 200 if r.get("ok") else 400)
        if path == "/api/update/prepare":
            import updater
            res = updater.prepare("")  # 下载+校验+解压+备份
            self._log_op("准备更新", res.get("ok"), res.get("msg"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/update/apply":
            import sys as _sys, subprocess as _sp
            import updater
            res = _update_cache.get("result") or updater.check()
            if not res.get("has_update"):
                return self._send_error("当前已是最新版本, 无需更新")
            ver = body.get("version") or res.get("latest") or updater.current_version()
            script = os.path.join(updater.UPD_DIR, "apply_update.py")
            updater.build_apply_script(ver)
            if not os.path.exists(script):
                return self._send_error("更新脚本未生成, 请先执行「检查并下载」")
            flags = 0
            if os.name == "nt":
                flags = 0x00000008 | 0x00000004  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            try:
                _sp.Popen([_sys.executable, script], cwd=BASE_DIR,
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, creationflags=flags)
            except Exception as e:
                return self._send_error("无法启动更新器: " + str(e))
            self._log_op("应用更新", True, "v" + str(ver))
            threading.Timer(3, lambda: os._exit(0)).start()
            return self._send_json({"ok": True, "msg": "更新已启动, 服务即将重启(约 30 秒后请刷新页面)"})
        self._send_error("未知接口", 404)

    def _native_dialog(self, body):
        """调用 Windows 原生文件/目录选择对话框(ctypes + Win32 API)。"""
        mode = body.get("mode", "dir")
        title = body.get("title", "") or ("选择还原文件" if mode == "file" else "选择备份目录")
        start_dir = body.get("start_dir", "") or ""
        result = {"canceled": True}

        def _worker():
            # 对话框需要 STA 线程(COM 初始化)
            try:
                ctypes.windll.ole32.CoInitialize(None)
            except Exception:
                pass
            try:
                r = _win_open_file(title, start_dir) if mode == "file" else _win_open_dir(title, start_dir)
                result.clear()
                result.update(r)
            except Exception as e:
                result.clear()
                result.update({"error": f"对话框调用失败: {e}"})
            finally:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()  # 阻塞直到用户完成选择或取消
        return result

    def _browse(self, path):
        """目录浏览:返回子目录与 .sql/.sql.gz 文件(均为完整路径,按名排序)。"""
        path = path.strip().strip('"').strip("'")
        if not path:
            drives = [f"{d}:\\" for d in "CDEF" if os.path.exists(f"{d}:\\")]
            return {"path": "", "dirs": [{"name": d, "path": d} for d in drives],
                    "files": [], "is_root": True}
        if not os.path.isdir(path):
            parent = os.path.dirname(path.rstrip("\\/")) or "\\"
            path = parent
        try:
            entries = sorted(os.listdir(path), key=str.lower)
        except PermissionError:
            return {"path": path, "dirs": [], "files": [], "error": "无权限访问该目录"}
        dirs, files = [], []
        for e in entries:
            full = os.path.join(path, e)
            try:
                if os.path.isdir(full):
                    dirs.append({"name": e, "path": full})
                elif e.lower().endswith((".sql", ".sql.gz", ".gz")):
                    files.append({"name": e, "path": full,
                                  "size": os.path.getsize(full)})
            except OSError:
                continue
        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())
        parent = os.path.dirname(path.rstrip("\\/")) or "\\"
        return {"path": path, "parent": parent, "dirs": dirs,
                "files": files, "is_root": False}

    def _service_status(self):
        """本机 MySQL 服务状态 + 活动连接数据库可达性。"""
        from service_manager import service_status, detect_service_name
        s = config_store.get_settings()
        name = (s.get("mysql_service_name") or "").strip() or None
        if not name:
            name = detect_service_name()
        st = service_status(name)
        db_ok = None
        try:
            conn = _get_conn()
            _close(conn)
            db_ok = True
        except Exception:
            db_ok = False
        st["db_reachable"] = db_ok
        st["has_active_conn"] = bool(_current_conn_id)
        return st

    def _handle_service_restart(self):
        """重启本机 MySQL 服务并验证启动成功。"""
        from service_manager import service_status, detect_service_name, restart_service
        s = config_store.get_settings()
        name = (s.get("mysql_service_name") or "").strip() or None
        if not name:
            name = detect_service_name()
        if not name:
            self._log_op("重启数据库服务", False, "未检测到本机服务")
            return self._send_error("未检测到本机 MySQL 服务。可在「服务设置」中填写服务名后重试。")
        service_status(name)

        def _verify():
            if not _current_conn_id:
                return True
            try:
                cfg = config_store.get_connection(_current_conn_id)
                if not cfg:
                    return True
                conn = mysql_client.connect(cfg)
                _close(conn)
                return True
            except Exception:
                return False

        result = restart_service(name, verify_cb=_verify)
        self._log_op("重启数据库服务", result.get("ok"), "{0}: {1}".format(name, result.get("msg")))
        return self._send_json(result, 200 if result.get("ok") else 400)

    def _parse_user_path(self, path):
        """从 /api/users/<user>@<host>[/grants] 解析 (user, host, suffix)。"""
        rest = path[len("/api/users/"):]
        parts = rest.split("/")
        uh = parts[0]
        if "@" not in uh:
            return None
        import urllib.parse as _up
        uh = _up.unquote(uh)  # 前端对 user@host 做 percent-encoding
        user, host = uh.split("@", 1)
        suffix = parts[1] if len(parts) > 1 else ""
        return user, host, suffix

    def _handle_user_grants(self, path):
        parsed = self._parse_user_path(path)
        if not parsed:
            return self._send_error("非法的用户标识", 404)
        user, host, _s = parsed
        conn = _get_conn()
        try:
            grants = mysql_client.show_grants(conn, user, host)
            return self._send_json({"ok": True, "user": user, "host": host, "grants": grants})
        except mysql_client.DbError as e:
            return self._send_error(str(e))
        finally:
            _close(conn)

    def _handle_user_create(self, body):
        """创建 MySQL 用户并按范围授权。"""
        user = (body.get("user") or "").strip()
        host = (body.get("host") or "%").strip() or "%"
        password = body.get("password") or ""
        scope_all = bool(body.get("scope_all"))
        databases = [d for d in (body.get("databases") or []) if d]
        privileges = body.get("privileges") or []
        if not user:
            return self._send_error("请输入用户名")
        if not password:
            return self._send_error("请设置密码")
        if not scope_all and not databases:
            return self._send_error("请选择授权数据库(或选择「全部数据库」)")
        if not privileges:
            return self._send_error("请至少选择一个权限")
        conn = _get_conn()
        try:
            mysql_client.create_user(conn, user, host, password)
            if scope_all:
                mysql_client.grant_privileges(conn, user, host, "*", privileges)
            else:
                for db in databases:
                    mysql_client.grant_privileges(conn, user, host, db, privileges)
            self._log_op("新增MySQL用户", True, "{0}@{1}".format(user, host))
            return self._send_json({"ok": True})
        except mysql_client.DbError as e:
            self._log_op("新增MySQL用户", False, "{0}@{1} {2}".format(user, host, e))
            return self._send_error(str(e))
        finally:
            _close(conn)

    def _handle_user_update(self, path, body):
        """修改 MySQL 用户：改密 / 编辑授权(先撤销再重授)。"""
        parsed = self._parse_user_path(path)
        if not parsed:
            return self._send_error("非法的用户标识", 404)
        user, host, _s = parsed
        conn = _get_conn()
        try:
            changed = False
            if body.get("password"):
                mysql_client.change_user_password(conn, user, host, body["password"])
                self._log_op("修改MySQL用户密码", True, "{0}@{1}".format(user, host))
                changed = True
            if body.get("privileges") is not None or body.get("databases") is not None:
                scope_all = bool(body.get("scope_all"))
                databases = [d for d in (body.get("databases") or []) if d]
                privileges = body.get("privileges") or []
                if scope_all:
                    mysql_client.revoke_all_db(conn, user, host, "*")
                    mysql_client.grant_privileges(conn, user, host, "*", privileges)
                else:
                    for db in databases:
                        mysql_client.revoke_all_db(conn, user, host, db)
                        mysql_client.grant_privileges(conn, user, host, db, privileges)
                self._log_op("修改MySQL用户授权", True, "{0}@{1}".format(user, host))
                changed = True
            if not changed:
                return self._send_error("无修改内容")
            return self._send_json({"ok": True})
        except mysql_client.DbError as e:
            self._log_op("修改MySQL用户", False, "{0}@{1} {2}".format(user, host, e))
            return self._send_error(str(e))
        finally:
            _close(conn)

    def _handle_user_delete(self, path):
        parsed = self._parse_user_path(path)
        if not parsed:
            return self._send_error("非法的用户标识", 404)
        user, host, _s = parsed
        conn = _get_conn()
        try:
            mysql_client.drop_user(conn, user, host)
            self._log_op("删除MySQL用户", True, "{0}@{1}".format(user, host))
            return self._send_json({"ok": True})
        except mysql_client.DbError as e:
            self._log_op("删除MySQL用户", False, "{0}@{1} {2}".format(user, host, e))
            return self._send_error(str(e))
        finally:
            _close(conn)

    def do_PUT(self):
        path = self.path.split("?")[0]
        if not self._auth_guard():
            return
        try:
            if path.startswith("/api/connections/"):
                cid = path.split("/")[-1]
                body = self._read_body()
                config_store.save_connection(body, cid=cid)
                self._log_op("修改连接", True, f"{body.get('name')}({body.get('host')}:{body.get('port')})")
                return self._send_json({"ok": True})
            if path.startswith("/api/schedules/"):
                tid = path.split("/")[-1]
                body = self._read_body()
                old = schedule_store.get_task(tid)
                if not old:
                    return self._send_error("任务不存在", 404)
                try:
                    schedule_store.save_task(body, tid=tid)
                except ValueError as e:
                    return self._send_error(str(e))
                # 引擎切换一致性: native -> builtin 时反注册系统计划任务
                new_engine = body.get("engine", old.get("engine"))
                if old.get("engine") == "native" and new_engine == "builtin" \
                        and old.get("native_registered"):
                    r = native_scheduler.unregister(schedule_store.get_task(tid))
                    if r.get("ok"):
                        schedule_store.set_native_registered(tid, False)
                # builtin -> native 且要求注册时,由前端随后调 register 接口
                return self._send_json({"ok": True})
            if path.startswith("/api/users/"):
                body = self._read_body()
                return self._handle_user_update(path, body)
            if path == "/api/settings":

                body = self._read_body()
                s = config_store.save_settings(body)
                return self._send_json({"ok": True, "settings": s})
            self._send_error("未知接口", 404)
        except Exception as e:
            self._send_error(f"服务器错误: {e}", 500)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if not self._auth_guard():
            return
        try:
            if path.startswith("/api/connections/"):
                cid = path.split("/")[-1]
                cfg = config_store.get_connection(cid)
                config_store.delete_connection(cid)
                self._log_op("删除连接", True, (cfg.get("name") if cfg else cid))
                return self._send_json({"ok": True})
            if path.startswith("/api/schedules/"):
                tid = path.split("/")[-1]
                # 若注册过系统计划任务,先反注册
                t = schedule_store.get_task(tid)
                if t and t.get("engine") == "native" and t.get("native_registered"):
                    native_scheduler.unregister(t)
                if not schedule_store.delete_task(tid):
                    return self._send_error("任务不存在", 404)
                return self._send_json({"ok": True})
            if path.startswith("/api/users/"):
                return self._handle_user_delete(path)
            if path.startswith("/api/backups/"):

                rid = path.split("/")[-1]
                backup_engine.delete_backup_record(rid)
                return self._send_json({"ok": True})
            self._send_error("未知接口", 404)
        except Exception as e:
            self._send_error(f"服务器错误: {e}", 500)



    # ---------- 认证处理 ----------
    def _handle_login(self, body):
        """处理登录请求。全量模式要求用户名 + 密码匹配。"""
        password = body.get("password", "")
        username = body.get("username", "").strip()
        if not username:
            self._log_op("登录", False, "未输入用户名")
            return self._send_error("请输入用户名", 400)
        if not password:
            self._log_op("登录", False, "未输入密码", operator=username)
            return self._send_error("请输入密码", 400)
        if not config_store.is_password_set():
            return self._send_error("尚未设置管理员密码", 403)
        # 校验用户名(全量模式凭据唯一权威按用户名匹配)
        admin_name = config_store.get_admin_username()
        if admin_name and username != admin_name:
            self._log_op("登录", False, f"用户名不匹配({username})", operator=username)
            return self._send_error("用户名或密码错误", 401)
        locked, locked_until = config_store.get_admin_lock_status()
        if locked and locked_until:
            self._log_op("登录", False, "账号已锁定", operator=username)
            return self._send_error("账号已锁定,请稍后再试", 423)
        try:
            ok_login = config_store.verify_admin(password)
        except config_store.SystemDbUnavailable as e:
            # 全量模式凭据只存系统库: 系统库不可达时明确报错, 不回退文件层旧密码
            self._log_op("登录", False, "系统库不可用无法验证", operator=username)
            return self._send_error("系统库不可用,无法验证登录. 请检查数据库连接后重试.", 503)
        if ok_login:
            config_store.update_admin_login_success()
            import secrets as _secrets
            import time as _time_mod
            token = _secrets.token_hex(32)
            uname = config_store.get_admin_username()
            _sessions[token] = (uname, _time_mod.time() + SESSION_TIMEOUT)
            self._log_op("登录", True, "登录成功", operator=uname)
            return self._send_json({"ok": True, "token": token, "username": uname})
        else:
            config_store.update_admin_login_fail(0, None)
            self._log_op("登录", False, "密码错误", operator=username)
            return self._send_error("密码错误", 401)

    def _handle_logout(self):
        """处理登出请求。"""
        token = self.headers.get("Authorization", "").strip()
        if token.startswith("Bearer "):
            token = token[7:]
        if token and token in _sessions:
            del _sessions[token]
            self._log_op("登出", True)
        return self._send_json({"ok": True})

    def _handle_change_password(self, body):
        """修改密码。"""
        old_password = body.get("old_password", "")
        new_password = body.get("new_password", "")
        if not old_password or not new_password:
            return self._send_error("请填写原密码和新密码", 400)
        if len(new_password) < 6:
            return self._send_error("新密码长度至少 6 位", 400)
        if not config_store.verify_admin(old_password):
            self._log_op("修改密码", False, "原密码错误")
            return self._send_error("原密码错误", 401)
        config_store.set_admin_password(new_password)
        self._log_op("修改密码", True)
        return self._send_json({"ok": True})


    # ---------- 找回密码处理 ----------
    def _handle_request_reset_code(self):
        """请求找回密码验证码。生成并输出到终端。"""
        if not config_store.is_password_set():
            return self._send_error("尚未设置管理员密码", 403)
        code = _generate_reset_code()
        return self._send_json({"ok": True, "message": "验证码已输出到服务端终端,请查看"})

    def _handle_reset_password(self, body):
        """使用验证码重置密码。"""
        code = body.get("code", "").strip()
        new_password = body.get("new_password", "")
        if not code or not new_password:
            return self._send_error("请填写验证码和新密码", 400)
        if len(new_password) < 6:
            return self._send_error("新密码长度至少 6 位", 400)
        # 验证 code
        entry = _reset_codes.get(code)
        if not entry:
            return self._send_error("验证码无效或已过期", 400)
        username, expire_ts = entry
        if _time.time() > expire_ts:
            del _reset_codes[code]
            return self._send_error("验证码已过期,请重新获取", 400)
        # 重置密码
        config_store.set_admin_password(new_password)
        del _reset_codes[code]  # 使用后删除
        self._log_op("重置密码", True, operator=username)
        return self._send_json({"ok": True, "message": "密码重置成功,请使用新密码登录"})


    def _handle_change_username(self, body):
        """修改管理员用户名。"""
        username = body.get("username", "").strip()
        if not username:
            return self._send_error("请输入用户名", 400)
        if len(username) < 2:
            return self._send_error("用户名至少 2 个字符", 400)
        config_store.set_admin(username, "")
        self._log_op("修改用户名", True, f"新用户名={username}")
        return self._send_json({"ok": True, "username": username})


    def _handle_switch_to_full_mode(self, body):
        """从轻量模式切换到全量模式。"""
        if config_store._is_full_mode():
            return self._send_json({"ok": True, "message": "已经是全量模式"})
        sys_db_name = body.get("sys_db_name", "_mysql_console").strip()
        admin_user = body.get("admin_user", "admin").strip()
        admin_pass = body.get("admin_pass", "")
        if not admin_pass or len(admin_pass) < 6:
            return self._send_error("管理员密码至少 6 位", 400)
        try:
            config_store.switch_to_full_mode(sys_db_name, admin_user, admin_pass)
            self._log_op("切换全量模式", True, f"系统库={sys_db_name}")
            return self._send_json({"ok": True, "message": "已切换到全量模式,请重新登录"})
        except Exception as e:
            self._log_op("切换全量模式", False, str(e))
            return self._send_error(str(e), 500)

def main():
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=_update_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"MySQL Console 已启动: http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == "__main__":
    main()
