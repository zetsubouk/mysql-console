# -*- coding: utf-8 -*-
"""离线单元测试(2026-08-28 新增):纯逻辑/纯函数,零依赖、无需 MySQL、无需 mysqldump。

覆盖(按模块):
- backup_engine: 备份文件白名单 resolve_backup_file(穿越/敏感路径/后缀/不存在)、_safe_filename、
  _gz_uncompressed_size(构造真实 gzip 验证 ISIZE)、_cli_args 参数构造(隔离目录 + 假客户端文件)
- env_probe: find_tool(配置为目录/完整可执行文件)、parse_version(多格式文本)
- schedule_store: is_due(每日/每周 tm_wday 映射/每月/一次性/禁用开关)、describe(周期文案)、
  save_task 校验与钳制、delete/set_enabled/update_run_status 闭环
- local_store: meta/连接/设置 CRUD + reset_all + clear_lite_data(保留最小 bootstrap)
- config_store: Fernet 加密往返、pbkdf2 哈希/校验、lite 模式默认键补齐、管理员密码 lite 链路
- mysql_client(mock pymysql): connect 失败→DbError、test() 成功路径、_q/_q1 列名与行归一

隔离:脚本开头设置 MC_DATA_DIR=工作区内临时目录,绝不触碰真实 data/。
用法: python tests/test_units.py   (需 pymysql+cryptography,同 test_api.py 的环境)
"""
import atexit
import gzip
import os
import shutil
import sys
import time
import unittest
from unittest import mock

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(WORKSPACE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)   # 目录结构化: 代码在 src/

# ---- 隔离:必须先于任何模块导入设置 MC_DATA_DIR ----
_TMP = os.path.join(WORKSPACE, "tests", "_units_tmp")
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)
os.environ["MC_DATA_DIR"] = _TMP

import local_store              # noqa: E402
import config_store             # noqa: E402
import backup_engine            # noqa: E402
import env_probe                # noqa: E402
import schedule_store           # noqa: E402
import mysql_client             # noqa: E402

assert local_store.DATA_DIR == _TMP, "隔离失败: 数据目录未指向临时目录"
assert backup_engine.DEFAULT_BACKUP_DIR == os.path.join(_TMP, "backups")

atexit.register(lambda: shutil.rmtree(_TMP, ignore_errors=True))


