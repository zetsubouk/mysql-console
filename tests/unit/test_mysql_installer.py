# -*- coding: utf-8 -*-
"""mysql_installer 单元测试(阶段1: 本机数据库检测)。

纯离线: 聚合逻辑用注入数据,端口探测只绑本机随机端口(127.0.0.1,端口 0 由内核分配),
不需要 MySQL、不访问外网、不触碰真实 data/。
用法: python tests/unit/test_mysql_installer.py
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(WORKSPACE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import mysql_installer  # noqa: E402


def _free_port():
    """向内核要一个当前空闲端口(绑定后立即释放),用于"端口未开放"断言。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _TcpServer:
    """一次性本地 TCP 服务: 可选发送首包(模拟 MySQL 握手),用于 probe_port 测试。"""

    def __init__(self, payload=b""):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.payload = payload
        self.th = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.th.start()
        return self

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
            if self.payload:
                conn.sendall(self.payload)
            conn.close()
        except Exception:
            pass

    def close(self):
        self.sock.close()


class AggregateDetectionTest(unittest.TestCase):
    """三分量聚合结论: 任一命中即 installed,端口被非 MySQL 占用不算。"""

    def test_hit_by_service(self):
        r = mysql_installer.aggregate_detection("MySQL80", "running", "", None)
        self.assertTrue(r["installed"])
        self.assertEqual(r["service_name"], "MySQL80")
        self.assertIn("MySQL80", r["summary"])

    def test_hit_by_binary(self):
        v = {"major": 8, "minor": 0, "patch": 36, "text": "8.0.36 MySQL Community"}
        r = mysql_installer.aggregate_detection("", "", r"C:\mysql\bin\mysqld.exe", v)
        self.assertTrue(r["installed"])
        self.assertEqual(r["mysqld_path"], r"C:\mysql\bin\mysqld.exe")
        self.assertEqual(r["version"]["major"], 8)

    def test_hit_by_port_greeting_docker_scenario(self):
        # Docker 映射场景: 无服务无可执行文件,但 3306 有 MySQL 握手 → 也算"有数据库"
        r = mysql_installer.aggregate_detection("", "", "", None,
                                                3306, True, "mysql")
        self.assertTrue(r["installed"])
        self.assertEqual(r["port_hint"], "mysql")

    def test_open_port_not_mysql_not_installed(self):
        # 端口被其它程序占用: 不能误导用户当数据库用
        r = mysql_installer.aggregate_detection("", "", "", None,
                                                3306, True, "unknown")
        self.assertFalse(r["installed"])
        self.assertIn("其它程序", r["summary"])

    def test_nothing_found(self):
        r = mysql_installer.aggregate_detection("", "", "", None, 3306, False, "")
        self.assertFalse(r["installed"])
        self.assertFalse(r["port_open"])
        self.assertIn("未检测到", r["summary"])
        # 字段完整性: 前端渲染依赖这些键
        for k in ("installed", "service_name", "service_state", "mysqld_path",
                  "version", "port", "port_open", "port_hint", "summary"):
            self.assertIn(k, r)


class ProbePortTest(unittest.TestCase):
    """端口探测: 真实本机 socket 验证三种结论。"""

    def test_mysql_greeting(self):
        # 模拟 MySQL 握手首包: 协议头 + 版本串 + 认证插件名
        payload = b"\x00\x00\x00\x0a" + b"8.0.36\x00" + b"mysql_native_password\x00"
        srv = _TcpServer(payload).start()
        try:
            ok, kind = mysql_installer.probe_port("127.0.0.1", srv.port, timeout=0.5)
            self.assertTrue(ok)
            self.assertEqual(kind, "mysql")
        finally:
            srv.close()

    def test_mariadb_greeting_case_insensitive(self):
        payload = b"\x00\x00\x00\x0a" + b"11.2.2-MariaDB\x00"
        srv = _TcpServer(payload).start()
        try:
            ok, kind = mysql_installer.probe_port("127.0.0.1", srv.port, timeout=0.5)
            self.assertTrue(ok)
            self.assertEqual(kind, "mysql")
        finally:
            srv.close()

    def test_open_but_not_mysql(self):
        # 监听但不发首包(如 HTTP 服务等待请求) → 开放但特征 unknown
        srv = _TcpServer().start()
        try:
            ok, kind = mysql_installer.probe_port("127.0.0.1", srv.port, timeout=0.5)
            self.assertTrue(ok)
            self.assertEqual(kind, "unknown")
        finally:
            srv.close()

    def test_closed_port(self):
        ok, kind = mysql_installer.probe_port("127.0.0.1", _free_port(), timeout=0.5)
        self.assertFalse(ok)
        self.assertEqual(kind, "")


