# -*- coding: utf-8 -*-
"""本地导入模块测试（全离线）。"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ub_import as I                                # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ubimp_")
        self.data = os.path.join(self.root, "data")
        os.makedirs(self.data, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def touch(self, *parts, size=16):
        p = os.path.join(self.root, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"0" * size)
        return p


class TestImportDir(Base):
    def test_creates_dir_and_readme(self):
        d = I.import_dir(self.data)
        self.assertTrue(os.path.isdir(d))
        readme = [f for f in os.listdir(d) if f.endswith(".txt")]
        self.assertEqual(len(readme), 1)

    def test_idempotent(self):
        a = I.import_dir(self.data)
        b = I.import_dir(self.data)
        self.assertEqual(a, b)


class TestFind(Base):
    def test_find_charts_flat(self):
        self.touch("c", "a.mc")
        self.touch("c", "b.mcz")
        self.touch("c", "cover.jpg")
        got = I.find_charts_in_dir(os.path.join(self.root, "c"))
        self.assertEqual(len(got), 2)

    def test_find_charts_recursive(self):
        self.touch("c", "a.mc")
        self.touch("c", "sub", "b.mc")
        flat = I.find_charts_in_dir(os.path.join(self.root, "c"))
        deep = I.find_charts_in_dir(os.path.join(self.root, "c"), recursive=True)
        self.assertEqual(len(flat), 1)
        self.assertEqual(len(deep), 2)

    def test_find_packs(self):
        self.touch("c", "x.7z")
        self.touch("c", "y.zip")
        self.touch("c", "z.mc")
        got = I.find_packs_in_dir(os.path.join(self.root, "c"))
        self.assertEqual(len(got), 2)


class TestClassify(Base):
    def test_mixed_inputs(self):
        mc = self.touch("c", "a.mc")
        pack = self.touch("p", "u.7z")
        self.touch("c", "audio.ogg")          # 应被忽略
        r = I.classify_paths([mc, pack])
        self.assertEqual(r["charts"], [os.path.abspath(mc)])
        self.assertEqual(r["packs"], [os.path.abspath(pack)])

    def test_directory_expands(self):
        self.touch("c", "a.mc")
        self.touch("c", "b.mc")
        self.touch("c", "u.7z")
        r = I.classify_paths([os.path.join(self.root, "c")])
        self.assertEqual(len(r["charts"]), 2)
        self.assertEqual(len(r["packs"]), 1)

    def test_empty_and_none(self):
        self.assertEqual(I.classify_paths(None),
                         {"charts": [], "packs": []})
        self.assertEqual(I.classify_paths([]),
                         {"charts": [], "packs": []})
        self.assertEqual(I.classify_paths([os.path.join(self.root, "nope")]),
                         {"charts": [], "packs": []})


class TestUnitDir(Base):
    def test_single_chart_uses_original_dir(self):
        mc = self.touch("c", "only.mc")
        self.touch("c", "song.ogg")
        unit = I.chart_unit_dir(mc, self.data)
        self.assertEqual(os.path.abspath(unit), os.path.abspath(os.path.dirname(mc)))

    def test_multiple_charts_get_staged_dir(self):
        self.touch("c", "a.mc")
        mc = self.touch("c", "b.mc")
        self.touch("c", "song.ogg")
        self.touch("c", "cover.jpg")
        unit = I.chart_unit_dir(mc, self.data)
        self.assertNotEqual(os.path.abspath(unit), os.path.abspath(os.path.dirname(mc)))
        # 谱面 + 音源 + 封面 都应被带过去，而另一个谱面不该出现
        names = set(os.listdir(unit))
        self.assertIn("b.mc", names)
        self.assertIn("song.ogg", names)
        self.assertIn("cover.jpg", names)
        self.assertNotIn("a.mc", names)

    def test_force_stage_on_single(self):
        mc = self.touch("c", "only.mc")
        unit = I.chart_unit_dir(mc, self.data, force_stage=True)
        self.assertNotEqual(os.path.abspath(unit), os.path.abspath(os.path.dirname(mc)))
        self.assertIn("only.mc", os.listdir(unit))

    def test_plan_units_flags(self):
        self.touch("c1", "a.mc")
        self.touch("c2", "x.mc")
        self.touch("c2", "y.mc")
        paths = I.find_charts_in_dir(os.path.join(self.root, "c1")) + \
            I.find_charts_in_dir(os.path.join(self.root, "c2"))
        units = I.plan_units(paths, self.data)
        self.assertEqual(len(units), 3)
        staged = [u for u in units if u["staged"]]
        self.assertEqual(len(staged), 2)          # c2 下两个谱面都需暂存


class TestCleanup(Base):
    def test_cleanup_stage(self):
        self.touch("c", "a.mc")
        self.touch("c", "b.mc")
        I.chart_unit_dir(os.path.join(self.root, "c", "b.mc"), self.data)
        self.assertTrue(os.path.isdir(I.stage_root(self.data)))
        self.assertTrue(os.listdir(I.stage_root(self.data)))
        I.cleanup_stage(self.data)
        self.assertFalse(os.path.isdir(os.path.join(self.data, I.STAGE_DIR_NAME)))


class TestSafeName(Base):
    def test_strips_illegal_chars(self):
        self.assertEqual(I._safe_name('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")

    def test_empty_becomes_chart(self):
        self.assertEqual(I._safe_name(""), "chart")
        self.assertEqual(I._safe_name("   "), "chart")

    def test_truncates(self):
        self.assertLessEqual(len(I._safe_name("x" * 200)), 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