class BackupEngineTest(unittest.TestCase):
    """备份引擎纯函数:文件白名单、gzip ISIZE、_cli_args。"""

    @classmethod
    def setUpClass(cls):
        cls.bk = os.path.join(_TMP, "backups")
        os.makedirs(cls.bk, exist_ok=True)
        cls.ok = os.path.join(cls.bk, "ok.sql")
        with open(cls.ok, "w", encoding="utf-8") as f:
            f.write("CREATE DATABASE x;")
        # 假客户端文件(内容无关,仅验证路径解析);按平台命名,理由同 EnvProbeTest
        cls.bin_dir = os.path.join(_TMP, "bin")
        os.makedirs(cls.bin_dir, exist_ok=True)
        dump_name = "mysqldump.exe" if env_probe.IS_WIN else "mysqldump"
        cls.exe = os.path.join(cls.bin_dir, dump_name)
        with open(cls.exe, "wb") as f:
            f.write(b"dummy")

    def test_resolve_whitelist_ok(self):
        rp = backup_engine.resolve_backup_file(self.ok)
        self.assertEqual(rp, os.path.realpath(self.ok))

    def test_resolve_rejects_outside_sql(self):
        # 允许目录之外、后缀合法的 .sql → 必须拒绝(防任意文件读取)
        evil = os.path.join(_TMP, "evil.sql")
        with open(evil, "w", encoding="utf-8") as f:
            f.write("evil")
        try:
            self.assertIsNone(backup_engine.resolve_backup_file(evil))
        finally:
            os.remove(evil)

    def test_resolve_rejects_suffix_and_nonexist_and_traversal(self):
        wrong = os.path.join(self.bk, "ok.txt")
        with open(wrong, "w", encoding="utf-8") as f:
            f.write("no")
        try:
            self.assertIsNone(backup_engine.resolve_backup_file(wrong))   # 后缀不符
        finally:
            os.remove(wrong)
        self.assertIsNone(backup_engine.resolve_backup_file(os.path.join(self.bk, "nope.sql")))  # 不存在
        self.assertIsNone(backup_engine.resolve_backup_file("../evil.sql"))                      # 穿越
        self.assertIsNone(backup_engine.resolve_backup_file(os.path.join(_TMP, ".secret.key")))  # 敏感文件

    def test_safe_filename(self):
        self.assertEqual(backup_engine._safe_filename("a/b:c*d?e\"f<g>h|i"),
                         "a_b_c_d_e_f_g_h_i")

    def test_gz_uncompressed_size(self):
        p = os.path.join(self.bk, "gz_size_test.sql.gz")
        data = b"A" * 100000
        with gzip.open(p, "wb") as f:
            f.write(data)
        try:
            self.assertEqual(backup_engine._gz_uncompressed_size(p), 100000)
        finally:
            os.remove(p)
        # 文件过短/不存在 → 0(不抛)
        self.assertEqual(backup_engine._gz_uncompressed_size(os.path.join(self.bk, "no.gz")), 0)

    def test_cli_args_full_file_and_dir(self):
        conn = {"host": "10.0.0.1", "port": 3307, "user": "app", "password": "pw"}
        # 完整可执行文件路径
        config_store.save_settings({"mysql_bin": self.exe})
        args = backup_engine._cli_args(conn, "mysqldump.exe")
        self.assertEqual(args[0], os.path.abspath(self.exe))
        self.assertIn("--host=10.0.0.1", args)
        self.assertIn("--port=3307", args)
        self.assertIn("--user=app", args)
        self.assertIn("--password=pw", args)
        # 配置为目录
        config_store.save_settings({"mysql_bin": self.bin_dir})
        args = backup_engine._cli_args(conn, "mysqldump.exe")
        self.assertEqual(args[0], os.path.abspath(self.exe))
        config_store.save_settings({"mysql_bin": ""})  # 复位


class EnvProbeTest(unittest.TestCase):
    """客户端探测与版本解析(纯函数,不执行真实命令)。"""

    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.join(_TMP, "fakebin")
        os.makedirs(cls.dir, exist_ok=True)
        # 按平台命名,避免非 Windows 上文件名不匹配导致回退 PATH 命中真实工具
        cls.exe_name = "mysql.exe" if env_probe.IS_WIN else "mysql"
        cls.full = os.path.join(cls.dir, cls.exe_name)
        with open(cls.full, "wb") as f:
            f.write(b"dummy")

    def test_find_tool_dir(self):
        self.assertEqual(env_probe.find_tool("mysql", self.dir), os.path.abspath(self.full))

    def test_find_tool_full_file(self):
        self.assertEqual(env_probe.find_tool("mysql", self.full), os.path.abspath(self.full))

    def test_parse_version_formats(self):
        v = env_probe.parse_version("mysqldump  Ver 8.0.42 for Win64")
        self.assertEqual((v["major"], v["minor"], v["patch"]), (8, 0, 42))
        v = env_probe.parse_version("10.5.25-MariaDB")
        self.assertEqual((v["major"], v["minor"], v["patch"]), (10, 5, 25))
        self.assertIsNone(env_probe.parse_version("not a version"))
        self.assertIsNone(env_probe.parse_version(None))