class FindServerTest(unittest.TestCase):
    """mysqld 定位: PATH 命中 / 扫描目录命中(含 .exe 补全)。"""

    def test_via_which(self):
        with mock.patch.object(mysql_installer.shutil, "which",
                               side_effect=lambda n: "/usr/sbin/mysqld" if n == "mysqld" else None):
            self.assertEqual(mysql_installer.find_server(), "/usr/sbin/mysqld")

    def test_via_dir_scan_with_exe_suffix(self):
        # PATH 未命中,扫描目录命中 Windows 式 mysqld.exe
        # 需同时 patch IS_WIN: .exe 补全分支仅在 Windows 下生效,沙箱是 Linux
        fake_dir = os.path.join("C:" + os.sep, "no_such_mysql_dir")
        with mock.patch.object(mysql_installer.shutil, "which", return_value=None), \
             mock.patch.object(mysql_installer, "server_candidate_dirs", return_value=[fake_dir]), \
             mock.patch.object(mysql_installer, "IS_WIN", True), \
             mock.patch.object(mysql_installer.os.path, "isfile",
                               side_effect=lambda p: p == os.path.join(fake_dir, "mysqld.exe")):
            self.assertEqual(mysql_installer.find_server(),
                             os.path.join(fake_dir, "mysqld.exe"))

    def test_not_found_returns_empty(self):
        with mock.patch.object(mysql_installer.shutil, "which", return_value=None), \
             mock.patch.object(mysql_installer, "server_candidate_dirs", return_value=[]):
            self.assertEqual(mysql_installer.find_server(), "")

    def test_candidate_dirs_sanity(self):
        # 返回值必须是"实际存在"的目录列表(供扫描直接使用,不再二次过滤)
        dirs = mysql_installer.server_candidate_dirs()
        self.assertIsInstance(dirs, list)
        self.assertTrue(all(os.path.isdir(d) for d in dirs))


class ServerBinVersionTest(unittest.TestCase):
    """mysqld --version 解析: 复用 env_probe.parse_version 的正则。"""

    def test_parse_mysqld_output(self):
        fake = mock.Mock()
        fake.stdout = "mysqld Ver 8.0.36 for Linux on x86_64 (MySQL Community)".encode()
        fake.stderr = b""
        with mock.patch.object(mysql_installer.subprocess, "run", return_value=fake):
            v = mysql_installer.server_bin_version("/usr/sbin/mysqld")
            self.assertEqual((v["major"], v["minor"], v["patch"]), (8, 0, 36))

    def test_bad_binary_returns_none(self):
        with mock.patch.object(mysql_installer.subprocess, "run",
                               side_effect=OSError("exec fail")):
            self.assertIsNone(mysql_installer.server_bin_version("/bad/path"))

    def test_empty_path_short_circuit(self):
        self.assertIsNone(mysql_installer.server_bin_version(""))


