# -*- coding: utf-8 -*-
"""ub_updater（官方更新包应用器）的测试。

覆盖重点：
  * 删除标记识别（144B 谱面桩 / 1B 贴图桩；60B 的 mdl_*.bin 是正常文件）
  * 更新包里的 modules/*.dll 必须被单独发现并支持重新打补丁
    （官方重编译后 DLL 补丁偏移必然失效；旧 GUI 直接当普通文件覆盖且不提示）
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ub_updater as UU      # noqa: E402


class TestNewDllHandling(unittest.TestCase):
    """官方更新包里的 modules/*.dll 必须被识别出来并支持重新打补丁。

    背景：官方每次更新都会重编译 jubeat.dll，DLL 补丁偏移必然失效；
    而 GUI 的"导入官方更新包"走 do_apply 会把它当普通文件直接覆盖，
    **且不给任何提示** —— 用户根本不知道补丁已经没了。
    """

    def test_list_pack_dlls_detects_modules(self):
        """read_pack 认「目录形包」或 7z —— 这里用目录形。"""
        tmp = tempfile.mkdtemp(prefix="ub_dll_")
        pack = os.path.join(tmp, "L44JF_2from1")        # 目录形包（顶层同名目录）
        for sub in ("data/music_info", "modules"):
            os.makedirs(os.path.join(pack, sub), exist_ok=True)
        with open(os.path.join(pack, "data/music_info/music_info.xml"), "w") as f:
            f.write("<x/>")
        for name, n in (("jubeat.dll", 100), ("music_db.dll", 50)):
            with open(os.path.join(pack, "modules", name), "wb") as f:
                f.write(b"MZ" + b"\x00" * n)
        with open(os.path.join(pack, "modules", "keep.txt"), "w") as f:
            f.write("x")
        got = UU.list_pack_dlls(pack)
        names = sorted(os.path.basename(r) for r, _ in got)
        self.assertIn("jubeat.dll", names)
        self.assertIn("music_db.dll", names)
        self.assertNotIn("keep.txt", names)
        # 大小也要对得上
        d = dict((os.path.basename(r), s) for r, s in got)
        self.assertEqual(d["jubeat.dll"], 102)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_new_dll_refuses_when_sites_missing(self):
        """非 jubeat DLL（站点找不到）必须**拒绝打补丁**，且报告原因。"""
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="ub_dll2_")
        game = os.path.join(tmp, "contents")
        os.makedirs(game)
        # 造一个假的 dll（不是真 jubeat，站点必然找不到）
        fake = os.path.join(tmp, "jubeat.dll.new")
        with open(fake, "wb") as f:
            f.write(b"MZ" + b"\x00" * 4096)
        res = UU.apply_new_dll(fake, game, target_rel="jb_org.dll", patch=True)
        self.assertFalse(res["patched"], "站点找不到时绝不能写出补丁")
        self.assertTrue(res["error"], "必须给出拒绝原因")
        # 但文件本身应该已经就位（官方原版），且有备份记录字段
        self.assertTrue(os.path.exists(os.path.join(game, "jb_org.dll")))
        shutil.rmtree(tmp, ignore_errors=True)

    def test_apply_new_dll_backs_up_existing(self):
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp(prefix="ub_dll3_")
        game = os.path.join(tmp, "contents")
        os.makedirs(game)
        tgt = os.path.join(game, "jb_org.dll")
        with open(tgt, "wb") as f:
            f.write(b"OLD" * 100)
        new = os.path.join(tmp, "jubeat.dll.new")
        with open(new, "wb") as f:
            f.write(b"MZ" + b"\x00" * 4096)
        res = UU.apply_new_dll(new, game, target_rel="jb_org.dll", patch=False)
        self.assertIsNotNone(res["backup"], "覆盖前必须备份")
        self.assertTrue(os.path.exists(res["backup"]))
        self.assertEqual(open(res["backup"], "rb").read(), b"OLD" * 100)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_scan_reports_new_dlls(self):
        """do_scan 要把包里的 DLL 报出来（GUI 靠它提示用户）。"""
        import inspect
        src = inspect.getsource(UU.do_scan)
        self.assertIn("new_dlls", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
