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
import ssh_tunnel               # noqa: E402
import schedule_store           # noqa: E402
import mysql_client             # noqa: E402
import system_db                # noqa: E402

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

    def test_resolve_whitelist_zip(self):
        # 多库备份产物 .zip 也在白名单内
        zp = os.path.join(self.bk, "bundle.zip")
        with open(zp, "wb") as f:
            f.write(b"PK\x05\x06")  # 空 zip 尾,白名单只看后缀与路径
        self.assertEqual(backup_engine.resolve_backup_file(zp), os.path.realpath(zp))

    def test_list_backups_maps_full_mode_row(self):
        # 全量模式行(系统库列名)→ 前端 shape 的映射
        row = {"id": "abc", "type": "backup", "created_at": "2026-08-29 13:00:00",
               "target": "127.0.0.1", "object": "db1,db2", "file_path": self.ok,
               "file_size": 123, "duration_ms": 1500, "result": "success", "error_msg": ""}
        with mock.patch.object(backup_engine, "_read_history", return_value=[row]):
            items = backup_engine.list_backups()
        it = items[0]
        self.assertEqual(it["time"], "2026-08-29 13:00:00")
        self.assertEqual(it["dbs"], ["db1,db2"])
        self.assertEqual(it["path"], self.ok)
        self.assertEqual(it["size"], 123)
        self.assertEqual(it["elapsed"], 1.5)
        self.assertTrue(it["exists"])

    def test_save_history_lite_appends(self):
        # 轻量模式 _save_history 追加且可读回(回归:2026-08-29 历史 不显示 bug)
        rec = {"id": "t1", "type": "backup", "time": "t", "path": "/x.sql.gz",
               "size": 1, "elapsed": 0.1, "result": "success"}
        with mock.patch.object(backup_engine, "_is_full_mode", return_value=False), \
             mock.patch.object(backup_engine, "_read_history", return_value=[]), \
             mock.patch.object(backup_engine, "_write_history") as w:
            backup_engine._save_history(rec)
            w.assert_called_once_with([rec])

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

    def test_db_major_parse(self):
        # 连接 db_version → 版本族:''/auto→0(自动), 5.x→5, 8.x→8;空白/大小写容错
        for v in ("", "auto", "AUTO", "  "):
            self.assertEqual(backup_engine._db_major({"db_version": v}), 0)
        self.assertEqual(backup_engine._db_major({}), 0)          # 未声明字段
        for v in ("5", "5.5", "5.6", "5.7", " 5.7 "):
            self.assertEqual(backup_engine._db_major({"db_version": v}), 5)
        for v in ("8", "8.0", "8.0.42", "8.4", "8.x"):
            self.assertEqual(backup_engine._db_major({"db_version": v}), 8)

    def test_cli_args_forwards_want_major(self):
        # 声明版本族的连接 → find_tool_versioned 收到对应 want_major;自动 → 0
        conns = [
            ({"host": "h", "port": 3306, "user": "u", "password": "p",
              "db_version": "5.7"}, 5),
            ({"host": "h", "port": 3306, "user": "u", "password": "p",
              "db_version": "8.x"}, 8),
            ({"host": "h", "port": 3306, "user": "u", "password": "p"}, 0),
        ]
        fake = r"D:\fake\mysqldump.exe" if env_probe.IS_WIN else "/fake/mysqldump"
        config_store.save_settings({"mysql_bin": ""})
        for conn, want in conns:
            with self.subTest(want=want):
                with mock.patch.object(env_probe, "find_tool_versioned",
                                       return_value=fake) as m:
                    backup_engine._cli_args(conn, "mysqldump.exe")
                self.assertEqual(m.call_args.kwargs["want_major"], want)
                self.assertEqual(m.call_args.args[0], "mysqldump")