class CliEntryTest(unittest.TestCase):
    """CLI 入口: 退出码契约(install 脚本依赖 0=有库 / 1=无库)。"""

    def test_exit_0_when_installed(self):
        with mock.patch.object(mysql_installer, "detect_local_server",
                               return_value={"installed": True, "summary": "检测到本机数据库",
                                             "service_name": "MySQL80", "service_state": "running",
                                             "mysqld_path": "", "version": None,
                                             "port": 3306, "port_open": True, "port_hint": "mysql"}):
            self.assertEqual(mysql_installer.main(["--detect-only"]), 0)

    def test_exit_1_when_not_installed(self):
        with mock.patch.object(mysql_installer, "detect_local_server",
                               return_value={"installed": False, "summary": "未检测到本机数据库",
                                             "service_name": "", "service_state": "",
                                             "mysqld_path": "", "version": None,
                                             "port": 3306, "port_open": False, "port_hint": ""}):
            self.assertEqual(mysql_installer.main(["--detect-only"]), 1)

    def test_exit_1_when_detect_crashes(self):
        # 检测自身异常绝不能让 install 脚本中断: 捕获后按"未检测到"退出
        with mock.patch.object(mysql_installer, "detect_local_server",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(mysql_installer.main(["--detect-only"]), 1)

    def test_invalid_args_exit_2(self):
        self.assertEqual(mysql_installer.main([]), 2)


class FormatReportTest(unittest.TestCase):
    """CLI 报告文本: 关键字段必须可见。"""

    def test_report_contains_fields(self):
        r = mysql_installer.aggregate_detection("MySQL80", "running", "/usr/sbin/mysqld",
                                                {"major": 8, "minor": 0, "patch": 36,
                                                 "text": "mysqld Ver 8.0.36"},
                                                3306, True, "mysql")
        txt = mysql_installer.format_detect_report(r)
        self.assertIn("MySQL80", txt)
        self.assertIn("/usr/sbin/mysqld", txt)
        self.assertIn("8.0.36", txt)
        self.assertIn("3306", txt)

    def test_report_not_installed_has_guide(self):
        r = mysql_installer.aggregate_detection("", "", "", None, 3306, False, "")
        txt = mysql_installer.format_detect_report(r)
        self.assertIn("未检测到", txt)
        self.assertIn("向导", txt)


class HumanMemTest(unittest.TestCase):
    """档位化: ≥2G 取整 G,否则对齐 128M。"""

    def test_gigabytes(self):
        self.assertEqual(mysql_installer._human_mem(8 * mysql_installer._GB), "8G")
        self.assertEqual(mysql_installer._human_mem(2 * mysql_installer._GB), "2G")

    def test_megabyte_alignment(self):
        n = 4 * mysql_installer._GB // 8  # 512M
        self.assertEqual(mysql_installer._human_mem(n), "512M")
        # 小于 128M 时保底 128M
        self.assertEqual(mysql_installer._human_mem(1024), "128M")

    def test_to_bytes(self):
        conv = mysql_installer._to_bytes
        self.assertEqual(conv("512M"), 512 * 1024 ** 2)
        self.assertEqual(conv("1G"), 1024 ** 3)
        self.assertEqual(conv("3306"), 3306)
        self.assertEqual(conv("64m"), 64 * 1024 ** 2)   # 大小写不敏感
        self.assertIsNone(conv("abc"))
        self.assertIsNone(conv(""))


class SuggestBufferPoolTest(unittest.TestCase):
    """buffer pool 内存分档边界(≤2G/≤4G/≤16G/>16G + co_exist 收敛 + 封顶)。"""

    GB = mysql_installer._GB

    def test_tiny_and_unknown(self):
        self.assertEqual(mysql_installer.suggest_buffer_pool(1 * self.GB), "128M")
        self.assertEqual(mysql_installer.suggest_buffer_pool(None), "128M")
        self.assertEqual(mysql_installer.suggest_buffer_pool(0), "128M")

    def test_boundaries(self):
        GB = self.GB
        self.assertEqual(mysql_installer.suggest_buffer_pool(2 * GB), "128M")       # ≤2G
        self.assertEqual(mysql_installer.suggest_buffer_pool(2 * GB + 1), "512M")   # 25%
        self.assertEqual(mysql_installer.suggest_buffer_pool(4 * GB), "1G")         # ≤4G
        self.assertEqual(mysql_installer.suggest_buffer_pool(4 * GB + 1), "2G")     # 50%
        self.assertEqual(mysql_installer.suggest_buffer_pool(16 * GB), "8G")
        self.assertEqual(mysql_installer.suggest_buffer_pool(16 * GB + 1), "9G")    # 60%
        self.assertEqual(mysql_installer.suggest_buffer_pool(64 * GB), "32G")       # 封顶

    def test_co_exist_converges(self):
        GB = self.GB
        self.assertEqual(mysql_installer.suggest_buffer_pool(16 * GB, co_exist=True), "4G")
        self.assertEqual(mysql_installer.suggest_buffer_pool(64 * GB, co_exist=True), "16G")


class SuggestMaxConnectionsTest(unittest.TestCase):
    """连接数分档边界(50/100/200/300)。"""

    GB = mysql_installer._GB

    def test_tiers(self):
        GB = self.GB
        f = mysql_installer.suggest_max_connections
        self.assertEqual(f(2 * GB), 50)
        self.assertEqual(f(2 * GB + 1), 100)
        self.assertEqual(f(4 * GB), 100)
        self.assertEqual(f(4 * GB + 1), 200)
        self.assertEqual(f(8 * GB), 200)
        self.assertEqual(f(8 * GB + 1), 300)
        self.assertEqual(f(None), 100)


class BuildSuggestionsTest(unittest.TestCase):
    """汇总建议: 字段完整性 + 版本决定排序规则。"""

    def test_keys_present(self):
        sug = mysql_installer.build_config_suggestions(8 * mysql_installer._GB, 4)
        for k in ("character_set_server", "collation_server", "innodb_buffer_pool_size",
                  "max_connections", "innodb_redo_log_capacity", "max_allowed_packet",
                  "port", "notes"):
            self.assertIn(k, sug)
        self.assertEqual(sug["character_set_server"], "utf8mb4")
        self.assertEqual(sug["innodb_buffer_pool_size"], "4G")

    def test_collation_by_major_version(self):
        self.assertEqual(
            mysql_installer.build_config_suggestions(version_major=8)["collation_server"],
            "utf8mb4_0900_ai_ci")
        self.assertEqual(
            mysql_installer.build_config_suggestions(version_major=5)["collation_server"],
            "utf8mb4_unicode_ci")


class RenderMyCnfTest(unittest.TestCase):
    """my.ini 渲染: 新旧 redo 参数、排序规则回退、lower_case、mysqlx。"""

    GB = mysql_installer._GB

    def _cfg(self):
        return {"basedir": "/opt/mysql", "datadir": "/opt/mysql/data", "port": 3306,
                "character_set_server": "utf8mb4", "collation_server": "auto",
                "innodb_buffer_pool_size": "1G", "max_connections": 200,
                "innodb_redo_log_capacity": "1G", "max_allowed_packet": "64M",
                "lower_case_table_names": None}

    def test_modern_uses_redo_capacity(self):
        txt = mysql_installer.render_my_cnf(self._cfg(), (8, 0, 36))
        self.assertIn("innodb_redo_log_capacity=1G", txt)
        self.assertNotIn("innodb_log_file_size", txt)
        self.assertIn("collation_server=utf8mb4_0900_ai_ci", txt)
        self.assertIn("mysqlx=OFF", txt)
        self.assertNotIn("lower_case_table_names", txt)   # None → 不输出

    def test_legacy_uses_log_file_size(self):
        txt = mysql_installer.render_my_cnf(self._cfg(), (5, 7, 44))
        self.assertIn("innodb_log_file_size=512M", txt)   # 1G 折半
        self.assertIn("innodb_log_files_in_group=2", txt)
        self.assertNotIn("innodb_redo_log_capacity", txt)
        # 5.7 没有 0900 排序规则,auto 必须回退
        self.assertIn("collation_server=utf8mb4_unicode_ci", txt)

    def test_pre_8_0_30_boundary(self):
        txt = mysql_installer.render_my_cnf(self._cfg(), (8, 0, 29))
        self.assertIn("innodb_log_file_size", txt)

    def test_explicit_collation_kept(self):
        cfg = self._cfg()
        cfg["collation_server"] = "utf8mb4_general_ci"
        txt = mysql_installer.render_my_cnf(cfg, (5, 7, 44))
        self.assertIn("collation_server=utf8mb4_general_ci", txt)

    def test_lower_case_emitted_when_provided(self):
        cfg = self._cfg()
        cfg["lower_case_table_names"] = 1
        txt = mysql_installer.render_my_cnf(cfg, (8, 0, 36))
        self.assertIn("lower_case_table_names=1", txt)

    def test_paths_and_port(self):
        txt = mysql_installer.render_my_cnf(self._cfg(), (8, 0, 36))
        self.assertIn("basedir=/opt/mysql", txt)
        self.assertIn("datadir=/opt/mysql/data", txt)
        self.assertIn("port=3306", txt)
        self.assertTrue(txt.startswith("[mysqld]"))


class ValidatePathsTest(unittest.TestCase):
    """路径预检: 绝对路径格式 / 非 ASCII / 非空 datadir。"""

    def test_posix_ok(self):
        errs, warns = mysql_installer.validate_install_paths("/opt/mysql", "/opt/mysql/data",
                                                             is_win=False)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])

    def test_windows_drive_letter(self):
        errs, warns = mysql_installer.validate_install_paths(r"D:\mysql", r"D:\mysql\data",
                                                             is_win=True)
        self.assertEqual(errs, [])
        self.assertEqual(warns, [])

    def test_relative_path_rejected(self):
        errs, _ = mysql_installer.validate_install_paths("mysql", "data", is_win=False)
        self.assertEqual(len(errs), 2)

    def test_windows_path_without_drive_rejected(self):
        errs, _ = mysql_installer.validate_install_paths("mysql", "mysql\\data", is_win=True)
        self.assertEqual(len(errs), 2)

    def test_empty_paths_rejected(self):
        errs, _ = mysql_installer.validate_install_paths("", "", is_win=False)
        self.assertEqual(len(errs), 2)

    def test_non_ascii_warns_on_windows(self):
        _, warns = mysql_installer.validate_install_paths(r"D:\数据库", r"D:\数据库\data",
                                                          is_win=True)
        self.assertTrue(any("非 ASCII" in w for w in warns))

    def test_nonempty_datadir_warns(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.txt"), "w") as f:
                f.write("1")
            _, warns = mysql_installer.validate_install_paths("/opt/m", d, is_win=False)
            self.assertTrue(any("非空" in w for w in warns))


class CdnUrlsTest(unittest.TestCase):
    """CDN 直链构造: maj.min 目录 + 平台文件名。"""

    def test_win_and_linux_candidates(self):
        win, linux = mysql_installer._cdn_urls("9.7.2")
        self.assertEqual(win, "https://cdn.mysql.com/Downloads/MySQL-9.7/mysql-9.7.2-winx64.zip")
        self.assertEqual(len(linux), 2)   # glibc2.28 / glibc2.17 两个候选
        self.assertIn("glibc2.28", linux[0])

    def test_glibc_candidate_order(self):
        # 先探测新 glibc(9.x 只发布 2.28),老标签兜底
        self.assertEqual(mysql_installer._GLIBC_CANDIDATES[0], "glibc2.28")


_EOL_FIXTURE = [
    {"cycle": "9.7", "latest": "9.7.2", "lts": True, "latestReleaseDate": "2026-07-28"},
    {"cycle": "9.6", "latest": "9.6.1", "lts": False, "latestReleaseDate": "2026-01-16"},
    {"cycle": "9.5", "latest": "9.5.2", "lts": False},
]


class _FakeResp:
    """模拟 urlopen 返回的上下文管理器(仅 read 需要)。"""

    status = 200

    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FetchOfficialVersionsTest(unittest.TestCase):
    """官方枚举: 版本号来自 JSON,直链必须 HEAD 验证通过才收录。"""

    def _run_fetch(self, probe_map):
        data = json.dumps(_EOL_FIXTURE).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(data)), \
             mock.patch.object(mysql_installer, "_probe_url",
                               side_effect=lambda url, timeout=8: probe_map(url)):
            return mysql_installer._fetch_official_versions()

    def test_pick_and_skip(self):
        def probe(url):
            # 9.7.2: win + linux(glibc2.28) 均在架;9.6.1: 仅 win;9.5.2: 已下架
            return ("9.7.2-winx64" in url or "9.6.1-winx64" in url
                    or "9.7.2-linux-glibc2.28" in url)
        out = self._run_fetch(probe)
        self.assertEqual([v["version"] for v in out], ["9.7.2", "9.6.1"])  # 9.5.2 win 404 → 剔除
        by_ver = {v["version"]: v for v in out}
        self.assertTrue(by_ver["9.7.2"]["lts"])
        # 9.7.2 glibc2.28 命中;9.6.1 两个 glibc 全 404 → linux_url 留空但仍列出
        self.assertIn("glibc2.28", by_ver["9.7.2"]["linux_url"])
        self.assertEqual(by_ver["9.6.1"]["linux_url"], "")

    def test_cap(self):
        fixture = [{"cycle": "9.%d" % i, "latest": "9.%d.0" % i, "lts": False}
                   for i in range(10)]
        data = json.dumps(fixture).encode()
        with mock.patch("urllib.request.urlopen", return_value=_FakeResp(data)), \
             mock.patch.object(mysql_installer, "_probe_url", return_value=True):
            out = mysql_installer._fetch_official_versions()
        self.assertEqual(len(out), mysql_installer._MAX_ENUMERATED)

    def test_all_down_aborts(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=_FakeResp(json.dumps(_EOL_FIXTURE).encode())), \
             mock.patch.object(mysql_installer, "_probe_url", return_value=False):
            with self.assertRaises(RuntimeError):
                mysql_installer._fetch_official_versions()


