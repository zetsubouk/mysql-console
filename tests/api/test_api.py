# -*- coding: utf-8 -*-
"""API 层回归测试(2026-08-28 新增)。

在「隔离的临时 data 目录」上启动真实 HTTP 服务(server.Handler + ThreadingHTTPServer 原样复用),
覆盖主要路由链路。**不触碰真实 data/、不需要真实 MySQL、不需要 mysqldump、不打真实网络**。

用法:
    python tests/test_api.py                 # 全部离线用例(默认)
    python -m unittest tests.test_api        # 等价

隔离机制:
- 运行前设置环境变量 MC_DATA_DIR=<临时目录>(local_store/backup_engine 读取),
  因此 config.db/.secret.key/备份目录全部落在临时目录,真实 data/ 零接触。
- 模块级断言 local_store.DATA_DIR 确实指向临时目录(防止环境钩子被误删后测试静默打到真实数据)。
- 服务进程内启动(端口 0 随机),测完 shutdown + atexit 删除临时目录。

覆盖范围:
1. 轻量模式核心链路: health / auth-status / setup-env / version / settings 读写 / 连接增删改查 /
   schedules 增删改查+切换 / 备份历史 / 备份文件列表+下载 / **下载白名单(防任意文件读取)** /
   task 查询 / 无活动连接时监控类接口错误可读性(400 而非 500) / 静态页 / setup probe-client 与 test-db
2. 降级链路: 无活动连接发起备份 → 400「请先激活连接」;激活假连接发起备份 → 任务以可读错误终止
   (未找到 mysqldump 或连接失败),绝不以「服务器错误: Traceback」形式崩坏。
3. 全量模式认证守卫(无真实 MySQL 也能测): monkeypatch is_password_set=True 模拟「已设密码」,
   受保护路由 401 / 免认证路径 200 / login 503「系统库不可用」可读错误。

刻意不测(交互/GUI 或需真环境): /api/dialog、/api/browse(弹原生对话框)、/api/update/*(真网络)、
/api/service/restart(真重启服务)、/api/users/* 与 /api/backup-files 的真实备份还原闭环
(已有 tests/test_e2e.py 覆盖)。
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(WORKSPACE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)   # 目录结构化: 代码在 src/

# ---- 隔离:必须先于任何模块导入设置 MC_DATA_DIR ----
# 隔离目录建在工作区内(tests/_api_tmp),避免系统临时区写权限/沙箱限制;已加入 .gitignore。
_TMP = os.path.join(WORKSPACE, "tests", "_api_tmp")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["MC_DATA_DIR"] = _TMP

import server                # noqa: E402  导入后所有存储/备份路径均落在 _TMP
import local_store           # noqa: E402
import config_store          # noqa: E402
import backup_engine         # noqa: E402
import schedule_store        # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

# 模块级自检:数据目录确实被隔离(否则后续断言可能打到真实 data/)
assert local_store.DATA_DIR == os.path.join(_TMP), \
    f"隔离失败: local_store.DATA_DIR={local_store.DATA_DIR} 应等于 {_TMP}"
assert config_store.DATA_DIR == _TMP
assert backup_engine.DEFAULT_BACKUP_DIR == os.path.join(_TMP, "backups")

atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))


def _restore_env(key, val):
    """恢复环境变量:原本未设置则删除,否则还原旧值。"""
    if val is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = val


class ApiTest(unittest.TestCase):
    """轻量模式核心 API 链路 + 全量模式认证守卫。"""

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.th.join(timeout=5)

    # ---------------- 工具 ----------------
    def req(self, method, path, body=None, token=None, raw=False, headers=None, timeout=10):
        """发 HTTP 请求,返回 (code, json或原始字节)。headers 为额外请求头。"""
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        r = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("Authorization", "Bearer " + token)
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                payload = resp.read()
                code = resp.status
        except urllib.error.HTTPError as e:
            payload = e.read()
            code = e.code
        if raw:
            return code, payload
        try:
            return code, json.loads(payload.decode("utf-8"))
        except Exception:
            return code, payload.decode("utf-8", "replace")

    def post(self, path, body, token=None):
        return self.req("POST", path, body, token)

    def put_json(self, path, body):
        return self.req("PUT", path, body)

    # ---------------- 基础健康 ----------------
    def test_01_health(self):
        code, j = self.req("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(j, {"ok": True})

    def test_01b_security_headers(self):
        # 安全响应头(2026-09-10):nosniff + DENY 必须出现在所有响应
        r = urllib.request.Request("http://127.0.0.1:%d/api/health" % self.port)
        with urllib.request.urlopen(r, timeout=10) as resp:
            self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")

    def test_02_auth_status_lite(self):
        # 全新轻量模式:未设管理员密码
        code, j = self.req("GET", "/api/auth-status")
        self.assertEqual(code, 200)
        self.assertFalse(j.get("password_set"))

    def test_02b_lite_login_lock_423(self):
        """lite 模式登录失败锁定(2026-09-08 修复,§40.4):真实 HTTP 链路
        4 次错密 401 → 第 5 次 423 → 过期解锁后正确密码可登录。"""
        config_store.set_admin("admin", "right-pass-123")
        try:
            # 第 1-5 次错误密码:401(第 5 次记账达到阈值,锁定自下次尝试起生效)
            for i in range(5):
                code, j = self.post("/api/login",
                                    {"username": "admin", "password": "wrong"})
                self.assertEqual(code, 401, f"第{i+1}次失败应 401")
            # 锁定期内:错误/正确密码均 423(锁检查先于密码验证)
            code, j = self.post("/api/login", {"username": "admin", "password": "wrong"})
            self.assertEqual(code, 423, "达到阈值后必须锁定")
            code, j = self.post("/api/login",
                                {"username": "admin", "password": "right-pass-123"})
            self.assertEqual(code, 423)
            # 锁定过期(locked_until 划到过去)→ 正确密码恢复登录
            import time as _t
            local_store.save_settings({"admin_locked_until": _t.strftime(
                "%Y-%m-%d %H:%M:%S", _t.localtime(_t.time() - 60))})
            code, j = self.post("/api/login",
                                {"username": "admin", "password": "right-pass-123"})
            self.assertEqual(code, 200)
            self.assertTrue(j.get("token"), "解锁后应签发会话 token")
        finally:
            # 复位为「未设密码」的全新 lite 态,不影响后续用例(如 test_99)
            local_store.save_settings({
                "admin_username": "", "admin_password_hash": "",
                "admin_login_fail_count": 0, "admin_locked_until": ""})
            import handlers
            handlers._sessions.clear()

    def test_02c_reset_code_throttle_and_invalidation(self):
        """找回密码防滥用(2026-09-10):60s 节流 + 失败 5 次作废全部未用码。"""
        import handlers
        old_ts = handlers._RESET_CODE_LAST_TS
        old_fail = handlers._RESET_FAIL_COUNT
        handlers._reset_codes.clear()
        handlers._RESET_FAIL_COUNT = 0
        handlers._RESET_CODE_LAST_TS = 0.0
        try:
            with mock.patch.object(config_store, "is_password_set", return_value=True):
                code, j = self.post("/api/request-reset-code", {})
                self.assertEqual(code, 200)
                first_code = list(handlers._reset_codes.keys())[0]
                # 节流:紧接着再取 → 429
                code, j = self.post("/api/request-reset-code", {})
                self.assertEqual(code, 429, "60s 内重复获取必须被节流")
                # 错误尝试计数:第 1-4 次「无效」,第 5 次「作废」且未用码清空
                handlers._RESET_CODE_LAST_TS = 0.0
                bad = "999999" if first_code != "999999" else "999998"
                for i in range(4):
                    code, j = self.post("/api/reset-password",
                                        {"code": bad, "new_password": "newpass123"})
                    self.assertEqual(code, 400)
                    self.assertIn("无效", str(j))
                code, j = self.post("/api/reset-password",
                                    {"code": bad, "new_password": "newpass123"})
                self.assertEqual(code, 400)
                self.assertIn("作废", str(j), "第 5 次失败必须作废全部未用码")
                self.assertEqual(len(handlers._reset_codes), 0, "作废后不得残留可用码")
                # 之前真实签发的码也被作废
                code, j = self.post("/api/reset-password",
                                    {"code": first_code, "new_password": "newpass123"})
                self.assertEqual(code, 400)
        finally:
            handlers._reset_codes.clear()
            handlers._RESET_FAIL_COUNT = old_fail
            handlers._RESET_CODE_LAST_TS = old_ts

    def test_02d_session_cleanup(self):
        """会话周期清理(2026-09-10,PLAN 6.1):过期会话/重置码被清,未过期保留,并发安全(pop)。"""
        import handlers
        live_token = "live-token-xyz"
        dead_token = "dead-token-abc"
        handlers._sessions[live_token] = ("admin", handlers._time.time() + 3600)
        handlers._sessions[dead_token] = ("admin", handlers._time.time() - 1)
        handlers._reset_codes["999001"] = ("admin", handlers._time.time() - 1)
        try:
            handlers._clear_expired_sessions()
            self.assertNotIn(dead_token, handlers._sessions, "过期会话必须被清理")
            self.assertIn(live_token, handlers._sessions, "未过期会话必须保留")
            self.assertNotIn("999001", handlers._reset_codes, "过期重置码必须被清理")
        finally:
            handlers._sessions.pop(live_token, None)
            handlers._sessions.pop(dead_token, None)

    def test_03_setup_env(self):
        code, j = self.req("GET", "/api/setup/env")
        self.assertEqual(code, 200)
        self.assertIsInstance(j, dict)
        # items: [Python, PyMySQL, cryptography, mysqldump, mysql] 环境检测项
        self.assertIn("items", j)
        self.assertIn("all_required_ok", j)
        names = [it.get("name", "") for it in j.get("items", [])]
        self.assertTrue(any("Python" in n for n in names), names)

    def test_04_version(self):
        code, j = self.req("GET", "/api/version")
        self.assertEqual(code, 200)
        from version import __version__
        self.assertEqual(j.get("version"), __version__)

    def test_04b_setup_db_detect(self):
        # 本机数据库检测: 只断言结构与类型(测试环境有无 MySQL 不确定),不断言具体值
        # timeout=30:db-detect 在慢速 Windows runner 上探测服务/端口可超 10s(§41 偶发抖动)
        code, j = self.req("GET", "/api/setup/db-detect", timeout=30)
        self.assertEqual(code, 200)
        self.assertIsInstance(j, dict)
        for k in ("installed", "service_name", "service_state", "mysqld_path",
                  "version", "port", "port_open", "port_hint", "summary"):
            self.assertIn(k, j)
        self.assertIsInstance(j["installed"], bool)
        self.assertIsInstance(j["port_open"], bool)
        self.assertEqual(j["port"], 3306)

    def test_04c_setup_mysql_suggestions(self):
        # 参数建议接口: 显式传内存走纯计算路径;附带路径时返回预检与 my.ini 预览
        # 路径按当前平台给合法值(Windows 要求盘符开头,posix 要求绝对路径)
        import sys as _sys
        if _sys.platform == "win32":
            basedir, datadir = "C:\\mysql-test", "C:\\mysql-test\\data"
        else:
            basedir, datadir = "/opt/mysql", "/opt/mysql/data"
        code, j = self.req("POST", "/api/setup/mysql-suggestions",
                           {"version": "8.0.36", "mem_total_bytes": 8 * 1024 ** 3,
                            "cpu_cores": 4, "basedir": basedir, "datadir": datadir})
        self.assertEqual(code, 200)
        self.assertTrue(j.get("ok"))
        self.assertEqual(j["version"], [8, 0, 36])
        sug = j["suggestions"]
        self.assertEqual(sug["innodb_buffer_pool_size"], "4G")   # 8G×50%
        self.assertEqual(sug["max_connections"], 200)
        self.assertEqual(sug["collation_server"], "utf8mb4_0900_ai_ci")
        self.assertEqual(j["errors"], [])
        self.assertIn("[mysqld]", j["my_cnf_preview"])
        self.assertIn("basedir=", j["my_cnf_preview"])

    def test_04d_setup_mysql_suggestions_legacy_and_bad_paths(self):
        # 5.7 排序规则回退 + 非法路径被预检拦截、预览留空
        code, j = self.req("POST", "/api/setup/mysql-suggestions",
                           {"version": "5.7.44", "mem_total_bytes": 2 * 1024 ** 3,
                            "basedir": "relative/path", "datadir": "data"})
        self.assertEqual(code, 200)
        self.assertEqual(j["suggestions"]["collation_server"], "utf8mb4_unicode_ci")
        self.assertEqual(len(j["errors"]), 2)
        self.assertEqual(j["my_cnf_preview"], "")

    def test_04e_setup_mysql_versions_offline_fallback(self):
        # 版本列表: mock 网络失败 → 必须回退内置清单(接口契约: 永不报错、永不为空)
        import mysql_installer
        with mock.patch.object(mysql_installer, "_fetch_official_versions",
                               side_effect=RuntimeError("network down")):
            code, j = self.req("GET", "/api/setup/mysql-versions")
        self.assertEqual(code, 200)
        self.assertEqual(j.get("source"), "builtin")
        self.assertTrue(j.get("versions"))
        self.assertIn("win_url", j["versions"][0])
        self.assertIn("linux_url", j["versions"][0])

    # ---------------- 安全加固(访问令牌 / CSRF / security-inform) ----------------
    def test_50_security_info_loopback(self):
        # 默认回环绑定:不强制访问令牌
        code, j = self.req("GET", "/api/security/info")
        self.assertEqual(code, 200)
        self.assertIn("access_token_required", j)
        self.assertIn("tls", j)

    def test_51_access_token_enforced_when_exposed(self):
        import security
        old_host, old_at = os.environ.get("MC_HOST"), os.environ.get("MC_ACCESS_TOKEN")
        try:
            os.environ["MC_HOST"] = "0.0.0.0"
            os.environ["MC_ACCESS_TOKEN"] = "secret-token-123"
            # 无令牌 → 401 + access_required
            code, j = self.req("GET", "/api/health")
            self.assertEqual(code, 401)
            self.assertTrue(j.get("access_required"))
            # 带正确令牌 → 通过
            code, j = self.req("GET", "/api/health", headers={"X-Access-Token": "secret-token-123"})
            self.assertEqual(code, 200)
            # 错误令牌 → 401
            code, _ = self.req("GET", "/api/health", headers={"X-Access-Token": "wrong"})
            self.assertEqual(code, 401)
        finally:
            _restore_env("MC_HOST", old_host)
            _restore_env("MC_ACCESS_TOKEN", old_at)

    def test_52_access_token_not_enforced_on_loopback(self):
        import security
        old_host, old_at = os.environ.get("MC_HOST"), os.environ.get("MC_ACCESS_TOKEN")
        try:
            os.environ["MC_HOST"] = "127.0.0.1"
            os.environ["MC_ACCESS_TOKEN"] = "whatever"
            code, j = self.req("GET", "/api/health")
            self.assertEqual(code, 200)
        finally:
            _restore_env("MC_HOST", old_host)
            _restore_env("MC_ACCESS_TOKEN", old_at)

    def test_53_csrf_blocks_wrong_origin(self):
        code, j = self.req("POST", "/api/logout", headers={"Origin": "http://evil.example.com"})
        self.assertEqual(code, 403)

    def test_54_csrf_allows_same_origin_and_no_origin(self):
        # 同源 Origin 放行
        r_host = "%s:%d" % ("127.0.0.1", self.port)
        code, _ = self.req("POST", "/api/logout", headers={"Origin": "http://" + r_host})
        self.assertIn(code, (200, 401))  # 不因 CSRF 拦截即可
        # 无 Origin(CLI/脚本)放行
        code, _ = self.req("POST", "/api/logout")
        self.assertIn(code, (200, 401))

    def test_55_security_pure_helpers(self):
        import security
        self.assertTrue(security.is_loopback("127.0.0.1"))
        self.assertTrue(security.is_loopback("localhost"))
        self.assertFalse(security.is_loopback("0.0.0.0"))
        self.assertTrue(security.origin_allowed("a.com", "http://a.com"))
        self.assertFalse(security.origin_allowed("a.com", "http://evil.com"))
        self.assertTrue(security.origin_allowed("a.com", None))
        self.assertTrue(security.origin_allowed("a.com:8090", "http://a.com:8090"))
        self.assertTrue(security.origin_allowed("a.com", "https://a.com"))

    # ---------------- 设置 ----------------
    def test_05_settings_defaults_and_roundtrip(self):
        code, j = self.req("GET", "/api/settings")
        self.assertEqual(code, 200)
        self.assertEqual(j.get("run_mode"), "lite")
        # DEFAULT_SETTINGS 自动补齐:新键应存在
        for k in ("mysql_bin", "backup_dir", "alert_max_conn", "update_check_interval"):
            self.assertIn(k, j)
        # PUT 回写 + 回读
        code, j = self.put_json("/api/settings", {"alert_max_conn": 150})
        self.assertEqual(code, 200)
        code, j = self.req("GET", "/api/settings")
        self.assertEqual(j.get("alert_max_conn"), 150)

    # ---------------- 连接 CRUD ----------------
    def test_06_connection_crud(self):
        # 新建
        code, j = self.post("/api/connections", {
            "name": "测试连接", "host": "127.0.0.1", "port": 1,
            "user": "root", "password": "x", "note": "api-test",
        })
        self.assertEqual(code, 201)
        cid = j.get("id")
        self.assertTrue(cid)
        # 列表包含
        code, lst = self.req("GET", "/api/connections")
        self.assertEqual(code, 200)
        self.assertTrue(any(c["id"] == cid for c in lst))
        # 编辑
        code, j = self.req("PUT", "/api/connections/" + cid, {"note": "api-test-改"})
        self.assertEqual(code, 200)
        # 激活假连接:无真实 MySQL → 400 可读错误(非 500)
        code, j = self.post("/api/connect", {"id": cid})
        self.assertEqual(code, 400)
        self.assertNotIn("Traceback", str(j))
        # 激活不存在的连接 → 400「连接不存在」
        code, j = self.post("/api/connect", {"id": "badid"})
        self.assertEqual(code, 400)
        self.assertTrue("连接不存在" in str(j))
        # 删除
        code, j = self.req("DELETE", "/api/connections/" + cid)
        self.assertEqual(code, 200)
        code, lst = self.req("GET", "/api/connections")
        self.assertFalse(any(c["id"] == cid for c in lst))

    def test_06b_remote_os_and_remote_check(self):
        # 连接保存/回读 remote_os(远程服务器类型)
        code, j = self.post("/api/connections", {
            "name": "远程测试", "host": "db.example.com", "port": 3306,
            "user": "root", "password": "x", "remote_os": "windows",
            "ssh_host": "j.example", "ssh_user": "u",
        })
        self.assertEqual(code, 201)
        cid = j.get("id")
        try:
            code, lst = self.req("GET", "/api/connections")
            c = next((x for x in lst if x["id"] == cid), None)
            self.assertEqual(c["remote_os"], "windows")
            # 探测接口:未配置 SSH 主机 → 400 可读(非 500)
            code, j2 = self.post("/api/connections/remote-check", {})
            self.assertEqual(code, 400)
            self.assertNotIn("Traceback", str(j2))
            # 探测接口:配置了 SSH 但本机无 ssh → 400 可读
            import handlers
            with mock.patch.object(handlers.ssh_tunnel, "ssh_available", return_value=False):
                code, j2 = self.post("/api/connections/remote-check", {"id": cid})
                self.assertEqual(code, 400)
                self.assertNotIn("Traceback", str(j2))
            # 探测接口:mock 探测成功 → 200 + os 回填
            with mock.patch.object(handlers.ssh_tunnel, "ssh_available", return_value=True), \
                 mock.patch.object(handlers.ssh_tunnel, "probe_remote_env",
                                   return_value={"os": "linux", "git_bash": False,
                                                 "detail": "Linux"}):
                code, j2 = self.post("/api/connections/remote-check", {"id": cid})
                self.assertEqual(code, 200)
                self.assertEqual(j2["os"], "linux")
        finally:
            self.req("DELETE", "/api/connections/" + cid)

    def test_06c_backup_files_remote(self):
        import handlers
        # 无激活连接 → 400「请先激活连接」
        code, j = self.post("/api/backup-files/remote", {})
        self.assertEqual(code, 400)
        # 本地连接 → 400「本机连接无需远程还原文件」(本地模式不被远程功能破坏)
        code, j = self.post("/api/connections", {
            "name": "本机", "host": "127.0.0.1", "port": 3306, "user": "r", "password": "x"})
        cid = j["id"]
        server._set_active_conn(cid)
        try:
            code, j = self.post("/api/backup-files/remote", {})
            self.assertEqual(code, 400)
            self.assertIn("本机连接", str(j))
        finally:
            self.req("DELETE", "/api/connections/" + cid)
            server._set_active_conn(None)
        # 远程连接 + mock 探测/列目录 → 200 files
        code, j = self.post("/api/connections", {
            "name": "远程", "host": "db.example.com", "port": 3306, "user": "r",
            "password": "x", "ssh_host": "j", "remote_backup_dir": "/bak"})
        cid2 = j["id"]
        server._set_active_conn(cid2)
        try:
            with mock.patch.object(handlers.ssh_tunnel, "ssh_available", return_value=True), \
                 mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                                   return_value={"os": "linux", "git_bash": False}), \
                 mock.patch.object(backup_engine.ssh_tunnel, "ssh_run",
                                   return_value="__OK__\ndb1_20260831.sql.gz\t100\t2026-08-31 10:00"):
                code, j = self.post("/api/backup-files/remote", {})
            self.assertEqual(code, 200)
            self.assertEqual(j["dir"], "/bak")
            self.assertEqual(len(j.get("files", [])), 1)
            # 手动指定远程目录
            with mock.patch.object(handlers.ssh_tunnel, "ssh_available", return_value=True), \
                 mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                                   return_value={"os": "linux", "git_bash": False}), \
                 mock.patch.object(backup_engine.ssh_tunnel, "ssh_run",
                                   return_value="__OK__\ndb2.sql\t50\t2026-08-30 09:00"):
                code, j = self.post("/api/backup-files/remote", {"dir": "/other"})
            self.assertEqual(code, 200)
            self.assertEqual(j["dir"], "/other")
            # Windows 未配 Git Bash → 400 可读引导
            with mock.patch.object(handlers.ssh_tunnel, "ssh_available", return_value=True), \
                 mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                                   return_value={"os": "windows", "git_bash": False}):
                code, j = self.post("/api/backup-files/remote", {})
            self.assertEqual(code, 400)
            self.assertIn("Git Bash", str(j))
        finally:
            self.req("DELETE", "/api/connections/" + cid2)
            server._set_active_conn(None)

    # ---------------- 无活动连接的错误可读性 ----------------
    def test_07_monitor_errors_readable(self):
        # 未激活任何连接时,监控类接口必须 400 可读错误而非 500
        for path in ("/api/overview", "/api/databases", "/api/users",
                     "/api/processlist", "/api/monitor", "/api/monitor/full",
                     "/api/dashboard/health", "/api/dashboard/innodb",
                     "/api/dashboard/tablespace", "/api/dashboard/replication",
                     "/api/alerts", "/api/variables"):
            code, body = self.req("GET", path, raw=True)
            self.assertEqual(code, 400, "%s 期望 400,实际 %d: %s" % (path, code, body[:120]))
            txt = body.decode("utf-8", "replace")
            self.assertNotIn("服务器错误", txt, path)
            self.assertNotIn("Traceback", txt, path)

    def test_08_unknown_api_404(self):
        code, j = self.req("GET", "/api/does-not-exist")
        self.assertEqual(code, 404)

    # ---------------- 定时任务(内置引擎,不碰系统计划任务) ----------------
    def test_09_schedule_crud(self):
        # 新建(builtin,不注册系统计划任务)
        code, j = self.post("/api/schedules", {
            "name": "测试任务", "engine": "builtin", "freq": "daily",
            "time": "02:00", "dbs": [], "keep": 7,
        })
        self.assertEqual(code, 201)
        tid = j.get("id")
        self.assertTrue(tid)
        # 列表含描述
        code, lst = self.req("GET", "/api/schedules")
        self.assertEqual(code, 200)
        t = next((x for x in lst if x["id"] == tid), None)
        self.assertIsNotNone(t)
        self.assertTrue(t.get("desc"))
        # 编辑
        code, j = self.req("PUT", "/api/schedules/" + tid, {"keep": 3})
        self.assertEqual(code, 200)
        # 启停
        code, j = self.post("/api/schedules/toggle", {"id": tid, "enabled": False})
        self.assertEqual(code, 200)
        code, lst = self.req("GET", "/api/schedules")
        t = next(x for x in lst if x["id"] == tid)
        self.assertFalse(t.get("enabled"))
        # 环境信息
        code, env = self.req("GET", "/api/schedules/env")
        self.assertEqual(code, 200)
        self.assertIn("os", env)
        # 删除
        code, j = self.req("DELETE", "/api/schedules/" + tid)
        self.assertEqual(code, 200)
        code, lst = self.req("GET", "/api/schedules")
        self.assertFalse(any(x["id"] == tid for x in lst))

    # ---------------- 备份历史与文件白名单 ----------------
    def test_10_backup_history_empty(self):
        code, j = self.req("GET", "/api/backups")
        self.assertEqual(code, 200)
        self.assertEqual(j, [])

    def test_11_backup_files_whitelist(self):
        # 配置一个临时备份目录并放一个 .sql
        bk = os.path.join(_TMP, "backup-test")
        os.makedirs(bk, exist_ok=True)
        with open(os.path.join(bk, "hello.sql"), "w", encoding="utf-8") as f:
            f.write("CREATE DATABASE x;")
        code, j = self.put_json("/api/settings", {"backup_dir": bk})
        self.assertEqual(code, 200)
        # 列表可见
        code, lst = self.req("GET", "/api/backup-files")
        self.assertEqual(code, 200)
        self.assertTrue(any(f["name"] == "hello.sql" for f in lst))
        # 合法下载
        q = urllib.parse.urlencode({"file": os.path.join(bk, "hello.sql")})
        code, data = self.req("GET", "/api/backup-files/download?" + q, raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"CREATE DATABASE", data)
        # 白名单:允许目录外的 .sql 文件必须 404(防任意文件读取)
        # 放在隔离数据目录根(不在 backup-test 也不在默认 backups 内),确保文件存在且后缀合法
        evil = os.path.join(_TMP, "evil.sql")
        with open(evil, "w", encoding="utf-8") as f:
            f.write("evil")
        try:
            q = urllib.parse.urlencode({"file": evil})
            code, j = self.req("GET", "/api/backup-files/download?" + q)
            self.assertEqual(code, 404, "允许目录外的 .sql 不应可下载")
        finally:
            os.remove(evil)
        # 敏感文件/非法路径(存在但不在允许目录,或后缀不符,或尝试穿越)→ 404
        for bad in ("server.py", "data/config.db", "config.db",
                    "../hello.sql", "../data/backups/../config.db"):
            q = urllib.parse.urlencode({"file": bad})
            code, j = self.req("GET", "/api/backup-files/download?" + q)
            self.assertEqual(code, 404, "非法下载应 404: " + bad)

    # ---------------- 备份降级链路 ----------------
    def test_12_backup_requires_active_conn(self):
        code, j = self.post("/api/backup", {"dbs": []})
        self.assertEqual(code, 400)
        self.assertTrue("请先激活连接" in str(j))

    def test_13_backup_task_fails_gracefully(self):
        # 激活一个假连接(127.0.0.1:1,必拒绝)
        code, j = self.post("/api/connections", {
            "name": "假连接", "host": "127.0.0.1", "port": 1,
            "user": "root", "password": "",
        })
        cid = j["id"]
        server._set_active_conn(cid)
        try:
            code, j = self.post("/api/backup", {"dbs": [], "gzip": False})
            self.assertEqual(code, 202)
            tid = j.get("task_id")
            # 轮询任务至终态
            status = "running"
            deadline = time.time() + 20
            while time.time() < deadline:
                code, t = self.req("GET", "/api/task/" + tid)
                self.assertEqual(code, 200)
                status = t.get("status")
                if status in ("done", "failed"):
                    break
                time.sleep(0.3)
            self.assertIn(status, ("done", "failed"), "备份任务应在 20s 内终止")
            # 错误必须可读(未找到客户端 / 连接失败),不得以「服务器错误: Traceback」崩坏
            err = t.get("error") or ""
            self.assertTrue(err, "失败任务应有 error 文本")
            self.assertNotIn("Traceback", err)
            self.assertNotIn("服务器错误", err)
            # 任务不存在 → 404
            code, j = self.req("GET", "/api/task/nonexistent")
            self.assertEqual(code, 404)
        finally:
            server._set_active_conn(None)
            self.req("DELETE", "/api/connections/" + cid)

    def test_13b_backup_busy_returns_409(self):
        # 互斥(2026-09-08 修复 _task_lock 死代码):任务进行中发起新备份/还原 → 409 可读错误
        code, j = self.post("/api/connections", {
            "name": "假连接409", "host": "127.0.0.1", "port": 1,
            "user": "root", "password": "",
        })
        cid = j["id"]
        server._set_active_conn(cid)
        # p_restore 在互斥检查前先校验文件存在,需给一个真实文件才能走到 409
        dump = os.path.join(_TMP, "busy_restore_fixture.sql")
        with open(dump, "w", encoding="utf-8") as f:
            f.write("-- fixture")
        try:
            with backup_engine._task_lock:   # 直接占住互斥锁,模拟任务进行中
                code, j = self.post("/api/backup", {"dbs": [], "gzip": False})
                self.assertEqual(code, 409)
                self.assertIn("正在进行", str(j))
                code, j = self.post("/api/restore", {"target_db": "x", "file": dump})
                self.assertEqual(code, 409)
                self.assertIn("正在进行", str(j))
        finally:
            os.remove(dump)
            server._set_active_conn(None)
            self.req("DELETE", "/api/connections/" + cid)

    def test_13c_task_cancel_unknown_returns_404(self):
        # 取消接口(2026-09-10):无进行中任务/未知 tid → 404;非 cancel 后缀 → 404
        code, j = self.post("/api/task/deadbeef1234/cancel", {})
        self.assertEqual(code, 404)
        code, j = self.post("/api/task/whatever", {})
        self.assertEqual(code, 404)

    def test_13d_oversized_body_413(self):
        # 请求体上限(2026-09-10):Content-Length 超过 10MB → 413 且不断言内容
        import socket as _socket
        with _socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            head = (b"POST /api/query HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Content-Type: application/json\r\nContent-Length: 11000000\r\n\r\n{}")
            s.sendall(head)
            data = s.recv(65536)
        status_line = data.split(b"\r\n", 1)[0]
        self.assertIn(b"413", status_line, "超大请求体必须回 413,实际: %r" % status_line)

    # ---------------- 引导相关 ----------------
    def test_14_setup_probe_bad_path(self):
        # 无效客户端路径 → 400 + ok False
        code, j = self.post("/api/setup/probe-client", {"path": "Z:\\no-such-dir-xyz"})
        self.assertEqual(code, 400)
        self.assertFalse(j.get("ok"))

    def test_15_setup_test_db_refused(self):
        # 连不上的主机 → 200 + ok False + 可读错误(不是 500)
        code, j = self.post("/api/setup/test-db", {
            "host": "127.0.0.1", "port": 1, "user": "root", "password": "",
        })
        self.assertEqual(code, 200)
        self.assertFalse(j.get("ok"))
        self.assertIn("error", j)

    def test_16_logs_lite_empty(self):
        # 轻量模式不记录操作日志 → 空列表
        code, j = self.req("GET", "/api/logs")
        self.assertEqual(code, 200)
        self.assertEqual(j, [])

    def test_17_static_pages(self):
        code, data = self.req("GET", "/", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"<!DOCTYPE", data[:500])
        code, data = self.req("GET", "/login.html", raw=True)
        self.assertEqual(code, 200)
        code, j = self.req("GET", "/no-such-page.html")
        self.assertEqual(code, 404)

    # ---------------- 用户管理 URL 解析(bug1 回归,2026-08-28) ----------------
    def test_18_user_path_encode_roundtrip(self):
        # 前端 encodeURIComponent("user@host") 会把 @ 编码成 %40、% 编码成 %25;
        # 解析必须先 unquote 再判断 "@",否则一切用户标识都会被误判非法。
        self.assertEqual(server.Handler._parse_user_path(None, "/api/users/root%40%25/grants"),
                         ("root", "%", "grants"))
        self.assertEqual(server.Handler._parse_user_path(None, "/api/users/app%40localhost/grants"),
                         ("app", "localhost", "grants"))
        self.assertEqual(server.Handler._parse_user_path(None, "/api/users/app%40localhost"),
                         ("app", "localhost", ""))
        # 未编码形式(兼容旧调用)
        self.assertEqual(server.Handler._parse_user_path(None, "/api/users/app@localhost/grants"),
                         ("app", "localhost", "grants"))
        # 真正非法(不含 @)
        self.assertIsNone(server.Handler._parse_user_path(None, "/api/users/plainname/grants"))

    def test_19_user_grants_encoded_url_not_404(self):
        # 修复前:编码后的 @ 未先解码即判非法 → 404「非法的用户标识」;
        # 修复后应正常走「未激活连接」的可读 400,而不是 404。
        code, j = self.req("GET", "/api/users/root%40%25/grants")
        self.assertEqual(code, 400)
        self.assertNotIn("非法的用户标识", str(j))
        self.assertIn("连接", str(j))

    # ---------------- 全量模式认证守卫(无真实 MySQL 也可测) ----------------
    def test_99_full_mode_auth_guard(self):
        import local_store as ls
        ls.set_meta("run_mode", "full")   # 本地 meta = 全量
        try:
            # 模拟「系统库已设密码但暂不可达」:is_password_set 保守返回 True(见 config_store)
            with mock.patch.object(config_store, "is_password_set", return_value=True):
                # 免认证路径仍可访问
                code, j = self.req("GET", "/api/health")
                self.assertEqual(code, 200)
                code, j = self.req("GET", "/api/auth-status")
                self.assertEqual(code, 200)
                # 受保护路径:无 token / 假 token → 401
                code, j = self.req("GET", "/api/connections")
                self.assertEqual(code, 401)
                code, j = self.req("GET", "/api/overview")
                self.assertEqual(code, 401)
                code, j = self.req("GET", "/api/backup-files")
                self.assertEqual(code, 401)
                code, j = self.req("GET", "/api/connections", token="bogus-token")
                self.assertEqual(code, 401)
                # 登录:系统库不可达 → 503 可读错误(不回退文件层旧密码)
                code, j = self.post("/api/login", {"username": "admin", "password": "x"})
                self.assertEqual(code, 503)
                self.assertIn("系统库不可用", str(j))
        finally:
            ls.set_meta("run_mode", "lite")   # 复位,不影响其他用例


class TlsApiTest(unittest.TestCase):
    """MC_TLS 真实握手(2026-09-10,PLAN_HARDENING 5.6):自签证书 + HTTPS 请求闭环。

    此前 security.wrap_socket 的服务端路径零测试触达,TLS 特性只能线上暴露问题。
    客户端用不校验证书的 SSLContext(自签证书本就无受信链,这里只验证握手与 HTTP 闭环)。
    """

    @classmethod
    def setUpClass(cls):
        import ssl
        import security
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.httpd.socket = security.wrap_socket(cls.httpd.socket, local_store.DATA_DIR, "127.0.0.1")
        cls.th = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.th.join(timeout=5)

    def test_https_health_roundtrip(self):
        import ssl
        import http.client
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, context=ctx, timeout=10)
        try:
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            body = resp.read()
            self.assertEqual(resp.status, 200, body)
            self.assertEqual(json.loads(body.decode("utf-8")).get("ok"), True)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)