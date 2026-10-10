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


class TestSystemCjkDetect(unittest.TestCase):
    """只读检测：判断系统是否已有中文字体（决定要不要动系统外观）。"""

    def test_returns_bool(self):
        r = F.system_has_cjk_font()
        self.assertIsInstance(r, bool)

    def test_current_machine_has_cjk(self):
        """开发机是中文系统，应当检测到中文字体——这也意味着不会去改系统外观。"""
        if os.name != "nt":
            self.skipTest("非 Windows")
        self.assertTrue(F.system_has_cjk_font(),
                        "本机应能检测到中文字体（否则会误改系统 UI 字体）")


class TestSessionUiFontShape(unittest.TestCase):
    """只验证接口形态与常量，**不真的修改系统设置**。"""

    def test_has_apis(self):
        for name in ("install_session_ui_font", "restore_session_ui_font",
                     "ensure_ui_font", "system_has_cjk_font"):
            self.assertTrue(callable(getattr(F, name, None)), "缺少 %s" % name)

    def test_spi_constants(self):
        self.assertEqual(F.SPI_GETNONCLIENTMETRICS, 0x0029)
        self.assertEqual(F.SPI_SETNONCLIENTMETRICS, 0x002A)
        # 只广播、不写注册表持久项（不污染系统）
        self.assertFalse(F.SPIF_SENDCHANGE & F.SPIF_UPDATEINIFILE)

    def test_logfont_structure_size(self):
        """LOGFONTW 布局必须正确，否则 SPI 调用会读错内存。"""
        if os.name != "nt":
            self.skipTest("非 Windows")
        import ctypes
        # 14 个字段：5×LONG + 8×BYTE + WCHAR[32] = 20 + 8 + 64 = 92
        self.assertEqual(ctypes.sizeof(F._LOGFONTW), 92)

    def test_ensure_ui_font_skips_when_system_has_cjk(self):
        """系统已有中文字体时，不应去改会话界面字体。"""
        if os.name != "nt" or not F.system_has_cjk_font():
            self.skipTest("系统无中文字体，跳过")
        info = F.ensure_ui_font()
        self.assertFalse(info.get("session_ui"))
        self.assertIn("已有中文字体", info.get("session_ui_note", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