class VersionsCacheTest(unittest.TestCase):
    """版本缓存: 往返 / TTL 过期 / 损坏文件静默降级。"""

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.cache_path = os.path.join(self._td.name, "mysql_versions_cache.json")
        p = mock.patch.object(mysql_installer, "_versions_cache_path",
                              return_value=self.cache_path)
        p.start()
        self.addCleanup(p.stop)

    def test_roundtrip(self):
        vers = [{"version": "9.7.2", "lts": True}]
        mysql_installer._save_versions_cache(vers)
        entry = mysql_installer._load_versions_cache()
        self.assertIsNotNone(entry)
        self.assertEqual(entry["versions"][0]["version"], "9.7.2")

    def test_expired(self):
        mysql_installer._save_versions_cache([{"version": "8.0.45"}])
        # 手动把时间戳改到 TTL 之外
        with open(self.cache_path, "w", encoding="utf-8") as f:
            f.write(mysql_installer.json_dumps(
                {"ts": time.time() - mysql_installer._VERSIONS_CACHE_TTL - 10,
                 "versions": [{"version": "8.0.45"}]}))
        self.assertIsNone(mysql_installer._load_versions_cache())

    def test_corrupted(self):
        with open(self.cache_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(mysql_installer._load_versions_cache())

    def test_missing(self):
        self.assertIsNone(mysql_installer._load_versions_cache())


class ListOfficialVersionsTest(unittest.TestCase):
    """对外入口三级降级: 缓存 → 官方 API → 内置兜底。"""

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        p = mock.patch.object(mysql_installer, "_versions_cache_path",
                              return_value=os.path.join(self._td.name, "cache.json"))
        p.start()
        self.addCleanup(p.stop)

    def test_cache_hit_no_network(self):
        mysql_installer._save_versions_cache([{"version": "9.9.9", "lts": True}])
        with mock.patch.object(mysql_installer, "_fetch_official_versions",
                               side_effect=RuntimeError("network down")):
            r = mysql_installer.list_official_versions()
        self.assertEqual(r["source"], "cache")
        self.assertEqual(r["versions"][0]["version"], "9.9.9")

    def test_fallback_builtin_never_empty(self):
        with mock.patch.object(mysql_installer, "_fetch_official_versions",
                               side_effect=RuntimeError("network down")):
            r = mysql_installer.list_official_versions(force=True)
        self.assertEqual(r["source"], "builtin")
        self.assertTrue(r["versions"])
        # 内置清单必须 LTS 优先,且双平台直链齐全
        self.assertTrue(r["versions"][0]["lts"])
        for v in r["versions"]:
            self.assertTrue(v["win_url"].startswith("https://cdn.mysql.com/"))
            self.assertTrue(v["linux_url"].startswith("https://cdn.mysql.com/"))

    def test_fetch_success_then_cached(self):
        fake = [{"version": "9.7.2", "lts": True, "win_url": "https://x/win.zip",
                 "linux_url": "https://x/lin.tar.xz"}]
        with mock.patch.object(mysql_installer, "_fetch_official_versions",
                               return_value=fake):
            r1 = mysql_installer.list_official_versions(force=True)
            self.assertEqual(r1["source"], "official-api")
            r2 = mysql_installer.list_official_versions()
            self.assertEqual(r2["source"], "cache")
            self.assertEqual(r2["versions"][0]["version"], "9.7.2")


class SortVersionsTest(unittest.TestCase):
    """展示排序: LTS 优先,同组版本号新→旧。"""

    def test_order(self):
        out = mysql_installer._sort_versions([
            {"version": "8.0.45", "lts": False},
            {"version": "9.6.1", "lts": False},
            {"version": "9.7.2", "lts": True},
            {"version": "9.2.2", "lts": False},
        ])
        self.assertEqual([v["version"] for v in out],
                         ["9.7.2", "9.6.1", "9.2.2", "8.0.45"])


class ValidateInstallCfgTest(unittest.TestCase):
    """安装前置校验: 快速失败信息必须可读。"""

    GB = mysql_installer._GB

    def _good(self, **over):
        import tempfile
        cfg = {"version": "8.0.45",
               "basedir": os.path.join(self._td.name, "mysql"),
               "datadir": os.path.join(self._td.name, "mysql", "data"),
               "port": 3307, "root_password": "secret66"}
        cfg.update(over)
        return cfg

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)

    def test_bad_version(self):
        with self.assertRaises(mysql_installer._InstallAbort) as cm:
            mysql_installer._validate_install_cfg(self._good(version="abc"))
        self.assertIn("版本号", str(cm.exception))

    def test_short_password(self):
        with self.assertRaises(mysql_installer._InstallAbort) as cm:
            mysql_installer._validate_install_cfg(self._good(root_password="123"))
        self.assertIn("6 位", str(cm.exception))

    def test_same_dirs(self):
        d = os.path.join(self._td.name, "m")
        with self.assertRaises(mysql_installer._InstallAbort) as cm:
            mysql_installer._validate_install_cfg(self._good(basedir=d, datadir=d))
        self.assertIn("相同", str(cm.exception))

    def test_nonempty_datadir_requires_force(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "old.ibd"), "w") as f:
                f.write("x")
            base = os.path.join(self._td.name, "b")
            with self.assertRaises(mysql_installer._InstallAbort) as cm:
                mysql_installer._validate_install_cfg(self._good(basedir=base, datadir=d))
            self.assertIn("确认", str(cm.exception))
            # 显式 force 放行(破坏性操作用户已确认)
            out = mysql_installer._validate_install_cfg(
                self._good(basedir=base, datadir=d, force=True))
            self.assertEqual(out[0], "8.0.45")

    def test_port_occupied(self):
        srv = _TcpServer().start()
        try:
            with self.assertRaises(mysql_installer._InstallAbort) as cm:
                mysql_installer._validate_install_cfg(self._good(port=srv.port))
            self.assertIn("占用", str(cm.exception))
        finally:
            srv.close()

    def test_happy_path(self):
        out = mysql_installer._validate_install_cfg(self._good())
        self.assertEqual(out[0], "8.0.45")
        self.assertEqual(out[3], 3307)