class BackupOptsTest(unittest.TestCase):
    """备份/还原参数管理:内置参数、settings 默认、当次覆盖、黑名单、shlex 边界。"""

    def test_builtin_default(self):
        # 未配置 = 内置参数
        self.assertEqual(backup_engine.resolve_backup_opts(None),
                         backup_engine.BUILTIN_BACKUP_OPTS)
        self.assertEqual(backup_engine.resolve_restore_opts(None),
                         backup_engine.BUILTIN_RESTORE_OPTS)

    def test_settings_default_used_when_none(self):
        config_store.save_settings({"backup_opts": "--ignore-table=db.t1 --skip-lock-tables"})
        opts = backup_engine.resolve_backup_opts(None)
        self.assertIn("--ignore-table=db.t1", opts)
        self.assertIn("--skip-lock-tables", opts)
        self.assertIn("--single-transaction", opts)  # 内置仍在
        config_store.save_settings({"backup_opts": ""})  # 复位

    def test_explicit_list_replaces_builtin(self):
        # 当次传入 = 完整清单整体替换(高级用户可删内置参数)
        opts = backup_engine.resolve_backup_opts(["--single-transaction"])
        self.assertEqual(opts, ["--single-transaction"])
        # 空列表 = 完全无参数执行
        self.assertEqual(backup_engine.resolve_backup_opts([]), [])

    def test_forbidden_opt_rejected(self):
        for bad in ("--password=xxx", "--host=1.2.3.4", "--result-file=/tmp/x", "-p pw"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    backup_engine.resolve_backup_opts(bad.split())
        with self.assertRaises(ValueError):
            backup_engine.resolve_restore_opts(["--one-database"])

    def test_shlex_quoted_token(self):
        # 含空格参数须引号包裹:--where="created > '2026-01-01'" 之类
        config_store.save_settings({"backup_opts": '--where "id > 100"'})
        opts = backup_engine.resolve_backup_opts(None)
        self.assertIn("--where", opts)
        self.assertIn("id > 100", opts)
        config_store.save_settings({"backup_opts": ""})

    def test_restore_opts_pass_through(self):
        # 当次传入 = 完整清单整体替换
        opts = backup_engine.resolve_restore_opts(["--force", "--init-command=SET x=1"])
        self.assertEqual(opts, ["--force", "--init-command=SET x=1"])


class SshTunnelTest(unittest.TestCase):
    """SSH 隧道:命令构造、端口选择、生命周期重写(纯逻辑,不真实起 ssh)。"""

    def test_not_ssh_cfg_passthrough(self):
        cfg = {"host": "db.internal", "port": 3306}
        self.assertFalse(ssh_tunnel.is_ssh_cfg(cfg))
        self.assertEqual(ssh_tunnel.build_tunnel_cmd(cfg, 13306), [])
        # _maybe_tunnel 透传原配置并停止无隧道
        with mock.patch.object(ssh_tunnel, "start_tunnel",
                               return_value=(None, dict(cfg))), \
             mock.patch.object(ssh_tunnel, "stop_tunnel") as stop:
            with backup_engine._maybe_tunnel(cfg) as eff:
                self.assertEqual(eff, cfg)
            stop.assert_called_once_with(None)

    def test_build_cmd_default_bind(self):
        # 未给 bind_* 时,默认转发到连接自身的 host:port
        key = os.path.join(_TMP, "id_ed25519_test")
        with open(key, "wb") as f:
            f.write(b"key")
        try:
            cfg = {"ssh_enabled": True, "ssh_host": "jump.example", "ssh_port": 2222,
                   "ssh_user": "u", "ssh_key": key, "host": "db.host", "port": 3307}
            cmd = ssh_tunnel.build_tunnel_cmd(cfg, 13306)
        finally:
            os.remove(key)
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("-L", cmd)
        self.assertIn("127.0.0.1:13306:db.host:3307", cmd)
        self.assertIn("-i", cmd)
        self.assertIn(key, cmd)
        self.assertTrue(cmd[-1].endswith("u@jump.example"))

    def test_build_cmd_bind_override_and_raise(self):
        cfg = {"ssh_enabled": True, "ssh_host": "j.example", "ssh_user": "u",
               "ssh_bind_host": "127.0.0.1", "ssh_bind_port": 3306}
        cmd = ssh_tunnel.build_tunnel_cmd(cfg, 15000)
        self.assertIn("127.0.0.1:15000:127.0.0.1:3306", cmd)
        self.assertNotIn("-i", cmd)  # 无 key 时不加 -i
        # 配置了 key 但文件不存在 → 明确报错
        cfg2 = dict(cfg, ssh_key=os.path.join(_TMP, "nope_key"))
        with self.assertRaises(ValueError):
            ssh_tunnel.build_tunnel_cmd(cfg2, 15000)
        # 空白 ssh_host 视为未启用,返回空命令
        self.assertEqual(ssh_tunnel.build_tunnel_cmd(dict(cfg, ssh_host=" "), 15000), [])

    def test_pick_free_port(self):
        p = ssh_tunnel.pick_free_port()
        self.assertIsInstance(p, int)
        self.assertGreater(p, 0)

    def test_maybe_tunnel_rewrites_endpoint(self):
        # 启用隧道时,把 host/port 改写为本地转发端点,并起/停配套
        cfg = {"ssh_enabled": True, "ssh_host": "j", "host": "db", "port": 3306}
        info = {"proc": object(), "local_port": 13306}
        eff = dict(cfg, host="127.0.0.1", port=13306)
        with mock.patch.object(ssh_tunnel, "start_tunnel", return_value=(info, eff)) as st, \
             mock.patch.object(ssh_tunnel, "stop_tunnel") as stop:
            with backup_engine._maybe_tunnel(cfg) as got:
                self.assertEqual(got["host"], "127.0.0.1")
                self.assertEqual(got["port"], 13306)
            st.assert_called_once_with(cfg)
            stop.assert_called_once_with(info)

    def test_ssh_run_and_probe_remote_env(self):
        """ssh_run 通用执行 + 远程服务器类型探测(linux/windows-GitBash/windows-cmd/unknown)。"""
        cfg = {"ssh_host": "j", "ssh_user": "u"}
        fake = mock.Mock()
        fake.stdout = b"Linux\n"
        fake.returncode = 0
        with mock.patch.object(ssh_tunnel.subprocess, "run", return_value=fake) as run:
            self.assertEqual(ssh_tunnel.ssh_run(cfg, "uname -s"), "Linux")
        args = run.call_args[0][0]
        self.assertEqual(args[0], "ssh")
        self.assertIn("u@j", args)
        self.assertIn("uname -s", args)
        # 失败/无 ssh_host → 空串(不抛)
        with mock.patch.object(ssh_tunnel.subprocess, "run", side_effect=OSError):
            self.assertEqual(ssh_tunnel.ssh_run(cfg, "x"), "")
        self.assertEqual(ssh_tunnel.ssh_run({}, "uname -s"), "")

        with mock.patch.object(ssh_tunnel, "ssh_run", return_value="Linux"):
            self.assertEqual(ssh_tunnel.probe_remote_env(cfg)["os"], "linux")
        with mock.patch.object(ssh_tunnel, "ssh_run", return_value="MINGW64_NT-10.0-22631"):
            env = ssh_tunnel.probe_remote_env(cfg)
            self.assertEqual(env["os"], "windows")
            self.assertTrue(env["git_bash"])
        with mock.patch.object(ssh_tunnel, "ssh_run",
                               side_effect=["", "Microsoft Windows [版本 10.0.19045.1234]"]):
            env = ssh_tunnel.probe_remote_env(cfg)
            self.assertEqual(env["os"], "windows")
            self.assertFalse(env["git_bash"])
        with mock.patch.object(ssh_tunnel, "ssh_run", return_value=""):
            self.assertEqual(ssh_tunnel.probe_remote_env(cfg)["os"], "unknown")


class LocalStoreSshFieldsTest(unittest.TestCase):
    """连接 SSH 字段持久化与读写闭环。"""

    def test_save_get_ssh_fields(self):
        cid = local_store.save_connection({
            "name": "t", "host": "1.2.3.4", "port": 3306, "user": "root",
            "password": "pw", "ssh_enabled": True, "ssh_host": "jump",
            "ssh_port": 22, "ssh_user": "u", "ssh_key": "/k/id", "ssh_bind_host": "127.0.0.1",
            "remote_os": "windows", "db_version": "8.x",
        })
        try:
            row = local_store.get_connection(cid)
            self.assertTrue(row["ssh_enabled"])
            self.assertEqual(row["ssh_host"], "jump")
            self.assertEqual(row["ssh_port"], 22)
            self.assertEqual(row["ssh_key"], "/k/id")
            self.assertEqual(row["ssh_bind_host"], "127.0.0.1")
            self.assertEqual(row["remote_os"], "windows")
            self.assertEqual(row["db_version"], "8.x")
            # 更新开关
            local_store.save_connection({"ssh_enabled": False}, cid)
            self.assertFalse(local_store.get_connection(cid)["ssh_enabled"])
            # 更新服务器类型
            local_store.save_connection({"remote_os": "linux"}, cid)
            self.assertEqual(local_store.get_connection(cid)["remote_os"], "linux")
            # 更新数据库版本
            local_store.save_connection({"db_version": "5.7"}, cid)
            self.assertEqual(local_store.get_connection(cid)["db_version"], "5.7")
        finally:
            local_store.delete_connection(cid)


class RemoteStorageTest(unittest.TestCase):
    """远程备份存储判定与命令构造(纯逻辑)。"""

    def test_storage_of_local_hosts(self):
        for h in ("localhost", "127.0.0.1", "::1", "LOCALHOST"):
            self.assertEqual(backup_engine.storage_of({"host": h, "port": 3306}), "local")
        self.assertEqual(backup_engine.storage_of({"host": "db.example.com"}), "remote")

    def test_remote_dir_default_and_override(self):
        self.assertEqual(backup_engine.REMOTE_DEFAULT_DIR, "~/mysql-console-backups")
        self.assertEqual(backup_engine._remote_dir({}), backup_engine.REMOTE_DEFAULT_DIR)
        self.assertEqual(backup_engine._remote_dir({"remote_backup_dir": "/data/bak"}),
                         "/data/bak")

    def test_ssh_config_required_for_remote(self):
        with self.assertRaises(RuntimeError):
            backup_engine._ssh_config({"host": "db.example.com"})
        self.assertTrue(backup_engine._ssh_config({"host": "db", "ssh_host": "j"}))

    def test_remote_commands_quoting(self):
        self.assertEqual(backup_engine._remote_copy_cmd("/d/a.sql").split()[-1], "/d/a.sql")
        gz = backup_engine._remote_copy_cmd("/d/a b.sql.gz")
        self.assertIn("gzip -dc", gz)
        self.assertIn("'/d/a b.sql.gz'", gz)
        self.assertTrue(backup_engine._remote_size_cmd("/d/a.sql").startswith("wc -c < "))
        self.assertTrue(backup_engine._remote_size_cmd("/d/a.sql.gz").startswith("gzip -dc "))

    def test_backup_dispatches_to_remote(self):
        org = {"host": "db.example.com", "port": 3306, "ssh_host": "j"}
        with mock.patch.object(backup_engine, "_remote_backup", return_value={"result": "success"}) as m:
            r = backup_engine._run_backup(org, dict(org), [], None)
        m.assert_called_once()
        self.assertEqual(r["result"], "success")

    def test_restore_dispatches_to_remote(self):
        org = {"host": "db.example.com", "ssh_host": "j"}
        with mock.patch.object(backup_engine, "_remote_restore", return_value={"result": "success"}) as m:
            r = backup_engine._run_restore(org, dict(org), None, "/d/a.sql", None, None, storage="remote")
        m.assert_called_once()
        self.assertEqual(r["result"], "success")

    def test_dump_to_remote_uses_size_cmd(self):
        """修复:取远程文件大小必须传“命令”而非路径(否则 ssh 把路径当命令执行恒判失败)。"""
        org = {"host": "db.example.com", "port": 3306, "user": "root",
               "password": "", "ssh_host": "j", "ssh_user": "u"}
        remote_path = "/d/mydb_20260831.sql.gz"

        class _FakeStream:
            """伪 Popen:stdin/stderr 读空、wait 返回 0。"""
            def __init__(self):
                self.data = b""
                self.stdin = self
                self.stdout = self
            def read(self, n=-1):
                return b""
            def readline(self, n=-1):
                return b""
            def write(self, chunk):
                self.data += chunk
                return len(chunk)
            def close(self):
                pass
            def wait(self):
                return 0

        dump_proc, ssh_proc = _FakeStream(), _FakeStream()

        def _popen(args, **kw):
            return ssh_proc if args[0] == "ssh" else dump_proc

        with mock.patch.object(backup_engine, "_cli_args",
                               return_value=["mysqldump.exe", "--host=db"]), \
             mock.patch.object(backup_engine.subprocess, "Popen", side_effect=_popen), \
             mock.patch.object(backup_engine.ssh_tunnel, "remote_file_size", return_value=12345) as rfs, \
             mock.patch.object(backup_engine, "_version_warning", return_value=""):
            ssh_rc, dump_rc, size, errs = backup_engine._dump_to_remote(
                org, dict(org), "mydb", remote_path, False, [], [], None)
        self.assertEqual(ssh_rc, 0)
        self.assertEqual(dump_rc, 0)
        self.assertEqual(size, 12345)
        # 关键:remote_file_size 收到的第二参数是“大小命令”而非路径
        call_arg = rfs.call_args[0][1]
        self.assertTrue(call_arg.startswith("gzip -dc "))
        self.assertIn(remote_path, call_arg)
        self.assertNotEqual(call_arg, remote_path)

    def test_remote_backup_windows_gitbash_blocked(self):
        """remote_os=windows 且未检测到 Git Bash → 明确报错,不给管道报错。"""
        org = {"host": "db.example.com", "ssh_host": "j", "remote_os": "windows"}
        with mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                               return_value={"os": "windows", "git_bash": False, "detail": "ver"}):
            with self.assertRaises(RuntimeError) as ctx:
                backup_engine._remote_backup(org, dict(org), [], True, None, None)
        self.assertIn("Git Bash", str(ctx.exception))

    def test_remote_backup_windows_gitbash_passes(self):
        """remote_os=windows 且 Git Bash 就绪 → 正常走远程备份链路。"""
        org = {"host": "db.example.com", "ssh_host": "j", "remote_os": "windows"}
        with mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                               return_value={"os": "windows", "git_bash": True,
                                             "detail": "MINGW64_NT-10.0"}), \
             mock.patch.object(backup_engine, "_prefetch_tables", return_value=[]), \
             mock.patch.object(backup_engine, "_version_warning", return_value=""), \
             mock.patch.object(backup_engine, "_dump_to_remote", return_value=(0, 0, 100, [])) as d, \
             mock.patch.object(backup_engine, "_save_history"), \
             mock.patch.object(backup_engine, "_log"):
            rec = backup_engine._remote_backup(org, dict(org), [], True, None, None)
        self.assertEqual(rec["result"], "success")
        d.assert_called_once()

    def test_remote_list_cmd(self):
        cmd = backup_engine._remote_list_cmd("~/mysql-console-backups")
        self.assertIn("find", cmd)
        self.assertIn("__OK__", cmd)
        self.assertIn("__NO_DIR__", cmd)
        self.assertIn("-printf", cmd)
        self.assertIn("'*.sql'", cmd)
        self.assertIn("'*.sql.gz'", cmd)
        self.assertIn("maxdepth 1", cmd)
        # 路径含空格被 shlex 引号包裹
        self.assertIn("'/d/my baks'", backup_engine._remote_list_cmd("/d/my baks"))

    def test_parse_remote_ls(self):
        text = ("db1_20260831.sql.gz\t123456\t2026-08-31 10:00\n"
                "db2_20260830.sql\t456\t2026-08-30 09:00\n"
                "junk no tab line\n"
                "bad\tnotnum\tx\n")
        files = backup_engine._parse_remote_ls(text, "/remote/bak")
        self.assertEqual(len(files), 2)                    # 非法行忽略
        self.assertEqual(files[0]["name"], "db2_20260830.sql")   # 按名倒序
        self.assertEqual(files[0]["size"], 456)
        self.assertEqual(files[0]["path"], "/remote/bak/db2_20260830.sql")
        self.assertFalse(files[0]["compressed"])
        self.assertTrue(files[1]["compressed"])            # db1_*.sql.gz
        self.assertEqual(files[1]["mtime"], "2026-08-31 10:00")

    def test_list_remote_files_ok(self):
        org = {"host": "db.example.com", "ssh_host": "j", "remote_backup_dir": "/bak"}
        with mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                               return_value={"os": "linux", "git_bash": False}), \
             mock.patch.object(backup_engine.ssh_tunnel, "ssh_run",
                               return_value="__OK__\ndb1_20260831.sql.gz\t100\t2026-08-31 10:00"):
            rdir, files = backup_engine.list_remote_files(org)
        self.assertEqual(rdir, "/bak")
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["name"], "db1_20260831.sql.gz")

    def test_list_remote_files_no_dir(self):
        org = {"host": "db.example.com", "ssh_host": "j"}
        with mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                               return_value={"os": "linux", "git_bash": False}), \
             mock.patch.object(backup_engine.ssh_tunnel, "ssh_run", return_value="__NO_DIR__"):
            with self.assertRaises(RuntimeError) as ctx:
                backup_engine.list_remote_files(org)
        self.assertIn("目录不存在", str(ctx.exception))

    def test_list_remote_files_windows_no_gitbash(self):
        org = {"host": "db.example.com", "ssh_host": "j"}
        with mock.patch.object(backup_engine.ssh_tunnel, "probe_remote_env",
                               return_value={"os": "windows", "git_bash": False}):
            with self.assertRaises(RuntimeError) as ctx:
                backup_engine.list_remote_files(org)
        self.assertIn("Git Bash", str(ctx.exception))