class ScheduleStoreTest(unittest.TestCase):
    """定时任务:到点匹配、周期描述、保存校验与 CRUD 闭环。"""

    @staticmethod
    def _t(**kw):
        t = schedule_store._default_task()
        t.update(kw)
        return t

    def test_is_due_daily(self):
        at = time.struct_time((2026, 8, 28, 2, 0, 0, 4, 240, -1))
        self.assertTrue(schedule_store.is_due(self._t(freq="daily", time="02:00", enabled=True), at))
        self.assertFalse(schedule_store.is_due(self._t(freq="daily", time="02:30", enabled=True), at))

    def test_is_due_weekly_mapping(self):
        # 2026-08-28 是周五(tm_wday=4 ↔ 任务 weekday=5)
        fri = time.struct_time((2026, 8, 28, 9, 0, 0, 4, 240, -1))
        sat = time.struct_time((2026, 8, 29, 9, 0, 0, 5, 241, -1))
        self.assertTrue(schedule_store.is_due(
            self._t(freq="weekly", time="09:00", weekday=5, enabled=True), fri))
        self.assertFalse(schedule_store.is_due(
            self._t(freq="weekly", time="09:00", weekday=5, enabled=True), sat))

    def test_is_due_monthly_and_once(self):
        d28 = time.struct_time((2026, 8, 28, 12, 0, 0, 4, 240, -1))
        d27 = time.struct_time((2026, 8, 27, 12, 0, 0, 3, 239, -1))
        self.assertTrue(schedule_store.is_due(
            self._t(freq="monthly", time="12:00", day_of_month=28, enabled=True), d28))
        self.assertFalse(schedule_store.is_due(
            self._t(freq="monthly", time="12:00", day_of_month=28, enabled=True), d27))
        t1 = time.struct_time((2026, 8, 30, 3, 30, 0, 6, 242, -1))
        t2 = time.struct_time((2026, 8, 30, 3, 31, 0, 6, 242, -1))
        self.assertTrue(schedule_store.is_due(
            self._t(freq="once", at_once="2026-08-30T03:30", enabled=True), t1))
        self.assertFalse(schedule_store.is_due(
            self._t(freq="once", at_once="2026-08-30T03:30", enabled=True), t2))

    def test_is_due_enabled_switch(self):
        at = time.struct_time((2026, 8, 28, 2, 0, 0, 4, 240, -1))
        t = self._t(freq="daily", time="02:00", enabled=False)
        self.assertFalse(schedule_store.is_due(t, at))                       # 默认检查开关
        self.assertTrue(schedule_store.is_due(t, at, check_enabled=False))   # 单测模式

    def test_describe(self):
        self.assertEqual(schedule_store.describe(self._t(freq="hourly")), "每小时")
        self.assertEqual(schedule_store.describe(self._t(freq="hourly", interval_hours=5)), "每 5 小时")
        self.assertEqual(schedule_store.describe(self._t(freq="daily", time="02:00")), "每天 02:00")
        self.assertEqual(schedule_store.describe(self._t(freq="weekly", time="09:00", weekday=0)), "每周日 09:00")
        self.assertEqual(schedule_store.describe(self._t(freq="monthly", day_of_month=15, time="02:30")), "每月 15 日 02:30")
        self.assertEqual(schedule_store.describe(self._t(freq="once", at_once="2026-08-30T03:30")), "一次性: 2026-08-30 03:30")

    def test_save_task_validation(self):
        with self.assertRaises(ValueError):
            schedule_store.save_task(self._t(name="x", freq="weird"))
        with self.assertRaises(ValueError):
            schedule_store.save_task(self._t(freq="daily", time="25:99"))
        with self.assertRaises(ValueError):
            schedule_store.save_task(self._t(freq="once", at_once=""))
        with self.assertRaises(ValueError):
            schedule_store.save_task(self._t(name="", freq="daily", time="02:00"))

    def test_save_task_clamps_and_crud(self):
        cid = local_store.save_connection(
            {"name": "c1", "host": "127.0.0.1", "port": 3306, "user": "root", "password": ""})
        tid = schedule_store.save_task(
            self._t(name="任务A", freq="daily", time="02:00", keep=500, conn_id=""))
        t = schedule_store.get_task(tid)
        self.assertEqual(t["keep"], 99)                  # keep 钳制到上限
        self.assertEqual(t["conn_id"], cid)              # 未指定连接 → 自动取第一个
        self.assertEqual(len(t["id"]), 12)
        self.assertEqual(t["name"], "任务A")
        # 启停与执行状态回写
        self.assertTrue(schedule_store.set_enabled(tid, False))
        self.assertFalse(schedule_store.get_task(tid)["enabled"])
        schedule_store.update_run_status(tid, "success")
        t = schedule_store.get_task(tid)
        self.assertEqual(t["last_result"], "success")
        self.assertTrue(t["last_run"])
        # 删除
        self.assertTrue(schedule_store.delete_task(tid))
        self.assertIsNone(schedule_store.get_task(tid))
        self.assertFalse(schedule_store.delete_task(tid))   # 已不存在
        local_store.delete_connection(cid)

    def test_migrate_legacy(self):
        # 旧 schedule_* 配置迁移为 builtin 任务并关闭旧开关
        config_store.save_settings({"schedule_enabled": True, "schedule_cron": "30 3 * * *"})
        tasks = schedule_store._migrate_legacy()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["freq"], "daily")
        self.assertEqual(tasks[0]["time"], "03:30")
        self.assertFalse(config_store.get_settings().get("schedule_enabled"))


