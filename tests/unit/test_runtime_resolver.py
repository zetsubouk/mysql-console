# -*- coding: utf-8 -*-
"""runtime_resolver 离线单元测试(2026-08-29 新增)。

纯标准库 + mock,零依赖、无需真实 Python 探测、无网络请求:
- 版本比较与下载源列表(官方优先/镜像兜底)
- 嵌入式 ._pth 解注(幂等/缺失/多文件)
- bundled/venv 运行时探测(按平台命名假文件)
- resolve 三级解析优先级(bundled > system > none,失败候选保留明细)
- 系统探测 subprocess mock(真实执行语义/商店占位符/损坏输出)
- 运行时缓存读写与失效
- 防穿越解压(.. 条目跳过)
- 离线装依赖命令构造(pip 轮子自启动/--no-index/--find-links/--target)

用法: python tests/unit/test_runtime_resolver.py   (纯标准库,无需 pymysql)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(WORKSPACE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import runtime_resolver as rr     # noqa: E402

_WIN = rr.IS_WIN


def _fake_exe(dirpath, kind="python"):
    """按平台造一个"假解释器"文件(仅测路径解析,不会被执行)。"""
    os.makedirs(dirpath, exist_ok=True)
    if _WIN:
        p = os.path.join(dirpath, "python.exe")
    elif kind == "venv":
        p = os.path.join(dirpath, "python")
    else:
        p = os.path.join(dirpath, "bin", "python3")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"dummy")
    return p


class VersionTest(unittest.TestCase):
    """版本比较与下载源。"""

    def test_version_satisfies(self):
        self.assertTrue(rr.version_satisfies((3, 10, 0)))
        self.assertTrue(rr.version_satisfies((3, 13, 1)))
        self.assertFalse(rr.version_satisfies((3, 9, 7)))
        self.assertFalse(rr.version_satisfies((2, 7, 18)))
        self.assertFalse(rr.version_satisfies(None))

    def test_embed_urls_order(self):
        urls = rr._embed_urls()
        self.assertEqual(len(urls), 3)
        self.assertIn("www.python.org", urls[0])
        self.assertIn("huaweicloud", urls[1])
        self.assertIn("npmmirror", urls[2])
        for u in urls:
            self.assertIn(rr.embed_zip_name(), u)

    def test_embed_zip_name(self):
        self.assertEqual(rr.embed_zip_name("3.12.10"),
                         "python-3.12.10-embed-amd64.zip")


class PpthPatchTest(unittest.TestCase):
    """._pth 解注:首次/幂等/缺失。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mc_ppth_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, name="python312._pth", content="python312.zip\n.\n#import site\n"):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="ascii") as f:
            f.write(content)
        return p

    def test_first_patch(self):
        p = self._write()
        self.assertTrue(rr.patch_embedded_ppth(self.tmp))
        with open(p, encoding="ascii") as f:
            self.assertIn("\nimport site", f.read())

    def test_idempotent(self):
        p = self._write(content="python312.zip\n.\nimport site\n")
        self.assertTrue(rr.patch_embedded_ppth(self.tmp))
        with open(p, encoding="ascii") as f:
            self.assertEqual(f.read(), "python312.zip\n.\nimport site\n")

    def test_missing(self):
        self.assertFalse(rr.patch_embedded_ppth(self.tmp))


