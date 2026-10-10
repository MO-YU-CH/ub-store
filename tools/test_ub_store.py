#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ub_store.py -- ub_store 核心逻辑单测（**不依赖真实网络**）

用假传输（MockTransport）替掉 HTTP，覆盖：
  * 列表解析（含 mode 位掩码判定）、分页参数
  * 本地关键词过滤
  * 下载流程（hash 校验通过 / 不符 / code=-3 / -2）
  * 凭据存取与清除（且**断言不落密码**）
  * 安装编排转调 ub_newsong 的参数
  * 边界：缓存目录不可写、.mcz 不含 .mc
运行： python tools/test_ub_store.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ub_store as S  # noqa: E402


# ---------------------------------------------------------------------------
class MockTransport(S.Client):
    """把 request() 换成查表；记录调用，绝不联网。"""

    def __init__(self, routes, cfg=None):
        super().__init__(cfg or S.default_config())
        self.routes = routes            # list[ (predicate, handler) ]
        self.calls = []

    def request(self, url, method="GET", body=None, headers=None,
                retries=None, raw=False):
        self.calls.append({"url": url, "method": method, "body": body, "raw": raw})
        for pred, handler in self.routes:
            if pred(url, method, body):
                r = handler(url, method, body)
                if raw:
                    return r          # (bytes, headers)
                # 模拟真实 request() 的副作用：记录最近响应（供登录诊断日志）
                try:
                    text = json.dumps(r, ensure_ascii=False)
                except Exception:                            # noqa: BLE001
                    text = str(r)
                self._last_resp = {"url": url, "status": 200, "text": text}
                return r
        raise AssertionError("未匹配的路由: %s" % url)


