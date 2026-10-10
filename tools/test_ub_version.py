#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ub_version.py -- theme_info 版本分类注册表读写回归测试

全部在临时沙盒里跑（从真机 theme_info.xml 复制一份当样本），不碰真机。
覆盖：
  * 解析：块数 / 字段 / pos_index 排序 / data_num
  * 槽位：下一可用 theme_id / pos_index / theme_version / block_id
  * 新增：干跑不写盘、写入后读回、幂等
  * 冲突：theme_id / pos_index 重复要报错
  * 编码：SHIFT_JIS 可编码性（ASCII 名字可用，非法字符报错）
  * 回退：移除后**逐字节还原**
"""
import glob
import hashlib
import os
import shutil
import sys
import tempfile
import unittest


def _strip_name(raw: bytes, name: str) -> bytes:
    """把样本里已存在的同名块剔除（保证测试可重复）。"""
    txt = raw.decode("shift_jis", errors="replace")
    out = []
    for m in UV.BLOCK_RE.finditer(txt):
        if name in m.group(0):
            continue
        out.append(m.group(0))
    if len(out) == len(UV.BLOCK_RE.findall(txt)):
        return raw
    head = txt[: txt.index("<body")] + "\n"
    body = txt[txt.index("<body"): txt.index("<body") + txt[txt.index("<body"):].index(">") + 1] + "\n"
    return (head + body + "".join(out) + "</body>\n</theme_data>").encode("shift_jis")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ub_version as UV          # noqa: E402

REAL = r'<GAME_ROOT>\data\theme_info\theme_info.xml'
HAVE_REAL = os.path.exists(REAL)


def _synth_xml() -> bytes:
    """自造一个最小 theme_info.xml（不依赖真机）。"""
    def blk(bid, tid, pos, ty, tv, name):
        return ("<data __type='void' id='%d'>\n"
                "<theme_id __type='s8'>\n%d\n</theme_id>\n"
                "<pos_index __type='s8'>\n%d\n</pos_index>\n"
                "<theme_type __type='u16'>\n%d\n</theme_type>\n"
                "<theme_version __type='u16'>\n%d\n</theme_version>\n"
                "<name_j __type='str'>\n%s\n</name_j>\n"
                "</data>\n" % (bid, tid, pos, ty, tv, name))
    txt = ("<?xml version='1.0' encoding='SHIFT_JIS'?>\n"
           "<theme_data __type='void' model='jubeat' formver='1.0.1'>\n"
           "<header __type='void'>\n<data_num __type='s32'>\n2\n</data_num>\n"
           "<excel __type='str'>\ntheme_info.xlsx\n</excel>\n</header>\n"
           "<body __type='void'>\n")
    txt += blk(2, 1, 0, 0, 0, "サイバー")
    txt += blk(1, 2, 1, 0, 10, "アベニュー")
    txt += "</body>\n</theme_data>"
    return txt.encode("shift_jis")


class TestThemeInfo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 取样优先用真机**改动前的备份**（避免被已应用的自作譜污染）；没有就现造。
        src = None
        d = os.path.dirname(REAL)
        if os.path.isdir(d):
            baks = sorted(glob.glob(os.path.join(d, "theme_info.xml.bak_*")))
            if baks:
                src = open(baks[0], "rb").read()
            elif os.path.exists(REAL):
                src = open(REAL, "rb").read()
        if src is None:
            src = _synth_xml()
        # 兜底：万一取到的样本里已经有 自作譜，先剔掉，保证测试可重复
        cls._src = _strip_name(src, "自作譜")

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ubver_")
        self.game = os.path.join(self.root, "game")
        d = os.path.join(self.game, "data", "theme_info")
        os.makedirs(d)
        self.path = os.path.join(d, "theme_info.xml")
        with open(self.path, "wb") as f:
            f.write(self._src)
        self.md5_0 = hashlib.md5(self._src).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # ---- 解析 ----
    def test_read_themes_sorted_by_pos(self):
        th = UV.read_themes(self.game)
        self.assertGreaterEqual(len(th), 2)
        self.assertEqual([t["pos_index"] for t in th],
                         sorted(t["pos_index"] for t in th))
        self.assertTrue(all(set(t) >= {"block_id", "theme_id", "pos_index",
                                       "theme_type", "theme_version", "name_j", "raw"}
                            for t in th))

    def test_next_slots_are_max_plus_one(self):
        th = UV.read_themes(self.game)
        s = UV.next_theme_slots(th)
        self.assertEqual(s["theme_id"], max(t["theme_id"] for t in th) + 1)
        self.assertEqual(s["pos_index"], max(t["pos_index"] for t in th) + 1)
        self.assertEqual(s["theme_version"], max(t["theme_version"] for t in th) + 1)
        self.assertEqual(s["block_id"], max(t["block_id"] for t in th) + 1)

    def test_real_file_has_avenue_at_last_pos(self):
        if not HAVE_REAL:
            self.skipTest("无真机样本")
        th = UV.read_themes(self.game)
        last = th[-1]
        self.assertEqual(last["name_j"], "アベニュー")
        self.assertEqual(last["theme_version"], 10)

    # ---- 新增 ----
    def test_dry_run_does_not_write(self):
        r = UV.add_theme(self.game, "自作譜", apply=False)
        self.assertEqual(r["action"], "dry-run")
        self.assertEqual(hashlib.md5(open(self.path, "rb").read()).hexdigest(),
                         self.md5_0)

    def test_add_then_readback(self):
        r = UV.add_theme(self.game, "自作譜", apply=True)
        self.assertEqual(r["action"], "written")
        self.assertEqual(r["n_after"], r["n_before"] + 1)
        self.assertTrue(os.path.exists(r["backup"]))
        th = UV.read_themes(self.game)
        self.assertEqual(len(th), r["n_after"])
        self.assertEqual(th[-1]["name_j"], "自作譜")
        self.assertEqual(th[-1]["pos_index"], 49 if HAVE_REAL else 2)
        self.assertEqual(r["verify"]["present"], ["自作譜"])

    def test_add_is_idempotent(self):
        UV.add_theme(self.game, "自作譜", apply=True)
        n1 = len(UV.read_themes(self.game))
        r2 = UV.add_theme(self.game, "自作譜", apply=True)
        self.assertEqual(r2["action"], "exists")
        self.assertEqual(len(UV.read_themes(self.game)), n1)

    def test_theme_id_conflict_raises(self):
        th = UV.read_themes(self.game)
        taken = th[0]["theme_id"]
        with self.assertRaises(ValueError):
            UV.add_theme(self.game, "X", theme_id=taken, apply=False)

    def test_pos_index_conflict_raises(self):
        th = UV.read_themes(self.game)
        taken = th[0]["pos_index"]
        with self.assertRaises(ValueError):
            UV.add_theme(self.game, "X", pos_index=taken, apply=False)

    def test_set_data_num_option(self):
        r = UV.add_theme(self.game, "自作譜", apply=True, set_data_num=True)
        self.assertEqual(r["data_num_after"], r["n_after"])
        self.assertEqual(r["verify"]["data_num"], r["n_after"])

    def test_data_num_untouched_by_default(self):
        r = UV.add_theme(self.game, "自作譜", apply=True)
        self.assertNotIn("data_num_after", r)
        self.assertEqual(r["verify"]["data_num"], r["data_num_before"])

    # ---- 回退 ----
    def test_remove_restores_byte_exact(self):
        UV.add_theme(self.game, "自作譜", apply=True)
        rm = UV.remove_theme(self.game, "自作譜", apply=True)
        self.assertEqual(rm["action"], "written")
        self.assertEqual(rm["removed"], 1)
        self.assertEqual(hashlib.md5(open(self.path, "rb").read()).hexdigest(),
                         self.md5_0, "移除后未逐字节还原")

    def test_remove_missing_is_noop(self):
        r = UV.remove_theme(self.game, "不存在", apply=True)
        self.assertEqual(r["action"], "not-found")

    # ---- 编码 ----
    def test_shiftjis_name_ok(self):
        r = UV.add_theme(self.game, "自作譜", apply=True)
        self.assertEqual(r["action"], "written")
        self.assertEqual(UV.read_themes(self.game)[-1]["name_j"], "自作譜")

    def test_unencodable_name_raises(self):
        with self.assertRaises(UnicodeEncodeError):
            UV.add_theme(self.game, "emoji😀", apply=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
