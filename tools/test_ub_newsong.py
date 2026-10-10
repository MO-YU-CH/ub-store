# -*- coding: utf-8 -*-
"""自制谱「难度分档」规则测试（纯离线，不需要游戏目录）。

规则（用户 2026-10-10 定）：
  · 只有 1 个难度 -> 三档同谱（bsc / adv / ext 都写它）
  · 有多个难度   -> 不三档同谱：从**难到易**依次填 ext -> adv -> bsc
                    （依据难度名 meta.version；名字认不出时用音符数，少=易）
  · 档位不足      -> 缺的档复用已填的最易那份
  · 超过 3 档     -> 取最难的 3 个
  · 定数（level） -> 固定 1
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ub_newsong as N       # noqa: E402
import ub_chart as UC        # noqa: E402


# --------------------------------------------------------------------------
# 桩
# --------------------------------------------------------------------------
class FakeChart(object):
    def __init__(self, version, notes, title="T"):
        self.version = version
        self.notes = list(range(notes))
        self.title = title
        self.artist = "A"
        self.audio_hint = ""
        self.preview_ms = 0
        self.bpm_at_beat = []


class _Stats(object):
    def __init__(self, notes):
        self.notes = notes

    def as_dict(self):
        return {"notes_in": self.notes}


class _Member(object):
    def __init__(self, name):
        self.name = name
        self.data = b""


class _FakeIfs(object):
    """模拟真实容器：谱面成员固定为 ext.eve / adv.eve / bsc.eve。"""

    def __init__(self, path=None):
        self.path = path
        self._m = [_Member(n) for n in ("ext.eve", "adv.eve", "bsc.eve")]

    def chart_members(self):
        return self._m

    def audio_members(self):
        return []

    def to_bytes(self):
        return b"FAKEIFS"


# --------------------------------------------------------------------------
# 分类
# --------------------------------------------------------------------------
class TestClassify(unittest.TestCase):
    def test_known_names(self):
        for name in ("BSC", "Basic", "basic", "Easy", "Beginner", "Novice"):
            self.assertEqual(N.classify_slot(FakeChart(name, 10)), "bsc", name)
        for name in ("ADV", "Advanced", "advance", "Hard", "Normal", "Medium"):
            self.assertEqual(N.classify_slot(FakeChart(name, 10)), "adv", name)
        for name in ("EXT", "Extreme", "Expert", "Challenge", "Another",
                     "Insane", "EXT LV.30", "Extreme LV.12"):
            self.assertEqual(N.classify_slot(FakeChart(name, 10)), "ext", name)

    def test_unknown_names(self):
        for name in ("", "contest map 4", "DRG", "my chart", "3"):
            self.assertIsNone(N.classify_slot(FakeChart(name, 10)), name)

    def test_slot_of_member(self):
        self.assertEqual(N.slot_of_member("ext.eve"), "ext")
        self.assertEqual(N.slot_of_member("ADV.EVE"), "adv")
        self.assertEqual(N.slot_of_member("bsc.eve"), "bsc")
        self.assertIsNone(N.slot_of_member("song.bin"))
        self.assertIsNone(N.slot_of_member(""))


# --------------------------------------------------------------------------
# 分派
# --------------------------------------------------------------------------
class TestPlanSlotMap(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(N.plan_slot_map([]), {})

    def test_single_difficulty_is_all_three(self):
        m = N.plan_slot_map([FakeChart("EXT LV.30", 800)])
        self.assertEqual(m, {"bsc": 0, "adv": 0, "ext": 0})

    def test_three_named_order_independent(self):
        a = [FakeChart("BSC", 200), FakeChart("ADV", 500), FakeChart("EXT", 800)]
        b = [FakeChart("EXT", 800), FakeChart("BSC", 200), FakeChart("ADV", 500)]
        want = {"ext": 2, "adv": 1, "bsc": 0}
        self.assertEqual(N.plan_slot_map(a), want)
        self.assertEqual(N.plan_slot_map(b), {"ext": 0, "adv": 2, "bsc": 1})

    def test_unnamed_falls_back_to_note_count(self):
        charts = [FakeChart("contest map 1", 800),
                  FakeChart("contest map 2", 200),
                  FakeChart("contest map 3", 500)]
        m = N.plan_slot_map(charts)
        self.assertEqual(charts[m["bsc"]].notes.__len__(), 200)   # 最少 = bsc
        self.assertEqual(charts[m["adv"]].notes.__len__(), 500)
        self.assertEqual(charts[m["ext"]].notes.__len__(), 800)   # 最多 = ext

    def test_two_difficulties_reuses_easiest(self):
        charts = [FakeChart("ADV", 400), FakeChart("EXT", 800)]
        m = N.plan_slot_map(charts)
        self.assertEqual(m["ext"], 1)
        self.assertEqual(m["adv"], 0)
        self.assertEqual(m["bsc"], 0, "bsc 应复用最易那份")

    def test_more_than_three_takes_hardest(self):
        charts = [FakeChart("BSC", 100), FakeChart("ADV", 300),
                  FakeChart("EXT", 600), FakeChart("EXT2", 900)]
        m = N.plan_slot_map(charts)
        # 取最难的 3 个（300/600/900），最易的 100 被丢弃
        self.assertEqual(charts[m["ext"]].notes.__len__(), 900)
        self.assertEqual(charts[m["adv"]].notes.__len__(), 600)
        self.assertEqual(charts[m["bsc"]].notes.__len__(), 300)
        self.assertNotIn(100, [len(charts[m[k]].notes) for k in ("bsc", "adv", "ext")])

    def test_explicit_slot_only_that_one(self):
        charts = [FakeChart("BSC", 100), FakeChart("EXT", 800)]
        self.assertEqual(N.plan_slot_map(charts, slot="ext"), {"ext": 0})


# --------------------------------------------------------------------------
# 定数
# --------------------------------------------------------------------------
class TestDefaultLevel(unittest.TestCase):
    def test_default_level_is_one(self):
        self.assertEqual(N.DEFAULT_LEVEL, 1)


# --------------------------------------------------------------------------
# build_new_ifs 的分档写入
# --------------------------------------------------------------------------
class TestBuildNewIfsSlots(unittest.TestCase):
    def _run(self, charts, slot_map, tmpdir, diff=0):
        out = os.path.join(tmpdir, "out.ifs")
        with mock.patch.object(UC, "parse_malody", return_value=charts), \
             mock.patch.object(
                 UC, "chart_to_eve",
                 side_effect=lambda ch, hold_policy="split":
                 (("EVE:" + ch.version).encode(), _Stats(len(ch.notes)))), \
             mock.patch.object(UC, "MiscIfs", _FakeIfs):
            det = N.build_new_ifs("src.ifs", "x.mc", diff, None, out, "split",
                                  own_audio=False, slot_map=slot_map)
        return det

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="ubns_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_multi_difficulty_writes_distinct_eves(self):
        charts = [FakeChart("BSC", 200), FakeChart("ADV", 500), FakeChart("EXT", 800)]
        det = self._run(charts, N.plan_slot_map(charts), self.tmp)
        self.assertEqual(sorted(det["replaced"]), ["adv.eve", "bsc.eve", "ext.eve"])
        self.assertEqual(det["stats"]["notes_in"], 800, "主档统计应取 ext")
        self.assertEqual(det["slot_map"], {"ext": 2, "adv": 1, "bsc": 0})
        self.assertEqual(det["slots"]["ext.eve"]["version"], "EXT")
        self.assertEqual(det["slots"]["bsc.eve"]["version"], "BSC")

    def test_single_difficulty_writes_same_eve_three_times(self):
        charts = [FakeChart("EXT", 700)]
        det = self._run(charts, N.plan_slot_map(charts), self.tmp)
        vers = {v["version"] for v in det["slots"].values()}
        self.assertEqual(vers, {"EXT"})
        self.assertEqual({v["diff"] for v in det["slots"].values()}, {0})

    def test_no_slot_map_keeps_same_diff_for_all(self):
        charts = [FakeChart("BSC", 200), FakeChart("EXT", 800)]
        det = self._run(charts, None, self.tmp, diff=1)
        self.assertEqual({v["diff"] for v in det["slots"].values()}, {1})

    def test_out_of_range_slot_map_raises(self):
        charts = [FakeChart("BSC", 200)]
        with self.assertRaises(ValueError):
            self._run(charts, {"ext": 5, "adv": 0, "bsc": 0}, self.tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