def md5s(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


# ---------------------------------------------------------------------------
class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ubstore_test_")
        self._orig = (S.DATA_DIR, S.CONFIG_PATH, S.CRED_PATH,
                      S.MANIFEST_PATH, S.CACHE_DIR)
        S.DATA_DIR = os.path.join(self.tmp, "data")
        S.CONFIG_PATH = os.path.join(S.DATA_DIR, "config.json")
        S.CRED_PATH = os.path.join(S.DATA_DIR, "credentials.json")
        S.MANIFEST_PATH = os.path.join(S.DATA_DIR, "installed.json")
        S.CACHE_DIR = os.path.join(S.DATA_DIR, "cache")
        os.makedirs(S.DATA_DIR, exist_ok=True)

    def tearDown(self):
        (S.DATA_DIR, S.CONFIG_PATH, S.CRED_PATH,
         S.MANIFEST_PATH, S.CACHE_DIR) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
class TestHelpers(BaseCase):
    def test_has_mode_bitmask(self):
        self.assertTrue(S.has_mode(16, 4))          # 1<<4
        self.assertFalse(S.has_mode(8, 4))          # 1<<3 (Catch)
        self.assertTrue(S.has_mode(16 | 8, 4))      # 组合
        self.assertFalse(S.has_mode("x", 4))

    def test_cover_url(self):
        self.assertEqual(S.cover_url(38959, "http://a/b!small"), "http://a/b!small")
        self.assertEqual(S.cover_url(38959, "/cover/38959!small?t=1"),
                         S.DEFAULT_COVER_BASE + "/cover/38959!small?t=1")
        self.assertEqual(S.cover_url(38959, None),
                         S.DEFAULT_COVER_BASE + "/cover/38959!small")
        self.assertEqual(S.cover_url(None, None), "")

    def test_local_filter(self):
        items = [{"title": "Beluga", "artist": "S-C-U", "username": "Niconi"},
                 {"title": "Zzz", "artist": "Beluga", "username": "x"},
                 {"title": "Other", "artist": "y", "username": "z"}]
        self.assertEqual(len(S.local_filter(items, "")), 3)
        self.assertEqual(len(S.local_filter(items, "  ")), 3)
        got = S.local_filter(items, "beluga")          # 命中标题 + 艺人
        self.assertEqual(len(got), 2)
        self.assertEqual([x["title"] for x in S.local_filter(items, "niconi")], ["Beluga"])

    def test_code_text(self):
        self.assertIn("成功", S.code_text(0))
        self.assertIn("-1000", S.code_text(-1000))

    def test_md5_hex(self):
        self.assertEqual(S.md5_hex("abc"),
                         "900150983cd24fb0d6963f7d28e17f72")


# ---------------------------------------------------------------------------
class TestListAndPaging(BaseCase):
    def _client(self):
        payload = {"code": 0, "data": {"total": 70, "list": [
            {"username": "Niconi", "version": "EXT  Lv.18", "lastedit": 1789809789,
             "mode": 4, "title": "Beluga", "artist": "BEMANI Sound Team",
             "cover": "http://cni.machart.top/cover/38959!small", "id": 154501},
            {"username": "himiko", "version": "EXT-Short", "lastedit": 1788358561,
             "mode": 4, "title": "NIGHT OF FLOWER", "artist": "SOUND HOLIC",
             "cover": "", "id": 160441}]}}

        def h(url, method, body):
            return json.loads(json.dumps(payload))

        return MockTransport([(lambda u, m, b: "chart/filter" in u, h)])

    def test_parse_total_and_items(self):
        c = self._client()
        r = c.legacy_list(status=0, mode=4, count=30, page=0)
        self.assertEqual(r["total"], 70)
        self.assertEqual(len(r["list"]), 2)
        self.assertEqual(r["list"][0]["id"], 154501)
        # 注意：legacy filter 的 mode 是**单个模式值**（4=Pad），不是位掩码
        self.assertEqual(r["list"][0]["mode"], 4)
        self.assertFalse(S.has_mode(r["list"][0]["mode"], 4))

    def test_store_list_bitmask_mode(self):
        """store/list 的 mode 是位掩码：16 => 含 Pad。"""
        payload = {"code": 0, "hasMore": False, "next": 1789809789, "data": [
            {"sid": 38959, "title": "Beluga", "artist": "S-C-U",
             "cover": "/cover/38959!small", "mode": 16}]}
        c = MockTransport([(lambda u, m, b: "store/list" in u,
                            lambda u, m, b: json.loads(json.dumps(payload)))])
        res = c.store_list("Beluga", uid=1, key="SK")
        self.assertEqual(res["data"][0]["mode"], 16)
        self.assertTrue(S.has_mode(res["data"][0]["mode"], 4))
        self.assertFalse(S.has_mode(res["data"][0]["mode"], 3))
        # 商店接口的 query key 必须是自己传入的凭证
        self.assertIn("key=SK", c.calls[0]["url"])

    def test_paging_params_passed(self):
        c = self._client()
        c.legacy_list(status=2, mode=4, count=15, page=3)
        url = c.calls[0]["url"]
        self.assertIn("status=2", url)
        self.assertIn("count=15", url)
        self.assertIn("page=3", url)
        self.assertIn("mode=4", url)

    def test_bad_code_raises(self):
        c = MockTransport([(lambda u, m, b: True, lambda u, m, b: {"code": -1})])
        with self.assertRaises(S.StoreError) as cm:
            c.legacy_list()
        self.assertEqual(cm.exception.code, -1)

    # ---- 「列表获取不全」回归：必须按游标/页码把全库取完 ----
    def test_store_list_all_follows_cursor_until_hasmore_false(self):
        """store/list 每页 20 条 + next 游标；必须循环 from=next 才拿得全。"""
        pages = [
            {"code": 0, "hasMore": True, "next": 300,
             "data": [{"sid": i, "title": "T%d" % i} for i in range(20)]},
            {"code": 0, "hasMore": True, "next": 200,
             "data": [{"sid": i, "title": "T%d" % i} for i in range(20, 40)]},
            # 第 3 页故意与第 2 页重叠 5 条，验证去重
            {"code": 0, "hasMore": False, "next": 0,
             "data": [{"sid": i, "title": "T%d" % i} for i in range(35, 55)]},
        ]
        state = {"n": 0}

        def h(url, method, body):
            i = min(state["n"], len(pages) - 1)
            state["n"] += 1
            return json.loads(json.dumps(pages[i]))

        c = MockTransport([(lambda u, m, b: "store/list" in u, h)])
        r = c.store_list_all(uid=1, key="SK")
        self.assertEqual(r["pages"], 3)
        self.assertEqual(r["total"], 55)            # 60 - 5 条重叠
        ids = [it["sid"] for it in r["data"]]
        self.assertEqual(len(ids), len(set(ids)))   # 无重复
        # 第二页必须把第一页的 next 当成 from 传回去（游标，而不是偏移量）
        self.assertIn("from=300", c.calls[1]["url"])
        self.assertIn("from=200", c.calls[2]["url"])

    def test_store_list_all_stops_when_no_progress(self):
        """接口若一直 hasMore=true 但内容不变（游标不动），必须能停下来。"""
        stuck = {"code": 0, "hasMore": True, "next": 999,
                 "data": [{"sid": 1, "title": "A"}]}
        c = MockTransport([(lambda u, m, b: "store/list" in u,
                            lambda u, m, b: json.loads(json.dumps(stuck)))])
        r = c.store_list_all(uid=1, key="SK")
        self.assertEqual(r["total"], 1)
        self.assertLessEqual(r["pages"], 3)         # 必须很快退出，不能死循环

    def test_legacy_list_all_pages_until_no_fresh(self):
        """匿名列表忽略 count、total 不可信 ⇒ 取到「某页无新条目」为止。"""
        pages = [
            {"code": 0, "data": {"total": 999, "list":
                [{"id": i, "title": "L%d" % i} for i in range(30)]}},
            {"code": 0, "data": {"total": 999, "list":
                [{"id": i, "title": "L%d" % i} for i in range(30, 45)]}},
            {"code": 0, "data": {"total": 999, "list": []}},
        ]
        state = {"n": 0}

        def h(url, method, body):
            i = min(state["n"], len(pages) - 1)
            state["n"] += 1
            return json.loads(json.dumps(pages[i]))

        c = MockTransport([(lambda u, m, b: "chart/filter" in u, h)])
        r = c.legacy_list_all(status=0)
        self.assertEqual(r["total"], 45)
        self.assertEqual(r["pages"], 3)
        self.assertIn("page=0", c.calls[0]["url"])
        self.assertIn("page=1", c.calls[1]["url"])
        self.assertIn("page=2", c.calls[2]["url"])

    # ---- 「只筛选 ub（纯 Pad）谱面」----
    def test_is_pad_only_bitmask(self):
        """只有 mode 位掩码恰好 == 16 才算「纯 Pad（ub）」；48/17/249 都是多模式。"""
        self.assertEqual(S.MASK_PAD, 16)
        self.assertTrue(S.is_pad_only({"mode": 16}))
        self.assertTrue(S.is_pad_only({"mode": "16"}))
        self.assertFalse(S.is_pad_only({"mode": 48}))    # PAD|TAIKA
        self.assertFalse(S.is_pad_only({"mode": 17}))    # PAD|KEY
        self.assertFalse(S.is_pad_only({"mode": 249}))
        self.assertFalse(S.is_pad_only({"mode": 0}))
        self.assertFalse(S.is_pad_only({}))
        # has_mode 的语义是「含位」，与 is_pad_only 的「只含」要区分开
        self.assertTrue(S.has_mode(48, S.MODE_PAD))
        self.assertFalse(S.is_pad_only({"mode": 48}))

    def test_store_list_all_pad_only_filters_multi_mode(self):
        page = {"code": 0, "hasMore": False, "next": 0, "data": [
            {"sid": 1, "title": "pad only", "mode": 16},
            {"sid": 2, "title": "pad+taiko", "mode": 48},
            {"sid": 3, "title": "pad+key", "mode": 17},
        ]}
        c = MockTransport([(lambda u, m, b: "store/list" in u,
                            lambda u, m, b: json.loads(json.dumps(page)))])
        r = c.store_list_all(uid=1, key="SK", pad_only=True)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["data"][0]["sid"], 1)
        self.assertEqual(r["scanned"], 3)
        r2 = c.store_list_all(uid=1, key="SK", pad_only=False)
        self.assertEqual(r2["total"], 3)

    # ---- 下载前必须把「歌曲 sid」解析成「谱面 cid」（点下载报错的根因）----
    def test_diff_rank_prefers_ext(self):
        self.assertEqual(S.Client._diff_rank("EXT Lv.18"), 0)
        self.assertEqual(S.Client._diff_rank("EXTREME Lv.23"), 1)
        self.assertEqual(S.Client._diff_rank("ADV Lv.11"), 2)
        self.assertEqual(S.Client._diff_rank("BSC LV.5"), 5)
        # 未识别的排最后
        self.assertEqual(S.Client._diff_rank("contest chart Lv.9"), len(S.Client.CHART_PREFER))
        self.assertEqual(S.Client._diff_rank(""), len(S.Client.CHART_PREFER))

    def test_pick_cid_prefers_ext_and_hits_store_charts(self):
        charts = {"code": 0, "data": [
            {"cid": 11, "version": "BSC LV.5", "level": 5, "mode": 4, "type": 2},
            {"cid": 12, "version": "ADV Lv.11", "level": 11, "mode": 4, "type": 2},
            {"cid": 13, "version": "EXT Lv.18", "level": 18, "mode": 4, "type": 2},
        ]}
        c = MockTransport([(lambda u, m, b: "store/charts" in u,
                            lambda u, m, b: json.loads(json.dumps(charts)))])
        r = c.pick_cid(38959, uid=1, key="SK")
        self.assertEqual(r["cid"], 13)              # EXT 优先，而不是 BSC/列表顺序
        self.assertEqual(len(r["charts"]), 3)
        self.assertIn("sid=38959", c.calls[0]["url"])
        self.assertIn("key=SK", c.calls[0]["url"])

    def test_pick_cid_falls_back_to_highest_level(self):
        """难度名都不认识时，取等级最高的那个（稳定兜底，不能抛错）。"""
        charts = {"code": 0, "data": [
            {"cid": 21, "version": "contest chart Lv.9", "level": 9},
            {"cid": 22, "version": "custom Lv.15", "level": 15},
        ]}
        c = MockTransport([(lambda u, m, b: "store/charts" in u,
                            lambda u, m, b: json.loads(json.dumps(charts)))])
        self.assertEqual(c.pick_cid(1, uid=1, key="SK")["cid"], 22)

    def test_pick_cid_explicit_chart_and_empty(self):
        charts = {"code": 0, "data": [{"cid": 11, "version": "EXT Lv.10", "level": 10}]}
        c = MockTransport([(lambda u, m, b: "store/charts" in u,
                            lambda u, m, b: json.loads(json.dumps(charts)))])
        self.assertEqual(c.pick_cid(1, uid=1, key="SK", chart=11)["cid"], 11)
        with self.assertRaises(S.StoreError):
            c.pick_cid(1, uid=1, key="SK", chart=99)      # 不属于本曲
        c2 = MockTransport([(lambda u, m, b: "store/charts" in u,
                             lambda u, m, b: {"code": 0, "data": []})])
        with self.assertRaises(S.StoreError):
            c2.pick_cid(1, uid=1, key="SK")

    # ---- 双来源合并浏览（商店谱面 + 公开谱面）----
    def test_browse_all_merges_sources_and_dedupes(self):
        """商店与公开是两个**几乎不重叠**的库，必须合并；跨来源同曲按标题去重。"""
        store_page = {"code": 0, "hasMore": False, "next": 0, "data": [
            {"sid": 1, "title": "StoreSong", "artist": "A", "mode": 16}]}
        anon_page = {"code": 0, "data": {"total": 999, "list": [
            {"id": 101, "title": "AnonSong", "artist": "B", "mode": 4},
            {"id": 102, "title": "StoreSong", "artist": "A", "mode": 4}]}}
        c = MockTransport([
            (lambda u, m, b: "store/list" in u,
             lambda u, m, b: json.loads(json.dumps(store_page))),
            (lambda u, m, b: "chart/filter" in u,
             lambda u, m, b: json.loads(json.dumps(anon_page))),
        ])
        orig = S.has_store_credential
        S.has_store_credential = lambda: True
        try:
            r = c.browse_all(mode=4, anon_pages=1, uid=1, key="SK")
        finally:
            S.has_store_credential = orig
        self.assertEqual(sorted(it["title"] for it in r["data"]),
                         ["AnonSong", "StoreSong"])       # 跨来源去重成 2 条
        self.assertEqual(r["parts"]["store"], 1)
        self.assertEqual(r["parts"]["anon"], 2)           # 匿名侧新增 2 条（其一与商店重复）
        self.assertEqual(r["total"], 2)
        anon = [it for it in r["data"] if it["__src"] == "anon"][0]
        self.assertEqual(anon["cid"], 101)                # 匿名条目补出 cid，供下载用

    def test_browse_all_anon_only(self):
        """未登录/关掉商店来源时，仍能拿到公开谱面。"""
        anon_page = {"code": 0, "data": {"total": 999, "list": [
            {"id": 5, "title": "OnlyAnon", "artist": "C", "mode": 4}]}}
        c = MockTransport([(lambda u, m, b: "chart/filter" in u,
                            lambda u, m, b: json.loads(json.dumps(anon_page)))])
        r = c.browse_all(mode=4, anon_pages=1, include_store=False)
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["data"][0]["cid"], 5)
        self.assertEqual(r["parts"]["store"], 0)

    # ---- 增量浏览会话（先拉一批，翻页再拉下一批）----
    def test_browse_session_incremental_batches(self):
        """一次只拉一批（商店 1 页 + 公开各 status 1 页），翻页时再拉下一批。"""
        store_pages = [
            {"code": 0, "hasMore": True, "next": 900,
             "data": [{"sid": 1, "title": "S1", "artist": "A", "mode": 16}]},
            {"code": 0, "hasMore": False, "next": 0,
             "data": [{"sid": 2, "title": "S2", "artist": "A", "mode": 16}]},
        ]
        anon_pages = [
            {"code": 0, "data": {"total": 999, "list": [
                {"id": 11, "title": "A1", "artist": "B", "mode": 4}]}},
            {"code": 0, "data": {"total": 0, "list": []}},      # 第二批为空 → 公开源取尽
        ]
        state = {"s": 0, "a": 0}

        def h_store(u, m, b):
            i = min(state["s"], len(store_pages) - 1)
            state["s"] += 1
            return json.loads(json.dumps(store_pages[i]))

        def h_anon(u, m, b):
            i = min(state["a"] // 4, len(anon_pages) - 1)   # 每个 status 各问一次
            state["a"] += 1
            return json.loads(json.dumps(anon_pages[i]))

        c = MockTransport([
            (lambda u, m, b: "store/list" in u, h_store),
            (lambda u, m, b: "chart/filter" in u, h_anon),
        ])
        sess = S.BrowseSession(c, mode=4, uid=1, key="SK")
        new1, more1 = sess.next_batch()
        self.assertTrue(more1)                              # 商店还有下一页
        self.assertEqual(len([x for x in new1 if x["__src"] == "store"]), 1)
        self.assertEqual(len([x for x in new1 if x["__src"] == "anon"]), 1)   # 去重后只剩 1（4 次同页）
        new2, more2 = sess.next_batch()
        self.assertFalse(more2)                             # 两来源都取尽（商店 hasMore=False、公开页空）
        titles = [x["title"] for x in new1 + new2]
        self.assertEqual(sorted(titles), ["A1", "S1", "S2"])
        self.assertEqual(len(titles), len(set(titles)))      # 无重复
        # 第二批必须把第一批的 next 游标当 from 传回去（游标，不是偏移量）
        self.assertTrue(any("from=900" in x["url"] for x in c.calls))
        self.assertFalse(any("from=30" in x["url"] for x in c.calls))

    def test_browse_session_pad_only_and_dedupe_across_sources(self):
        """pad_only 过滤多模式；跨来源同曲只留一条。"""
        store_pages = [{"code": 0, "hasMore": False, "next": 0, "data": [
            {"sid": 1, "title": "Dup", "artist": "A", "mode": 16},
            {"sid": 2, "title": "Multi", "artist": "A", "mode": 48}]}]
        anon_pages = [{"code": 0, "data": {"total": 9, "list": [
            {"id": 11, "title": "Dup", "artist": "A", "mode": 4},
            {"id": 12, "title": "Fresh", "artist": "C", "mode": 4}]}}]
        c = MockTransport([
            (lambda u, m, b: "store/list" in u,
             lambda u, m, b: json.loads(json.dumps(store_pages[0]))),
            (lambda u, m, b: "chart/filter" in u,
             lambda u, m, b: json.loads(json.dumps(anon_pages[0]))),
        ])
        sess = S.BrowseSession(c, mode=4, pad_only=True, uid=1, key="SK")
        new, _more = sess.next_batch()
        titles = sorted(x["title"] for x in new)
        self.assertEqual(titles, ["Dup", "Fresh"])          # Multi 被 pad_only 滤掉、Dup 跨源去重
        self.assertEqual(new[0]["__src"], "store")          # 同曲优先保留商店条目


# ---------------------------------------------------------------------------
class TestLogin(BaseCase):
    def test_login_hashes_password_and_never_logs_plain(self):
        seen = {}

        def h(url, method, body):
            seen.update(body or {})
            return {"code": 0, "uid": 42989, "key": "K", "token": "T",
                    "storeKey": "SK"}

        c = MockTransport([(lambda u, m, b: "login/wt" in u, h)])
        r = c.login("alice", "secret-plain")
        self.assertEqual(r["uid"], 42989)
        self.assertEqual(seen["name"], "alice")
        self.assertEqual(seen["psw"], S.md5_hex("secret-plain"))   # md5, 非明文
        self.assertEqual(seen["ver"], 1)
        self.assertNotIn("secret-plain", json.dumps(seen, ensure_ascii=False))

    def test_login_error_code_propagates(self):
        c = MockTransport([(lambda u, m, b: "login" in u,
                            lambda u, m, b: {"code": -3})])
        with self.assertRaises(S.StoreError) as cm:
            c.login("alice", "pw")
        self.assertEqual(cm.exception.code, -3)

    def test_login_empty_rejected(self):
        c = MockTransport([])
        with self.assertRaises(S.StoreError):
            c.login("", "")

    # ---- 错误映射（按社区前端 i18n + 真实字段名修正后）----
    def test_login_minus3_is_bad_credentials(self):
        """-3 必须报「账号或密码错误」，不能再说「登录成功但未返回凭据」。"""
        c = MockTransport([(lambda u, m, b: "login/wt" in u,
                            lambda u, m, b: {"code": -3})])
        with self.assertRaises(S.StoreError) as cm:
            c.login("alice", "pw")
        self.assertEqual(cm.exception.code, -3)
        self.assertEqual(cm.exception.message, "账号或密码错误")
        self.assertNotIn("未返回凭据", cm.exception.message)

    def test_login_minus2_is_rejected(self):
        c = MockTransport([(lambda u, m, b: "login" in u,
                            lambda u, m, b: {"code": -2})])
        with self.assertRaises(S.StoreError) as cm:
            c.login("alice", "pw")
        self.assertEqual(cm.exception.code, -2)
        self.assertIn("请求被拒绝", cm.exception.message)

    def test_login_unknown_code(self):
        c = MockTransport([(lambda u, m, b: "login" in u,
                            lambda u, m, b: {"code": -99})])
        with self.assertRaises(S.StoreError) as cm:
            c.login("alice", "pw")
        self.assertEqual(cm.exception.code, -99)
        self.assertIn("-99", cm.exception.message)

    def test_login_log_redacts_echoed_credential(self):
        """登录响应里的 token/tokenStore 绝不落盘到诊断日志。"""
        c = MockTransport([(lambda u, m, b: "login/wt" in u,
                            lambda u, m, b: {"code": 0, "uid": 7,
                                             "token": "TOK_SECRET_VALUE",
                                             "tokenStore": "STORE_SECRET_VALUE"})])
        c.login("alice", "S3cretPw")
        with open(S.login_log_path(), encoding="utf-8") as fh:
            blob = fh.read()
        self.assertNotIn("TOK_SECRET_VALUE", blob)
        self.assertNotIn("STORE_SECRET_VALUE", blob)
        self.assertIn("[REDACTED]", blob)

    def test_login_success_uses_token_not_key(self):
        """关键回归：成功响应只有 token（无 key）时也必须判成功。

        这正是旧实现的真 bug —— 前端成功判据是 uid && (token||tokenStore)，
        凭证字段是 token，而不是游客接口的 key。
        """
        c = MockTransport([(lambda u, m, b: "login/wt" in u,
                            lambda u, m, b: {"code": 0, "uid": 42989,
                                             "token": "TOK123", "tokenStore": "SKEY"})])
        r = c.login("alice", "pw")
        self.assertEqual(r["uid"], 42989)
        self.assertEqual(r["key"], "TOK123")        # 用 token 作为后续请求的 key
        self.assertEqual(r["storeKey"], "SKEY")

    def test_login_code0_without_credential_lists_field_names(self):
        c = MockTransport([(lambda u, m, b: "login" in u,
                            lambda u, m, b: {"code": 0, "message": "ok"})])
        with self.assertRaises(S.StoreError) as cm:
            c.login("alice", "pw")
        self.assertEqual(cm.exception.code, -1000)
        self.assertIn("未返回凭据", cm.exception.message)
        # 必须把“实际字段名”列出来（便于诊断，不含值）
        self.assertIn("message", cm.exception.message)
        self.assertIn("code", cm.exception.message)

    # ---- 脱敏日志 ----
    def test_login_writes_redacted_log(self):
        c = MockTransport([(lambda u, m, b: "login" in u,
                            lambda u, m, b: {"code": 0, "uid": 7, "token": "TOK"})])
        c.login("alice", "S3cretPw")
        p = S.login_log_path()
        self.assertTrue(os.path.exists(p), p)
        with open(p, encoding="utf-8") as fh:
            blob = fh.read()
        self.assertNotIn("S3cretPw", blob)                  # 无明文密码
        self.assertNotIn(S.md5_hex("S3cretPw"), blob)       # 无 psw 的 md5
        self.assertNotIn("alice", blob)                     # name 只记长度
        self.assertIn('"psw_len": 32', blob)                # 32 => md5 正确
        self.assertIn('"name_len": 5', blob)
        self.assertIn('"ver": 1', blob)

    def test_login_log_redacts_echoed_secret(self):
        """即使服务端把 psw 回显在响应体里，也必须被擦除。"""
        echoed = S.md5_hex("S3cretPw")
        c = MockTransport([(lambda u, m, b: "login" in u,
                            lambda u, m, b: {"code": -1, "echo": echoed,
                                             "pwd": "S3cretPw"})])
        with self.assertRaises(S.StoreError):
            c.login("alice", "S3cretPw")
        with open(S.login_log_path(), encoding="utf-8") as fh:
            blob = fh.read()
        self.assertNotIn(echoed, blob)
        self.assertNotIn("S3cretPw", blob)
        self.assertIn("[REDACTED]", blob)

    def test_login_log_rotates_over_1mb(self):
        os.makedirs(S.DATA_DIR, exist_ok=True)
        with open(S.login_log_path(), "w", encoding="utf-8") as f:
            f.write("x" * (S.LOG_MAX_BYTES + 16))
        S.log_login_attempt({"event": "login"})
        self.assertTrue(os.path.exists(S.login_log_path() + ".1"))


# ---------------------------------------------------------------------------
class TestLoginWireFormat(BaseCase):
    """用本地回环 HTTP 服务抓真实请求，核对“与社区前端一致”。"""

    def test_login_posts_form_urlencoded(self):
        import http.server
        import threading

        captured = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                captured["path"] = self.path
                captured["ctype"] = self.headers.get("Content-Type") or ""
                captured["malody"] = self.headers.get("Malody") or ""
                captured["body"] = self.rfile.read(n).decode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"code":0,"uid":1,"token":"T"}')

            def log_message(self, *a):        # 静音
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        th = threading.Thread(target=srv.handle_request)
        th.daemon = True
        th.start()
        cfg = S.default_config()
        cfg["api_base"] = "http://127.0.0.1:%d/api" % srv.server_address[1]
        cfg["proxy"] = None
        cfg["min_interval"] = 0
        try:
            r = S.Client(cfg).login("alice", "S3cretPw")
        finally:
            th.join(timeout=5)
            srv.server_close()

        self.assertEqual(captured["path"], "/api/account/login/wt")
        self.assertIn("application/x-www-form-urlencoded", captured["ctype"])
        self.assertNotIn("application/json", captured["ctype"])
        self.assertEqual(captured["malody"], "6.3.22")
        # body 逐字段与前端一致：name / psw(小写 32 位 md5) / ver=1
        self.assertEqual(captured["body"],
                         "name=alice&psw=%s&ver=1" % S.md5_hex("S3cretPw"))
        self.assertEqual(r["key"], "T")


