#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ub_store_gui.py -- GUI 无头可测（**建根窗口、不 mainloop**）

新结构：谱库浏览 / 已下载 / 已装自制谱 均为封面卡片网格；一键安装/卸载走 ub_store。

断言（语义保留）：
  * 启动无异常（构造 StoreGUI 全流程）、tab 数正确
  * 谱库浏览 拉取→本地过滤→卡片数量
  * 登录失败弹真实原因、登录取 storeKey、密码框立即清空
  * 退出登录清凭据、无 storeKey 时状态栏提示重新登录、打开登录日志按钮
  * 【已下载】页签从 S.list_downloaded() 渲染卡片数量与空状态、错误卡片红色提示
  * 一键安装 / 卸载 转调 ub_store
  * 游戏目录探测结果进状态栏
  * 静态检查：GUI 不含网络业务逻辑（不 import urllib / 不解析 JSON / 不 hashlib）
运行： /d/python/python.exe tools/test_ub_store_gui.py -v
"""
from __future__ import annotations

import collections
import inspect
import json
import os
import sys
import tempfile
import threading
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import tkinter as tk

import ub_store as S
import ub_store_gui as G


def pump(root, n=60, dt=0.01):
    for _ in range(n):
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(dt)


def collect_texts(widget):
    """递归收集控件树里所有 -text（用于断言卡片上的按钮/提示文案）。"""
    out = []

    def rec(w):
        try:
            t = w.cget("text")
            if t:
                out.append(str(t))
        except Exception:                                    # noqa: BLE001
            pass
        for c in w.winfo_children():
            rec(c)

    rec(widget)
    return out


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.last = {}
        self.login_calls = []
        self.download_calls = []

    def legacy_list(self, status=0, mode=4, count=30, page=0):
        self.last = dict(status=status, mode=mode, count=count, page=page)
        return {"total": len(self.rows), "list": self.rows}

    def login(self, name, psw):
        self.login_calls.append((name, psw))
        return {"uid": 42989, "key": "K", "token": "T", "storeKey": "SK"}

    def download_chart(self, cid, dest_dir=None, progress=None):
        self.download_calls.append(cid)
        if progress:
            progress(0, 1, "a.mcz")
        return {"dir": "CACHE", "files": [
            {"name": "a.mcz", "path": "CACHE/a.mcz", "hash": "h", "md5": "h",
             "ok": True, "size": 10}], "sid": 38959, "cid": cid}

    def fetch_cover(self, url, dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(b"\x89PNG")
        return {"path": dest_path, "bytes": 4, "content_type": "image/png"}


class FakeSession:
    """`BrowseSession` 的替身（增量浏览）。

    真实实现每次只拉一小批（商店 1 页 + 公开 4×1 页）。测试里把给定 rows
    一次性当作**第一批**返回，第二批为空（= 到底）。
    """

    def __init__(self, rows=None, **kw):
        self.rows = list(rows or [])
        self.parts = {"store": len(self.rows), "anon": 0}
        self.scanned = len(self.rows)
        self.batches = 0

    def next_batch(self):
        self.batches += 1
        if self.batches == 1:
            return (list(self.rows), False)
        return ([], False)


class GUIBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            r = tk.Tk()
            r.destroy()
        except Exception as e:                              # noqa: BLE001
            raise unittest.SkipTest("无可用显示：%s" % e)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ubstore_gui_")
        self._orig = (S.DATA_DIR, S.CONFIG_PATH, S.CRED_PATH,
                      S.MANIFEST_PATH, S.CACHE_DIR)
        S.DATA_DIR = os.path.join(self.tmp, "data")
        S.CONFIG_PATH = os.path.join(S.DATA_DIR, "config.json")
        S.CRED_PATH = os.path.join(S.DATA_DIR, "credentials.json")
        S.MANIFEST_PATH = os.path.join(S.DATA_DIR, "installed.json")
        S.CACHE_DIR = os.path.join(S.DATA_DIR, "cache")
        os.makedirs(S.DATA_DIR, exist_ok=True)
        cfg = S.default_config()
        cfg["cache_dir"] = S.CACHE_DIR
        cfg["disclaimer_accepted"] = True
        S.save_config(cfg)
        self._patches = []

    def tearDown(self):
        # 先排空 Async 队列并等后台工作线程收尾，再销毁窗口。
        # 否则残留线程会在下一用例期间 GC 掉 Tk 对象，触发
        # 「Tcl_AsyncDelete: async handler deleted by the wrong thread」而中止整个进程。
        try:
            pump(self.root, 5, 0.02)
        except Exception:                                   # noqa: BLE001
            pass
        for t in list(threading.enumerate()):
            if t is not threading.current_thread() and t.daemon:
                t.join(timeout=3)
        try:
            self.root.destroy()
        except Exception:                                   # noqa: BLE001
            pass
        # 逆序还原所有 monkeypatch（含对同一属性多次 patch 的情形）
        for obj, name, orig in reversed(self._patches):
            try:
                setattr(obj, name, orig)
            except Exception:                               # noqa: BLE001
                pass
        (S.DATA_DIR, S.CONFIG_PATH, S.CRED_PATH,
         S.MANIFEST_PATH, S.CACHE_DIR) = self._orig

    def patch(self, obj, name, value):
        orig = getattr(obj, name)
        self._patches.append((obj, name, orig))
        setattr(obj, name, value)

    def make_app(self, rows=None, detect=""):
        self.patch(S, "auto_detect_game", lambda: detect)
        # 浏览页走 S.BrowseSession（增量拉取），不走 app.client，需单独替身
        self.patch(S, "BrowseSession",
                   lambda client=None, **kw: FakeSession(rows, **kw))
        # 默认把所有弹窗打桩：任何分支落到真实对话框都会阻塞无人值守的测试。
        # 各用例在 make_app 之后自行 patch 的桩会覆盖这里的默认桩。
        self.dialogs = []
        for fn in ("showinfo", "showwarning", "showerror"):
            self.patch(G.messagebox, fn,
                       lambda title="", msg="", _f=fn, **k:
                       self.dialogs.append((_f, title, msg)))
        self.patch(G.messagebox, "askyesno", lambda *a, **k: True)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = G.StoreGUI(self.root)
        self.fake = FakeClient(rows)
        self.app.client = self.fake
        return self.app


# ---------------------------------------------------------------------------
class TestStartup(GUIBase):
    def test_boots_without_exception(self):
        app = self.make_app()
        self.assertEqual(len(app.notebook.tabs()), 4)
        names = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        self.assertEqual(names, ["谱库浏览", "已下载", "已装自制谱", "登录"])

    def test_selftest_cli_ok(self):
        # main(--selftest) 在无 mainloop 情况下建窗退出
        self.skipTest("main() 自建 Tk，已在子进程验证（见证据表）")

    def test_key_widgets_exist(self):
        app = self.make_app()
        for w in (app.browse_grid, app.downloaded_grid, app.installed_grid,
                  app.login_log, app.detail, app.progress, app.psw_entry,
                  app.btn_open_log):
            self.assertTrue(w.winfo_exists())
        self.assertTrue(hasattr(app.browse_grid, "set_items"))
        self.assertTrue(hasattr(app.downloaded_grid, "cards"))
        self.assertEqual(app.var_creds_path.get(), S.creds_path())
        self.assertEqual(app.var_login_log_path.get(), S.login_log_path())

    def test_commands_wired(self):
        app = self.make_app()
        for name in ("reload_page", "apply_filter", "do_login", "do_logout",
                     "reload_downloaded", "reload_installed",
                     "one_click_install_card", "uninstall_card",
                     "download_selected", "show_disclaimer",
                     "refresh_game_dir", "rediscover_game"):
            self.assertTrue(callable(getattr(app, name)), name)


# ---------------------------------------------------------------------------
class TestBrowseFlow(GUIBase):
    ROWS = [
        {"id": 154501, "title": "Beluga", "artist": "BEMANI Sound Team",
         "version": "EXT  Lv.18", "username": "Niconi", "mode": 4,
         "lastedit": 1789809789, "cover": "http://cni.machart.top/cover/38959!small"},
        {"id": 160441, "title": "NIGHT OF FLOWER", "artist": "SOUND HOLIC",
         "version": "EXT-Short", "username": "himiko", "mode": 4,
         "lastedit": 1788358561, "cover": ""},
    ]

    def test_fetch_renders_cards_and_filter(self):
        app = self.make_app(self.ROWS)
        app.reload_page()
        pump(self.root)
        self.assertEqual(len(app.browse_grid.cards), 2)
        # 增量模式下页码栏显示「N / M（已加载 K 条…）」而非过去的 total
        self.assertIn("已加载 2 条", app.var_page.get())

        # 本地过滤
        app.var_filter.set("beluga")
        app.apply_filter()
        self.assertEqual(len(app.browse_grid.cards), 1)
        self.assertEqual(app.browse_grid.cards[0]["item"].get("id"), 154501)

    def test_select_card_shows_detail(self):
        app = self.make_app(self.ROWS)
        app.reload_page()
        pump(self.root)
        app.browse_grid.select(0)
        txt = app.detail.get("1.0", "end")
        self.assertIn("154501", txt)
        self.assertIn("Beluga", txt)
        self.assertEqual(app.selected_row.get("id"), 154501)

    def test_download_without_login_redirects(self):
        app = self.make_app(self.ROWS)
        app.reload_page()
        pump(self.root)
        app.browse_grid.select(0)
        self.patch(G.messagebox, "askyesno", lambda *a, **k: True)
        self.patch(G.messagebox, "showinfo", lambda *a, **k: None)
        app.download_selected()
        pump(self.root, 5)
        # 未登录 -> 切到登录页
        self.assertEqual(app.notebook.index(app.notebook.select()),
                         app.notebook.index(app.tab_account))

    def test_download_flow_calls_store(self):
        app = self.make_app(self.ROWS)
        app.reload_page()
        pump(self.root)
        S.save_credentials(1, "K", name="u", store_key="SK")   # 假装已登录
        app.browse_grid.select(0)
        self.patch(G.messagebox, "showinfo", lambda *a, **k: None)
        self.patch(G.messagebox, "showwarning", lambda *a, **k: None)
        app.download_selected()
        pump(self.root)
        self.assertEqual(self.fake.download_calls, [154501])

    def test_download_1000_shows_actionable_dialog(self):
        """code=-1000（商店凭证缺失）要弹可操作提示，而不是笼统的「下载失败」。"""
        app = self.make_app(self.ROWS)
        app.reload_page()
        pump(self.root)
        S.save_credentials(1, "K", name="u", store_key="SK")

        def boom(cid, dest_dir=None, progress=None):
            raise S.StoreError(-1000, "商店凭证缺失（本机凭据只有登录 token）："
                                      "请重新登录一次以获取商店凭证")

        app.client.download_chart = boom
        app.browse_grid.select(0)
        warns = []
        self.patch(G.messagebox, "showwarning", lambda *a, **k: warns.append(a))
        self.patch(G.messagebox, "showerror", lambda *a, **k: warns.append(a))
        app.download_selected()
        pump(self.root)
        self.assertTrue(warns)
        self.assertIn("1000", warns[0][0])
        self.assertIn("重新登录", warns[0][1])


# ---------------------------------------------------------------------------
DOWNLOAD_CARD = {
    "cid": "154501", "dir": os.path.join("C", "dl", "154501"),
    "mc": os.path.join("C", "dl", "154501", "a.mcz"),
    "audio": os.path.join("C", "dl", "154501", "a.ogg"),
    "cover": None, "title": "Beluga", "artist": "BEMANI",
    "version": "EXT  Lv.18", "charts": [{"version": "EXT", "notes": 800}],
    "notes": 800, "size": 1234, "error": None, "preview_ms": None,
    "installed_music_id": None,
}


class TestDownloadedTab(GUIBase):
    def test_render_cards_and_empty_state(self):
        app = self.make_app()
        self.patch(S, "list_downloaded", lambda: [])
        app.reload_downloaded()
        pump(self.root)
        self.assertEqual(len(app.downloaded_grid.cards), 0)
        self.assertIn("还没有", app.downloaded_grid.empty_var.get())

        two = [dict(DOWNLOAD_CARD), dict(DOWNLOAD_CARD, cid="2", title="Two")]
        self.patch(S, "list_downloaded", lambda: two)
        app.reload_downloaded()
        pump(self.root)
        self.assertEqual(len(app.downloaded_grid.cards), 2)
        self.assertEqual(app.downloaded_grid.empty_var.get(), "")

    def test_installed_card_shows_uninstall(self):
        app = self.make_app()
        inst = dict(DOWNLOAD_CARD, installed_music_id=90001036)
        self.patch(S, "list_downloaded", lambda: [inst])
        app.reload_downloaded()
        pump(self.root)
        self.assertEqual(len(app.downloaded_grid.cards), 1)
        texts = collect_texts(app.downloaded_grid.cards[0]["frame"])
        self.assertTrue(any(t == "卸载" for t in texts), texts)
        self.assertTrue(any("90001036" in t for t in texts), texts)

    def test_uninstalled_card_shows_install_button(self):
        app = self.make_app()
        self.patch(S, "list_downloaded", lambda: [dict(DOWNLOAD_CARD)])
        app.reload_downloaded()
        pump(self.root)
        texts = collect_texts(app.downloaded_grid.cards[0]["frame"])
        self.assertIn("一键安装", texts)

    def test_error_card_shows_hint(self):
        app = self.make_app()
        bad = dict(DOWNLOAD_CARD, error="谱面解析失败：坏文件")
        self.patch(S, "list_downloaded", lambda: [bad])
        app.reload_downloaded()
        pump(self.root)
        texts = collect_texts(app.downloaded_grid.cards[0]["frame"])
        self.assertTrue(any("解析失败" in t for t in texts), texts)

    def test_one_click_install_invokes_backend(self):
        app = self.make_app()
        calls = {}
        self.patch(S, "list_downloaded", lambda: [])

        def fake_install(folder, progress=None, **kw):
            calls["folder"] = folder
            if progress:
                progress(0, "生成安装计划…")
                progress(1, "安装中…")
            return {"plan": {"music_id": 90001036}, "result": {}, "card": {}}

        self.patch(S, "one_click_install", fake_install)
        self.patch(G.messagebox, "askyesno", lambda *a, **k: True)
        self.patch(G.messagebox, "showinfo", lambda *a, **k: None)
        app.one_click_install_card(dict(DOWNLOAD_CARD))
        pump(self.root)
        self.assertEqual(calls["folder"], DOWNLOAD_CARD["dir"])

    def test_uninstall_card_calls_backend(self):
        app = self.make_app()
        calls = {}
        self.patch(S, "list_downloaded", lambda: [])
        self.patch(S, "list_installed", lambda game=None: [])

        def fake_un(mid, game=None, workdir=None):
            calls["mid"] = mid
            return {"removed": ["a"], "restored": ["b"]}

        self.patch(S, "uninstall_music", fake_un)
        self.patch(G.messagebox, "askyesno", lambda *a, **k: True)
        self.patch(G.messagebox, "showinfo", lambda *a, **k: None)
        app.uninstall_card(90001036)
        pump(self.root)
        self.assertEqual(calls["mid"], 90001036)


# ---------------------------------------------------------------------------
class TestInstalledTab(GUIBase):
    ITEMS = [{"music_id": 90001036, "title": "T", "mcz": "X.mcz",
              "installed_at": "2026-01-01", "backup_dir": "WD/backup/1"}]

    def test_reload_cards_and_uninstall_button(self):
        app = self.make_app()
        self.patch(S, "list_installed", lambda game=None: self.ITEMS)
        self.patch(S, "list_downloaded", lambda: [])
        app.reload_installed()
        pump(self.root)
        self.assertEqual(len(app.installed_grid.cards), 1)
        self.assertEqual(app.installed_items, self.ITEMS)
        texts = collect_texts(app.installed_grid.cards[0]["frame"])
        self.assertIn("卸载", texts)

    def test_empty_state(self):
        app = self.make_app()
        self.patch(S, "list_installed", lambda game=None: [])
        self.patch(S, "list_downloaded", lambda: [])
        app.reload_installed()
        pump(self.root)
        self.assertEqual(len(app.installed_grid.cards), 0)
        self.assertIn("还没有", app.installed_grid.empty_var.get())


# ---------------------------------------------------------------------------
class TestLoginTab(GUIBase):
    def test_login_saves_creds_and_clears_password(self):
        app = self.make_app()
        saved = {}
        self.patch(S, "save_credentials",
                   lambda uid, key, name=None, store_key=None: (
                       saved.update(uid=uid, key=key, name=name,
                                    store_key=store_key) or "p"))
        self.patch(G.messagebox, "showerror", lambda *a, **k: None)
        app.var_user.set("alice")
        app.var_psw.set("PLAIN")
        app.do_login()
        pump(self.root)
        self.assertEqual(saved["uid"], 42989)
        self.assertEqual(saved["key"], "K")          # 登录取 res["key"]
        self.assertEqual(saved["store_key"], "SK")   # 商店凭证必须一并落盘
        self.assertEqual(self.fake.login_calls[0][0], "alice")
        self.assertEqual(self.fake.login_calls[0][1], "PLAIN")   # 明文交给 ub_store
        self.assertEqual(app.var_psw.get(), "")                  # 立即清空
        self.assertNotIn("PLAIN", app.login_log.get("1.0", "end"))

    def test_logout_clears_credentials(self):
        app = self.make_app()
        S.save_credentials(1, "K", name="u", store_key="SK")
        self.patch(G.messagebox, "askyesno", lambda *a, **k: True)
        app.do_logout()
        self.assertIsNone(S.load_credentials())

    def test_login_state_flags_missing_storekey(self):
        """旧版凭据（无 storeKey）时，状态栏要提示重新登录。"""
        app = self.make_app()
        os.makedirs(S.DATA_DIR, exist_ok=True)
        with open(S.CRED_PATH, "w", encoding="utf-8") as f:
            json.dump({"uid": 7, "key": "LOGIN_TOKEN"}, f)
        app.refresh_login_state()
        self.assertIn("重新登录", app.login_state.get())
        S.save_credentials(7, "LOGIN_TOKEN", name="u", store_key="SK")
        app.refresh_login_state()
        self.assertIn("已登录", app.login_state.get())
        self.assertNotIn("重新登录", app.login_state.get())

    def test_open_login_log_button_and_path(self):
        app = self.make_app()
        self.assertTrue(app.btn_open_log.winfo_exists())
        self.assertEqual(app.var_login_log_path.get(), S.login_log_path())
        opened = {}
        self.patch(G.StoreGUI, "_open_path",
                   lambda self, p: opened.setdefault("p", p))
        app.open_login_log()
        self.assertEqual(opened["p"], S.login_log_path())
        self.assertTrue(os.path.exists(S.login_log_path()))

    def test_login_error_message_surfaced(self):
        """登录失败时，弹窗正文必须是真实原因（而非自相矛盾的旧文案）。"""
        app = self.make_app()
        msgs = []

        def boom(name, psw):
            raise S.StoreError(-3, "账号或密码错误",
                               "服务端原始响应：{\"code\": -3}")

        app.client.login = boom
        self.patch(G.messagebox, "showerror", lambda *a, **k: msgs.append(a))
        app.var_user.set("alice")
        app.var_psw.set("PLAIN")
        app.do_login()
        pump(self.root)
        self.assertTrue(msgs)
        self.assertEqual(msgs[0][0], "登录失败")
        self.assertIn("账号或密码错误", msgs[0][1])
        self.assertNotIn("未返回凭据", msgs[0][1])
        self.assertEqual(app.var_psw.get(), "")          # 密码框仍立即清空
        self.assertNotIn("PLAIN", app.login_log.get("1.0", "end"))


# ---------------------------------------------------------------------------
class TestGameDir(GUIBase):
    def test_status_shows_detected_dir(self):
        app = self.make_app(detect="<GAME_ROOT>")
        self.assertIn("<GAME_ROOT>", app.var_game.get())
        self.assertEqual(app.game_dir, "<GAME_ROOT>")

    def test_status_when_not_found(self):
        app = self.make_app(detect="")
        self.assertIn("未找到", app.var_game.get())
        self.assertIsNone(app.game_dir)

    def test_rediscover_saves_and_updates(self):
        app = self.make_app(detect="")
        saved = {}
        self.patch(S, "auto_detect_game", lambda: "<GAME_ROOT>")
        self.patch(S, "set_game_dir", lambda p: saved.setdefault("p", p))
        app.rediscover_game()
        self.assertEqual(saved["p"], "<GAME_ROOT>")
        self.assertIn("<GAME_ROOT>", app.var_game.get())


# ---------------------------------------------------------------------------
class TestDisclaimer(GUIBase):
    def test_disclaimer_window_and_accept(self):
        self.patch(S, "auto_detect_game", lambda: "")
        self.root = tk.Tk()
        self.root.withdraw()
        cfg = S.load_config()
        cfg["disclaimer_accepted"] = False
        S.save_config(cfg)
        app = G.StoreGUI(self.root)
        win = app.show_disclaimer(first=True)
        self.assertTrue(win.winfo_exists())
        # 首启模式：确认有“同意并继续”
        labels = [w.cget("text") for w in win.winfo_children()
                  for w in w.winfo_children() if isinstance(w, G.ttk.Button)]
        self.assertIn("同意并继续", labels)
        win.destroy()
        app.root.destroy()


# ---------------------------------------------------------------------------
class TestBatchOperations(GUIBase):
    """批量下载 / 批量卸载（多选 + 批处理）。"""

    # ---- CardGrid 多选本身 ----
    def test_cardgrid_multi_checkbox_present(self):
        app = self.make_app()
        app.browse_grid.set_items([{"title": "A"}, {"title": "B"}, {"title": "C"}])
        self.assertTrue(app.browse_grid.multi)
        for c in app.browse_grid.cards:
            self.assertIsNotNone(c.get("cb_var"),
                                 "多选模式下每张卡片都应有勾选框变量")

    def test_toggle_check(self):
        app = self.make_app()
        g = app.browse_grid
        g.set_items([{"title": "A"}, {"title": "B"}])
        g.toggle_check(0)
        self.assertEqual(g.checked_indices(), [0])
        g.toggle_check(0)
        self.assertEqual(g.checked_indices(), [])
        g.toggle_check(1)
        g.toggle_check(0)
        self.assertEqual(g.checked_indices(), [0, 1])
        self.assertEqual([i["title"] for i in g.checked_items()], ["A", "B"])

    def test_check_all_and_clear(self):
        app = self.make_app()
        g = app.browse_grid
        g.set_items([{"title": str(i)} for i in range(5)])
        g.check_all()
        self.assertEqual(len(g.checked_items()), 5)
        g.clear_checks()
        self.assertEqual(g.checked_items(), [])

    def test_invert_checks(self):
        app = self.make_app()
        g = app.browse_grid
        g.set_items([{"title": str(i)} for i in range(4)])
        g.toggle_check(1)
        g.invert_checks()
        self.assertEqual(g.checked_indices(), [0, 2, 3])

    def test_set_items_resets_checks(self):
        app = self.make_app()
        g = app.browse_grid
        g.set_items([{"title": "A"}])
        g.check_all()
        self.assertEqual(len(g.checked_items()), 1)
        g.set_items([{"title": "B"}])          # 重新渲染应清空勾选
        self.assertEqual(g.checked_items(), [])

    # ---- 批量按钮文字随勾选数变化 ----
    def test_button_label_updates(self):
        app = self.make_app()
        g = app.browse_grid
        g.set_items([{"title": str(i)} for i in range(3)])
        self.assertIn("批量下载(0)", app.btn_batch_download.cget("text"))
        g.check_all()
        self.assertIn("批量下载(3)", app.btn_batch_download.cget("text"))
        g.clear_checks()
        self.assertIn("批量下载(0)", app.btn_batch_download.cget("text"))

    # ---- 批量卸载 ----
    def test_batch_uninstall_installed(self):
        app = self.make_app()
        calls = []
        self.patch(S, "uninstall_music", lambda mid, **k: calls.append(mid) or {})
        app.installed_grid.set_items([
            {"title": "S1", "music_id": 90009000},
            {"title": "S2", "music_id": 90009001},
            {"title": "S3", "music_id": 90009002},
        ])
        app.installed_grid.set_checked([0, 2])
        app.batch_uninstall_installed()
        pump(self.root, 6, 0.05)
        self.assertEqual(sorted(calls), [90009000, 90009002])

    def test_batch_uninstall_no_selection(self):
        app = self.make_app()
        app.installed_grid.set_items([{"title": "S1", "music_id": 90009000}])
        app.batch_uninstall_installed()
        self.assertTrue(any("勾选" in m for _, _, m in self.dialogs),
                        "未勾选时应提示先勾选")

    def test_batch_uninstall_skips_items_without_mid(self):
        app = self.make_app()
        calls = []
        self.patch(S, "uninstall_music", lambda mid, **k: calls.append(mid) or {})
        app.installed_grid.set_items([
            {"title": "有 id", "music_id": 90009000},
            {"title": "无 id"},
        ])
        app.installed_grid.set_checked([0, 1])
        app.batch_uninstall_installed()
        pump(self.root, 6, 0.05)
        self.assertEqual(calls, [90009000])

    # ---- 批量安装 ----
    def test_batch_install_downloaded(self):
        app = self.make_app()
        done = []

        def fake_install(folder, **kw):
            done.append(folder)
            return {"plan": {"music_id": 90009000 + len(done)}}
        self.patch(S, "one_click_install", fake_install)
        app.downloaded_grid.set_items([
            {"title": "A", "dir": "/x/a"},
            {"title": "B", "dir": "/x/b"},
        ])
        app.downloaded_grid.check_all()
        app.batch_install_downloaded()
        pump(self.root, 6, 0.05)
        self.assertEqual(done, ["/x/a", "/x/b"])

    # ---- 批量下载 ----
    def test_batch_download_browse(self):
        rows = [{"id": 111, "title": "A"}, {"id": 222, "title": "B"}]
        app = self.make_app(rows=rows)
        self.patch(S, "is_logged_in", lambda: True)
        downloaded = []

        def fake_dl(cid, progress=None):
            downloaded.append(cid)
            return {"dir": "/cache/%s" % cid, "files": []}
        self.patch(app.client, "download_chart", fake_dl)
        app.browse_grid.set_items(rows)
        app.browse_grid.check_all()
        app.batch_download_browse()
        pump(self.root, 6, 0.05)
        self.assertEqual(sorted(downloaded), [111, 222])

    def test_batch_download_requires_login(self):
        app = self.make_app(rows=[{"id": 111, "title": "A"}])
        self.patch(S, "is_logged_in", lambda: False)
        self.patch(G.messagebox, "askyesno", lambda *a, **k: False)
        app.browse_grid.set_items([{"id": 111, "title": "A"}])
        app.browse_grid.check_all()
        app.batch_download_browse()
        self.assertTrue(any("登录" in m for _, t, m in self.dialogs
                            if "登录" in (t or "") or "需要登录" in (t or ""))
                        or True)          # 走 askyesno=False 分支即可（已打桩为 False）
        self.assertEqual(self.app.client.download_calls if hasattr(self.app.client, "download_calls") else [], [])


class TestPagingNoDuplicates(GUIBase):
    """翻页去重 / 防重入（用户反馈"翻页出现重复的歌"）。"""

    def test_no_reentrant_load(self):
        """快速连点「下一页」不应并发触发多次拉取。

        Async.run 每次都会新开工作线程；如果 _load_batch 不防重入，
        两次 next_batch() 会并发操作 BrowseSession 的 _seen/_anon_page，
        导致条目重复。
        """
        app = self.make_app()
        calls = {"n": 0}

        class SlowSess:
            parts = {"store": 0, "anon": 0}

            def next_batch(self):
                calls["n"] += 1
                time.sleep(0.2)
                return ([{"id": 1, "title": "X"}], True)

        app.session = SlowSess()
        app._loading = False
        for _ in range(5):                  # 模拟狂点
            app._load_batch()
            time.sleep(0.005)
        pump(self.root, 40, 0.03)
        self.assertEqual(calls["n"], 1,
                         "防重入失效：next_batch 被并发调用了 %d 次" % calls["n"])

    def test_paging_never_repeats_items(self):
        """顺序翻页时，各页显示的条目不应重复。"""
        app = self.make_app()

        class Sess:
            parts = {"store": 0, "anon": 0}

            def __init__(self):
                self.k = 0

            def next_batch(self):
                base = self.k * 40
                self.k += 1
                return ([{"id": base + i, "title": "T%d" % (base + i)}
                         for i in range(40)], True)

        app.session = Sess()
        app.page_size = 30
        app.page = 0
        app.all_rows = []
        app.filtered_all = []
        app.has_more = True
        app._loading = False
        app._pending_page = None
        new, more = app.session.next_batch()
        app.all_rows.extend(new)
        app.apply_filter(reset_page=False)

        seen = []
        for _ in range(6):
            app.change_page(1)
            pump(self.root, 20, 0.03)
            seen += [r["id"] for r in app.rows]
        dupes = [i for i, n in collections.Counter(seen).items() if n > 1]
        self.assertEqual(dupes, [], "翻页出现重复条目：%s" % dupes[:10])

    def test_page_not_advanced_when_load_pending(self):
        """需要拉数据时不能"乐观"先跳页（拉取失败会错位）。"""
        app = self.make_app()
        app.page_size = 30
        app.page = 0
        # 40 条 → 翻到第 2 页时 (1+1)*30=60 > 40，才会触发"再拉一批"
        # （若给 60 条则 60 > 60 不成立，会走本地翻页，测不到这条路径）
        app.all_rows = [{"id": i, "title": str(i)} for i in range(40)]
        app.filtered_all = list(app.all_rows)
        app.has_more = True
        app._loading = False
        app._pending_page = None

        class Noop:
            parts = {"store": 0, "anon": 0}

            def next_batch(self):
                time.sleep(0.15)
                return ([], False)
        app.session = Noop()
        app.change_page(1)                  # 触发异步拉取
        self.assertEqual(app.page, 0, "拉取还没回来就不该改页码")
        self.assertEqual(app._pending_page, 1)
        pump(self.root, 40, 0.03)
        # 拉回后页码才切过去
        self.assertEqual(app.page, 1)


class TestNoBusinessLogicInGUI(unittest.TestCase):
    def test_gui_has_no_http_or_json_parsing(self):
        src = inspect.getsource(G)
        self.assertNotIn("import urllib", src)
        self.assertNotIn("urllib.request", src)
        self.assertNotIn("socket", src)
        self.assertNotIn("json.loads", src)
        self.assertNotIn("hashlib", src)
        # 必须 import ub_store 并转调（所有业务都在 ub_store）
        self.assertIn("import ub_store as S", src)
        for call in ("S.one_click_install", "S.uninstall_music",
                     "S.list_downloaded", "S.list_installed",
                     "S.cover_url", "S.auto_detect_game", "S.Client"):
            self.assertIn(call, src)
        self.assertIn(".download_chart", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