class LocalStoreTest(unittest.TestCase):
    """本地 SQLite 存储层 CRUD + 清理语义。"""

    def test_meta_json(self):
        local_store.set_meta_json("obj", {"a": 1, "中文": "值"})
        self.assertEqual(local_store.get_meta_json("obj"), {"a": 1, "中文": "值"})
        self.assertIsNone(local_store.get_meta_json("nope"))

    def test_connection_crud(self):
        cid = local_store.save_connection(
            {"name": "t1", "host": "127.0.0.1", "port": 3306, "user": "root", "password": "pw"})
        c = local_store.get_connection(cid)
        self.assertEqual(c["name"], "t1")
        self.assertEqual(c["password"], "pw")
        local_store.save_connection({"note": "改"}, cid)
        self.assertEqual(local_store.get_connection(cid)["note"], "改")
        local_store.set_active_conn(cid)
        self.assertEqual(local_store.get_active_conn()["id"], cid)
        self.assertTrue(local_store.get_connection(cid)["active"])
        local_store.delete_connection(cid)
        self.assertIsNone(local_store.get_connection(cid))

    def test_settings_roundtrip(self):
        s = local_store.save_settings({"alert_max_conn": 150, "backup_dir": os.path.join(_TMP, "bk")})
        # local_store 是字符串层(JSON 序列化);类型化解析在 config_store 层
        self.assertEqual(local_store.get_settings()["alert_max_conn"], "150")
        self.assertEqual(config_store.get_settings()["alert_max_conn"], 150)
        self.assertIn("backup_dir", s)

    def test_clear_lite_data_keeps_bootstrap(self):
        local_store.set_meta("run_mode", "full")
        local_store.set_meta("sys_db_name", "_mc_test")
        local_store.set_meta_json("bootstrap", {"host": "127.0.0.1"})
        local_store.set_meta_json("schedules", [{"id": "x"}])
        local_store.set_meta_json("backup_history", [{"id": "h"}])
        local_store.set_meta("admin_password_hash", "h")
        local_store.save_connection({"name": "c", "host": "h", "port": 1, "user": "u", "password": ""})
        local_store.save_settings({"run_mode": "full"})
        local_store.clear_lite_data()
        self.assertEqual(local_store.list_connections(), [])
        self.assertEqual(local_store.get_settings(), {})
        self.assertIsNone(local_store.get_meta("schedules"))
        self.assertIsNone(local_store.get_meta("backup_history"))
        self.assertIsNone(local_store.get_meta("admin_password_hash"))
        # 最小 bootstrap 必须保留(删了会死锁,见 HANDOFF 血泪陷阱 8)
        self.assertEqual(local_store.get_meta("run_mode"), "full")
        self.assertEqual(local_store.get_meta("sys_db_name"), "_mc_test")
        self.assertEqual(local_store.get_meta_json("bootstrap"), {"host": "127.0.0.1"})

    def test_reset_all_full_wipe(self):
        local_store.set_meta("run_mode", "lite")
        local_store.save_connection({"name": "c", "host": "h", "port": 1, "user": "u", "password": ""})
        local_store.reset_all()
        self.assertEqual(local_store.list_connections(), [])
        self.assertIsNone(local_store.get_meta("run_mode"))