# ---------------------------------------------------------------------------
class TestCredentials(BaseCase):
    def test_save_load_clear(self):
        p = S.save_credentials(42989, "KEY123", name="alice", store_key="STORE123")
        self.assertTrue(os.path.exists(p))
        c = S.load_credentials()
        self.assertEqual(c["uid"], 42989)
        self.assertEqual(c["key"], "KEY123")
        self.assertEqual(c["storeKey"], "STORE123")
        self.assertEqual(S.store_key_of(c), "STORE123")
        self.assertTrue(S.has_store_credential())
        self.assertTrue(S.is_logged_in())
        self.assertTrue(S.clear_credentials())
        self.assertIsNone(S.load_credentials())
        self.assertFalse(S.is_logged_in())

    def test_store_key_of_accepts_legacy_tokenstore_field(self):
        """旧字段名 tokenStore 也应能读到 storeKey。"""
        self.assertEqual(S.store_key_of({"storeKey": "A"}), "A")
        self.assertEqual(S.store_key_of({"tokenStore": "B"}), "B")
        self.assertEqual(S.store_key_of({"storeKey": "", "tokenStore": "C"}), "C")
        self.assertEqual(S.store_key_of({}), "")
        self.assertEqual(S.store_key_of(None), "")

    def test_old_credentials_without_storekey(self):
        """旧版凭据（只有 uid/key）仍可读，但 has_store_credential() 为 False。"""
        os.makedirs(S.DATA_DIR, exist_ok=True)
        with open(S.CRED_PATH, "w", encoding="utf-8") as f:
            json.dump({"uid": 7, "key": "TOKEN"}, f)
        c = S.load_credentials()
        self.assertEqual(c["uid"], 7)
        self.assertIsNone(c.get("storeKey"))
        self.assertFalse(S.has_store_credential())

    def test_password_not_persisted(self):
        S.save_credentials(1, "K", name="alice", store_key="SK")
        with open(S.CRED_PATH, encoding="utf-8") as fh:
            blob = fh.read()
        self.assertNotIn("secret", blob)
        d = json.loads(blob)
        self.assertEqual(set(d) & {"psw", "password", "passwd"}, set())

    def test_plain_password_nowhere_on_disk(self):
        """跑一遍带密码的登录，确认临时目录里找不到明文密码。"""
        c = MockTransport([(lambda u, m, b: "login" in u,
                            lambda u, m, b: {"code": 0, "uid": 7, "key": "K"})])
        c.login("alice", "PLAINTEXT_PW_XYZ")
        for dp, _dn, fns in os.walk(S.DATA_DIR):
            for f in fns:
                blob = open(os.path.join(dp, f), "rb").read()
                self.assertNotIn(b"PLAINTEXT_PW_XYZ", blob)


