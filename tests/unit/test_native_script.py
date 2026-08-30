# -*- coding: utf-8 -*-
"""native_script 生成器单元测试(2026-08-30 新增):纯函数、零依赖、不注册系统任务。

覆盖:
- Windows .ps1: UTF-8 BOM + CRLF、mysqldump 参数(--result-file/--single-transaction/--triggers -R)、
  MYSQL_PWD 传密码、密码/库名单引号转义、keep 内嵌、gzip(GzipStream)、日志文件名、
  退出码、全库模式(ALL_MODE=1)
- Linux .sh: shebang + 无 CRLF、mysqldump 参数、MYSQL_PWD、密码/库名单引号转义、
  gzip -9、keep、退出码、全库模式、chmod 700(Linux 平台才断言权限位)
- 默认备份目录回退: backup_dir 为空 -> <MC_DATA_DIR>/backups

隔离: 设置 MC_DATA_DIR 指向工作区临时目录,脚本生成到独立临时目录,不触碰真实 data/。
用法: python tests/test_native_script.py
"""
import os
import shutil
import sys
import tempfile
import unittest

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(WORKSPACE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# 隔离数据目录:必须在 import native_script(内部 import paths)之前设置
_TMPDATA = tempfile.mkdtemp(prefix="mc_ns_data_")
os.environ["MC_DATA_DIR"] = _TMPDATA

import native_script  # noqa: E402


def _task(**kw):
    t = {
        "id": "test12345678", "name": "测试任务A",
        "dbs": ["test_db1", "test_db2", "db with'quote"],
        "keep": 5, "backup_dir": "",
    }
    t.update(kw)
    return t


_CONN = {"host": "127.0.0.1", "port": 3306, "user": "root",
         "password": "p@ss'w'ord;%&"}
_SETTINGS = {"mysql_bin": "", "backup_dir": ""}


class TestNativeScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mc_ns_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(_TMPDATA, ignore_errors=True)

    # ---------------- Windows ps1 ----------------

    def test_ps1_bom_crlf(self):
        p = native_script.build(_task(), _CONN, _SETTINGS, self.tmp, "windows")
        with open(p, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM")
        self.assertIn(b"\r\n", raw, "应使用 CRLF 换行")
        self.assertEqual(os.path.basename(p), "backup_test12345678.ps1")

    def test_ps1_mysqldump_args(self):
        p = native_script.build(_task(), _CONN, _SETTINGS, self.tmp, "windows")
        with open(p, "rb") as f:
            t = f.read().decode("utf-8-sig")
        for frag in ("--result-file=", "--single-transaction=TRUE", "--quick",
                     "--triggers", " -R", "--default-character-set=",
                     "--protocol=tcp", "$env:MYSQL_PWD"):
            self.assertIn(frag, t, "缺少 %s" % frag)
        self.assertIn("GzipStream", t, "缺少 gzip 压缩")
        self.assertIn("exit $failCount", t, "缺少退出码")
        self.assertIn("mysqlconsole_backup_test12345678.log", t, "日志文件名错误")

    def test_ps1_escape_and_keep(self):
        p = native_script.build(_task(), _CONN, _SETTINGS, self.tmp, "windows")
        with open(p, "rb") as f:
            t = f.read().decode("utf-8-sig")
        self.assertIn("'p@ss''w''ord;%&'", t, "密码单引号未转义")
        self.assertIn("'test_db1','test_db2','db with''quote'", t, "库列表转义错误")
        self.assertIn("$KEEP       = 5", t, "keep 未内嵌")
        self.assertIn("$ALL_MODE   = 0", t, "非全库模式 ALL_MODE 应为 0")

    def test_ps1_all_mode(self):
        p = native_script.build(_task(dbs=[]), _CONN, _SETTINGS, self.tmp, "windows")
        with open(p, "rb") as f:
            t = f.read().decode("utf-8-sig")
        self.assertIn("$ALL_MODE   = 1", t)
        self.assertIn("information_schema", t, "全库模式应包含系统库排除逻辑")

    # ---------------- Linux sh ----------------

    def test_sh_basic(self):
        p = native_script.build(_task(), _CONN, _SETTINGS, self.tmp, "linux")
        with open(p, "r", encoding="utf-8") as f:
            t = f.read()
        self.assertTrue(t.startswith("#!/usr/bin/env bash"), "缺少 shebang")
        self.assertNotIn("\r\n", t, "sh 不应有 CRLF")
        self.assertEqual(os.path.basename(p), "backup_test12345678.sh")
        if os.name != "nt":  # Windows 的 os.chmod 不实现 POSIX 权限位
            self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o700", "应 chmod 700")

    def test_sh_mysqldump_args(self):
        p = native_script.build(_task(), _CONN, _SETTINGS, self.tmp, "linux")
        with open(p, "r", encoding="utf-8") as f:
            t = f.read()
        for frag in ("--result-file=", "--single-transaction=TRUE", "--quick",
                     "--triggers", " -R", 'MYSQL_PWD="$PASS"', "gzip -9 -f",
                     "exit $failCount", "mysqlconsole_backup_test12345678.log"):
            self.assertIn(frag, t, "缺少 %s" % frag)

    def test_sh_escape_and_keep(self):
        p = native_script.build(_task(), _CONN, _SETTINGS, self.tmp, "linux")
        with open(p, "r", encoding="utf-8") as f:
            t = f.read()
        self.assertIn("'p@ss'\\''w'\\''ord;%&'", t, "密码单引号未转义")
        self.assertIn("'test_db1' 'test_db2' 'db with'\\''quote'", t, "库列表转义错误")
        self.assertIn("KEEP=5", t, "keep 未内嵌")
        self.assertIn("ALL_MODE=0", t)

    def test_sh_all_mode(self):
        p = native_script.build(_task(dbs=[]), _CONN, _SETTINGS, self.tmp, "linux")
        with open(p, "r", encoding="utf-8") as f:
            t = f.read()
        self.assertIn("ALL_MODE=1", t)
        self.assertIn("information_schema", t)

    # ---------------- 公共 ----------------

    def test_default_backup_dir_fallback(self):
        """backup_dir 为空时回退 <MC_DATA_DIR>/backups(与 backup_engine 默认一致)。"""
        p = native_script.build(_task(backup_dir=""), _CONN, _SETTINGS, self.tmp, "windows")
        with open(p, "rb") as f:
            t = f.read().decode("utf-8-sig")
        expect = os.path.join(_TMPDATA, "backups").replace("\\", "/")
        self.assertIn(expect, t, "默认备份目录未回退到 data/backups")

    def test_task_backup_dir_wins(self):
        p = native_script.build(_task(backup_dir="D:/custom/bak"),
                                _CONN, {"mysql_bin": "", "backup_dir": "D:/global/bak"},
                                self.tmp, "windows")
        with open(p, "rb") as f:
            t = f.read().decode("utf-8-sig")
        self.assertIn("D:/custom/bak", t)
        self.assertNotIn("D:/global/bak", t, "任务级 backup_dir 应优先于全局设置")


if __name__ == "__main__":
    unittest.main(verbosity=2)
