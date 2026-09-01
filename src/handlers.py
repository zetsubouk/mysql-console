# -*- coding: utf-8 -*-
"""业务处理层(2026-08-31 从 server.py 拆出)。

职责:
- 持有共享运行状态:会话(_sessions)/重置码(_reset_codes)/更新缓存(_update_cache)/
  当前连接(_current_conn_id)与引擎辅助函数(_get_conn/_close 等);
- 认证守卫模块函数(_is_auth_required/_check_auth/_check_access_token/_check_csrf);
- 后台循环(scheduler_loop/_update_loop/_alert_history_loop)与原生对话框辅助;
- HandlerBase:所有 GET/POST/PUT/DELETE 的业务处理器(g_*/p_*/_handle_*),
  路由由 routes.py 注册表驱动,路由分发在 _route_get/_route_post 收敛。

server.py 只保留 HTTP 传输层 + 组合 Handler(HandlerBase, BaseHTTPRequestHandler)。
本模块不反向 import server,避免循环依赖。
"""
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time as _time

import config_store
import local_store
import mysql_client
import backup_engine
import schedule_store
import native_scheduler
import env_probe
import ssh_tunnel
import paths
import routes
import security
import metrics as _metrics

APP_ROOT = paths.APP_ROOT

# ---------- 共享运行状态 ----------
_lock = threading.Lock()
try:
    _current_conn_id = config_store.get_active_conn_id()  # 启动恢复激活连接;系统库未就绪时=None
    if not _current_conn_id:
        _current_conn_id = None
except Exception:
    _current_conn_id = None

# session token -> (username, expire_ts)
_sessions = {}
SESSION_TIMEOUT = 8 * 3600  # 8 小时

# 找回密码验证码 -> (username, expire_ts)
_reset_codes = {}
RESET_CODE_TIMEOUT = 600  # 10 分钟

# 自动更新检查缓存(启动/定时后台填充, 前端徽标读取, 避免每次即时打 GitHub)
_update_cache = {"ts": 0.0, "result": None}

# 无需认证的路径
_AUTH_FREE_PATHS = {"/api/login", "/api/auth-status", "/api/health", "/api/request-reset-code", "/api/reset-password", "/api/security/info", "/api/version"}
# 无需访问令牌的路径(唯一:向登录页通告「是否强制访问令牌」)
_TOKEN_FREE_PATHS = {"/api/security/info"}

# 内置调度器:遍历所有 enabled 且 engine=builtin 的任务;防同一分钟重复触发。
_last_fire = {}  # tid -> "YYYYMMDDHHMM"


# ---------- 认证守卫 ----------
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


def _check_access_token(handler) -> bool:
    """访问令牌校验(0.0.0.0 暴露时),非强制路径恒通过。"""
    path = handler.path.split("?")[0]
    if path in _TOKEN_FREE_PATHS or not security.access_token_required():
        return True
    incoming = handler.headers.get("X-Access-Token", "").strip()
    if security.check_access_token(incoming):
        return True
    pending = bool(security.effective_access_token())  # 已设置令牌却未通过 → 需要
    handler._send_json({"error": "需要访问令牌", "code": 401, "access_required": pending},
                       401 if pending else 503)
    return False


def _check_csrf(handler, method) -> bool:
    """CSRF 纵深防御:对写请求校验 Origin 与 Host 同源(防御 DNS Rebind / 恶意站点诱导)。"""
    if method not in ("POST", "PUT", "DELETE"):
        return True
    req_host = handler.headers.get("Host", "")
    origin = handler.headers.get("Origin", "")
    if security.origin_allowed(req_host, origin, allow_null=True):
        return True
    handler._send_json({"error": "非法的跨站请求(Origin 不匹配)", "code": 403}, 403)
    return False


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


# ---------- 原生文件/目录对话框 ----------
# Windows: ctypes 直调 Win32 API(不依赖 PowerShell)
# macOS:   osascript(AppleScript,系统自带)
# Linux:   zenity(主流桌面发行版预装;无桌面/无 zenity 时报可读错误)

_IS_WIN = os.name == "nt"