# ---------------------------------------------------------------------------
class TestAuthRouting(BaseCase):
    """商店接口用 storeKey；社区接口用登录 token（复现并锁定本次修复的 bug）。"""

    def _client(self, payload=None):
        payload = payload if payload is not None else {"code": 0, "data": []}
        return MockTransport([(lambda u, m, b: True,
                               lambda u, m, b: json.loads(json.dumps(payload)))])

    def test_store_endpoints_use_storekey(self):
        # 登录 token 与商店凭证**故意不同**，用来验证选对了哪一个
        S.save_credentials(1083306, "LOGIN_TOKEN", name="u", store_key="STORE_KEY")
        for call in (lambda c: c.store_list("x"),
                     lambda c: c.store_charts(38959),
                     lambda c: c.download_meta(157376)):
            c = self._client({"code": 0, "items": [{"name": "a.mcz",
                                                    "file": "http://f/a.mcz"}]})
            try:
                call(c)
            except S.StoreError:
                pass          # download_meta 可能因 items 结构不同抛错，这里是看 URL
            url = c.calls[0]["url"]
            self.assertIn("key=STORE_KEY", url)
            self.assertNotIn("LOGIN_TOKEN", url)
            self.assertIn("uid=1083306", url)

    def test_community_endpoint_uses_login_token(self):
        S.save_credentials(1083306, "LOGIN_TOKEN", name="u", store_key="STORE_KEY")
        c = self._client({"code": 0, "cid": 1})
        c.chart_info(157376)
        url = c.calls[0]["url"]
        self.assertIn("key=LOGIN_TOKEN", url)
        self.assertNotIn("STORE_KEY", url)

    def test_store_without_login_raises_actionable(self):
        c = self._client()
        with self.assertRaises(S.StoreError) as cm:
            c.store_list("x")
        self.assertEqual(cm.exception.code, -1000)
        self.assertIn("登录", cm.exception.message)

    def test_store_with_old_credentials_asks_relogin(self):
        # 旧版凭据：只有 uid/key，没有 storeKey
        os.makedirs(S.DATA_DIR, exist_ok=True)
        with open(S.CRED_PATH, "w", encoding="utf-8") as f:
            json.dump({"uid": 7, "key": "LOGIN_TOKEN"}, f)
        c = self._client()
        with self.assertRaises(S.StoreError) as cm:
            c.download_meta(157376)
        self.assertEqual(cm.exception.code, -1000)
        self.assertIn("重新登录", cm.exception.message)

    def test_store_explicit_key_param_still_honored(self):
        """显式传 key 时按其值（供高级用法/测试），不读磁盘。"""
        c = self._client({"code": 0, "data": []})
        c.store_charts(38959, uid=1, key="EXPLICIT")
        self.assertIn("key=EXPLICIT", c.calls[0]["url"])


