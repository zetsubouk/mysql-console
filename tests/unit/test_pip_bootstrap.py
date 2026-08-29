# -*- coding: utf-8 -*-
"""pip_bootstrap 离线单元测试(2026-08-30 新增)。

纯标准库 + mock,零依赖、无网络请求:
- PEP 503 simple 索引 HTML 解析(绝对/相对链接、#sha256 片段、py2.py3 排除)
- 最新 pip 轮子挑选与相对地址补全
- get-pip 源列表顺序(官方优先)
- pip_ok 探测 subprocess mock
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

WORKSPACE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(WORKSPACE, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import pip_bootstrap as pb     # noqa: E402

_SAMPLE_HTML = """
<!DOCTYPE html><html><body>
<h1>Links for pip</h1><br/>
<a href="pip-25.2-py3-none-any.whl#sha256=aaaa">pip-25.2-py3-none-any.whl</a><br/>
<a href="../../packages/aa/bb/pip-24.0-py3-none-any.whl#sha256=bbbb">pip-24.0-py3-none-any.whl</a><br/>
<a href="https://files.example.org/cc/pip-24.3.1-py3-none-any.whl">pip-24.3.1-py3-none-any.whl</a><br/>
<a href="pip-25.2-py2.py3-none-any.whl#sha256=cccc">pip-25.2-py2.py3-none-any.whl</a><br/>
<a href="somepkg-1.0-py3-none-any.whl#sha256=dddd">somepkg-1.0-py3-none-any.whl</a><br/>
<a href="pip-25.2.tar.gz#sha256=eeee">sdist</a><br/>
</body></html>
"""


class ParseIndexTest(unittest.TestCase):
    """simple 索引 HTML 解析。"""

    def test_parse_filters_non_pip3_wheels(self):
        urls = pb.parse_pip_wheel_urls(_SAMPLE_HTML)
        bases = [os.path.basename(u) for u in urls]
        self.assertIn("pip-25.2-py3-none-any.whl", bases)
        self.assertIn("pip-24.3.1-py3-none-any.whl", bases)
        self.assertNotIn("pip-25.2-py2.py3-none-any.whl", bases)
        self.assertFalse(any("somepkg" in b for b in bases))
        self.assertFalse(any(b.endswith(".tar.gz") for b in bases))

    def test_parse_empty(self):
        self.assertEqual(pb.parse_pip_wheel_urls(""), [])
        self.assertEqual(pb.parse_pip_wheel_urls(None), [])

    def test_pick_newest(self):
        urls = pb.parse_pip_wheel_urls(_SAMPLE_HTML)
        pick = pb.pick_newest_pip_wheel(urls)
        self.assertTrue(pick.endswith("pip-25.2-py3-none-any.whl"))

    def test_pick_relative_joined(self):
        pick = pb.pick_newest_pip_wheel(["../../packages/x/pip-24.0-py3-none-any.whl"])
        self.assertTrue(pick.startswith("https://"))
        self.assertIn("pypi.tuna.tsinghua.edu.cn", pick)

    def test_pick_none(self):
        self.assertIsNone(pb.pick_newest_pip_wheel([]))
        self.assertIsNone(pb.pick_newest_pip_wheel(["garbage.whl"]))


class UrlsOrderTest(unittest.TestCase):
    """get-pip 源顺序:官方优先。"""

    def test_order(self):
        self.assertIn("bootstrap.pypa.io", pb.GET_PIP_URLS[0])
        self.assertTrue(all(u.startswith("https://") for u in pb.GET_PIP_URLS))
        self.assertTrue(pb.TUNA_PIP_INDEX.startswith("https://"))


class PipOkTest(unittest.TestCase):
    """pip 可用性探测(mock subprocess)。"""

    def test_ok(self):
        p = mock.Mock(returncode=0, stdout=b"pip 25.2", stderr=b"")
        with mock.patch.object(subprocess, "run", return_value=p):
            self.assertTrue(pb.pip_ok("py"))

    def test_fail(self):
        p = mock.Mock(returncode=1, stdout=b"", stderr=b"No module named pip")
        with mock.patch.object(subprocess, "run", return_value=p):
            self.assertFalse(pb.pip_ok("py"))

    def test_exception(self):
        with mock.patch.object(subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("py", 1)):
            self.assertFalse(pb.pip_ok("py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