class BundledTest(unittest.TestCase):
    """bundled/venv 假文件探测。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mc_rt_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_bundled_found(self):
        exe = _fake_exe(os.path.join(self.tmp, "runtime", "python"))
        self.assertEqual(rr.bundled_runtime(self.tmp), exe)

    def test_bundled_missing(self):
        self.assertIsNone(rr.bundled_runtime(self.tmp))

    def test_venv_found(self):
        if _WIN:
            exe = _fake_exe(os.path.join(self.tmp, ".venv", "Scripts"))
        else:
            exe = _fake_exe(os.path.join(self.tmp, ".venv", "bin"), kind="venv")
        self.assertEqual(rr.venv_runtime(self.tmp), exe)


class ResolveTest(unittest.TestCase):
    """resolve 三级优先级。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mc_rs_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_bundled_wins(self):
        exe = _fake_exe(os.path.join(self.tmp, "runtime", "python"))
        with mock.patch.object(rr, "_probe_one", return_value=(3, 12, 10)):
            r = rr.resolve(self.tmp)
        self.assertEqual(r["source"], "bundled")
        self.assertEqual(r["exe"], exe)
        self.assertTrue(r["satisfies"])

    def test_system_fallback(self):
        with mock.patch.object(rr, "probe_system_pythons", return_value=[
                {"cmd": ["python"], "version": (3, 11, 4), "satisfies": True}]):
            r = rr.resolve(self.tmp)
        self.assertEqual(r["source"], "system")
        self.assertEqual(r["cmd"], ["python"])
        self.assertTrue(r["satisfies"])

    def test_none_keeps_candidates(self):
        with mock.patch.object(rr, "probe_system_pythons", return_value=[
                {"cmd": ["py", "-3"], "version": (3, 9, 7), "satisfies": False},
                {"cmd": ["python"], "version": None, "satisfies": False}]):
            r = rr.resolve(self.tmp)
        self.assertEqual(r["source"], "none")
        self.assertFalse(r["satisfies"])
        self.assertEqual(len(r["candidates"]), 2)
        self.assertEqual(r["candidates"][0]["version"], (3, 9, 7))


class ProbeTest(unittest.TestCase):
    """系统探测:subprocess 真实执行语义(含失败/乱输出/商店占位符)。"""

    def _run_mock(self, stdout, returncode=0):
        p = mock.Mock(returncode=returncode,
                      stdout=stdout, stderr=b"")
        return mock.patch.object(subprocess, "run", return_value=p)

    def test_good_version(self):
        with self._run_mock(b"3.11.4\n"):
            cands = rr.probe_system_pythons()
        self.assertEqual(cands[0]["version"], (3, 11, 4))
        self.assertTrue(cands[0]["satisfies"])

    def test_store_stub_no_output(self):
        # Windows 商店占位符: stdout 空 + 非零退出码 → 视为不可用
        with self._run_mock(b"", returncode=9009):
            cands = rr.probe_system_pythons()
        self.assertIsNone(cands[0]["version"])
        self.assertFalse(cands[0]["satisfies"])

    def test_garbage_output(self):
        with self._run_mock(b"Python was not found; run without arguments\n"):
            cands = rr.probe_system_pythons()
        # 无版本号文本 → 全部解析失败视为 None(即使 returncode==0 也不误判)
        for c in cands:
            if c["version"] is not None:
                self.fail("纯文本不应被解析出版本: %r" % c)

    def test_timeout_exception(self):
        with mock.patch.object(subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("py", 1)):
            cands = rr.probe_system_pythons()
        self.assertTrue(all(c["version"] is None for c in cands))