class DownloadPackageTest(unittest.TestCase):
    """多镜像下载: 用 file:// 本地 URL 模拟,验证坏源切换与过小内容拒绝。"""

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        # 放宽下限以便用小文件测试
        p = mock.patch.object(mysql_installer, "_INSTALL_MIN_BYTES", 100)
        p.start()
        self.addCleanup(p.stop)

    def _local_url(self, name, size=1024):
        p = os.path.join(self._td.name, name)
        with open(p, "wb") as f:
            f.write(b"M" * size)
        return "file://" + p

    def test_failover_to_second_mirror(self):
        dest = os.path.join(self._td.name, "out.bin")
        urls = ["file://" + os.path.join(self._td.name, "no_such.bin"),
                self._local_url("ok.bin")]
        state, lock, cfg = {}, threading.Lock(), {}
        out = mysql_installer._download_package(urls, dest, state, lock, cfg)
        self.assertEqual(out, dest)
        self.assertEqual(os.path.getsize(dest), 1024)

    def test_too_small_content_rejected(self):
        dest = os.path.join(self._td.name, "small.bin")
        urls = [self._local_url("tiny.bin", size=10)]
        with self.assertRaises(mysql_installer._InstallAbort):
            mysql_installer._download_package(urls, dest, {}, threading.Lock(), {})

    def test_all_failed(self):
        dest = os.path.join(self._td.name, "never.bin")
        with self.assertRaises(mysql_installer._InstallAbort) as cm:
            mysql_installer._download_package(["file:///no/such1", "file:///no/such2"],
                                              dest, {}, threading.Lock(), {})
        self.assertIn("所有下载源", str(cm.exception))