# ---------------------------------------------------------------------------
class TestDownload(BaseCase):
    def setUp(self):
        super().setUp()
        # 商店/下载接口需要「商店凭证 storeKey」，先落一份完整凭据
        S.save_credentials(1, "LOGIN_TOKEN", name="u", store_key="STORE_KEY")

    def _route(self, items, blobs, meta_code=0):
        def api_h(url, method, body):
            return {"code": meta_code, "items": items,
                    "sid": 38959, "cid": 157376}

        def file_h(url, method, body):
            return blobs[url], {}

        return MockTransport([
            (lambda u, m, b: "store/download" in u, api_h),
            (lambda u, m, b: u.startswith("http://files") or u.startswith("https://files"),
             file_h)])

    def test_download_hash_ok(self):
        b1 = b"MC-CONTENT"
        blobs = {"https://files/a.mcz": b1}
        items = [{"name": "a.mcz", "hash": md5s(b1), "file": "https://files/a.mcz"}]
        c = self._route(items, blobs)
        dest = os.path.join(self.tmp, "dl1")
        r = c.download_chart(157376, dest_dir=dest)
        self.assertEqual(len(r["files"]), 1)
        self.assertTrue(r["files"][0]["ok"])
        with open(os.path.join(dest, "a.mcz"), "rb") as fh:
            self.assertEqual(fh.read(), b1)

    def test_download_hash_mismatch_raises(self):
        blobs = {"https://files/a.mcz": b"WRONG"}
        items = [{"name": "a.mcz", "hash": md5s(b"RIGHT"), "file": "https://files/a.mcz"}]
        c = self._route(items, blobs)
        with self.assertRaises(S.StoreError) as cm:
            c.download_chart(157376, dest_dir=os.path.join(self.tmp, "dl2"))
        self.assertIn("md5", cm.exception.message.lower())

    def test_download_zip_mc_matches_inner_hash(self):
        """服务端把 .mc 压成 zip：声明 hash 是解压后 .mc 的 md5，应校验通过。"""
        import io
        inner = b'{"meta":{},"note":[]}'
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.mc", inner)
        zip_bytes = buf.getvalue()
        # 关键：raw 字节 md5 != 声明 hash，但 zip 内 a.mc 的 md5 == 声明 hash
        self.assertNotEqual(md5s(zip_bytes), md5s(inner))
        blobs = {"https://files/a.mc": zip_bytes}
        items = [{"name": "a.mc", "hash": md5s(inner), "file": "https://files/a.mc"}]
        c = self._route(items, blobs)
        dest = os.path.join(self.tmp, "dl_zip")
        r = c.download_chart(157376, dest_dir=dest)
        self.assertTrue(r["files"][0]["ok"])
        # 落盘的仍是原始字节（zip），安装管线可直接解析
        with open(os.path.join(dest, "a.mc"), "rb") as fh:
            self.assertEqual(fh.read(), zip_bytes)

    def test_download_zip_mc_wrong_inner_hash_raises(self):
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.mc", b"REAL")
        blobs = {"https://files/a.mc": buf.getvalue()}
        items = [{"name": "a.mc", "hash": md5s(b"OTHER"), "file": "https://files/a.mc"}]
        c = self._route(items, blobs)
        with self.assertRaises(S.StoreError) as cm:
            c.download_chart(157376, dest_dir=os.path.join(self.tmp, "dl_zip_bad"))
        self.assertIn("md5", cm.exception.message.lower())

    def test_download_code_minus3(self):
        c = MockTransport([(lambda u, m, b: "store/download" in u,
                            lambda u, m, b: {"code": -3, "items": None,
                                             "sid": 0, "cid": 0, "uid": 0})])
        with self.assertRaises(S.StoreError) as cm:
            c.download_chart(157376)
        self.assertEqual(cm.exception.code, -3)
        self.assertIn("登录", cm.exception.message)

    def test_download_code_minus2_or_empty(self):
        c = MockTransport([(lambda u, m, b: "store/download" in u,
                            lambda u, m, b: {"code": 0, "items": []})])
        with self.assertRaises(S.StoreError) as cm:
            c.download_chart(1)
        self.assertEqual(cm.exception.code, -2)

    def test_cache_dir_not_writable(self):
        c = self._route([], {})
        # 用一个“存在的文件”充当目录 -> makedirs 失败
        bad = os.path.join(self.tmp, "afile")
        open(bad, "w").close()
        with self.assertRaises(Exception):
            c.download_chart(1, dest_dir=os.path.join(bad, "sub"))