class CacheTest(unittest.TestCase):
    """运行时缓存读写与失效。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mc_ca_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.exe = _fake_exe(os.path.join(self.tmp, "runtime", "python"))

    def test_roundtrip(self):
        rr.write_runtime_cache(self.tmp, self.exe)
        self.assertEqual(rr.read_runtime_cache(self.tmp),
                         os.path.abspath(self.exe))

    def test_stale_path(self):
        rr.write_runtime_cache(self.tmp, self.exe)
        os.remove(self.exe)
        self.assertIsNone(rr.read_runtime_cache(self.tmp))

    def test_rejects_relative(self):
        p = os.path.join(self.tmp, "runtime", "resolved_python.txt")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("py -3\n")
        self.assertIsNone(rr.read_runtime_cache(self.tmp))


class SafeExtractTest(unittest.TestCase):
    """防路径穿越解压。"""

    def test_skips_dotdot(self):
        tmp = tempfile.mkdtemp(prefix="mc_ex_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        zpath = os.path.join(tmp, "e.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("python.exe", b"ok")
            zf.writestr("../evil.txt", b"bad")
            zf.writestr("Lib/site.txt", b"ok2")
        dest = os.path.join(tmp, "out")
        with zipfile.ZipFile(zpath) as zf:
            rr._safe_extract(zf, dest)
        self.assertTrue(os.path.isfile(os.path.join(dest, "python.exe")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "Lib", "site.txt")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(dest), "evil.txt")))


class InstallDepsTest(unittest.TestCase):
    """离线装依赖命令构造(pip 轮子自启动)。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mc_dp_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.wheels = os.path.join(self.tmp, "wheels")
        os.makedirs(self.wheels)
        for name in ("pip-24.0-py3-none-any.whl", "pymysql-1.1.1-py3-none-any.whl"):
            with open(os.path.join(self.wheels, name), "wb") as f:
                f.write(b"PK")

    def test_find_pip_wheel(self):
        self.assertEqual(os.path.basename(rr.find_pip_wheel(self.wheels)),
                         "pip-24.0-py3-none-any.whl")

    def test_no_pip_wheel(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertIsNone(rr.find_pip_wheel(empty))
        r = rr.install_deps_offline(["py"], empty, "req.txt")
        self.assertFalse(r["ok"])

    def test_offline_cmd_shape(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return mock.Mock(returncode=0, stdout=b"done", stderr=b"")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            r = rr.install_deps_offline(["C:/rt/python.exe"], self.wheels,
                                        "requirements.txt",
                                        target="C:/rt/Lib/site-packages")
        self.assertTrue(r["ok"])
        cmd = captured["cmd"]
        self.assertIn("--no-index", cmd)
        self.assertIn("--find-links", cmd)
        self.assertIn(self.wheels, cmd)
        self.assertIn("--target", cmd)
        self.assertTrue(cmd[1].endswith(os.path.join("pip-24.0-py3-none-any.whl", "pip")))

    def test_online_cmd_mirror(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            rr.install_deps_online(["py"], "req.txt", mirror=rr.PYPI_MIRROR_TSINGHUA)
        self.assertIn(rr.PYPI_MIRROR_TSINGHUA, captured["cmd"])


class DownloadEmbedTest(unittest.TestCase):
    """嵌入式运行时就位:本地 zip 路径(mock 掉网络)。"""

    def test_local_zip_flow(self):
        tmp = tempfile.mkdtemp(prefix="mc_dl_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        # 构造一个足够大的合法嵌入式包(> EMBED_MIN_BYTES 才放行)。
        # 布局随平台:bundled_runtime 在 Windows 找 python.exe,其余平台找 bin/python3。
        zpath = os.path.join(tmp, "embed.zip")
        pad = b"0" * (rr.EMBED_MIN_BYTES + 1024)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:
            if rr.IS_WIN:
                zf.writestr("python.exe", b"MZ fake")
            else:
                zf.writestr("bin/python3", b"#!/bin/sh\n")
            zf.writestr("python312._pth", "python312.zip\n.\n#import site\n")
            zf.writestr("pad.bin", pad)
        r = rr.download_embedded(tmp, log=lambda *a: None, zip_source=zpath)
        self.assertTrue(r["ok"], r["error"])
        self.assertEqual(r["exe"], rr.bundled_runtime(tmp))
        pth = os.path.join(tmp, "runtime", "python", "python312._pth")
        with open(pth, encoding="ascii") as f:
            self.assertIn("import site", f.read())
        self.assertFalse(os.path.exists(os.path.join(tmp, "runtime", "_embed.zip")))

    def test_missing_local_zip(self):
        tmp = tempfile.mkdtemp(prefix="mc_dl2_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        r = rr.download_embedded(tmp, log=lambda *a: None,
                                 zip_source=os.path.join(tmp, "nope.zip"))
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