class SafeExtractTest(unittest.TestCase):
    """解压安全: zip/tar 的路径穿越条目必须中止且不落盘。"""

    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)

    def _make_zip(self, names):
        import zipfile
        p = os.path.join(self._td.name, "t.zip")
        with zipfile.ZipFile(p, "w") as z:
            for n in names:
                z.writestr(n, "data")
        return p

    def _make_tar(self, names):
        import tarfile
        p = os.path.join(self._td.name, "t.tar.xz")
        with tarfile.open(p, "w:xz") as t:
            for n in names:
                info = tarfile.TarInfo(n)
                info.size = 4
                import io
                t.addfile(info, io.BytesIO(b"data"))
        return p

    def test_zip_ok(self):
        staging = os.path.join(self._td.name, "s1")
        mysql_installer._safe_zip_extract(self._make_zip(["mysql-8.0.45/bin/mysqld"]), staging)
        self.assertTrue(os.path.isdir(os.path.join(staging, "mysql-8.0.45", "bin")))

    def test_zip_slip_aborts(self):
        staging = os.path.join(self._td.name, "s2")
        with self.assertRaises(mysql_installer._InstallAbort):
            mysql_installer._safe_zip_extract(self._make_zip(["../evil.txt"]), staging)
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "evil.txt")))

    def test_tar_ok(self):
        staging = os.path.join(self._td.name, "s3")
        mysql_installer._safe_tar_extract(self._make_tar(["mysql-8.0.45/bin/", "mysql-8.0.45/a.txt"]), staging)
        self.assertTrue(os.path.isdir(os.path.join(staging, "mysql-8.0.45")))

    def test_tar_slip_aborts(self):
        staging = os.path.join(self._td.name, "s4")
        with self.assertRaises(mysql_installer._InstallAbort):
            mysql_installer._safe_tar_extract(self._make_tar(["x/../../evil.txt"]), staging)
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "evil.txt")))

    def _make_tar_members(self, members):
        """members: (name, linkname, kind) 三元组,kind ∈ sym|hard|file。"""
        import io
        import tarfile
        p = os.path.join(self._td.name, "t2.tar.xz")
        with tarfile.open(p, "w:xz") as t:
            for name, linkname, kind in members:
                if kind == "file":
                    info = tarfile.TarInfo(name)
                    info.size = 4
                    t.addfile(info, io.BytesIO(b"data"))
                    continue
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = linkname
                t.addfile(info)
        return p

    def test_tar_internal_symlink_allowed(self):
        # 官方包场景: lib/libmysqlclient.so -> libmysqlclient.so.22 (相对、目录内)
        staging = os.path.join(self._td.name, "s5")
        archive = self._make_tar_members([
            ("mysql-x/lib/libmysqlclient.so.22", "", "file"),
            ("mysql-x/lib/libmysqlclient.so", "libmysqlclient.so.22", "sym"),
        ])
        mysql_installer._safe_tar_extract(archive, staging)
        self.assertTrue(os.path.lexists(os.path.join(staging, "mysql-x", "lib",
                                                     "libmysqlclient.so")))

    def test_tar_escaping_symlink_aborts(self):
        staging = os.path.join(self._td.name, "s6")
        archive = self._make_tar_members([
            ("mysql-x/escape", "../../../outside.txt", "sym"),
        ])
        with self.assertRaises(mysql_installer._InstallAbort):
            mysql_installer._safe_tar_extract(archive, staging)
        self.assertFalse(os.path.exists(os.path.join(self._td.name, "outside.txt")))

    def test_tar_absolute_symlink_aborts(self):
        staging = os.path.join(self._td.name, "s7")
        archive = self._make_tar_members([
            ("mysql-x/abs", "/etc/passwd", "sym"),
        ])
        with self.assertRaises(mysql_installer._InstallAbort):
            mysql_installer._safe_tar_extract(archive, staging)