# ---------------------------------------------------------------------------
# 安装编排：确认参数被正确转发给 ub_newsong
# ---------------------------------------------------------------------------
class FakeNewsong:
    DEFAULT_BASE = 90001036

    def __init__(self):
        self.plan_args = None
        self.install_args = None
        self.uninstall_args = None

    def recommend_ids(self, game, base, count):
        return [90001036]

    def make_plan(self, game, mcz, **kw):
        self.plan_args = ((game, mcz), kw)
        return {"game": game, "music_id": 90001036, "title": kw.get("title") or "T",
                "mcz": mcz, "new_files": [], "table_changes": [],
                "source_music_id": kw.get("audio_from")}

    def install(self, plan, apply=False, workdir=None):
        self.install_args = (plan, apply, workdir)
        return {"apply": apply, "stamp": "20260101_000000", "written": [],
                "workdir": workdir or ""}

    def uninstall(self, game, music_id, workdir=None):
        self.uninstall_args = (game, music_id, workdir)
        return {"removed": ["x"], "restored": ["y"]}

    def verify(self, game, as_json=False):
        return {"ok": True, "checks": []}

    def default_workdir(self):
        return os.path.join(self._tmp, "ub_newsong")

    def game_paths(self, game):
        return {"ifs_base": os.path.join(game, "data", "ifs_pack"),
                "ifs_overlay": os.path.join(game, "data_mods", "omnimix", "ifs_pack")}

    def init(self, tmp):
        self._tmp = tmp
        return self