class SystemDbConnColsTest(unittest.TestCase):
    """旧系统库 mc_connection 列迁移兜底(修复全量模式编辑连接报 Unknown column 1054)。"""

    def tearDown(self):
        system_db._conn_cols_ready = False

    def test_migrate_connection_columns_backfills(self):
        # 模拟旧库缺列:information_schema 只返回基础旧列
        cur = mock.Mock()
        cur.fetchall.return_value = [("id",), ("name",), ("host",), ("port",), ("username",),
                                     ("password",), ("note",), ("is_active",), ("created_at",),
                                     ("updated_at",)]
        system_db._migrate_connection_columns(cur)
        alter_sql = " ".join(c[0][0] for c in cur.execute.call_args_list
                             if c[0][0].startswith("ALTER TABLE mc_connection"))
        for col in ("ssh_enabled", "ssh_host", "ssh_port", "ssh_user", "ssh_key",
                    "ssh_bind_host", "ssh_bind_port", "backup_dir",
                    "remote_backup_dir", "remote_os"):
            self.assertIn("ADD COLUMN %s " % col, alter_sql, "缺列未补: " + col)

    def test_ensure_conn_cols_backfills_and_idempotent(self):
        class _Cur:
            def __init__(self, rows):
                self.rows = rows
                self.executed = []
            def execute(self, sql, *a):
                self.executed.append(sql)
            def fetchall(self):
                return self.rows
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class _Conn:
            def __init__(self, cur):
                self.cur = cur
                self.commits = 0
            def cursor(self):
                return self.cur
            def commit(self):
                self.commits += 1

        old = [("id",), ("name",), ("host",), ("port",), ("username",), ("password",),
               ("note",), ("is_active",), ("created_at",), ("updated_at",)]   # 旧库:缺 ssh_*/backup_dir/remote_os
        cur = _Cur(old)
        conn = _Conn(cur)
        system_db._ensure_conn_cols(conn)
        alter_sql = " ".join(s for s in cur.executed if s.startswith("ALTER"))
        for col in ("ssh_enabled", "ssh_key", "remote_backup_dir", "remote_os"):
            self.assertIn("ADD COLUMN %s " % col, alter_sql, "缺列未补: " + col)
        self.assertTrue(system_db._conn_cols_ready)
        # 幂等:二次调用直接跳过,不再查/改
        cur.executed.clear()
        system_db._ensure_conn_cols(conn)
        self.assertEqual(cur.executed, [])

    def test_ensure_conn_cols_retries_on_failure(self):
        class _Cur:
            def __init__(self, rows, fail_alter):
                self.rows = rows
                self.fail_alter = fail_alter
            def execute(self, sql, *a):
                if sql.startswith("ALTER") and self.fail_alter:
                    raise Exception("ALTER failed")
            def fetchall(self):
                return self.rows
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class _Conn:
            def __init__(self, cur):
                self.cur = cur
            def cursor(self):
                return self.cur
            def commit(self):
                pass

        # ALTER 失败(如旧版 MySQL 拒绝 TEXT DEFAULT)→ 不置 ready,下次重试
        conn = _Conn(_Cur([("id",)], fail_alter=True))   # 缺全部列
        with mock.patch("sys.stderr") as serr:
            system_db._ensure_conn_cols(conn)
        self.assertFalse(system_db._conn_cols_ready)   # 未全成功 → 允许重试
        self.assertTrue(serr.write.called)             # 失败原因已打印,便于定位


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

    def setUp(self):
        # 清理进程内惰性缓存,避免多用例共享 _bundled_manifest/_bundled_sha_cache 相互污染
        env_probe._bundled_manifest = None
        env_probe._bundled_sha_cache.clear()

    def test_find_tool_dir(self):
        self.assertEqual(env_probe.find_tool("mysql", self.dir), os.path.abspath(self.full))

    def test_find_tool_full_file(self):
        self.assertEqual(env_probe.find_tool("mysql", self.full), os.path.abspath(self.full))

    def test_find_bundled_tool_version_sort(self):
        # 内置 tools/ 目录含多版本子目录时,取目录名版本最高者
        td = os.path.join(_TMP, "btools")
        exe = self.exe_name
        v57 = os.path.join(td, "mysql-5.7", "bin", exe)
        v80 = os.path.join(td, "mysql-8.0.42", "bin", exe)
        for p in (v57, v80):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy")
        with mock.patch.object(env_probe, "bundled_tools_dir", return_value=td):
            self.assertEqual(env_probe.find_bundled_tool(exe), os.path.abspath(v80))
        # 汇总:两个版本都在列表内,且版本文本解析正确
        with mock.patch.object(env_probe, "bundled_tools_dir", return_value=td):
            summary = env_probe.bundled_tools_summary()
        dirs = {s["dir"]: s["version"] for s in summary}
        self.assertIn(os.path.dirname(os.path.abspath(v57)), dirs)
        self.assertIn(os.path.dirname(os.path.abspath(v80)), dirs)
        self.assertTrue(dirs[os.path.dirname(os.path.abspath(v80))].startswith("8.0"))

    def test_bundled_tools_dir_absent_returns_empty(self):
        # 无内置 tools/ 时返回空串,find_tool 探测链不因内置目录抛错
        with mock.patch.object(env_probe, "bundled_tools_dir", return_value=""):
            self.assertEqual(env_probe.bundled_tools_dir(), "")
            self.assertIsNone(env_probe.find_bundled_tool(self.exe_name))

    def test_find_bundled_tool_want_major(self):
        # want_major 过滤:5 命中 5.7 族,8 命中 8.x 族,0=自动取最高
        td = os.path.join(_TMP, "btools_ver")
        exe = self.exe_name
        v57 = os.path.join(td, "mysql-5.7", "bin", exe)
        v80 = os.path.join(td, "mysql-8.0.42", "bin", exe)
        for p in (v57, v80):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b"dummy")
        with mock.patch.object(env_probe, "bundled_tools_dir", return_value=td):
            self.assertEqual(env_probe.find_bundled_tool(exe, want_major=5),
                             os.path.abspath(v57))
            self.assertEqual(env_probe.find_bundled_tool(exe, want_major=8),
                             os.path.abspath(v80))
            self.assertEqual(env_probe.find_bundled_tool(exe, want_major=0),
                             os.path.abspath(v80))

    def test_find_bundled_tool_want_major_miss(self):
        # 内置仅 8.x,声明 5 → 未命中返回 None,交由 find_tool_versioned 回退 PATH/常见目录
        td = os.path.join(_TMP, "btools_ver_miss")
        exe = self.exe_name
        v80 = os.path.join(td, "mysql-8.0.42", "bin", exe)
        os.makedirs(os.path.dirname(v80), exist_ok=True)
        with open(v80, "wb") as f:
            f.write(b"dummy")
        with mock.patch.object(env_probe, "bundled_tools_dir", return_value=td):
            self.assertIsNone(env_probe.find_bundled_tool(exe, want_major=5))
            # 自动模式仍然命中最高版本
            self.assertEqual(env_probe.find_bundled_tool(exe, want_major=0),
                             os.path.abspath(v80))

    def test_bundled_sha256_verified_and_skip(self):
        # 清单正确 → 校验通过并选中;篡改 → 校验失败被跳过
        td = os.path.join(_TMP, "btools_sha")
        exe = self.exe_name
        p = os.path.join(td, "mysql-8.0.42", "bin", exe)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"dummy-client-binary")
        good = env_probe._sha256(p)
        sums = os.path.join(td, "SHA256SUMS")
        rel = os.path.relpath(p, td).replace("\\", "/")
        with open(sums, "w", encoding="utf-8") as f:
            f.write("%s  %s\n" % (good, rel))
        env_probe._bundled_manifest = None
        env_probe._bundled_sha_cache.clear()
        with mock.patch.object(env_probe, "bundled_tools_dir", return_value=td):
            self.assertTrue(env_probe.bundled_verified(os.path.abspath(p)))
            self.assertEqual(env_probe.find_bundled_tool(exe), os.path.abspath(p))
        # 篡改后的哈希 → 校验失败,单个候选被跳过 → None
        with open(sums, "w", encoding="utf-8") as f:
            f.write("%s  %s\n" % ("0" * 64, rel))
        env_probe._bundled_manifest = None
        env_probe._bundled_sha_cache.clear()
        with mock.patch.object(env_probe, "bundled_tools_dir", return_value=td):
            self.assertFalse(env_probe.bundled_verified(os.path.abspath(p)))
            self.assertIsNone(env_probe.find_bundled_tool(exe))

    def test_path_version_none(self):
        # 不存在/空路径不抛异常,返回 None
        self.assertIsNone(env_probe.path_version(None))
        self.assertIsNone(env_probe.path_version(os.path.join(_TMP, "no-such-tool")))

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


