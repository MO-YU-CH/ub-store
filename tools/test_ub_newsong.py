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
import json
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
class TestIdBaseSafety(unittest.TestCase):
    """自制谱 id 必须留在官方当前世代段(90)内、且段内偏移够大。

    两条规则都来自 2026-10-10 的实机事故：
      · 跨段（99900000）→ 游戏登录闪退（W:EXCEPTION: avs-sort.c 刷屏）
      · 贴官方尾部（90001036）→ 迟早被官方更新撞号
    """

    def test_default_base_in_custom_segment(self):
        seg = N.DEFAULT_BASE // 1000000
        self.assertEqual(seg, N.CUSTOM_SEGMENT,
                         "自制段必须在 %d 段内" % N.CUSTOM_SEGMENT)

    def test_default_base_offset_big_enough(self):
        off = N.DEFAULT_BASE % 1000000
        self.assertGreaterEqual(off, N.OFFICIAL_SEQ_LIMIT)
        # 且离官方已用到的序号要拉开距离
        self.assertGreater(off - (N.OFFICIAL_ID_HINT_MAX % 1000000), 5000)

    def test_default_base_starts_with_90009(self):
        """用户指定的格式：90009xxx"""
        self.assertEqual(N.DEFAULT_BASE, 90009000)

    def test_default_base_is_valid(self):
        self.assertIsNone(N.id_problem(N.DEFAULT_BASE))

    # ---- id_problem：跨段必须被判为不可用 ----
    def test_cross_segment_rejected(self):
        # 99900000（99 段）正是导致闪退的那个值
        for bad in (99900000, 99900001, 99000000, 91000000, 80009000):
            prob = N.id_problem(bad)
            self.assertIsNotNone(prob, "%d 跨段应被拒绝" % bad)
            self.assertIn("段", prob)

    def test_too_close_to_official_rejected(self):
        for bad in (90001036, 90001000, 90000001, 90001999):
            prob = N.id_problem(bad)
            self.assertIsNotNone(prob, "%d 偏移太小应被拒绝" % bad)

    def test_good_ids_accepted(self):
        for good in (90009000, 90009001, 90009999, 90010000, 90020000):
            self.assertIsNone(N.id_problem(good), "%d 应该是可用的" % good)

    def test_negative_rejected(self):
        self.assertIsNotNone(N.id_problem(-1))

    # ---- safe_base：不合格的起点自动纠正 ----
    def test_safe_base_fixes_cross_segment(self):
        for bad in (99900000, 99000000, 91000000):
            fixed, note = N.safe_base(bad)
            self.assertEqual(fixed, 90009000)
            self.assertIsNotNone(note)
            self.assertIsNone(N.id_problem(fixed))

    def test_safe_base_fixes_official_tail(self):
        fixed, note = N.safe_base(90001036)
        self.assertEqual(fixed, 90009000)
        self.assertIsNotNone(note)

    def test_safe_base_keeps_good_value(self):
        fixed, note = N.safe_base(90009000)
        self.assertEqual(fixed, 90009000)
        self.assertIsNone(note)

    # ---- recommend_ids ----
    def test_recommend_ids_auto_corrects(self):
        """即使 base 配成跨段的值，也会被挪到安全区。"""
        with mock.patch.object(N, "scan_occupied", return_value={"union": set()}):
            ids = N.recommend_ids("X", base=99900000, count=3)
        self.assertEqual(ids, [90009000, 90009001, 90009002])
        for i in ids:
            self.assertIsNone(N.id_problem(i))

    def test_recommend_ids_skips_occupied(self):
        occ = {90009000, 90009001}
        with mock.patch.object(N, "scan_occupied", return_value={"union": occ}):
            ids = N.recommend_ids("X", base=90009000, count=3)
        self.assertEqual(ids, [90009002, 90009003, 90009004])


