# -*- coding: utf-8 -*-
"""MySQL Console 主服务(HTTP 传输层):API + 静态页 + 调度启动。

业务处理已拆分到业务层 handlers.HandlerBase(由 routes.py 注册表驱动路由),
本文件只保留:
- socket/HTTP 传输(BaseHTTPRequestHandler + ThreadingHTTPServer);
- JSON 编解码/静态资源/下载流;
- 认证守卫组合(_auth_guard)与 CSRF/访问令牌门;
- 后台线程启动(调度/更新检查/告警采样)与 TLS/部署安全门槛。
"""
import json
import mimetypes
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 嵌入式私有运行时(._pth 存在时)不会自动把脚本目录加入 sys.path[0]。
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import paths
import security
# 业务层(routes.py 已由 handlers 导入并在内部完成路由分发)
import mysql_client
import config_store
import backup_engine
import schedule_store
import native_scheduler
from handlers import (
    HandlerBase,
    scheduler_loop, _update_loop, _alert_history_loop,
    _is_auth_required, _check_auth, _check_access_token, _check_csrf,
    _set_active_conn,  # 向后兼容:历史测试/外部脚本通过 server._set_active_conn 激活连接
)

APP_ROOT = paths.APP_ROOT
STATIC_DIR = paths.static_dir()
# 绑定地址:默认仅回环 127.0.0.1;需要局域网/0.0.0.0 暴露时用环境变量 MC_HOST / MC_PORT。
# 注意:绑定到非回环地址会自动强制要求设置「访问令牌」(见 security.access_token_required)。
HOST = (os.environ.get("MC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
PORT = security.bind_port()

# 客户端断开/重置导致的连接中止(WinError 10053/10054、BrokenPipe)。
_CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class Handler(HandlerBase, BaseHTTPRequestHandler):
    server_version = "MySQLConsole/1.0"

    def log_message(self, fmt, *args):
        pass

    def handle_error(self, request, client_address):
        import sys as _sys
        exc = _sys.exc_info()[1]
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
        # 外层:访问令牌门(0.0.0.0 暴露时强制),先于会话认证
        if not _check_access_token(self):
            return False
        if not _is_auth_required(path):
            return True
        if _check_auth(self):
            return True
        self._send_json({"error": "未登录或登录已过期", "code": 401}, 401)
        return False

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

    def do_POST(self):
        path = self.path.split("?")[0]
        # 认证守卫（登录/认证状态/健康检查除外）
        if not self._auth_guard():
            return
        # CSRF 纵深防御:写请求校验 Origin/Host 同源
        if not _check_csrf(self, "POST"):
            return
        try:
            self._route_post(path)
        except mysql_client.DbError as e:
            self._send_error(str(e))
        except Exception as e:
            self._send_error(f"服务器错误: {e}", 500)

    def do_PUT(self):
        path = self.path.split("?")[0]
        if not self._auth_guard():
            return
        if not _check_csrf(self, "PUT"):
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
                patch = dict(body)
                # access_token 需 Fernet 加密后落库,单独走专属写入,避免明文直存
                at = patch.pop("access_token", None)
                if at is not None:
                    config_store.set_access_token(str(at).strip())
                s = config_store.save_settings(patch)
                return self._send_json({"ok": True, "settings": s})
            self._send_error("未知接口", 404)
        except Exception as e:
            self._send_error(f"服务器错误: {e}", 500)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if not self._auth_guard():
            return
        if not _check_csrf(self, "DELETE"):
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


def main():
    # Windows 控制台/重定向管道默认用 cp1252 之类的 ANSI 编码,无法输出中文启动横幅,
    # 会抛 UnicodeEncodeError 导致服务启动即崩;统一重配置为 UTF-8 输出(跨平台健壮)。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass  # 无 reconfigure(如被替换的流)或已锁定编码时静默跳过

    os.makedirs(paths.DATA_DIR, exist_ok=True)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=_update_loop, daemon=True).start()
    threading.Thread(target=_alert_history_loop, daemon=True).start()
    # 部署安全检查:绑定到非回环地址时必须已设置访问令牌
    if security.access_token_required():
        if not security.effective_access_token():
            print("=" * 56)
            print("  错误:监听地址 %s 是非回环地址,必须设置访问令牌!" % HOST)
            print("  请设置环境变量 MC_ACCESS_TOKEN=<你的令牌> 后再启动。")
            print("  (安全考量:0.0.0.0 暴露时缺少访问令牌将拒绝启动)")
            print("=" * 56)
            return
        if not security.tls_enabled():
            print("=" * 56)
            print("  警告:你正以 NON-TLS(明文 HTTP) 暴露到 %s。强烈建议设置" % HOST)
            print("        MC_TLS=1 启用 HTTPS(自签证书),避免凭据/备份内容在网络中明文传输。")
            print("=" * 56)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    scheme = "https" if security.tls_enabled() else "http"
    if security.tls_enabled():
        server.socket = security.wrap_socket(server.socket, paths.DATA_DIR, HOST)
    addr = "" if HOST == "0.0.0.0" else HOST
    print(f"MySQL Console 已启动: {scheme}://{addr or '127.0.0.1'}:{PORT}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == "__main__":
    main()