class ConfigStoreTest(unittest.TestCase):
    """加密/哈希/轻量设置链路(全离线)。"""

    def test_fernet_roundtrip(self):
        enc = config_store.encrypt("secret-中文")
        self.assertNotEqual(enc, "secret-中文")
        self.assertEqual(config_store.decrypt(enc), "secret-中文")
        self.assertEqual(config_store.decrypt("garbage"), "")

    def test_password_hash(self):
        h = config_store._hash_password("pw123")
        self.assertTrue(config_store._verify_password("pw123", h))
        self.assertFalse(config_store._verify_password("wrong", h))
        self.assertFalse(config_store._verify_password("pw123", ""))

    def test_lite_admin_and_settings(self):
        self.assertFalse(config_store.is_password_set())
        config_store.set_admin("admin", "pw123")
        self.assertTrue(config_store.is_password_set())
        self.assertEqual(config_store.get_admin_username(), "admin")
        self.assertTrue(config_store.verify_admin("pw123"))
        self.assertFalse(config_store.verify_admin("bad"))
        config_store.save_settings({"admin_password_hash": ""})  # 复位(避免影响其他用例)
        self.assertFalse(config_store.is_password_set())

    def test_settings_defaults_backfill(self):
        s = config_store.get_settings()
        for k in ("mysql_bin", "backup_dir", "run_mode", "update_check_interval",
                  "alert_max_conn", "alert_max_slow", "alert_max_running"):
            self.assertIn(k, s)
        config_store.save_settings({"alert_max_conn": 150})
        self.assertEqual(config_store.get_settings()["alert_max_conn"], 150)
        self.assertEqual(config_store.get_settings()["run_mode"], "lite")


class MysqlClientMockTest(unittest.TestCase):
    """mock pymysql 验证封装逻辑(无需真实 MySQL)。"""

    def _stub_cursor(self, version_row=("8.0.36",), rows=None, cols=None, desc=None):
        cur = mock.Mock()
        cur.execute = mock.Mock()
        if rows is not None:
            cur.fetchall.return_value = rows
        else:
            cur.fetchone.return_value = version_row
        cur.description = desc
        cur.__enter__ = mock.Mock(return_value=cur)
        cur.__exit__ = mock.Mock(return_value=False)
        conn = mock.Mock()
        conn.cursor = mock.Mock(return_value=cur)
        conn.close = mock.Mock()
        return conn, cur

    def test_test_success_path(self):
        conn, cur = self._stub_cursor()
        with mock.patch("mysql_client.pymysql.connect", return_value=conn):
            r = mysql_client.test({"host": "127.0.0.1", "port": 3306,
                                   "user": "root", "password": ""})
        self.assertEqual(r, {"ok": True, "version": "8.0.36"})
        conn.close.assert_called_once()

    def test_db_exists(self):
        conn, cur = self._stub_cursor(version_row=("mydb",))
        with mock.patch("mysql_client.pymysql.connect", return_value=conn):
            self.assertTrue(mysql_client.db_exists({"host": "h"}, "mydb"))
        cur.execute.assert_called_once_with(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
            ("mydb",))

    def test_real_connect_failure_maps_to_db_error(self):
        # 端口 1 必然拒绝:验证 connect() 把 pymysql 错误归一为可读 DbError
        with self.assertRaises(mysql_client.DbError) as ctx:
            mysql_client.connect({"host": "127.0.0.1", "port": 1}, timeout=3)
        self.assertIn("连接失败", str(ctx.exception))

    def test_q_and_q1(self):
        conn, cur = self._stub_cursor(
            rows=[("r1", 1), ("r2", 2)], cols=["name", "n"], desc=[("name",), ("n",)])
        cols, rows = mysql_client._q(conn, "SELECT name, n FROM t")
        self.assertEqual(cols, ["name", "n"])
        self.assertEqual(rows, [("r1", 1), ("r2", 2)])
        cur.execute.assert_called_once_with("SELECT name, n FROM t", None)
        conn2, cur2 = self._stub_cursor(version_row=("v",))
        self.assertEqual(mysql_client._q1(conn2, "SELECT 1"), ("v",))


if __name__ == "__main__":
    unittest.main(verbosity=2)