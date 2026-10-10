#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ub_grouptex.py -- 分组图集小贴图注册（ub_newsong.group-tex）回归测试

覆盖（全部在临时沙盒里跑，不碰真机游戏目录）：
  * 选图集：取「已加载 + 含同段最大且 < 本曲 id 的 BNR_ID」那一个
  * 注册：新增 BNR_ID/IDX_ID/IDX_MINI_ID 三条，f14 按 n 重算，回读重建字节一致
  * 不破坏原有条目：原条目 blob/hash/flag 逐字节不变
  * 幂等：同名已存在 -> noop，不重复追加
  * 撤销：删掉三条后图集**逐字节还原**（md5 与原始一致）
  * 侧车：uninstall 依赖的 group_tex_<id>.json 正确落盘/清理
  * 大曲绘派生：无大曲绘时退回占位图
"""
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import tdxt_encode as T          # noqa: E402
import ub_newsong as NS          # noqa: E402

MID = 90001036
ANCHOR = 90001035


def _mk_tdxt(w, h, fmt=0x10):
    from PIL import Image
    img = Image.new('RGBA', (w, h), (10, 20, 30, 255))
    return T.encode_image_to_tdxt(img, fmt=fmt)


def _synth_atlas(path, anchor=ANCHOR):
    """从零合成一个合法 PXET：含 BNR_ID/IDX_ID/IDX_MINI_ID<anchor> 三条。"""
    items = []
    for name, wh in (("BNR_ID%d" % anchor, (160, 160)),
                     ("IDX_ID%d" % anchor, (324, 40)),
                     ("IDX_MINI_ID%d" % anchor, (160, 24))):
        items.append(dict(name=name, hash=T.name_hash(name), flag=0,
                          blob=T.make_pxet_record(_mk_tdxt(*wh), False)))
    hdr = bytearray(0x40)
    hdr[0:4] = b'PXET'
    base = dict(hdr=bytes(hdr), f8=0, fc=0, f14=0, f18=0, ntex=0, items=[])
    blob = T.rebuild_pxet(base, items)
    with open(path, 'wb') as f:
        f.write(blob)
    return blob


class TestGroupTex(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='ubgt_')
        self.game = os.path.join(self.root, 'game')
        self.work = os.path.join(self.root, 'work')
        self.mdir = os.path.join(self.game, 'data_mods', 'omnimix', 'd3', 'model')
        os.makedirs(self.mdir)
        os.makedirs(os.path.join(self.game, 'data', 'music_info'))
        os.makedirs(self.work)
        with open(os.path.join(self.game, 'data', 'music_info', 'music_info.xml'),
                  'w', encoding='utf-8') as f:
            f.write('<root><data_num __type="s32">0</data_num></root>')
        self.atlas_name = 'tex_l44fo_bnr_j_09_021.bin'
        self.atlas = os.path.join(self.mdir, self.atlas_name)
        self.orig = _synth_atlas(self.atlas)
        self.orig_md5 = hashlib.md5(self.orig).hexdigest()
        # 日志里声明该图集「已被加载」
        with open(os.path.join(self.game, 'log.txt'), 'w', encoding='utf-8') as f:
            f.write('[2026/10/07 20:52:26] M:layeredfs: Using '
                    './data_mods/omnimix/d3/model/%s\n' % self.atlas_name)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # ---------------- 选图集 ----------------
    def test_loaded_list_parsed_from_log(self):
        self.assertIn(self.atlas_name, NS.loaded_group_atlases(self.game))

    def test_pick_prefers_loaded_atlas_with_nearest_lower_anchor(self):
        got = NS.pick_group_atlas(self.game, MID)
        self.assertEqual(got['name'], self.atlas_name)
        self.assertEqual(got['anchor'], ANCHOR)
        self.assertTrue(got['loaded'])

    # ---------------- 注册 ----------------
    def test_register_adds_three_and_keeps_f14(self):
        res = NS.register_group_tex(self.game, MID, 'テスト', apply=True,
                                    workdir=self.work)
        self.assertEqual(res['action'], 'written')
        self.assertEqual(res['n_before'], 3)
        self.assertEqual(res['n_after'], 6)
        self.assertTrue(res['verify']['f14_ok'])
        self.assertTrue(res['verify']['byte_exact_rebuild'])
        self.assertEqual(res['verify']['present'],
                         ['BNR_ID%d' % MID, 'IDX_ID%d' % MID, 'IDX_MINI_ID%d' % MID])
        d = T.read_pxet(self.atlas)
        self.assertEqual(d['f14'], T.pman_hash_param(len(d['items'])))

    def test_register_never_touches_existing_entries(self):
        before = {it['name']: (it['hash'], it['flag'], hashlib.md5(it['blob']).hexdigest())
                  for it in T.read_pxet(self.atlas)['items']}
        NS.register_group_tex(self.game, MID, 'テスト', apply=True, workdir=self.work)
        after = {it['name']: (it['hash'], it['flag'], hashlib.md5(it['blob']).hexdigest())
                 for it in T.read_pxet(self.atlas)['items']}
        for n, sig in before.items():
            self.assertEqual(after[n], sig, '原条目被改动: %s' % n)

    def test_new_entries_match_official_style(self):
        NS.register_group_tex(self.game, MID, 'テスト', apply=True, workdir=self.work)
        d = T.read_pxet(self.atlas)
        sib = next(it for it in d['items'] if it['name'] == 'IDX_ID%d' % ANCHOR)
        new = next(it for it in d['items'] if it['name'] == 'IDX_ID%d' % MID)
        hs = T.parse_tdxt_header(T.lzss_decompress(sib['blob']))
        hn = T.parse_tdxt_header(T.lzss_decompress(new['blob']))
        self.assertEqual(new['flag'], sib['flag'])
        self.assertEqual((hn['fmt'], hn['hf3'], hn['fmtflags']),
                         (hs['fmt'], hs['hf3'], hs['fmtflags']))
        self.assertEqual((hn['w'], hn['h']), (324, 40))

    def test_dry_run_does_not_write(self):
        res = NS.register_group_tex(self.game, MID, 'テスト', apply=False,
                                    workdir=self.work)
        self.assertEqual(res['action'], 'dry-run')
        self.assertEqual(hashlib.md5(open(self.atlas, 'rb').read()).hexdigest(),
                         self.orig_md5)

    def test_second_register_is_noop(self):
        NS.register_group_tex(self.game, MID, 'テスト', apply=True, workdir=self.work)
        res = NS.register_group_tex(self.game, MID, 'テスト', apply=True, workdir=self.work)
        self.assertEqual(res['action'], 'noop')
        self.assertEqual(len(res['skipped_existing']), 3)
        self.assertEqual(len(T.read_pxet(self.atlas)['items']), 6)

    # ---------------- 撤销 ----------------
    def test_unregister_restores_byte_exact(self):
        NS.register_group_tex(self.game, MID, 'テスト', apply=True, workdir=self.work)
        side = os.path.join(self.work, 'group_tex_%d.json' % MID)
        self.assertTrue(os.path.exists(side), '侧车记录缺失')
        u = NS.unregister_group_tex(self.game, MID, workdir=self.work)
        self.assertEqual(len(u['removed']), 3)
        self.assertTrue(u['f14_ok'])
        self.assertEqual(hashlib.md5(open(self.atlas, 'rb').read()).hexdigest(),
                         self.orig_md5, '撤销后未逐字节还原')
        self.assertFalse(os.path.exists(side), '侧车记录未清理')

    def test_unregister_without_sidecar_is_skipped(self):
        u = NS.unregister_group_tex(self.game, MID, workdir=self.work)
        self.assertIn('skipped', u)

    # ---------------- 小曲绘来源 ----------------
    def test_derive_returns_none_without_big_atlas(self):
        self.assertIsNone(NS._derive_small_jacket(self.game, MID, (160, 160)))

    def test_derive_decodes_big_atlas_when_present(self):
        big = NS.new_tex_path(self.game, MID)
        items = [dict(name='BNR_BIG_ID%d' % MID, hash=T.name_hash('BNR_BIG_ID%d' % MID),
                      flag=0, blob=T.make_pxet_record(_mk_tdxt(320, 320), False))]
        hdr = bytearray(0x40)
        hdr[0:4] = b'PXET'
        with open(big, 'wb') as f:
            f.write(T.rebuild_pxet(dict(hdr=bytes(hdr), f8=0, fc=0, f14=0, f18=0,
                                        ntex=0, items=[]), items))
        img = NS._derive_small_jacket(self.game, MID, (160, 160))
        self.assertIsNotNone(img)
        self.assertEqual(img.size, (160, 160))

    def test_placeholder_when_nothing_available(self):
        res = NS.register_group_tex(self.game, MID, 'テスト', apply=True, workdir=self.work)
        e = next(x for x in res['entries'] if x['entry'].startswith('BNR_ID'))
        self.assertEqual(e['from'], '(placeholder)')


if __name__ == '__main__':
    unittest.main(verbosity=2)