if _IS_WIN:
    import ctypes
    from ctypes import wintypes

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

    _SetWindowPos = ctypes.windll.user32.SetWindowPos
    _SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _SetWindowPos.restype = wintypes.BOOL

    ASFW_ANY = 0xFFFFFFFF
    HWND_TOPMOST = -1
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOACTIVATE = 0x0010
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
                    _SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(timeout), 0):
                _SystemParametersInfoW(_SPI_SETFOREGROUNDLOCKTIMEOUT, 0, None, SPIF_SENDCHANGE)
                _saved_lock_timeout = timeout.value
            _AllowSetForegroundWindow(ASFW_ANY)
            _SetForegroundWindow(hwnd)
        except Exception:
            pass
        return hwnd

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
                _SystemParametersInfoW(_SPI_SETFOREGROUNDLOCKTIMEOUT, 0,
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


def _posix_open_file(title, start_dir):
    """macOS 用 osascript,Linux 优先 zenity;都不可用返回可读错误。"""
    if shutil.which("osascript"):  # macOS
        script = (
            'set theFile to choose file with prompt "%s" %s\n'
            'return POSIX path of theFile'
        ) % (title.replace('"', '\\"'),
             ('default location (POSIX file "%s")' % start_dir.replace('"', '\\"')) if start_dir else "")
        try:
            p = subprocess.run(["osascript", "-e", script],
                               capture_output=True, timeout=600)
        except Exception as e:
            return {"error": f"对话框调用失败: {e}"}
        if p.returncode == 0:
            return {"path": p.stdout.decode("utf-8", "replace").strip()}
        err = p.stderr.decode("utf-8", "replace")
        if "User canceled" in err or "用户已取消" in err or err.strip() == "":
            return {"canceled": True}
        return {"error": f"对话框调用失败: {err.strip()[:200]}"}
    zenity = shutil.which("zenity")
    if zenity:  # Linux 桌面
        cmd = [zenity, "--file-selection", "--title", title]
        if start_dir and os.path.isdir(start_dir):
            cmd += ["--filename", start_dir.rstrip("/") + "/"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=600)
        except Exception as e:
            return {"error": f"对话框调用失败: {e}"}
        if p.returncode == 0:
            return {"path": p.stdout.decode("utf-8", "replace").strip()}
        return {"canceled": True}  # zenity 取消/关闭都是非 0
    return {"error": "未找到可用的图形选择工具(Linux 请安装 zenity: apt install zenity / yum install zenity)"}


def _posix_open_dir(title, start_dir):
    """macOS 用 osascript choose folder,Linux 优先 zenity --directory。"""
    if shutil.which("osascript"):  # macOS
        script = (
            'set theFolder to choose folder with prompt "%s" %s\n'
            'return POSIX path of theFolder'
        ) % (title.replace('"', '\\"'),
             ('default location (POSIX file "%s")' % start_dir.replace('"', '\\"')) if start_dir else "")
        try:
            p = subprocess.run(["osascript", "-e", script],
                               capture_output=True, timeout=600)
        except Exception as e:
            return {"error": f"对话框调用失败: {e}"}
        if p.returncode == 0:
            return {"path": p.stdout.decode("utf-8", "replace").strip()}
        err = p.stderr.decode("utf-8", "replace")
        if "User canceled" in err or "用户已取消" in err or err.strip() == "":
            return {"canceled": True}
        return {"error": f"对话框调用失败: {err.strip()[:200]}"}
    zenity = shutil.which("zenity")
    if zenity:  # Linux 桌面
        cmd = [zenity, "--file-selection", "--directory", "--title", title]
        if start_dir and os.path.isdir(start_dir):
            cmd += ["--filename", start_dir.rstrip("/") + "/"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=600)
        except Exception as e:
            return {"error": f"对话框调用失败: {e}"}
        if p.returncode == 0:
            return {"path": p.stdout.decode("utf-8", "replace").strip()}
        return {"canceled": True}
    return {"error": "未找到可用的图形选择工具(Linux 请安装 zenity: apt install zenity / yum install zenity)"}


# ---------- 连接辅助 ----------
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


# ---------- 后台循环 ----------
def _alert_history_loop():
    """后台线程:每 60s 采样当前告警状态并落盘。连接未激活/数据库不可达时静默跳过。"""
    while True:
        try:
            with _lock:
                cid = _current_conn_id
            if cid:
                cfg = config_store.get_connection(cid)
                if cfg:
                    s = config_store.get_settings()
                    conn = mysql_client.connect(cfg)
                    try:
                        res = mysql_client.alerts(
                            conn,
                            max_conn=int(s.get("alert_max_conn", 100)),
                            max_slow=int(s.get("alert_max_slow", 10)),
                            max_running=int(s.get("alert_max_running", 20)),
                        )
                        _metrics.append_alert_sample(res.get("alerts") or [])
                        h = mysql_client.health_score(conn)
                        _metrics.append_health_sample(h.get("score"))
                    finally:
                        _close(conn)
        except Exception:
            pass
        _time.sleep(60)


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
                if (not last) or _time.time() - last >= period:
                    import updater
                    _update_cache["result"] = updater.check()
                    _update_cache["ts"] = _time.time()
                    config_store.save_settings({"update_last_check": _time.time()})
        except Exception:
            pass
        _time.sleep(3600)


def scheduler_loop():
    while True:
        try:
            for task in schedule_store.list_tasks():
                if not task.get("enabled") or task.get("engine") != "builtin":
                    continue
                now = _time.localtime()
                mark = _time.strftime("%Y%m%d%H%M", now)
                due = False
                if task["freq"] == "hourly":
                    n = max(1, int(task.get("interval_hours", 1)))
                    last = task.get("last_run", "")
                    if not last:
                        due = now.tm_min == 0  # 从未跑过:整点触发
                    else:
                        try:
                            elapsed_min = (_time.mktime(now) -
                                           _time.mktime(_time.strptime(last, "%Y-%m-%d %H:%M:%S"))) / 60
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
        _time.sleep(20)


# ======================================================================
# HandlerBase: 全部 GET/POST 业务处理器(路由由 routes.py 驱动)
# ======================================================================
class HandlerBase:
    """业务层基类。server.Handler(HandlerBase, BaseHTTPRequestHandler) 继承之。"""

    # ---------- 路由分发(收敛到 routes.py 注册表) ----------
    def _route_get(self, path):
        if not routes.dispatch(self, "GET", path):
            self._send_error("未知接口", 404)

    def _route_post(self, path):
        body = self._read_body()
        if not routes.dispatch(self, "POST", path, body):
            self._send_error("未知接口", 404)

    # ---------- 通用辅助(供子类/自身调用) ----------
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

    def _read_logs(self):
        # 2026-08-27: 操作日志已在全量模式入库。全量→读系统库; 轻量模式不记录→空。
        return config_store.get_operation_logs(300)

    # ---------------- GET 处理器(routes.py GET_ROUTES 注册表驱动) ----------------
    def g_health(self):
        return self._send_json({"ok": True})

    def g_auth_status(self):
        return self._send_json({
            "password_set": config_store.is_password_set(),
            "username": config_store.get_admin_username(),
        })

    def g_security_info(self):
        return self._send_json({
            "access_token_required": security.access_token_required(),
            "access_token_set": bool(security.effective_access_token()),
            "tls": security.tls_enabled(),
            "host": security.target_host(),
        })

    def g_connections(self):
        try:
            return self._send_json(config_store.list_connections())
        except config_store.SystemDbUnavailable:
            return self._send_json([])  # 系统库不可达显示空(非陈旧残留)

    def g_overview(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.server_overview(conn))
        finally:
            _close(conn)

    def g_databases(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.database_list(conn))
        finally:
            _close(conn)

    def g_users(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.user_list(conn))
        finally:
            _close(conn)

    def g_processlist(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.process_list(conn))
        finally:
            _close(conn)

    def g_monitor(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.monitor_metrics(conn))
        finally:
            _close(conn)

    def g_monitor_full(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.monitor_full(conn))
        finally:
            _close(conn)

    def g_sys_resource(self):
        import sys_resources
        import urllib.parse
        disk = ""
        if "?" in self.path:
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            if qs.get("disk"):
                disk = qs["disk"][0]
        return self._send_json(sys_resources.sys_resources(disk))

    def g_dashboard_health(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.health_score(conn))
        finally:
            _close(conn)

    def g_dashboard_innodb(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.innodb_metrics(conn))
        finally:
            _close(conn)

    def g_dashboard_tablespace(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.tablespace_top(conn))
        finally:
            _close(conn)

    def g_dashboard_health_history(self, path):
        return self._send_json(_metrics.health_history_query(path))

    def g_dashboard_replication(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.replication_status(conn))
        finally:
            _close(conn)

    def g_alerts(self):
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

    def g_alerts_history(self, path):
        return self._send_json(_metrics.alert_history_query(path))

    def g_variables(self):
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.variables(conn))
        finally:
            _close(conn)

    def g_service_status(self):
        return self._send_json(self._service_status())

    def g_backups(self):
        return self._send_json(backup_engine.list_backups())

    def g_backup_params(self):
        s = config_store.get_settings()
        return self._send_json({
            "builtin_backup": backup_engine.BUILTIN_BACKUP_OPTS,
            "builtin_restore": backup_engine.BUILTIN_RESTORE_OPTS,
            "backup_opts": s.get("backup_opts", ""),
            "restore_opts": s.get("restore_opts", ""),
        })

    def g_backup_files(self):
        return self._send_json(backup_engine.list_backup_files())

    def g_backup_download(self):
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

    def g_logs(self):
        return self._send_json(self._read_logs())

    def g_settings(self):
        s = config_store.get_settings()
        # 不向后端泄露 access_token 密文,改为布尔,避免设置页显示一串密文
        if "access_token" in s:
            s["access_token"] = ""  # 由 /api/security/info 的 access_token_set 反映状态
        return self._send_json(s)

    def g_setup_env(self):
        return self._send_json(env_probe.env_summary(
            config_store.get_settings().get("mysql_bin", "")))

    def g_schedules(self):
        tasks = schedule_store.list_tasks()
        for t in tasks:
            t["desc"] = schedule_store.describe(t)
        return self._send_json(tasks)

    def g_schedules_env(self):
        return self._send_json(native_scheduler.env_info())

    def g_version(self):
        from version import __version__
        import sys as _sys
        # ponytail: platform 归一为 win64/linux，mac 归 linux（与 native_scheduler 一致）
        plat = "windows" if _sys.platform == "win32" else "linux"
        return self._send_json({"version": __version__, "platform": plat, "platform_raw": _sys.platform})

    def g_update_check(self):
        import updater
        r = updater.check()
        _update_cache["result"] = r; _update_cache["ts"] = _time.time()
        return self._send_json(r)

    def g_update_badge(self):
        r = _update_cache.get("result")
        if (not r) or (not r.get("offline") and _time.time() - _update_cache.get("ts", 0) > 6 * 3600):
            import updater
            r = updater.check()
            _update_cache["result"] = r; _update_cache["ts"] = _time.time()
        return self._send_json(r)

    def g_update_status(self):
        import updater
        return self._send_json({"version": updater.current_version(), "log": updater.read_status()})

    def g_ai_config(self):
        import ai_client
        return self._send_json(ai_client.public_config())

    # ---- GET 前缀处理器 ----
    def g_user_detail_or_grants(self, path):
        if path.endswith("/grants"):
            return self._handle_user_grants(path)
        self._send_error("未知接口", 404)
        return None

    def g_task(self, path):
        tid = path.split("/")[-1]
        t = backup_engine.get_task(tid)
        if not t:
            return self._send_error("任务不存在", 404)
        return self._send_json(t)

    def g_schedule_detail(self, path):
        parts = path.split("/")
        if len(parts) == 5 and parts[4] == "native-status":
            t = schedule_store.get_task(parts[3])
            if not t:
                return self._send_error("任务不存在", 404)
            return self._send_json(native_scheduler.status(t))
        self._send_error("未知接口", 404)
        return None

    def g_database_detail(self, path):
        name = path.split("/")[-1]
        conn = _get_conn()
        try:
            return self._send_json(mysql_client.database_detail(conn, name))
        finally:
            _close(conn)

    # ---------------- POST 处理器(routes.py POST_ROUTES 注册表驱动;登录/退出/AI/查询等见 _handle_*) ----------------
    def p_connections(self, body):
        is_update = bool(body.get("id"))
        cid = config_store.save_connection(body)
        self._log_op("修改连接" if is_update else "新增连接", True,
                     f"{body.get('name')}({body.get('host')}:{body.get('port')})")
        return self._send_json({"id": cid, "ok": True}, 201)

    def p_connections_test(self, body):
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

    def p_connections_remote_check(self, body):
        """探测远程服务器操作系统(SSH 只读)。body 为连接表单字段或含 id 引用已存连接。"""
        cid = body.get("id")
        if cid:
            cfg = config_store.get_connection(cid) or {}
        else:
            cfg = {
                "host": body.get("host", "127.0.0.1"),
                "port": int(body.get("port", 3306)),
                "user": body.get("user", "root"),
                "password": body.get("password", ""),
                "ssh_enabled": bool(body.get("ssh_enabled")),
                "ssh_host": body.get("ssh_host", ""),
                "ssh_port": int(body.get("ssh_port") or 22),
                "ssh_user": body.get("ssh_user", ""),
                "ssh_key": body.get("ssh_key", ""),
                "ssh_bind_host": body.get("ssh_bind_host", ""),
                "ssh_bind_port": int(body.get("ssh_bind_port") or 0),
            }
        if not (cfg.get("ssh_host") or "").strip():
            return self._send_error("未配置 SSH 主机,无法探测远程服务器环境")
        if not ssh_tunnel.ssh_available():
            return self._send_error("本机未找到 ssh 命令,无法探测远程环境"
                                    "(Windows: 设置→可选功能→OpenSSH 客户端)")
        try:
            env = ssh_tunnel.probe_remote_env(cfg)
        except Exception as e:
            return self._send_error(f"远程环境探测失败: {e}")
        return self._send_json({"ok": True, **env})

    def p_setup_probe_client(self, body):
        r = env_probe.probe_client(body.get("path", ""))
        return self._send_json(r, 200 if r.get("ok") else 400)

    def p_setup_test_db(self, body):
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

    def p_setup_db_check(self, body):
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

    def p_setup_drop_db(self, body):
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

    def p_setup_finish(self, body):
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
        # 新规则：初始化完成后生成 start/stop/init（安装包仅含 install）
        try:
            gen = self._ensure_runtime_scripts()
            if gen:
                self._log_op("生成运行脚本", True, ", ".join(gen))
        except Exception:
            pass
        return self._send_json({"ok": True, "conn_id": cid})

    def p_connect(self, body):
        cid = body.get("id")
        cfg = config_store.get_connection(cid)
        if not cfg:
            return self._send_error("连接不存在")
        mysql_client.test(cfg)  # 验证可用
        _set_active_conn(cid)
        self._log_op("切换连接", True, f"{cfg.get('name')}({cfg.get('host')}:{cfg.get('port')})")
        return self._send_json({"ok": True, "name": cfg["name"]})

    def p_kill(self, body):
        conn = _get_conn()
        try:
            mysql_client.kill_connection(conn, body.get("pid"))
            return self._send_json({"ok": True})
        finally:
            _close(conn)

    def p_backup(self, body):
        cfg = config_store.get_connection(_current_conn_id)
        if not cfg:
            return self._send_error("请先激活连接")
        dbs = body.get("dbs") or []
        gzip_ = bool(body.get("gzip", True))
        # 备份目录: 当次指定 > 该连接配置的 backup_dir > 全局默认
        backup_dir = body.get("backup_dir") or cfg.get("backup_dir") or None
        # extra_opts: 缺省=用 settings 默认;数组=当次覆盖(可为空)
        extra_opts = body.get("extra_opts") if isinstance(body.get("extra_opts"), list) else None
        try:
            backup_engine.resolve_backup_opts(extra_opts)
        except ValueError as e:
            return self._send_error(str(e))
        tid = backup_engine.start_backup_task(cfg, dbs, backup_dir, gzip_, extra_opts=extra_opts)
        return self._send_json({"task_id": tid, "ok": True}, 202)

    def p_restore(self, body):
        cfg = config_store.get_connection(_current_conn_id)
        if not cfg:
            return self._send_error("请先激活连接")
        target_db = body.get("target_db") or ""
        file_path = body.get("file", "")
        storage = body.get("storage") or "local"
        if storage != "remote" and (not file_path or not os.path.exists(file_path)):
            return self._send_error("还原文件不存在,请重新选择")
        extra_opts = body.get("extra_opts") if isinstance(body.get("extra_opts"), list) else None
        try:
            backup_engine.resolve_restore_opts(extra_opts)
        except ValueError as e:
            return self._send_error(str(e))
        tid = backup_engine.start_restore_task(cfg, target_db, file_path, extra_opts=extra_opts,
                                                storage=storage)
        return self._send_json({"task_id": tid, "ok": True}, 202)

    def p_backup_files_remote(self, body):
        """列出远程服务器备份目录下的 .sql/.sql.gz(还原时选择远程文件)。
        body: {conn_id?, dir?} 缺省用激活连接 + 该连接远程目录。"""
        cid = body.get("conn_id") or _current_conn_id
        cfg = config_store.get_connection(cid) if cid else None
        if not cfg:
            return self._send_error("请先激活连接")
        if backup_engine.storage_of(cfg) == "local":
            return self._send_error("本机连接无需远程还原文件")
        if not (cfg.get("ssh_host") or "").strip():
            return self._send_error("该连接未配置 SSH 主机,无法访问远程文件")
        if not ssh_tunnel.ssh_available():
            return self._send_error("本机未找到 ssh 命令,无法访问远程文件"
                                    "(Windows: 设置→可选功能→OpenSSH 客户端)")
        try:
            rdir, files = backup_engine.list_remote_files(cfg, body.get("dir"))
        except RuntimeError as e:
            return self._send_error(str(e))
        return self._send_json({"ok": True, "dir": rdir, "files": files})

    def p_dialog(self, body):
        return self._send_json(self._native_dialog(body))

    def p_browse(self, body):
        return self._send_json(self._browse(body.get("path", "")))

    def p_schedule(self, body):
        s = config_store.get_settings()
        s = config_store.save_settings({
            "schedule_enabled": bool(body.get("enabled", s.get("schedule_enabled"))),
            "schedule_cron": body.get("cron", s.get("schedule_cron")),
            "schedule_dbs": body.get("dbs", s.get("schedule_dbs")),
            "schedule_keep": int(body.get("keep", s.get("schedule_keep", 7))),
            "schedule_conn_id": body.get("conn_id", s.get("schedule_conn_id")),
        })
        return self._send_json({"ok": True, "settings": s})

    def p_schedules(self, body):
        try:
            tid = schedule_store.save_task(body)
        except ValueError as e:
            return self._send_error(str(e))
        return self._send_json({"id": tid, "ok": True}, 201)

    def p_schedules_toggle(self, body):
        tid = body.get("id", "")
        ok = schedule_store.set_enabled(tid, bool(body.get("enabled")))
        if not ok:
            return self._send_error("任务不存在", 404)
        return self._send_json({"ok": True})

    def p_schedules_register(self, body):
        t = schedule_store.get_task(body.get("id", ""))
        if not t:
            return self._send_error("任务不存在", 404)
        r = native_scheduler.register(t)
        if r.get("ok"):
            schedule_store.set_native_registered(t["id"], True)
        return self._send_json(r, 200 if r.get("ok") else 400)

    def p_schedules_unregister(self, body):
        t = schedule_store.get_task(body.get("id", ""))
        if not t:
            return self._send_error("任务不存在", 404)
        r = native_scheduler.unregister(t)
        if r.get("ok"):
            schedule_store.set_native_registered(t["id"], False)
        return self._send_json(r, 200 if r.get("ok") else 400)

    def p_update_prepare(self, body):
        import updater
        res = updater.prepare("")  # 下载+校验+解压+备份
        self._log_op("准备更新", res.get("ok"), res.get("msg"))
        return self._send_json(res, 200 if res.get("ok") else 400)

    def p_update_apply(self, body):
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
            _sp.Popen([_sys.executable, script], cwd=APP_ROOT,
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, creationflags=flags)
        except Exception as e:
            return self._send_error("无法启动更新器: " + str(e))
        self._log_op("应用更新", True, "v" + str(ver))
        threading.Timer(3, lambda: os._exit(0)).start()
        return self._send_json({"ok": True, "msg": "更新已启动, 服务即将重启(约 30 秒后请刷新页面)"})

    def _native_dialog(self, body):
        """调用系统原生文件/目录选择对话框(Win32 / osascript / zenity)。"""
        mode = body.get("mode", "dir")
        title = body.get("title", "") or ("选择还原文件" if mode == "file" else "选择备份目录")
        start_dir = body.get("start_dir", "") or ""
        result = {"canceled": True}

        def _worker():
            if _IS_WIN:
                # 对话框需要 STA 线程(COM 初始化)
                try:
                    import ctypes as _ct
                    _ct.windll.ole32.CoInitialize(None)
                except Exception:
                    pass
                try:
                    r = _win_open_file(title, start_dir) if mode == "file" else _win_open_dir(title, start_dir)
                except Exception as e:
                    r = {"error": f"对话框调用失败: {e}"}
                finally:
                    try:
                        import ctypes as _ct2
                        _ct2.windll.ole32.CoUninitialize()
                    except Exception:
                        pass
            else:
                fn = _posix_open_file if mode == "file" else _posix_open_dir
                try:
                    r = fn(title, start_dir)
                except Exception as e:
                    r = {"error": f"对话框调用失败: {e}"}
            result.clear()
            result.update(r)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=600)  # 对话框最长等 600s;超时兜底返回,避免请求永远挂起(前端 Failed to fetch)
        if t.is_alive():
            return {"error": "选择对话框超时未响应,请重试"}
        return result

    def _handle_query(self, body):
        """只读 SQL 查询:在后台线程执行并同步等待完成,返回最终结果。"""
        sql = (body.get("sql") or "").strip()
        if not sql:
            return self._send_error("SQL 为空")
        # 首个关键字即可安全判断只读性,先快速拒绝纯写语句,避免也开线程
        kw = mysql_client._query_leading_keyword(sql)
        if kw and (kw in mysql_client._WRITE_KEYWORDS or kw not in mysql_client._READ_KEYWORDS):
            return self._send_error(f"仅允许只读查询(SELECT/SHOW/DESC/EXPLAIN/WITH),语句以 {kw} 开头被拒绝")
        max_rows = body.get("max_rows") or int(config_store.get_settings().get("query_max_rows", mysql_client.QUERY_MAX_ROWS))
        db_name = (body.get("db") or "").strip() or None
        # 基于激活连接配置构建连接;指定 db 时等价 USE 该库(PyMySQL connect database=)
        with _lock:
            cid = _current_conn_id
        if not cid:
            raise mysql_client.DbError("尚未选择数据库连接,请先在「连接管理」中激活一个连接")
        cfg = config_store.get_connection(cid)
        if not cfg:
            raise mysql_client.DbError("连接配置不存在,请重新选择")
        conn = mysql_client.connect(cfg, database=db_name)
        outside = {}

        def _worker():
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT CONNECTION_ID()")
                    row = cur.fetchone()
                    outside["pid"] = row[0] if row else None
                res = mysql_client.run_query(conn, sql, max_rows=max_rows)
                res["pid"] = outside["pid"]
                outside.update(res)
            except Exception as e:
                outside["error"] = str(e)
            finally:
                _close(conn)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()
        if "error" in outside:
            err = outside["error"]
            killed = ("query execution was interrupted" in err.lower()
                      or "connection was killed" in err.lower())
            return self._send_json({"ok": False, "error": err, "killed": killed})
        # 日志只记语句前 80 字符,避免把库名/密文样例写全
        self._log_op("SQL 查询", True, sql[:80] + ("…" if len(sql) > 80 else ""))
        return self._send_json({
            "ok": True,
            "pid": outside.get("pid"),
            "columns": outside.get("columns", []),
            "rows": outside.get("rows", []),
            "truncated": outside.get("truncated", False),
            "affected": outside.get("affected", 0),
            "elapsed": outside.get("elapsed", 0.0),
        })

    def _handle_query_kill(self, body):
        """终止指定线程 ID 正在执行的查询。"""
        pid = body.get("pid")
        if not pid:
            return self._send_error("缺少 pid")
        # 用一条独立连接发 KILL QUERY,杀掉目标连接上的运行中查询
        conn = _get_conn()
        try:
            mysql_client.kill_query(conn, pid)
            return self._send_json({"ok": True})
        finally:
            _close(conn)

    # ---------------- AI 助手 ----------------
    def _handle_ai_config(self, body):
        """保存 AI 配置。api_key 加密落库;不改动时前端应回传原值(留空=清空)。"""
        import ai_client
        base_url = (body.get("base_url") or "").strip()
        model = (body.get("model") or "").strip()
        enabled = bool(body.get("enabled"))
        api_key = body.get("api_key") or ""
        # 前端未填 key 且已配置过 → 保留旧 key(避免误清空)
        if not api_key:
            cur = ai_client.public_config()
            if cur.get("has_key"):
                api_key = "__KEEP__"
        if api_key == "__KEEP__":
            import config_store as _cs
            api_key = _cs.decrypt(_cs.get_settings().get("ai_api_key_enc") or "")
        cfg = ai_client.save_config(base_url, api_key, model, enabled)
        self._log_op("保存 AI 设置", True, f"base_url={cfg.get('base_url')} model={cfg.get('model')}")
        return self._send_json({"ok": True, "config": cfg})

    def _handle_ai_sql_gen(self, body):
        """自然语言 → 只读 SQL(SELECT 等),带当前库 schema 上下文(限 20 表)。"""
        import ai_client
        prompt = (body.get("prompt") or "").strip()
        db_name = (body.get("db") or "").strip() or None
        if not prompt:
            return self._send_error("AI 生成:描述不能为空")
        # 优雅降级:未配置 AI 直接返回可读提示(非 500)
        if not ai_client.is_configured():
            return self._send_json({"ok": False, "error": "AI 功能未配置,请在「系统设置 → AI 设置」中启用并填写 API Key/模型", "unconfigured": True})
        conn = _get_conn()
        try:
            schema = mysql_client.schema_context(conn, db_name or "", max_tables=20) if db_name else ""
        finally:
            _close(conn)
        try:
            sql = ai_client.generate_sql(prompt, schema)
        except ai_client.AiError as e:
            return self._send_json({"ok": False, "error": str(e), "unconfigured": False})
        self._log_op("AI 生成 SQL", True, (prompt[:40] + "…") if len(prompt) > 40 else prompt)
        return self._send_json({"ok": True, "sql": sql})

    def _handle_ai_sql_analyze(self, body):
        """分析 SQL 与 EXPLAIN 结果,给出索引/改写建议。"""
        import ai_client
        sql = (body.get("sql") or "").strip()
        explain_rows = body.get("explain") or []
        db_name = (body.get("db") or "").strip() or None
        if not sql:
            return self._send_error("AI 分析:缺少 SQL")
        if not ai_client.is_configured():
            return self._send_json({"ok": False, "error": "AI 功能未配置,请在「系统设置 → AI 设置」中启用", "unconfigured": True})
        schema = ""
        if db_name:
            conn = _get_conn()
            try:
                schema = mysql_client.schema_context(conn, db_name, max_tables=20)
            finally:
                _close(conn)
        # EXPLAIN 结果转可读文本
        explain_text = "\n".join(" | ".join(str(x) for x in row) for row in (explain_rows or []))
        try:
            advice = ai_client.analyze_sql(sql, explain_text, schema)
        except ai_client.AiError as e:
            return self._send_json({"ok": False, "error": str(e), "unconfigured": False})
        return self._send_json({"ok": True, "advice": advice})

    def _handle_ai_report(self, body):
        """汇总告警/健康采样数据,生成摘要报告。report_type: alert | health。"""
        import ai_client
        if not ai_client.is_configured():
            return self._send_json({"ok": False, "error": "AI 功能未配置,请在「系统设置 → AI 设置」中启用", "unconfigured": True})
        rtype = body.get("type") or "health"
        try:
            ctx = _metrics.ai_report_context(rtype)
        except Exception as e:
            return self._send_error(str(e))
        try:
            text = ai_client.summarize_report(ctx, rtype)
        except ai_client.AiError as e:
            return self._send_json({"ok": False, "error": str(e), "unconfigured": False})
        return self._send_json({"ok": True, "report": text})


    def _handle_ai_test(self, body):
        """测试 AI 连通性：用当前输入的 base_url/api_key/model 直连，不落库。"""
        import ai_client, time as _t
        base_url = (body.get("base_url") or "").strip()
        model = (body.get("model") or "").strip()
        api_key = body.get("api_key") or ""
        # 允许用已保存的 key：前端留空且已配置时，复用落库的 key（与 _handle_ai_config 逻辑一致）
        if not api_key:
            import config_store as _cs
            cur = _cs.get_settings().get("ai_api_key_enc") or ""
            if cur:
                try: api_key = _cs.decrypt(cur)
                except: api_key = ""
        if not api_key:
            return self._send_json({"ok": False, "error": "请填写 API Key"})
        # base_url/model 为空则回退到已保存值
        if not base_url or not model:
            cfg = ai_client.public_config()
            if not base_url: base_url = cfg.get("base_url") or ""
            if not model: model = cfg.get("model") or ""
        try:
            r = ai_client.test_with_params(base_url, api_key, model)
            self._log_op("AI 测试", True, f"model={model} {r['elapsed_ms']}ms")
            return self._send_json({"ok": True, "elapsed_ms": r["elapsed_ms"], "model": model, "base_url": base_url})
        except ai_client.AiError as e:
            return self._send_json({"ok": False, "error": str(e)})

    # ponytail: async download avoids blocking HTTP thread 240s; reuse global state + poll
    _dl_state = {"status": "idle", "msg": "", "ok_cnt": 0, "error": ""}
    _dl_lock = threading.Lock()

    def _handle_setup_download_tools(self, body):
        """瘦版向导：异步下载 MySQL 客户端 tools（双版本 5.7+8.x），立即返回，轮询 status。"""
        import sys as _sys2
        try:
            import env_probe as _ep2
            if _ep2.bundled_tools_summary():
                return self._send_json({"ok": True, "message": "已内置 MySQL 客户端，无需下载", "has_tools": True})
        except: pass
        with self._dl_lock:
            if self._dl_state["status"] == "running":
                return self._send_json({"ok": True, "message": "下载进行中", "status": "running"})
            self._dl_state.update({"status": "running", "msg": "开始下载", "ok_cnt": 0, "error": ""})
        def _worker():
            import os as _os2, shutil as _sh2, urllib.request as _ur2, tarfile as _tf2, zipfile as _zf2, tempfile as _tmp2, sys as _sw, hashlib as _hl2
            import paths as _paths2
            plat = "win64" if _sw.platform == "win32" else "linux"
            OFFICIAL = {
                "win64": {"5.7": "https://dev.mysql.com/get/Downloads/MySQL-5.7/mysql-5.7.44-winx64.zip", "8.0": "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-winx64.zip"},
                "linux": {"5.7": "https://dev.mysql.com/get/Downloads/MySQL-5.7/mysql-5.7.44-linux-glibc2.12-x86_64.tar.gz", "8.0": "https://dev.mysql.com/get/Downloads/MySQL-8.0/mysql-8.0.36-linux-glibc2.12-x86_64.tar.gz"},
            }
            dst_base = _os2.path.join(_paths2.APP_ROOT, "tools")
            _os2.makedirs(dst_base, exist_ok=True)
            urls = OFFICIAL.get(plat, {})
            ok_cnt, last_err = 0, ""
            for ver, url in urls.items():
                sub = _os2.path.join(dst_base, f"mysql-{ver}")
                if _os2.path.isdir(sub) and any(_os2.path.isfile(_os2.path.join(sub, n)) for n in ("mysqldump","mysqldump.exe","mysql","mysql.exe")):
                    ok_cnt += 1
                    continue
                with self._dl_lock:
                    self._dl_state["msg"] = f"下载 MySQL {ver}..."
                # 30s 超时 + 单版本一次尝试（ponytail: 镜像回退可后续加 URL 列表）
                try:
                    tmp = _os2.path.join(_tmp2.gettempdir(), f"mysql-{ver}-{plat}.tmp")
                    req = _ur2.Request(url, headers={"User-Agent": "mysql-console"})
                    with _ur2.urlopen(req, timeout=30) as resp, open(tmp, "wb") as out:
                        _sh2.copyfileobj(resp, out)
                    if _os2.path.getsize(tmp) < 5*1024*1024:
                        last_err = "下载文件过小"
                        try: _os2.remove(tmp)
                        except: pass
                        continue
                    _os2.makedirs(sub, exist_ok=True)
                    if url.endswith(".zip"):
                        with _zf2.ZipFile(tmp) as zf:
                            for info in zf.infolist():
                                if info.filename.endswith(("mysqldump.exe","mysql.exe")):
                                    name = _os2.path.basename(info.filename)
                                    with zf.open(info) as src, open(_os2.path.join(sub, name), "wb") as dst:
                                        _sh2.copyfileobj(src, dst)
                                elif info.filename.endswith(".dll"):
                                    name = _os2.path.basename(info.filename)
                                    if name.lower() in ("libmysql.dll","vcruntime140.dll","msvcp140.dll"):
                                        with zf.open(info) as src, open(_os2.path.join(sub, name), "wb") as dst:
                                            _sh2.copyfileobj(src, dst)
                    else:
                        with _tf2.open(tmp, "r:gz") as tf:
                            for m in tf.getmembers():
                                if m.name.endswith(("bin/mysqldump","bin/mysql")):
                                    name = _os2.path.basename(m.name)
                                    f = tf.extractfile(m)
                                    if f:
                                        with open(_os2.path.join(sub, name), "wb") as dst:
                                            _sh2.copyfileobj(f, dst)
                                        _os2.chmod(_os2.path.join(sub, name), 0o755)
                    try: _os2.remove(tmp)
                    except: pass
                    ok_cnt += 1
                except Exception as e:
                    last_err = str(e)[:200]
                    continue
            try:
                lines=[]
                for root,_,fs in _os2.walk(dst_base):
                    for fn in fs:
                        fp=_os2.path.join(root,fn)
                        rel=_os2.path.relpath(fp, dst_base).replace("\\","/")
                        if rel == "SHA256SUMS": continue
                        h=_hl2.sha256()
                        with open(fp,"rb") as f:
                            for chunk in iter(lambda: f.read(1<<20), b""): h.update(chunk)
                        lines.append(f"{h.hexdigest()}  {rel}")
                if lines:
                    with open(_os2.path.join(dst_base,"SHA256SUMS"),"w",encoding="utf-8") as fh:
                        fh.write("\n".join(sorted(lines))+"\n")
            except: pass
            with self._dl_lock:
                if ok_cnt:
                    self._dl_state.update({"status": "done", "msg": f"已下载 {ok_cnt}/2 版本到 tools/", "ok_cnt": ok_cnt, "error": ""})
                else:
                    self._dl_state.update({"status": "failed", "msg": "下载失败", "ok_cnt": 0, "error": last_err or "网络不可达，可跳过或手动指定客户端目录"})
        threading.Thread(target=_worker, daemon=True).start()
        return self._send_json({"ok": True, "message": "已开始后台下载，请轮询状态", "status": "running"})

    def g_setup_download_tools_status(self, path=None):
        try:
            import env_probe as _ep2
            has = bool(_ep2.bundled_tools_summary())
        except: has = False
        with self._dl_lock:
            st = dict(self._dl_state)
        st["has_tools"] = has
        if has and st["status"] in ("idle","running"):
            st["status"] = "done"
        return self._send_json(st)

    def _ensure_runtime_scripts(self):
        """初始化完成后生成 start/stop/init（安装包仅含 install）。"""
        import sys as _sys3, os as _os3, shutil as _sh3, paths as _paths3
        plat = "win64" if _sys3.platform == "win32" else "linux"
        cand_dirs = [_os3.path.join(_paths3.APP_ROOT, "platforms", plat, "scripts"), _os3.path.join(_paths3.APP_ROOT, "scripts")]
        mapping = {"win64": ["start.bat","stop.bat","init.bat"], "linux": ["start.sh","stop.sh","init.sh"]}
        generated=[]
        for name in mapping.get(plat, []):
            dst = _os3.path.join(_paths3.APP_ROOT, name)
            if _os3.path.exists(dst):
                continue
            src = None
            for d in cand_dirs:
                cand = _os3.path.join(d, name)
                if _os3.path.isfile(cand):
                    src = cand
                    break
            if src:
                _sh3.copy2(src, dst)
                if name.endswith(".sh"):
                    try: _os3.chmod(dst, 0o755)
                    except: pass
                generated.append(name)
        return generated

    def _browse(self, path):
        """目录浏览:返回子目录与 .sql/.sql.gz 文件(均为完整路径,按名排序)。"""
        path = path.strip().strip('"').strip("'")
        if not path:
            if _IS_WIN:
                roots = [f"{d}:\\" for d in "CDEF" if os.path.exists(f"{d}:\\")]
            else:
                roots = ["/"]
            return {"path": "", "dirs": [{"name": r, "path": r} for r in roots],
                    "files": [], "is_root": True}
        if not os.path.isdir(path):
            parent = os.path.dirname(path.rstrip("\\/")) or os.path.abspath(os.sep)
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
                elif e.lower().endswith((".sql", ".sql.gz", ".gz", ".zip")):
                    files.append({"name": e, "path": full,
                                  "size": os.path.getsize(full)})
            except OSError:
                continue
        dirs.sort(key=lambda x: x["name"].lower())
        files.sort(key=lambda x: x["name"].lower())
        parent = os.path.dirname(path.rstrip("\\/")) or os.path.abspath(os.sep)
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
        import urllib.parse as _up
        uh = _up.unquote(uh)  # 先解码:编码后的 @ 是 %40,未解码时 "@" 不存在
        if "@" not in uh:
            return None
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
                # root 是超级管理员:授权不允许通过本工具修改(误改会锁死整个实例)
                if user.lower() == "root":
                    self._log_op("修改MySQL用户授权", False, "{0}@{1} 拒绝(root 保护)".format(user, host))
                    return self._send_error(
                        "root 是 MySQL 超级管理员,不允许通过本工具修改其授权。\n"
                        "如需调整请在 MySQL 命令行执行 GRANT/REVOKE,或登录后用专用账户管理。", 403)
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