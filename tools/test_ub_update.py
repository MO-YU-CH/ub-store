# -*- coding: utf-8 -*-
"""自更新模块测试（离线，全部用桩，不联网）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ub_update as U                                # noqa: E402


class TestParseVersion(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(U.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(U.parse_version("1.2.3"), (1, 2, 3))
        self.assertEqual(U.parse_version("V2.0"), (2, 0))

    def test_prerelease_and_build(self):
        self.assertEqual(U.parse_version("1.2.3-beta.1"), (1, 2, 3))
        self.assertEqual(U.parse_version("1.2.3+build9"), (1, 2, 3))

    def test_garbage_is_safe(self):
        self.assertEqual(U.parse_version(""), (0,))
        self.assertEqual(U.parse_version(None), (0,))
        self.assertEqual(U.parse_version("abc"), (0,))
        self.assertEqual(U.parse_version("v1.x.3"), (1, 0, 3))


class TestIsNewer(unittest.TestCase):
    def test_simple(self):
        self.assertTrue(U.is_newer("1.0.1", "1.0.0"))
        self.assertFalse(U.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(U.is_newer("0.9.9", "1.0.0"))

    def test_different_length(self):
        self.assertTrue(U.is_newer("1.1", "1.0.9"))
        self.assertFalse(U.is_newer("1.0", "1.0.0"))
        self.assertTrue(U.is_newer("1.0.0.1", "1.0.0"))

    def test_v_prefix(self):
        self.assertTrue(U.is_newer("v2.0.0", "1.9.9"))

    def test_numeric_not_lexical(self):
        # 字符串比较会把 "1.10.0" < "1.9.0"，数值比较才对
        self.assertTrue(U.is_newer("1.10.0", "1.9.0"))


class TestPickAsset(unittest.TestCase):
    def test_by_arch(self):
        self.assertEqual(U.pick_asset_key("win32"), "win32")
        self.assertEqual(U.pick_asset_key("win64"), "win64")

    def test_current_interpreter(self):
        key = U.pick_asset_key()
        self.assertIn(key, U.ASSET_NAMES)


class TestCheck(unittest.TestCase):
    """check() 全用桩，不联网。"""

    def _patch(self, payload=None, error=None):
        def fake(url, proxy=None, timeout=20):
            if error:
                raise error
            return payload
        return mock.patch.object(U, "_get_json", side_effect=fake)

    def test_newer_available_with_asset(self):
        payload = {
            "tag_name": "v1.2.0",
            "body": "修了些 bug",
            "html_url": "https://github.com/x/y/releases/tag/v1.2.0",
            "assets": [
                {"name": "ub_store.exe",
                 "browser_download_url": "https://e/ub_store.exe"},
                {"name": "ub_store_x64.exe",
                 "browser_download_url": "https://e/ub_store_x64.exe"},
            ],
        }
        with self._patch(payload):
            r = U.check(current="1.0.0", arch="win32")
        self.assertTrue(r["ok"])
        self.assertTrue(r["newer"])
        self.assertEqual(r["latest"], "v1.2.0")
        self.assertEqual(r["asset_name"], "ub_store.exe")
        self.assertEqual(r["asset_url"], "https://e/ub_store.exe")

    def test_already_latest(self):
        with self._patch({"tag_name": "v1.0.0", "assets": []}):
            r = U.check(current="1.0.0")
        self.assertTrue(r["ok"])
        self.assertFalse(r["newer"])
        self.assertIsNone(r["asset_url"])

    def test_missing_asset_reports_error(self):
        with self._patch({"tag_name": "v9.9.9", "assets": []}):
            r = U.check(current="1.0.0", arch="win32")
        self.assertTrue(r["ok"])
        self.assertTrue(r["newer"])
        self.assertIn("没有找到资产", r["error"])

    def test_404_falls_back_then_fails(self):
        """Release 404 且仓库文件也拿不到 → 整体失败（两路都试过）"""
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with self._patch(error=err), \
                mock.patch.object(U, "_get_text", side_effect=OSError("no net")):
            r = U.check()
        self.assertFalse(r["ok"])
        self.assertIn("尚未发布", r["error"])

    def test_network_error_falls_back_then_fails(self):
        with self._patch(error=OSError("boom")), \
                mock.patch.object(U, "_get_text", side_effect=OSError("boom")):
            r = U.check()
        self.assertFalse(r["ok"])
        self.assertIn("OSError", r["error"])

    # ---- 回退路径（Release 不可用时读仓库文件）----
    def test_falls_back_to_repo_version(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with self._patch(error=err), \
                mock.patch.object(U, "_get_text", return_value="1.2.0\n"), \
                mock.patch.object(U, "_opener") as op:
            op.return_value.open.return_value.close.return_value = None
            r = U.check(current="1.0.0", arch="win32")
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "repo")
        self.assertEqual(r["latest"], "1.2.0")
        self.assertTrue(r["newer"])
        self.assertIn("release/ub_store.exe", r["asset_url"])

    def test_fallback_same_version_not_newer(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with self._patch(error=err), \
                mock.patch.object(U, "_get_text", return_value="1.0.0"), \
                mock.patch.object(U, "_opener"):
            r = U.check(current="1.0.0")
        self.assertTrue(r["ok"])
        self.assertFalse(r["newer"])

    def test_fallback_empty_version_is_error(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with self._patch(error=err), \
                mock.patch.object(U, "_get_text", return_value="  \n"):
            r = U.check()
        self.assertFalse(r["ok"])

    def test_release_wins_over_repo(self):
        """Release 可用时不走回退。"""
        payload = {"tag_name": "v2.0.0", "body": "x", "html_url": "h",
                   "assets": [{"name": "ub_store.exe",
                               "browser_download_url": "https://e/a.exe"}]}
        with self._patch(payload), \
                mock.patch.object(U, "_get_text") as gt:
            r = U.check(current="1.0.0", arch="win32")
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "release")
        gt.assert_not_called()


class TestReplaceSelf(unittest.TestCase):
    #: 一个"最小合法"的 PyInstaller 单文件 exe 假体：开头 MZ + 末尾 88 字节 CArchive
    #: cookie（以 PYI_MAGIC 开头）。replace_self() 现在会校验这个尾巴
    #: （见 verify_pyinstaller_exe），所以夹具不能再拿 3 字节的 b"NEW" 糊弄。
    NEW_EXE = (b"MZ" + b"P" * 30
               + U.PYI_MAGIC + b"\0" * (U.PYI_COOKIE_SIZE - len(U.PYI_MAGIC)))

    def setUp(self):
        import tempfile
        self.d = tempfile.mkdtemp(prefix="ubupd_")
        self.exe = os.path.join(self.d, "app.exe")
        self.new = os.path.join(self.d, "app.new")
        with open(self.exe, "wb") as f:
            f.write(b"OLD")
        with open(self.new, "wb") as f:
            f.write(self.NEW_EXE)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_replace_no_restart(self):
        U.replace_self(self.new, restart=False, exe=self.exe)
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), self.NEW_EXE)
        self.assertTrue(os.path.exists(self.exe + U.OLD_SUFFIX))

    def test_rejects_truncated_new_exe(self):
        # 下载被截断（缺末尾 cookie）时必须拒绝，且**原 exe 一个字都不能动**
        trunc = os.path.join(self.d, "app.trunc.exe")
        with open(trunc, "wb") as f:
            f.write(self.NEW_EXE[:30])                      # 砍掉整条尾巴
        with self.assertRaises(U.UpdateError):
            U.replace_self(trunc, restart=False, exe=self.exe)
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"OLD")              # 内容没变
        self.assertFalse(os.path.exists(self.exe + U.OLD_SUFFIX))   # 也没留 .old

    def test_cleanup_old(self):
        U.replace_self(self.new, restart=False, exe=self.exe)
        old = self.exe + U.OLD_SUFFIX
        self.assertTrue(os.path.exists(old))
        self.assertTrue(U.cleanup_old(self.exe))
        self.assertFalse(os.path.exists(old))

    def test_missing_source_raises(self):
        with self.assertRaises(U.UpdateError):
            U.replace_self(os.path.join(self.d, "nope.exe"),
                           restart=False, exe=self.exe)

    def test_source_mode_raises(self):
        with mock.patch.object(U, "current_exe", return_value=None):
            with self.assertRaises(U.UpdateError):
                U.replace_self(self.new, restart=False)

    def test_rollback_on_copy_failure(self):
        # 拷贝失败时必须把原名改回去，不能把程序弄丢
        with mock.patch.object(U.shutil, "copy2",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                U.replace_self(self.new, restart=False, exe=self.exe)
        self.assertTrue(os.path.exists(self.exe))
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"OLD")


class TestDownload(unittest.TestCase):
    def test_download_writes_file(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="ubdl_")
        dest = os.path.join(d, "x.bin")

        class FakeResp:
            headers = {"Content-Length": "5"}

            def read(self, n=-1):
                if getattr(self, "_done", False):
                    return b""
                self._done = True
                return b"HELLO"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(U, "_opener") as m:
            m.return_value.open.return_value = FakeResp()
            U.download("https://e/x.bin", dest)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"HELLO")
        self.assertFalse(os.path.exists(dest + ".part"))
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_download_rejects_short_body(self):
        # 服务端谎报 Content-Length（实际少发）时必须报错，且**不留半截文件**。
        # 2026-10-16 实机砖掉就是这个原因：下载被截断在正好 40 MiB，
        # 旧代码却当成下载成功，把半截 exe 替换成了主程序
        # （启动报 "Cannot open PyInstaller archive"）。
        import tempfile
        d = tempfile.mkdtemp(prefix="ubdl_")
        dest = os.path.join(d, "x.bin")

        class ShortResp:
            headers = {"Content-Length": "5"}

            def read(self, n=-1):
                if getattr(self, "_done", False):
                    return b""
                self._done = True
                return b"HEL"                              # 只给 3 字节

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(U, "_opener") as m:
            m.return_value.open.return_value = ShortResp()
            with self.assertRaises(U.UpdateError):
                U.download("https://e/x.bin", dest)
        self.assertFalse(os.path.exists(dest))
        self.assertFalse(os.path.exists(dest + ".part"))
        import shutil
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
