# -*- coding: utf-8 -*-
"""自带字库模块测试（不依赖目标机是否装中文字体）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ub_font as F                                  # noqa: E402


class TestFontAsset(unittest.TestCase):
    def test_font_file_exists(self):
        p = F.font_path()
        self.assertIsNotNone(p, "找不到字体文件 %s" % F.FONT_FILENAME)
        self.assertTrue(os.path.isfile(p))
        self.assertGreater(os.path.getsize(p), 1000000, "字体体积异常（可能被截断）")

    def test_is_otf_or_ttf(self):
        with open(F.font_path(), "rb") as f:
            head = f.read(4)
        self.assertIn(head, (b"OTTO", b"\x00\x01\x00\x00", b"true"),
                      "字体文件头不对: %r" % head)


class TestRegister(unittest.TestCase):
    def test_register_on_windows(self):
        fam = F.register()
        if os.name != "nt":
            self.skipTest("非 Windows")
        self.assertIsNotNone(fam, "字体注册失败（AddFontResourceEx 返回 0）")
        self.assertEqual(fam, F.FONT_FAMILY)
        self.assertTrue(F.available())
        self.assertEqual(F.family(), F.FONT_FAMILY)

    def test_register_is_idempotent(self):
        a = F.register()
        b = F.register()
        self.assertEqual(a, b)

    def test_tk_font_renders_cjk(self):
        """关键验收：自带字体能真正渲染出简繁日字形（宽度 > 0）。"""
        if os.name != "nt":
            self.skipTest("非 Windows")
        if not F.register():
            self.skipTest("字体不可用")
        import tkinter
        import tkinter.font as tkfont
        try:
            r = tkinter.Tk()
        except Exception:                                    # noqa: BLE001
            self.skipTest("无显示环境")
        try:
            r.withdraw()
            f = tkfont.Font(root=r, family=F.FONT_FAMILY, size=14)
            # 简体专有字 + 日文假名/汉字，覆盖两类系统的缺口
            for ch in "谱铺载设删转检查确认装导更新包":
                self.assertGreater(f.measure(ch), 0, "缺字形: %s" % ch)
            for ch in "の漢あいうえお曲名":
                self.assertGreater(f.measure(ch), 0, "缺字形: %s" % ch)
        finally:
            r.destroy()

    def test_apply_to_tk_switches_default_font(self):
        if os.name != "nt":
            self.skipTest("非 Windows")
        if not F.register():
            self.skipTest("字体不可用")
        import tkinter
        import tkinter.font as tkfont
        try:
            r = tkinter.Tk()
        except Exception:                                    # noqa: BLE001
            self.skipTest("无显示环境")
        try:
            r.withdraw()
            ok = F.apply_to_tk(r)
            self.assertTrue(ok)
            got = tkfont.nametofont("TkDefaultFont", root=r).actual("family")
            self.assertEqual(got, F.FONT_FAMILY,
                             "TkDefaultFont 未切换到自带字体（实际 %s）" % got)
        finally:
            r.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