class TestInstallOrchestration(BaseCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeNewsong().init(self.tmp)
        self._orig_ns = S._newsong
        S._newsong = lambda: self.fake

    def tearDown(self):
        S._newsong = self._orig_ns
        super().tearDown()

    def test_build_plan_forwards_args(self):
        plan = S.build_install_plan("GAME", "X.mcz", audio_from=70001002,
                                    title="My Song", level=18, diff=1, slot="ext",
                                    jacket="J.png", music_id=90001036)
        (pos, kw) = self.fake.plan_args
        self.assertEqual(pos, ("GAME", "X.mcz"))
        self.assertEqual(kw["audio_from"], 70001002)
        self.assertEqual(kw["title"], "My Song")
        self.assertEqual(kw["level"], 18)
        self.assertEqual(kw["diff"], 1)
        self.assertEqual(kw["slot"], "ext")
        self.assertEqual(kw["jacket"], "J.png")
        self.assertEqual(kw["music_id"], 90001036)
        self.assertEqual(plan["music_id"], 90001036)

    def test_run_install_applies_and_records_manifest(self):
        plan = {"game": "GAME", "music_id": 90001036, "title": "T", "mcz": "X.mcz"}
        S.run_install(plan, apply=True)
        p, apply_, _wd = self.fake.install_args
        self.assertIs(p, plan)
        self.assertTrue(apply_)
        S.add_installed({"music_id": 90001036, "title": "T", "mcz": "X.mcz",
                         "game": "GAME"})
        inst = S.list_installed()
        self.assertEqual([x["music_id"] for x in inst], [90001036])

    def test_run_uninstall_clears_manifest(self):
        S.add_installed({"music_id": 90001036, "title": "T"})
        S.run_uninstall("GAME", 90001036)
        self.assertEqual(self.fake.uninstall_args[1], 90001036)
        self.assertEqual(S.list_installed(), [])

    def test_recommend_ids(self):
        self.assertEqual(S.recommend_music_ids("GAME", count=1), [90001036])


# ---------------------------------------------------------------------------
class TestEdgeCases(BaseCase):
    def test_mcz_without_mc_raises(self):
        import ub_chart as UC
        p = os.path.join(self.tmp, "nope.mcz")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("readme.txt", "hello")
            z.writestr("cover.png", b"\x89PNG")
        with self.assertRaises(ValueError) as cm:
            UC.parse_mcz(p)
        self.assertIn(".mc", str(cm.exception))

    def test_network_down(self):
        import urllib.error

        class Boom(S.Client):
            def request(self, url, **kw):
                raise S.StoreError(-1000, "网络不可达: connection refused", url)

        with self.assertRaises(S.StoreError) as cm:
            Boom().legacy_list()
        self.assertEqual(cm.exception.code, -1000)

    def test_bad_json_shape(self):
        c = MockTransport([(lambda u, m, b: True, lambda u, m, b: ["not", "dict"])])
        with self.assertRaises(S.StoreError):
            c.legacy_list()

    def test_config_roundtrip(self):
        cfg = S.load_config()
        cfg["cache_dir"] = os.path.join(self.tmp, "cacheX")
        S.save_config(cfg)
        self.assertEqual(S.load_config()["cache_dir"], os.path.join(self.tmp, "cacheX"))

    def test_stale_credentials_ignored(self):
        os.makedirs(S.DATA_DIR, exist_ok=True)
        with open(S.CRED_PATH, "w") as fh:
            fh.write("{ not json")
        self.assertIsNone(S.load_credentials())


class TestDeleteDownloaded(BaseCase):
    """本地下载文件的删除（delete_downloaded）——重点是安全约束。"""

    def setUp(self):
        super().setUp()
        cfg = S.default_config()
        cfg["cache_dir"] = S.CACHE_DIR          # cache_dir() 读的是 config
        S.save_config(cfg)
        os.makedirs(S.CACHE_DIR, exist_ok=True)

    def _make_dl(self, name="12345", size=1000):
        d = os.path.join(S.CACHE_DIR, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "chart.mc"), "wb") as f:
            f.write(b"m" * size)
        with open(os.path.join(d, "cover.jpg"), "wb") as f:
            f.write(b"c" * 100)
        return d

    def _mark_installed(self, title, music_id):
        """写 installed.json。注意 chart_card 是**按标题**匹配已装记录的。"""
        with open(S.MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": [{"title": title, "music_id": music_id}]},
                      f, ensure_ascii=False)

    # ---- 正常删除 ----
    def test_delete_normal(self):
        d = self._make_dl()
        r = S.delete_downloaded(d)
        self.assertEqual(r["freed"], 1100)
        self.assertFalse(os.path.exists(d))
        self.assertIsNone(r["installed_music_id"])

    def test_delete_then_list_downloaded_empty(self):
        d = self._make_dl()
        self.assertEqual(len(S.downloaded_dirs()), 1)
        S.delete_downloaded(d)
        self.assertEqual(S.downloaded_dirs(), [])

    # ---- 🔴 安全约束 ----
    def test_refuse_cache_root_itself(self):
        """绝不能把整个缓存目录端掉。"""
        self._make_dl()
        with self.assertRaises(S.StoreError):
            S.delete_downloaded(S.CACHE_DIR)
        self.assertTrue(os.path.isdir(S.CACHE_DIR))

    def test_refuse_outside_cache(self):
        """缓存目录之外的路径一律拒绝（防止误删任意目录）。"""
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with self.assertRaises(S.StoreError):
            S.delete_downloaded(outside)
        self.assertTrue(os.path.isdir(outside))

    def test_refuse_dotdot_traversal(self):
        """`cache/../outside` 这种穿越也要拦住。"""
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with self.assertRaises(S.StoreError):
            S.delete_downloaded(os.path.join(S.CACHE_DIR, "..", "outside"))
        self.assertTrue(os.path.isdir(outside))

    def test_refuse_nonexistent(self):
        with self.assertRaises(S.StoreError):
            S.delete_downloaded(os.path.join(S.CACHE_DIR, "nope"))

    def test_refuse_data_dir(self):
        """连数据目录本身也不能删（它是 cache 的父级）。"""
        with self.assertRaises(S.StoreError):
            S.delete_downloaded(S.DATA_DIR)
        self.assertTrue(os.path.isdir(S.DATA_DIR))

    # ---- 已装进游戏的：默认拒绝 ----
    def test_refuse_when_installed(self):
        d = self._make_dl(name="SongA")
        self._mark_installed("SongA", 90009000)
        with self.assertRaises(S.StoreError) as cm:
            S.delete_downloaded(d)
        self.assertIn("卸载", str(cm.exception))
        self.assertTrue(os.path.isdir(d), "拒绝时不应删除任何东西")

    def test_force_when_installed(self):
        d = self._make_dl(name="SongB")
        self._mark_installed("SongB", 90009000)
        r = S.delete_downloaded(d, force=True)
        self.assertFalse(os.path.exists(d))
        self.assertEqual(r["installed_music_id"], 90009000)
        self.assertTrue(r["warns"], "强制删除时应给出警告")


if __name__ == "__main__":
    unittest.main(verbosity=2)
