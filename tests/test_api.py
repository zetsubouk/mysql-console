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

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

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
    def req(self, method, path, body=None, token=None, raw=False):
        """发 HTTP 请求,返回 (code, json或原始字节)。"""
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        r = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            r.add_header("Content-Type", "application/json")
        if token:
            r.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
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

    def test_02_auth_status_lite(self):
        # 全新轻量模式:未设管理员密码
        code, j = self.req("GET", "/api/auth-status")
        self.assertEqual(code, 200)
        self.assertFalse(j.get("password_set"))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)