class TestBuildNewIfsSlots(unittest.TestCase):
    def _run(self, charts, slot_map, tmpdir, diff=0):
        out = os.path.join(tmpdir, "out.ifs")
        with mock.patch.object(UC, "parse_malody", return_value=charts), \
             mock.patch.object(
                 UC, "chart_to_eve",
                 side_effect=lambda ch, hold_policy="split", **kw:
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




class TestAudioOffsetAndTexChannel(unittest.TestCase):
    """两个实机问题：① 音符比音乐晚 ② 曲绘红变蓝。"""

    def test_chart_to_eve_swaps_disabled_by_default(self):
        """fmt=0x10 应按 RGBA 原样写（曾误按 BGRA 交换 → 红变蓝）。"""
        import tdxt_encode as T
        from PIL import Image
        if hasattr(T, '_SWAP_RB'):
            T._SWAP_RB = None                      # 清缓存，读环境
        img = Image.new('RGBA', (8, 8), (255, 0, 0, 255))
        blob = T.encode_image_to_tdxt(img, fmt=0x10)
        px = blob[T.HEADER_SIZE:T.HEADER_SIZE + 4]
        self.assertEqual(px, b'\xff\x00\x00\xff',
                         "红色像素应为 R,G,B,A = ff 00 00 ff，实际 %s" % px.hex(' '))

    def test_roundtrip_keeps_color(self):
        import tdxt_encode as T
        from PIL import Image
        img = Image.new('RGBA', (16, 8), (255, 0, 0, 255))
        img.paste(Image.new('RGBA', (8, 8), (0, 0, 255, 255)), (8, 0))
        back = T.decode_tdxt(T.encode_image_to_tdxt(img, fmt=0x10))
        self.assertEqual(back.getpixel((2, 2))[:3], (255, 0, 0))
        self.assertEqual(back.getpixel((12, 2))[:3], (0, 0, 255))


class TestSlotMapOffset(unittest.TestCase):
    """确保 eve 转换带上了 apply_bgm_offset（音符对齐音乐）。"""

    def test_eve_of_passes_apply_bgm_offset(self):
        import inspect
        import ub_newsong as N
        src = inspect.getsource(N.build_new_ifs)
        self.assertIn("apply_bgm_offset=True", src,
                      "build_new_ifs 必须传 apply_bgm_offset=True，"
                      "否则 Malody 的音源偏移被忽略、音符会比音乐晚")


class TestUninstallCleanliness(unittest.TestCase):
    """卸载要删干净；没删干净时 state 要留着方便重试，并报出 leftover。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="ub_uninst_")
        self.game = os.path.join(self.tmp, "contents")
        for sub in ("data_mods/omnimix/ifs_pack/d9000999",
                    "data_mods/omnimix/d3/model",
                    "data_mods/omnimix/music_info",
                    "data"):
            os.makedirs(os.path.join(self.game, sub), exist_ok=True)
        self.ifs = os.path.join(self.game, "data_mods/omnimix/ifs_pack/d9000999/90009999_msc.ifs")
        self.tex = os.path.join(self.game, "data_mods/omnimix/d3/model/tex_l44_bnr_big_id90009999.bin")
        self.mdl = os.path.join(self.game, "data_mods/omnimix/d3/model/mdl_l44_bnr_big_id90009999.bin")
        for p in (self.ifs, self.tex, self.mdl):
            with open(p, "wb") as f:
                f.write(b"x" * 16)
        self.tbl = os.path.join(self.game, "data_mods/omnimix/music_info/music_omni.xml")
        with open(self.tbl, "w", encoding="utf-8") as f:
            f.write("<data_num __type=\"s32\">0</data_num>")
        self.wd = os.path.join(self.tmp, "wd")
        os.makedirs(self.wd)
        self.state = os.path.join(self.wd, "state_90009999.json")
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump({"music_id": 90009999,
                       "new_files": [{"path": self.ifs, "kind": "ifs"},
                                     {"path": self.tex, "kind": "tex"},
                                     {"path": self.mdl, "kind": "mdl"}],
                       "backups": {}}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean_uninstall_reports_clean(self):
        r = N.uninstall(self.game, 90009999, workdir=self.wd)
        self.assertTrue(r["clean"], "应报告 clean=True")
        self.assertEqual(r["leftover"], [])
        for p in (self.ifs, self.tex, self.mdl):
            self.assertFalse(os.path.exists(p))
        self.assertFalse(os.path.exists(self.state), "删干净了才该移除 state")

    def test_leftover_keeps_state_and_reports(self):
        """模拟"文件删不掉"：替换 os.remove 让它对 ifs 抛错。"""
        real_remove = os.remove

        def fake_remove(p):
            if p == self.ifs:
                raise OSError(32, "being used by another process")
            return real_remove(p)
        with mock.patch.object(os, "remove", side_effect=fake_remove):
            r = N.uninstall(self.game, 90009999, workdir=self.wd)
        self.assertFalse(r["clean"], "有残留时不能报 clean")
        self.assertIn(self.ifs, r["leftover"])
        self.assertTrue(r["failed"], "应记录失败原因")
        self.assertTrue(os.path.exists(self.state),
                        "有残留时必须保留 state，方便用户重试")


class TestOrphanFiles(unittest.TestCase):
    """孤儿文件：曲表没条目了，文件还留着。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="ub_orphf_")
        self.game = os.path.join(self.tmp, "contents")
        for sub in ("data_mods/omnimix/ifs_pack/d9000999",
                    "data_mods/omnimix/d3/model",
                    "data_mods/omnimix/music_info",
                    "data"):
            os.makedirs(os.path.join(self.game, sub), exist_ok=True)
        self.tbl = os.path.join(self.game, "data_mods/omnimix/music_info/music_omni.xml")
        # 曲表里**没有** 90009999
        with open(self.tbl, "w", encoding="utf-8") as f:
            f.write("<data_num __type=\"s32\">0</data_num>")
        self.ifs = os.path.join(self.game, "data_mods/omnimix/ifs_pack/d9000999/90009999_msc.ifs")
        self.tex = os.path.join(self.game, "data_mods/omnimix/d3/model/tex_l44_bnr_big_id90009999.bin")
        for p in (self.ifs, self.tex):
            with open(p, "wb") as f:
                f.write(b"x" * 100)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_detects_orphan_files(self):
        got = N.find_orphan_files(self.game)
        ids = [x["music_id"] for x in got]
        self.assertIn(90009999, ids)
        rec = [x for x in got if x["music_id"] == 90009999][0]
        self.assertEqual(len(rec["files"]), 2)
        self.assertGreater(rec["size"], 0)

    def test_dry_run_does_not_delete(self):
        r = N.cleanup_orphan_files(self.game, dry_run=True)
        self.assertTrue(r["dry_run"])
        self.assertTrue(os.path.exists(self.ifs))

    def test_cleanup_deletes(self):
        r = N.cleanup_orphan_files(self.game)
        self.assertFalse(os.path.exists(self.ifs))
        self.assertFalse(os.path.exists(self.tex))
        self.assertGreater(r["freed"], 0)
        self.assertEqual(N.find_orphan_files(self.game), [])

    def test_ignores_official_ids(self):
        """官方 id（段内偏移小）不该被判成孤儿文件。"""
        p = os.path.join(self.game, "data_mods/omnimix/d3/model/tex_l44_bnr_big_id90001000.bin")
        with open(p, "wb") as f:
            f.write(b"y" * 10)
        ids = [x["music_id"] for x in N.find_orphan_files(self.game)]
        self.assertNotIn(90001000, ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