class WaitPortTest(unittest.TestCase):
    """端口就绪/关闭轮询。"""

    def test_wait_port_ready(self):
        srv = _TcpServer(b"8.0.36").start()
        try:
            self.assertTrue(mysql_installer._wait_port(srv.port, 3))
        finally:
            srv.close()

    def test_wait_port_timeout(self):
        self.assertFalse(mysql_installer._wait_port(_free_port(), 1))


class InstallWorkerAbortTest(unittest.TestCase):
    """编排 worker: 校验失败同步落入 failed 状态,信息可读,绝不悬空。"""

    def test_bad_cfg_fails_fast(self):
        state = {"status": "idle", "phase": "", "percent": 0, "msg": "", "error": ""}
        lock = threading.Lock()
        mysql_installer._install_worker({"version": ""}, state, lock)
        self.assertEqual(state["status"], "failed")
        self.assertIn("版本号", state["error"])

    def test_report_updates_state(self):
        state, lock = {}, threading.Lock()
        mysql_installer._report(state, lock, {}, percent=50, phase="download", msg="x")
        self.assertEqual(state["percent"], 50)
        self.assertEqual(state["phase"], "download")


class FormatInstallReportTest(unittest.TestCase):
    """CLI 安装报告字段。"""

    def test_fields(self):
        txt = mysql_installer.format_install_report({
            "status": "done", "phase": "done", "percent": 100,
            "msg": "MySQL 8.0.45 安装完成", "warnings": ["提示A"],
            "conn": {"port": 3306}})
        self.assertIn("done", txt)
        self.assertIn("提示A", txt)
        self.assertIn("3306", txt)


class DetectLocalServerIntegrationTest(unittest.TestCase):
    """真实环境冒烟(不 mock): 只验证"可执行且结构完整",不断言具体环境有无 MySQL。"""

    def test_real_detect_structure(self):
        r = mysql_installer.detect_local_server()
        self.assertIsInstance(r["installed"], bool)
        self.assertIsInstance(r["port_open"], bool)
        self.assertEqual(r["port"], 3306)
        self.assertTrue(r["summary"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