class QueryGuardTest(unittest.TestCase):
    """只读 SQL 守卫(2026-09-02 新增):防写操作与真实绕过回归。

    覆盖:关键字白/黑名单、前导注释剥离、可执行注释 /*!...*/、WITH+DML、
    SET GLOBAL/PERSIST、分号多语句,以及合法查询不被误杀。
    """

    @staticmethod
    def _stub_cursor(rows=None, desc=None):
        cur = mock.Mock()
        cur.execute = mock.Mock()
        cur.fetchmany.return_value = rows if rows is not None else []
        cur.fetchone.return_value = None
        cur.description = desc
        cur.__enter__ = mock.Mock(return_value=cur)
        cur.__exit__ = mock.Mock(return_value=False)
        conn = mock.Mock()
        conn.cursor = mock.Mock(return_value=cur)
        return conn, cur

    # ---------- 关键字提取 ----------
    def test_leading_keyword_basic(self):
        for sql, expect in [
            ("SELECT 1", "SELECT"),
            ("select 1", "SELECT"),
            ("SeLeCt * FROM t", "SELECT"),
            ("  \n\t SHOW TABLES", "SHOW"),
            ("DESC t", "DESC"),
            ("EXPLAIN SELECT 1", "EXPLAIN"),
            ("WITH c AS (SELECT 1) SELECT * FROM c", "WITH"),
            ("-- 注释\nSELECT 1", "SELECT"),
            ("# 注释\nSELECT 1", "SELECT"),
            ("/* 注释 */ SELECT 1", "SELECT"),
            ("DROP TABLE t", "DROP"),
            ("", ""),
            ("   ", ""),
            ("123 abc", ""),
        ]:
            self.assertEqual(mysql_client._query_leading_keyword(sql), expect, sql)

    def test_leading_keyword_exec_comment(self):
        # /*!...*/ 是 MySQL 可执行注释,必须返回哨兵而非剥离后继续
        for sql in [
            "/*!50000 DROP TABLE t */",
            "/*! DROP TABLE t */ SELECT 1",
            "-- x\n/*! DROP TABLE t */ SELECT 1",
            "/* 普通 */ /*! DROP TABLE t */ SELECT 1",
        ]:
            self.assertEqual(mysql_client._query_leading_keyword(sql), mysql_client._EXEC_COMMENT, sql)

    # ---------- run_query 守卫 ----------
    def test_run_query_allows_readonly(self):
        for sql in [
            "SELECT 1",
            "SHOW TABLES",
            "DESC t",
            "EXPLAIN SELECT 1",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "WITH cte AS (SELECT 'delete' AS x) SELECT * FROM cte",
            "-- hi\nSELECT 1",
            "SELECT 'a;b'",
            "SELECT 1;",
            "SELECT 1 -- 尾注释",
        ]:
            with self.subTest(sql=sql):
                conn, cur = self._stub_cursor(rows=[("1",)], desc=[("x",)])
                r = mysql_client.run_query(conn, sql)
                self.assertEqual(r["columns"], ["x"])
                cur.execute.assert_called()

    def test_run_query_rejects_write(self):
        for sql in [
            "DROP TABLE t",
            "DELETE FROM t",
            "UPDATE t SET a=1",
            "INSERT INTO t VALUES (1)",
            "TRUNCATE t",
            "ALTER TABLE t ADD c INT",
            "CREATE TABLE t (id INT)",
            "GRANT ALL ON *.* TO 'u'",
            "REVOKE ALL ON *.* FROM 'u'",
            "RENAME TABLE a TO b",
            "REPLACE INTO t VALUES (1)",
            "CALL sp()",
            "LOAD DATA INFILE '/etc/passwd' INTO TABLE t",
        ]:
            with self.subTest(sql=sql):
                conn, cur = self._stub_cursor()
                with self.assertRaises(mysql_client.DbError):
                    mysql_client.run_query(conn, sql)
                cur.execute.assert_not_called()

    def test_run_query_rejects_bypass(self):
        """记录在案的真实绕过手段:全部必须被拒绝,且不得触达 execute。"""
        for sql in [
            "/*!50000 DROP TABLE t */",
            "/*!50000 DROP TABLE t */ SELECT 1",
            "-- x\n/*! DROP TABLE t */ SELECT 1",
            "WITH cte AS (SELECT 1) DELETE FROM t",
            "WITH cte AS (SELECT 1) UPDATE t SET a=1",
            "WITH a AS (SELECT 1), b AS (SELECT 2) DELETE FROM t",
            "SET GLOBAL max_connections = 999",
            "SET PERSIST max_connections = 999",
            "SET @@GLOBAL.max_connections = 999",
            "SELECT 1; DROP TABLE t",
            "SELECT 1;\nSELECT 2",
        ]:
            with self.subTest(sql=sql):
                conn, cur = self._stub_cursor()
                with self.assertRaises(mysql_client.DbError):
                    mysql_client.run_query(conn, sql)
                cur.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)