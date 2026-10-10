#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ub_store_gui.py -- Malody 谱库浏览 / 下载 / 装进 jubeat 的 Tkinter 界面

只负责“界面 + 交互”；**所有业务逻辑都在 ub_store.py**（本文件不实现 HTTP/解析/安装）。
纯 stdlib（tkinter/ttk）；封面缩略图若可用则借 Pillow 显示，否则降级为占位块。

版式（参考网站歌曲展示，而非纯文字列表）：
  * 【谱库浏览】封面卡片网格（封面 / 曲名 / 艺人 / 难度角标），点选后在右侧看详情并下载。
  * 【已下载】  同款卡片网格，卡片下方【一键安装】/【卸载】，无需手选任何路径。
  * 【已装自制谱】同款卡片网格，卡片下方【卸载】。
  * 【登录】    保持不变。

安全：不内置任何账号密码；密码仅在登录框内即时交给 ub_store.login()（本地 md5），
     不写日志、不落盘。凭据（uid/登录 token/商店凭证）路径在界面上明示，
     并提供【退出登录/清除凭据】。
"""
from __future__ import annotations

import os
import queue
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import ub_store as S
try:
    import ub_update as U                  # 自更新模块（源码模式可能缺，容错）
except Exception:                                        # noqa: BLE001
    U = None
try:
    import ub_import as I                  # 本地导入（谱面 / 更新包）
except Exception:                                        # noqa: BLE001
    I = None
try:
    import ub_updater as UU                # 官方更新包应用器
except Exception:                                        # noqa: BLE001
    UU = None
try:
    import ub_font as FONT                 # 自带 CJK 字库（日文系统防方框）
except Exception:                                        # noqa: BLE001
    FONT = None

#: 界面统一字体族名。
#: 直接把族名写进每个控件，而不是依赖 ``font=("", N)`` 的"系统默认字体"——
#: 日文系统上"系统默认"是 MS UI Gothic，不含简体字形；而且空族名不一定跟着
#: TkDefaultFont 走，写死最可靠。注册失败时这个族名不存在，tkinter 会自动回退。
UI_FONT = FONT.FONT_FAMILY if FONT is not None else ""

APP_TITLE = "jubeat 自制谱商店 — Malody 谱库 → 下载 → 装进 jubeat"
DISCLAIMER = (
    "【免责声明】\n\n"
    "1. 本工具仅用于个人学习与本地游戏研究；谱面、曲目、曲绘的版权归原作者与版权方所有，\n"
    "   请勿用于商业用途或二次分发。使用者自行承担一切后果。\n"
    "2. 本工具不绕过任何鉴权、不破解、不内置账号密码：下载走 Malody 官方/公开接口，\n"
    "   登录凭据由使用者自行提供并仅保存在本机。\n"
    "3. 所有网络请求均带限速与重试上限；请遵守 Malody 社区服务条款，尊重创作者。\n"
    "4. 安装会向游戏目录写入新曲（只新增、不覆盖），并提供卸载与备份还原；\n"
    "   请务必先备份你的游戏数据。\n\n"
    "点击【同意并继续】表示你已阅读并接受上述条款。"
)

# ---- 深色主题色板 ----
C = {
    "bg": "#1e2027", "bg2": "#262933", "bg3": "#2f3340",
    "fg": "#e6e8ee", "fg_dim": "#9aa0ad", "accent": "#4f9cf9",
    "accent2": "#3fb37f", "danger": "#e06c75", "warn": "#e5c07b",
    "border": "#3a3f4b",
}

# ---- 卡片尺寸 ----
GRID_PAD = 10
CARD_W = 168
CARD_H = 252
COVER = 150


def _short_text(text, n):
    s = str(text)
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------------------
# 小工具：网络任务丢到后台线程，结果回到主线程（GUI 不卡）
# ---------------------------------------------------------------------------
class Async:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self._stopped = False
        self._after_id = None
        self._poll()

    def stop(self):
        self._stopped = True
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:                            # noqa: BLE001
                pass
            self._after_id = None

    def _poll(self):
        if self._stopped:
            return
        try:
            while True:
                fn, args, kwargs = self.q.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception:                        # noqa: BLE001
                    pass
        except queue.Empty:
            pass
        try:
            self._after_id = self.root.after(60, self._poll)
        except tk.TclError:                              # 窗口已销毁
            self._stopped = True

    def run(self, work, on_done=None, on_error=None):
        """work: callable -> 结果；on_done/on_error 在主线程执行。"""
        def _wrap():
            try:
                res = work()
            except Exception as ex:                      # noqa: BLE001
                if on_error:
                    self.q.put((on_error, (ex,), {}))
                return
            if on_done:
                self.q.put((on_done, (res,), {}))
        threading.Thread(target=_wrap, daemon=True).start()


# ---------------------------------------------------------------------------
# 可滚动的封面卡片网格（只做渲染 + 选中，不含业务逻辑）
# ---------------------------------------------------------------------------
class CardGrid(ttk.Frame):
    """封面卡片网格。

    set_items(items, footers=, badge_of=, cover_of=, empty_text=)
      * footers(item, parent_frame) 在卡片底部构造按钮/提示（各页不同）
      * badge_of(item) 返回右上角角标文字（难度/等级等），可为 None
      * cover_of(item) 返回封面描述：("local", 本地路径) / ("url", url, key) / None
      * cover_loader(spec, callback) 由外部注入（可异步）；callback(PhotoImage|None)
    """

    def __init__(self, master, *, card_w=CARD_W, card_h=CARD_H, cover_size=COVER,
                 cover_loader=None, on_select=None, multi=False, on_check=None):
        super().__init__(master)
        self.card_w = card_w
        self.card_h = card_h
        self.cover_size = cover_size
        self.cover_loader = cover_loader
        self.on_select = on_select
        #: 多选模式：卡片左上角出现勾选框，供批量下载/批量卸载使用
        self.multi = multi
        #: 勾选状态变化时的回调（用于刷新"批量处理(N)"按钮文字）
        self.on_check = on_check
        self.cards = []
        self._cols = 0
        self._selected = -1
        #: 多选模式下已勾选的卡片索引
        self._checked = set()

        self.canvas = tk.Canvas(self, bg=C["bg"], highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, bg=C["bg"])
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.vsb_visible = True
        self.inner.bind("<Configure>", self._sync_scroll)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

        self.empty_var = tk.StringVar(value="")
        self.empty_label = tk.Label(self.inner, textvariable=self.empty_var,
                                    bg=C["bg"], fg=C["fg_dim"], font=(UI_FONT, 11))

    # ---- 滚动 ----
    def _sync_scroll(self, _e=None):
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            pass

    def _on_canvas(self, e):
        try:
            self.canvas.itemconfigure(self._win, width=max(1, e.width))
        except tk.TclError:
            pass
        cols = max(1, (max(1, e.width) - GRID_PAD) // (self.card_w + GRID_PAD))
        if cols != self._cols:
            self._cols = cols
            self._layout()
        self._sync_scroll()

    def _bind_wheel(self, _e=None):
        try:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        except tk.TclError:
            pass

    def _unbind_wheel(self, _e=None):
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass

    def _on_wheel(self, e):
        try:
            self.canvas.yview_scroll(int(-e.delta / 120), "units")
        except tk.TclError:
            pass

    # ---- 布局 ----
    def _layout(self):
        cols = self._cols or 4
        for i, c in enumerate(self.cards):
            r, col = divmod(i, cols)
            try:
                c["frame"].grid(row=r, column=col, padx=GRID_PAD // 2,
                                pady=GRID_PAD // 2, sticky="n")
            except tk.TclError:
                pass
        for col in range(cols):
            self.inner.grid_columnconfigure(col, weight=1)

    # ---- 数据 ----
    def set_items(self, items, *, footers=None, badge_of=None, cover_of=None,
                  empty_text=""):
        for w in list(self.inner.winfo_children()):
            w.destroy()
        # 空状态标签会随上面的销毁一并消失，这里重建
        self.empty_label = tk.Label(self.inner, textvariable=self.empty_var,
                                    bg=C["bg"], fg=C["fg_dim"], font=(UI_FONT, 11))
        self.cards = []
        self._selected = -1
        self._checked = set()
        self._cols = 0
        for i, it in enumerate(items):
            self.cards.append(self._make_card(it, footers, badge_of, i))
        if self.on_check:
            try:
                self.on_check()
            except Exception:                                # noqa: BLE001
                pass

        if empty_text and not self.cards:
            self.empty_var.set(empty_text)
            self.empty_label.grid(row=0, column=0, padx=40, pady=40, sticky="w")
            self._cols = 4
        else:
            self.empty_var.set("")
            try:
                self.empty_label.grid_remove()
            except tk.TclError:
                pass

        self._layout()
        self._sync_scroll()

        if cover_of and self.cover_loader:
            for i, it in enumerate(items):
                spec = cover_of(it)
                if spec is None:
                    self.set_cover(i, None)
                    continue
                self.cover_loader(spec, lambda ph, i=i: self.set_cover(i, ph))

    def _make_card(self, item, footers, badge_of, idx=0):
        f = tk.Frame(self.inner, bg=C["bg2"], width=self.card_w, height=self.card_h,
                     highlightbackground=C["border"], highlightthickness=2)
        f.grid_propagate(False)

        # 多选模式：左上角勾选框（放在封面之上，避免被 cover 的 place 盖住）
        cb_var = None
        if self.multi:
            cb_var = tk.BooleanVar(master=f, value=idx in self._checked)
            cb = tk.Checkbutton(
                f, variable=cb_var, bg=C["bg2"], activebackground=C["bg2"],
                highlightthickness=0, bd=0, cursor="hand2",
                command=lambda i=idx: self.toggle_check(i))
            cb.place(x=3, y=3)
            # holder/cover 是后创建的子控件，会盖住勾选框 → 提到最上层
            try:
                cb.lift()
            except tk.TclError:
                pass

        holder = tk.Frame(f, bg=C["bg3"], width=self.cover_size,
                          height=self.cover_size)
        holder.pack(pady=(10, 4))
        holder.pack_propagate(False)
        cover = tk.Label(holder, bg=C["bg3"], fg=C["fg_dim"], text="载入中…",
                         font=(UI_FONT, 9))
        cover.place(x=0, y=0, relwidth=1, relheight=1)
        badge = tk.Label(holder, bg=C["accent"], fg="#ffffff", font=(UI_FONT, 8, "bold"),
                         padx=5, pady=1)
        bt = badge_of(item) if badge_of else None
        if bt:
            badge.configure(text=str(bt))
            badge.place(relx=1.0, x=-4, y=4, anchor="ne")

        title = tk.Label(f, text=_short_text(item.get("title") or "(无标题)", 18),
                         bg=C["bg2"], fg=C["fg"], font=(UI_FONT, 10, "bold"),
                         wraplength=self.card_w - 18, justify="left", anchor="w")
        title.pack(fill="x", padx=9, pady=(4, 0))
        artist = tk.Label(f, text=_short_text(item.get("artist") or "-", 22),
                          bg=C["bg2"], fg=C["fg_dim"], font=(UI_FONT, 8),
                          wraplength=self.card_w - 18, justify="left", anchor="w")
        artist.pack(fill="x", padx=9)

        footer = tk.Frame(f, bg=C["bg2"])
        footer.pack(side="bottom", fill="x", padx=7, pady=7)
        if footers:
            footers(item, footer)

        card = {"frame": f, "cover": cover, "item": item, "photo": None,
                "cb_var": cb_var, "idx": idx}

        def on_click(_e=None, i=idx):
            self.select(i)

        for w in (f, holder, cover, title, artist):
            w.bind("<Button-1>", on_click)
        return card

    # ---- 多选（批量下载 / 批量卸载）----
    def toggle_check(self, idx):
        """勾选/取消一张卡片。"""
        if idx in self._checked:
            self._checked.discard(idx)
        else:
            self._checked.add(idx)
        card = self.cards[idx] if 0 <= idx < len(self.cards) else None
        if card and card.get("cb_var") is not None:
            try:
                card["cb_var"].set(idx in self._checked)
            except tk.TclError:
                pass
        self._notify_check()

    def checked_indices(self):
        return sorted(i for i in self._checked if 0 <= i < len(self.cards))

    def checked_items(self):
        return [self.cards[i]["item"] for i in self.checked_indices()]

    def set_checked(self, indices, value=True):
        """批量设置勾选状态（索引集合）。"""
        for i in indices:
            if value:
                self._checked.add(i)
            else:
                self._checked.discard(i)
        for i, c in enumerate(self.cards):
            if c.get("cb_var") is not None:
                try:
                    c["cb_var"].set(i in self._checked)
                except tk.TclError:
                    pass
        self._notify_check()

    def check_all(self):
        self.set_checked(range(len(self.cards)), True)

    def clear_checks(self):
        self.set_checked(range(len(self.cards)), False)

    def invert_checks(self):
        """反选：只对"可勾选"的卡片生效（由 skip_check 决定）。"""
        newset = set()
        for i, c in enumerate(self.cards):
            if c.get("cb_var") is None:
                continue                                 # 该卡片不可勾选，跳过
            if i not in self._checked:
                newset.add(i)
        self.set_checked(range(len(self.cards)), False)
        for i in newset:
            self._checked.add(i)
        for i, c in enumerate(self.cards):
            if c.get("cb_var") is not None:
                try:
                    c["cb_var"].set(i in self._checked)
                except tk.TclError:
                    pass
        self._notify_check()

    def _notify_check(self):
        if self.on_check:
            try:
                self.on_check()
            except Exception:                                # noqa: BLE001
                pass

    # ---- 选中 / 封面 ----
    def select(self, idx):
        if idx < 0 or idx >= len(self.cards):
            return
        self._selected = idx
        for i, c in enumerate(self.cards):
            try:
                c["frame"].configure(
                    highlightbackground=C["accent"] if i == idx else C["border"])
            except tk.TclError:
                pass
        if self.on_select:
            self.on_select(self.cards[idx]["item"], idx)

    def set_cover(self, idx, ph, fallback="无封面"):
        if idx < 0 or idx >= len(self.cards):
            return
        card = self.cards[idx]
        lbl = card["cover"]
        try:
            if not lbl.winfo_exists():
                return
            if ph is None:
                lbl.configure(image="", text=fallback)
            else:
                lbl.configure(image=ph, text="")
                lbl.image = ph
                card["photo"] = ph
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------
class StoreGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = S.load_config()
        self.client = S.Client(self.cfg)
        self.async_ = Async(root)

        # 谱库浏览状态
        # 数据流：all_rows(全量) → filtered_all(关键词过滤后) → rows(当前页，仅用于渲染)
        self.page = 0
        self.page_size = 30
        self.total = 0
        self.rows = []
        self.filtered = []
        self.all_rows = []          # 取全后的完整列表（修「列表获取不全」的核心）
        self.filtered_all = []
        self.source_label = ""
        self.session = None          # BrowseSession：增量拉取会话
        self.has_more = True
        #: 正在拉取中（防重入：Async 每次新开线程，快速连点会并发拉取 → 条目重复）
        self._loading = False
        #: 等待数据到位后再切到的目标页（拉取失败/到底时不会跳错页）
        self._pending_page = None
        #: 是否成功拉过至少一批（否则首屏空列表只是"还没拉到"，不是"没有曲"）
        self._ever_loaded = False
        self.selected_row = None
        # 已下载 / 已装
        self.downloaded_items = []
        self.installed_items = []
        self.selected_downloaded = None
        self.selected_installed = None
        self._installed_covers = {}
        # 游戏目录 / 封面缓存
        self.game_dir = None
        self._cover_cache = {}

        self._setup_theme()
        self._build_menu()
        self._build()
        root.bind("<Destroy>", self._on_destroy, add="+")
        self._post_start()

    def _post_start(self):
        """启动后的收尾：清理遗留文件、启用拖放、按配置做静默版本检查。"""
        try:
            if U is not None and U.cleanup_old():
                self.set_status("已清理上次更新留下的旧文件")
        except Exception:                                    # noqa: BLE001
            pass
        # 字体状态提示：标题栏/菜单栏由系统绘制，系统没有中文字体时
        # 必须把**会话界面字体**也换掉才有救；这一步只在交互式会话里能做，
        # 经 SSH/服务启动会拿到 1459(ERROR_REQUIRES_INTERACTIVE_WINDOWSTATION)。
        try:
            info = getattr(self, "_font_info", None) or {}
            if info.get("session_ui"):
                self.set_status("已启用自带字库（含标题栏/菜单）")
            elif info.get("family") and info.get("had_cjk") is False:
                note = str(info.get("session_ui_note") or "")
                if "1459" in note:
                    self.set_status("提示：需在桌面直接运行才能修复标题栏中文")
                else:
                    self.set_status("提示：标题栏字体未能切换（%s）" % note)
        except Exception:                                    # noqa: BLE001
            pass
        self._enable_drop()
        if U is None:
            return
        if not self.cfg.get("check_update_on_start", True):
            return
        # 延迟 2s，别和首屏加载抢网络
        try:
            self.root.after(2000, lambda: self.check_update(interactive=False))
        except Exception:                                    # noqa: BLE001
            pass

    # ---- 拖放（原生 WM_DROPFILES，不依赖 tkinterdnd2）----
    def _enable_drop(self):
        """把文件拖到窗口上即可导入。任何环节失败都静默跳过，不影响主功能。"""
        if os.name != "nt" or any(a in sys.argv for a in
                                  ("--selftest", "--fontcheck", "--depcheck")):
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
            hwnd = user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            self._drop_hwnd = hwnd
            shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)

            WM_DROPFILES = 0x0233
            GWLP_WNDPROC = -4
            # WNDPROC 的返回类型必须按指针宽度来：LRESULT 在 64 位下是 64 位，
            # 用 c_long（32 位）会在回调返回时被截断，ctypes 报
            # "Exception ignored while converting result of ctypes callback"。
            LRESULT = (ctypes.c_ssize_t if ctypes.sizeof(ctypes.c_void_p) == 8
                       else ctypes.c_long)
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND,
                                         ctypes.c_uint, wintypes.WPARAM,
                                         wintypes.LPARAM)
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_p, set_p = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
                get_p.restype = ctypes.c_void_p
                get_p.argtypes = [wintypes.HWND, ctypes.c_int]
                set_p.restype = ctypes.c_void_p
                set_p.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            else:
                get_p, set_p = user32.GetWindowLongW, user32.SetWindowLongW
                get_p.restype = ctypes.c_long
                get_p.argtypes = [wintypes.HWND, ctypes.c_int]
                set_p.restype = ctypes.c_long
                set_p.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]

            old_proc = get_p(hwnd, GWLP_WNDPROC)

            # ★ 必须显式声明 CallWindowProcW 的类型：否则 64 位下 old_proc
            #   会被 ctypes 当成 int 截断，回调直接报 "Exception ignored"。
            CB = (ctypes.c_void_p if ctypes.sizeof(ctypes.c_void_p) == 8
                  else ctypes.c_long)
            user32.CallWindowProcW.restype = CB
            user32.CallWindowProcW.argtypes = [CB, wintypes.HWND, ctypes.c_uint,
                                               wintypes.WPARAM, wintypes.LPARAM]
            # DragQueryFileW 也要声明，避免参数被当 int
            shell32.DragQueryFileW.restype = ctypes.c_uint
            shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, ctypes.c_uint,
                                               ctypes.c_wchar_p, ctypes.c_uint]
            shell32.DragFinish.argtypes = [wintypes.HANDLE]

            def handler(h, msg, wp, lp):
                if msg == WM_DROPFILES:
                    try:
                        n = shell32.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)
                        buf = ctypes.create_unicode_buffer(32768)
                        paths = []
                        for i in range(n):
                            if shell32.DragQueryFileW(wp, i, buf, 32768):
                                paths.append(buf.value)
                        shell32.DragFinish(wp)
                        if paths:
                            self.root.after(0, lambda p=list(paths): self._on_drop(p))
                    except Exception:                        # noqa: BLE001
                        pass
                    return 0
                try:
                    return user32.CallWindowProcW(old_proc, h, msg, wp, lp)
                except Exception:                            # noqa: BLE001
                    return 0

            # ★ 必须保住引用，否则回调被 GC 会直接崩窗口
            self._wndproc = WNDPROC(handler)
            set_p(hwnd, GWLP_WNDPROC, ctypes.cast(self._wndproc, ctypes.c_void_p))
            self._drop_ok = True
        except Exception:                                    # noqa: BLE001
            self._wndproc = None
            self._drop_ok = False

    def _on_drop(self, paths):
        """处理拖入的文件/目录。"""
        if I is None:
            messagebox.showinfo("提示", "缺少 ub_import.py 模块。")
            return
        found = I.classify_paths(paths)
        self._handle_found(found, hint="拖入 %d 项" % len(paths))

        if not self.cfg.get("disclaimer_accepted"):
            self._disc_after = self.root.after(
                200, lambda: self.show_disclaimer(first=True))

    def _on_destroy(self, event=None):
        if event is None or getattr(event, "widget", None) is self.root:
            self.async_.stop()
            if getattr(self, "_disc_after", None) is not None:
                try:
                    self.root.after_cancel(self._disc_after)
                except Exception:                        # noqa: BLE001
                    pass
                self._disc_after = None

    # ---- 主题 ----
    def _setup_theme(self):
        r = self.root
        # ★ 先挂自带字库：
        #   ① 注册到进程（tkinter 控件用）
        #   ② 系统本身没有中文字体时，还把**当前会话的界面字体**也切过来——
        #      否则窗口标题栏 / 菜单栏（系统绘制的非客户区）照样是方框。
        self._font_info = {}
        try:
            if FONT is not None:
                self._font_info = FONT.ensure_ui_font()
                self._font_ok = FONT.apply_to_tk(r)
            else:
                self._font_ok = False
        except Exception:                                    # noqa: BLE001
            self._font_ok = False
        r.title(APP_TITLE)
        r.geometry("1120x740")
        r.minsize(940, 620)
        r.configure(bg=C["bg"])
        st = ttk.Style(r)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=C["bg"], foreground=C["fg"],
                     fieldbackground=C["bg2"], bordercolor=C["border"],
                     focuscolor=C["accent"])
        st.configure("TFrame", background=C["bg"])
        st.configure("Card.TFrame", background=C["bg2"], relief="flat")
        st.configure("TLabel", background=C["bg"], foreground=C["fg"])
        st.configure("Dim.TLabel", background=C["bg"], foreground=C["fg_dim"])
        st.configure("Head.TLabel", background=C["bg"], foreground=C["accent"],
                     font=(UI_FONT, 12, "bold"))
        st.configure("TButton", background=C["bg3"], foreground=C["fg"],
                     borderwidth=0, padding=(10, 6))
        st.map("TButton", background=[("active", C["accent"])],
               foreground=[("active", "#ffffff")])
        st.configure("Accent.TButton", background=C["accent"], foreground="#ffffff")
        st.map("Accent.TButton", background=[("active", "#6fb0ff")])
        st.configure("Danger.TButton", background=C["danger"], foreground="#ffffff")
        st.map("Danger.TButton", background=[("active", "#f0808a")])
        st.configure("TEntry", fieldbackground=C["bg2"], foreground=C["fg"],
                     insertcolor=C["fg"])
        st.configure("TCombobox", fieldbackground=C["bg2"], foreground=C["fg"],
                     background=C["bg3"], arrowcolor=C["fg"])
        st.configure("TNotebook", background=C["bg"], borderwidth=0)
        st.configure("TNotebook.Tab", background=C["bg3"], foreground=C["fg_dim"],
                     padding=(16, 8))
        st.map("TNotebook.Tab", background=[("selected", C["bg2"])],
               foreground=[("selected", C["accent"])])
        st.configure("TCheckbutton", background=C["bg"], foreground=C["fg"])
        st.map("TCheckbutton", background=[("active", C["bg"])])
        st.configure("TProgressbar", background=C["accent2"],
                     troughcolor=C["bg3"], bordercolor=C["border"])

    def _build_menu(self):
        m = tk.Menu(self.root)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="导入谱面文件…", command=self.import_charts)
        fm.add_command(label="导入官方更新包…", command=self.import_pack)
        fm.add_separator()
        fm.add_command(label="扫描导入目录", command=self.scan_import_dir)
        fm.add_command(label="打开导入目录", command=self.open_import_dir)
        fm.add_separator()
        fm.add_command(label="设置缓存目录…", command=self.choose_cache_dir)
        fm.add_command(label="打开数据目录", command=self.open_data_dir)
        fm.add_command(label="打开下载目录", command=self.open_download_dir)
        fm.add_command(label="打开登录日志", command=self.open_login_log)
        fm.add_separator()
        fm.add_command(label="退出", command=self.root.destroy)
        m.add_cascade(label="文件", menu=fm)

        adv = tk.Menu(m, tearoff=0)
        adv.add_command(label="重新探测游戏目录", command=self.rediscover_game)
        adv.add_command(label="手动指定游戏目录…", command=self.choose_game_dir)
        adv.add_separator()
        adv.add_command(label="网络代理设置…", command=self.choose_proxy)
        m.add_cascade(label="高级", menu=adv)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="检查更新…", command=self.check_update)
        hm.add_separator()
        hm.add_command(label="免责声明", command=lambda: self.show_disclaimer())
        hm.add_command(label="关于", command=self.show_about)
        m.add_cascade(label="帮助", menu=hm)
        try:
            self.root.config(menu=m)
        except Exception:                                # noqa: BLE001
            pass

    # ---- 布局 ----
    def _build(self):
        self.status = tk.StringVar(value="就绪")
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self.tab_browse = ttk.Frame(self.notebook)
        self.tab_downloaded = ttk.Frame(self.notebook)
        self.tab_installed = ttk.Frame(self.notebook)
        self.tab_account = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_browse, text="谱库浏览")
        self.notebook.add(self.tab_downloaded, text="已下载")
        self.notebook.add(self.tab_installed, text="已装自制谱")
        self.notebook.add(self.tab_account, text="登录")
        self._build_browse(self.tab_browse)
        self._build_downloaded(self.tab_downloaded)
        self._build_installed(self.tab_installed)
        self._build_account(self.tab_account)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        bar = tk.Frame(self.root, bg=C["bg2"], height=28)
        bar.pack(fill="x", side="bottom")
        tk.Label(bar, textvariable=self.status, bg=C["bg2"], fg=C["fg_dim"],
                 anchor="w").pack(side="left", padx=10)
        self.var_game = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.var_game, bg=C["bg2"], fg=C["fg_dim"],
                 anchor="w").pack(side="left", padx=16)
        self.login_state = tk.StringVar()
        tk.Label(bar, textvariable=self.login_state, bg=C["bg2"], fg=C["warn"],
                 anchor="e").pack(side="right", padx=10)
        self.refresh_login_state()
        self.refresh_game_dir()

    def _on_tab_changed(self, _evt=None):
        try:
            cur = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        if cur == self.notebook.index(self.tab_downloaded):
            self.reload_downloaded()
        elif cur == self.notebook.index(self.tab_installed):
            self.reload_installed()

    # ===================== 谱库浏览（卡片网格） =====================
    def _build_browse(self, root):
        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=8)

        ttk.Label(top, text="每页:").pack(side="left")
        self.var_count = tk.StringVar(value="30")
        ttk.Combobox(top, textvariable=self.var_count, width=5, state="readonly",
                     values=["15", "30", "50"]).pack(side="left", padx=(4, 12))
        ttk.Label(top, text="本地过滤:").pack(side="left")
        self.var_filter = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.var_filter, width=26)
        e.pack(side="left", padx=(4, 4))
        e.bind("<Return>", lambda *_: self.apply_filter())
        ttk.Button(top, text="过滤", command=self.apply_filter).pack(side="left", padx=4)
        # 「只显示纯 Pad（ub）」开关：默认取 config 的 store_pad_only，勾选即写回 config
        self.var_pad_only = tk.BooleanVar(
            value=bool(self.cfg.get("store_pad_only", True)))
        ttk.Checkbutton(top, text="只要纯 Pad(ub)",
                        variable=self.var_pad_only,
                        command=self.on_pad_only_toggle).pack(side="left", padx=(8, 4))
        ttk.Button(top, text="刷新", style="Accent.TButton",
                   command=self.reload_page).pack(side="left", padx=4)

        # ---- 批量操作条 ----
        bar = ttk.Frame(root)
        bar.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(bar, text="批量：", style="Dim.TLabel").pack(side="left")
        ttk.Button(bar, text="全选",
                   command=lambda: self.browse_grid.check_all()).pack(side="left", padx=2)
        ttk.Button(bar, text="清空",
                   command=lambda: self.browse_grid.clear_checks()).pack(side="left", padx=2)
        ttk.Button(bar, text="反选",
                   command=lambda: self.browse_grid.invert_checks()).pack(side="left", padx=2)
        self.btn_batch_download = ttk.Button(
            bar, text="批量下载(0)", style="Accent.TButton",
            command=self.batch_download_browse)
        self.btn_batch_download.pack(side="left", padx=(12, 4))
        ttk.Label(bar, text="（勾选卡片左上角方框后点按钮；一次拉整页会串行下载，较慢）",
                  style="Dim.TLabel").pack(side="left", padx=8)

        mid = ttk.Frame(root)
        mid.pack(fill="both", expand=True, padx=8)
        self.browse_grid = CardGrid(mid, cover_loader=self.load_cover_spec,
                                    on_select=self.on_browse_select,
                                    multi=True, on_check=self._refresh_browse_batch_button)
        self.browse_grid.pack(side="left", fill="both", expand=True)

        side = ttk.Frame(mid, style="Card.TFrame", width=252)
        side.pack(side="right", fill="y", padx=(8, 0))
        side.pack_propagate(False)
        tk.Label(side, text="谱面详情", bg=C["bg2"], fg=C["accent"],
                 font=(UI_FONT, 12, "bold")).pack(anchor="w", padx=10, pady=(10, 4))
        self.detail = tk.Text(side, height=14, width=30, bg=C["bg3"], fg=C["fg"],
                              relief="flat", wrap="word")
        self.detail.pack(padx=10, pady=6, fill="both", expand=True)
        self.detail.insert("end", "点击左侧卡片查看详情")
        self.detail.configure(state="disabled")
        ttk.Button(side, text="下载选中谱面", style="Accent.TButton",
                   command=self.download_selected).pack(fill="x", padx=10,
                                                        pady=(0, 6))
        ttk.Button(side, text="去【已下载】页",
                   command=lambda: self.notebook.select(
                       self.tab_downloaded)).pack(fill="x", padx=10, pady=(0, 10))

        nav = ttk.Frame(root)
        nav.pack(fill="x", padx=8, pady=8)
        ttk.Button(nav, text="← 上一页",
                   command=lambda: self.change_page(-1)).pack(side="left")
        self.var_page = tk.StringVar(value="1 / 1")
        ttk.Label(nav, textvariable=self.var_page).pack(side="left", padx=12)
        ttk.Button(nav, text="下一页 →",
                   command=lambda: self.change_page(1)).pack(side="left")
        ttk.Label(nav, text="(增量拉取：先拉一批，点【下一页】不够时再拉下一批；"
                            "商店谱面 + 公开谱面双来源合并去重；"
                            "「只要纯 Pad(ub)」可切换)",
                  style="Dim.TLabel").pack(side="left", padx=12)

        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=8, pady=(0, 8))

    # ===================== 已下载（卡片网格） =====================
    def _build_downloaded(self, root):
        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Button(top, text="刷新列表", style="Accent.TButton",
                   command=self.reload_downloaded).pack(side="left")
        ttk.Button(top, text="打开下载目录",
                   command=self.open_download_dir).pack(side="left", padx=6)
        ttk.Button(top, text="打开数据目录",
                   command=self.open_data_dir).pack(side="left", padx=6)
        ttk.Label(top, text="卡片下方【一键安装】装进游戏；已装的可【卸载】。",
                  style="Dim.TLabel").pack(side="left", padx=12)

        # ---- 批量操作条 ----
        bar = ttk.Frame(root)
        bar.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(bar, text="批量：", style="Dim.TLabel").pack(side="left")
        ttk.Button(bar, text="全选",
                   command=lambda: self._dl_grid().check_all()).pack(side="left", padx=2)
        ttk.Button(bar, text="清空",
                   command=lambda: self._dl_grid().clear_checks()).pack(side="left", padx=2)
        ttk.Button(bar, text="反选",
                   command=lambda: self._dl_grid().invert_checks()).pack(side="left", padx=2)
        self.btn_batch_install = ttk.Button(
            bar, text="批量安装(0)", style="Accent.TButton",
            command=self.batch_install_downloaded)
        self.btn_batch_install.pack(side="left", padx=(12, 4))
        self.btn_batch_uninstall = ttk.Button(
            bar, text="批量卸载(0)", command=self.batch_uninstall_downloaded)
        self.btn_batch_uninstall.pack(side="left", padx=4)
        self.btn_batch_delete = ttk.Button(
            bar, text="批量删除(0)", command=self.batch_delete_downloaded)
        self.btn_batch_delete.pack(side="left", padx=4)
        ttk.Label(bar, text="（勾选卡片左上角方框后点按钮；"
                            "【卸载】=从游戏移除，【删除】=只清本地文件）",
                  style="Dim.TLabel").pack(side="left", padx=8)

        self.downloaded_grid = CardGrid(
            root, cover_loader=self.load_cover_spec,
            on_select=self.on_downloaded_select,
            multi=True, on_check=self._refresh_downloaded_batch_buttons)
        self.downloaded_grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _dl_grid(self):
        return self.downloaded_grid

    def _refresh_downloaded_batch_buttons(self):
        """勾选变化时更新批量按钮上的数量。"""
        try:
            n = len(self.downloaded_grid.checked_items())
        except Exception:                                    # noqa: BLE001
            n = 0
        for btn, label in ((self.btn_batch_install, "批量安装"),
                           (self.btn_batch_uninstall, "批量卸载"),
                           (getattr(self, "btn_batch_delete", None), "批量删除")):
            if btn is None:
                continue
            try:
                btn.configure(text="%s(%d)" % (label, n))
            except Exception:                                # noqa: BLE001
                pass

    # ---- 批量删除（只清本地下载文件）----
    def batch_delete_downloaded(self):
        items = self.downloaded_grid.checked_items()
        if not items:
            messagebox.showinfo("提示", "请先勾选要删除的谱面（卡片左上角方框）。")
            return
        todo, blocked = [], []
        for it in items:
            name = it.get("title") or os.path.basename(it.get("dir") or "?")
            if not it.get("dir"):
                continue
            if it.get("installed_music_id"):
                blocked.append((name, it.get("dir"), it.get("installed_music_id")))
            else:
                todo.append((name, it.get("dir"), None))
        # 已装进游戏的：问要不要**一并卸载**（否则游戏里会留下无效曲目）
        if blocked:
            names = "、".join("%s(id=%s)" % (n, m) for n, _d, m in blocked[:6])
            if messagebox.askyesno(
                    "有已装进游戏的谱面",
                    "勾选的里面有 %d 个已经装进游戏：\n  %s\n\n"
                    "【是】= 先从游戏卸载，再删除这些本地文件（推荐）\n"
                    "【否】= 取消，什么都不做"
                    % (len(blocked), names)):
                todo += blocked
            else:
                return
        if not todo:
            messagebox.showinfo("提示", "没有可删除的条目。")
            return
        if not messagebox.askyesno(
                "批量删除",
                "将删除 %d 个谱面的下载文件：\n  %s\n\n"
                "其中 %d 个会先从游戏卸载。确定？"
                % (len(todo), "、".join(n for n, _d, _m in todo[:6])
                   + ("…" if len(todo) > 6 else ""), len(blocked))):
            return
        self.set_status("开始批量删除 %d 个…" % len(todo))
        self.progress.configure(value=0, maximum=len(todo))

        def work():
            ok, fail, total = [], [], 0
            for i, (name, folder, mid) in enumerate(todo, 1):
                self.async_.q.put((self.set_status,
                                   ("批量删除 %d/%d：%s" % (i, len(todo), name),), {}))
                self.async_.q.put((self.progress.configure, (), {"value": i - 1}))
                try:
                    if mid:
                        S.uninstall_music(mid)      # 先卸载，避免游戏里留孤儿条目
                    res = S.delete_downloaded(folder, force=True)
                    total += res.get("freed", 0)
                    ok.append(name)
                except Exception as ex:                      # noqa: BLE001
                    fail.append((name, str(ex)))
            return {"ok": ok, "fail": fail, "freed": total}

        def done(res):
            self.progress.configure(value=len(todo))
            ok, fail = res["ok"], res["fail"]
            mb = res.get("freed", 0) / 1048576.0
            self.set_status("批量删除完成：成功 %d，失败 %d，释放 %.1f MB"
                            % (len(ok), len(fail), mb))
            lines = []
            if ok:
                lines.append("已卸载并删除 %d 个，释放 %.1f MB：" % (len(ok), mb))
                for n in ok[:12]:
                    lines.append("   · %s" % n)
                if len(ok) > 12:
                    lines.append("   … 还有 %d 个" % (len(ok) - 12))
            if fail:
                lines.append("")
                lines.append("失败 %d 个：" % len(fail))
                for n, e in fail[:10]:
                    lines.append("   · %s：%s" % (n, str(e)[:80]))
            messagebox.showinfo("批量删除完成", "\n".join(lines))
            self.reload_downloaded()
            try:
                self.reload_installed()
            except Exception:                                # noqa: BLE001
                pass

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("批量删除失败", str(e)),
                                   self.set_status("批量删除失败：%s" % e)))

    # ---- 批量安装（已下载 -> 游戏）----
    def batch_install_downloaded(self):
        items = self.downloaded_grid.checked_items()
        if not items:
            messagebox.showinfo("提示", "请先勾选要安装的谱面（卡片左上角方框）。")
            return
        titles = [it.get("title") or os.path.basename(it.get("dir") or "?")
                  for it in items]
        head = "、".join(titles[:5]) + ("…" if len(titles) > 5 else "")
        if not messagebox.askyesno(
                "批量安装",
                "将安装 %d 个谱面：\n  %s\n\n"
                "  · 逐个安装，自动选空闲 id、只新增不覆盖\n"
                "  · 每个谱面编码音源约需十秒\n\n确定继续？" % (len(items), head)):
            return
        self.set_status("开始批量安装 %d 个…" % len(items))
        self.progress.configure(value=0, maximum=len(items))

        def work():
            ok, fail = [], []
            for i, it in enumerate(items, 1):
                name = it.get("title") or os.path.basename(it.get("dir") or "?")
                folder = it.get("dir")
                if not folder:
                    fail.append((name, "缺少下载目录信息"))
                    continue
                self.async_.q.put((self.set_status,
                                   ("批量安装 %d/%d：%s"
                                    % (i, len(items), name),), {}))
                self.async_.q.put((self.progress.configure, (), {"value": i - 1}))

                def prog(_i, msg, _n=name):
                    self.async_.q.put((self.set_status, ("%s：%s" % (_n, msg),), {}))
                try:
                    res = S.one_click_install(folder, progress=prog)
                    mid = ((res or {}).get("plan") or {}).get("music_id")
                    ok.append((name, mid))
                except Exception as ex:                      # noqa: BLE001
                    fail.append((name, str(ex)))
            return {"ok": ok, "fail": fail}

        def done(res):
            self.progress.configure(value=len(items))
            ok, fail = res["ok"], res["fail"]
            self.set_status("批量安装完成：成功 %d，失败 %d" % (len(ok), len(fail)))
            lines = []
            if ok:
                lines.append("成功 %d 个：" % len(ok))
                for n, mid in ok:
                    lines.append("   · %s  →  music_id=%s" % (n, mid))
            if fail:
                lines.append("")
                lines.append("失败 %d 个：" % len(fail))
                for n, e in fail[:12]:
                    lines.append("   · %s：%s" % (n, str(e)[:80]))
                if len(fail) > 12:
                    lines.append("   … 还有 %d 个" % (len(fail) - 12))
            messagebox.showinfo("批量安装完成", "\n".join(lines))
            self.reload_downloaded()
            try:
                self.reload_installed()
            except Exception:                                # noqa: BLE001
                pass

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("批量安装失败", str(e)),
                                   self.set_status("批量安装失败：%s" % e)))

    # ---- 批量卸载 ----
    def batch_uninstall_downloaded(self):
        items = self.downloaded_grid.checked_items()
        if not items:
            messagebox.showinfo("提示", "请先勾选要卸载的谱面（卡片左上角方框）。")
            return
        pairs = [(it.get("installed_music_id"),
                  it.get("title") or os.path.basename(it.get("dir") or "?"))
                 for it in items]
        todo = [(mid, name) for mid, name in pairs if mid]
        skipped = [name for mid, name in pairs if not mid]
        if not todo:
            messagebox.showinfo("提示",
                                "勾选的谱面里没有已装进游戏的（%d 个未安装）。"
                                % len(skipped))
            return
        if not messagebox.askyesno(
                "批量卸载",
                "将从游戏里卸载 %d 个自制谱：\n  %s\n\n"
                "（另有 %d 个未安装，会跳过）\n\n"
                "卸载会按安装记录逐项回滚，确定继续？"
                % (len(todo), "、".join(n for _, n in todo[:5])
                   + ("…" if len(todo) > 5 else ""), len(skipped))):
            return
        self._run_batch_uninstall(todo)

    def _run_batch_uninstall(self, todo):
        """todo = [(music_id, 名称), ...]"""
        self.set_status("开始批量卸载 %d 个…" % len(todo))
        self.progress.configure(value=0, maximum=len(todo))

        def work():
            ok, fail = [], []
            for i, (mid, name) in enumerate(todo, 1):
                self.async_.q.put((self.set_status,
                                   ("批量卸载 %d/%d：%s" % (i, len(todo), name),), {}))
                self.async_.q.put((self.progress.configure, (), {"value": i - 1}))
                try:
                    S.uninstall_music(mid)
                    ok.append((name, mid))
                except Exception as ex:                      # noqa: BLE001
                    fail.append((name, str(ex)))
            return {"ok": ok, "fail": fail}

        def done(res):
            self.progress.configure(value=len(todo))
            ok, fail = res["ok"], res["fail"]
            self.set_status("批量卸载完成：成功 %d，失败 %d" % (len(ok), len(fail)))
            lines = []
            if ok:
                lines.append("成功 %d 个：" % len(ok))
                for n, mid in ok:
                    lines.append("   · %s  (id=%s)" % (n, mid))
            if fail:
                lines.append("")
                lines.append("失败 %d 个：" % len(fail))
                for n, e in fail[:12]:
                    lines.append("   · %s：%s" % (n, str(e)[:80]))
            messagebox.showinfo("批量卸载完成", "\n".join(lines))
            try:
                self.reload_downloaded()
            except Exception:                                # noqa: BLE001
                pass
            try:
                self.reload_installed()
            except Exception:                                # noqa: BLE001
                pass

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("批量卸载失败", str(e)),
                                   self.set_status("批量卸载失败：%s" % e)))

    # ===================== 已装自制谱（卡片网格） =====================
    def _build_installed(self, root):
        top = ttk.Frame(root)
        top.pack(fill="x", padx=8, pady=8)
        ttk.Button(top, text="刷新列表", style="Accent.TButton",
                   command=self.reload_installed).pack(side="left")
        ttk.Button(top, text="打开备份目录",
                   command=self.open_backup_dir).pack(side="left", padx=6)
        tk.Label(top, text="数据目录:", bg=C["bg"], fg=C["fg_dim"]).pack(
            side="left", padx=(16, 4))
        self.var_data_dir = tk.StringVar(value=S.DATA_DIR)
        ttk.Entry(top, textvariable=self.var_data_dir, width=40,
                  state="readonly").pack(side="left")

        # ---- 批量操作条 ----
        bar = ttk.Frame(root)
        bar.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Label(bar, text="批量：", style="Dim.TLabel").pack(side="left")
        ttk.Button(bar, text="全选",
                   command=lambda: self.installed_grid.check_all()).pack(side="left", padx=2)
        ttk.Button(bar, text="清空",
                   command=lambda: self.installed_grid.clear_checks()).pack(side="left", padx=2)
        ttk.Button(bar, text="反选",
                   command=lambda: self.installed_grid.invert_checks()).pack(side="left", padx=2)
        self.btn_batch_uninstall_all = ttk.Button(
            bar, text="批量卸载(0)", style="Accent.TButton",
            command=self.batch_uninstall_installed)
        self.btn_batch_uninstall_all.pack(side="left", padx=(12, 4))
        ttk.Button(bar, text="检查残留数据",
                   command=self.check_orphans).pack(side="left", padx=(12, 4))
        ttk.Button(bar, text="编号台账",
                   command=self.show_id_ledger).pack(side="left", padx=4)
        ttk.Label(bar, text="（勾选卡片左上角方框后点按钮；"
                            "编号只用一次、卸载不回收，避免新曲继承旧成绩）",
                  style="Dim.TLabel").pack(side="left", padx=8)

        self.installed_grid = CardGrid(
            root, cover_loader=self.load_cover_spec,
            on_select=self.on_installed_select,
            multi=True, on_check=self._refresh_installed_batch_button)
        self.installed_grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def check_orphans(self):
        """检查两类"卸载/删除没清干净"的残留：

        A. **孤儿条目** —— 曲表里还有，文件却没了（游戏启动会报错/白框）
        B. **孤儿文件** —— 文件还在，曲表里已无条目（白占空间，卸载没删干净）
        """
        game = self._require_game()
        if not game:
            return
        self.set_status("正在检查残留数据…")

        def work():
            return {"entries": S.find_orphans(game),
                    "files": S.find_orphan_files(game)}

        def done(res):
            entries, files = res["entries"], res["files"]
            if not entries and not files:
                self.set_status("检查完成：没有残留 ✅")
                messagebox.showinfo(
                    "检查残留",
                    "没有发现问题。\n\n"
                    "（曲表条目与文件一一对应，没有孤儿）")
                return

            parts = []
            if entries:
                parts.append("【孤儿条目】曲表里有、文件已丢失（会让游戏报错）：")
                for o in entries[:10]:
                    parts.append("   · music_id=%s  缺 %d 个文件"
                                 % (o["music_id"], len(o["missing"])))
                if len(entries) > 10:
                    parts.append("   … 还有 %d 个" % (len(entries) - 10))
                parts.append("")
            if files:
                total_mb = sum(f["size"] for f in files) / 1048576.0
                parts.append("【孤儿文件】文件还在、曲表已无条目（白占 %.1f MB）："
                             % total_mb)
                for f in files[:10]:
                    parts.append("   · music_id=%s  %d 个文件"
                                 % (f["music_id"], len(f["files"])))
                if len(files) > 10:
                    parts.append("   … 还有 %d 个" % (len(files) - 10))
                parts.append("")

            n_items = len(entries) + len(files)
            if not messagebox.askyesno(
                    "发现 %d 处残留" % n_items,
                    "\n".join(parts) +
                    "\n【是】= 立即清理（清理条目会自动备份曲表）\n"
                    "【否】= 暂不处理"):
                self.set_status("发现 %d 处残留（未处理）" % n_items)
                return
            self._cleanup_all(game, entries, files)

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("检查失败", str(e)),
                                   self.set_status("检查失败：%s" % e)))

    def _cleanup_all(self, game, entries, files):
        self.set_status("正在清理残留…")

        def work():
            r1 = {"removed": [], "failed": []}
            r2 = {"removed": [], "failed": [], "freed": 0}
            if entries:
                r1 = S.cleanup_orphans(game, [o["music_id"] for o in entries])
            if files:
                r2 = S.cleanup_orphan_files(game, [f["music_id"] for f in files])
            return r1, r2

        def done(res):
            r1, r2 = res
            ok1, fail1 = r1["removed"], r1["failed"]
            ok2, fail2 = r2["removed"], r2["failed"]
            mb = r2.get("freed", 0) / 1048576.0
            self.set_status("清理完成：条目 %d，文件 %d 个（释放 %.1f MB）"
                            % (len(ok1), len(ok2), mb))
            msg = []
            if ok1:
                msg.append("已清理 %d 个孤儿条目：" % len(ok1))
                for mid, _bak in ok1:
                    msg.append("   · music_id=%s" % mid)
                msg.append("（曲表已备份为 *.bak_orphan_*）")
            if ok2:
                msg.append("")
                msg.append("已删除 %d 个孤儿文件，释放 %.1f MB：" % (len(ok2), mb))
            for lst, label in ((fail1, "条目"), (fail2, "文件")):
                if lst:
                    msg.append("")
                    msg.append("失败 %d 个%s：" % (len(lst), label))
                    for it in lst[:8]:
                        if isinstance(it, (tuple, list)):
                            msg.append("   · %s：%s" % (it[0], str(it[1])[:70]))
                        else:
                            msg.append("   · %s" % str(it)[:80])
            messagebox.showinfo("清理完成", "\n".join(msg))
            try:
                self.reload_installed()
            except Exception:                                # noqa: BLE001
                pass

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("清理失败", str(e)),
                                   self.set_status("清理失败：%s" % e)))

    def show_id_ledger(self):
        """显示编号台账：下一个可用编号 + 已用编号列表。

        编号**只用一次、卸载不回收** —— 因为 jubeat 的成绩按 music_id 存档，
        复用编号会让新曲继承旧曲的成绩。
        """
        game = self._require_game()
        if not game:
            return

        def work():
            info = S.list_used_ids()["used"]
            nxt = ""
            try:
                nxt = S.next_free_id(game)
            except Exception as ex:                           # noqa: BLE001
                nxt = "（%s）" % ex
            return info, nxt

        def done(res):
            used, nxt = res
            ids = sorted((int(k) for k in used), reverse=True)
            lines = ["下一个可用编号：  %s" % nxt, ""]
            lines.append("已分配过的编号：%d 个" % len(ids))
            if ids:
                lines.append("范围：%d ~ %d" % (min(ids), max(ids)))
                lines.append("")
                lines.append("最近 %d 个：" % min(20, len(ids)))
                for i in ids[:20]:
                    rec = used.get(str(i)) or {}
                    t = rec.get("title") or ""
                    lines.append("   %d%s" % (i, ("  " + t[:24]) if t else ""))
                if len(ids) > 20:
                    lines.append("   … 还有 %d 个" % (len(ids) - 20))
            lines.append("")
            lines.append("⚠️ 编号卸载后不会回收（避免新曲继承旧曲的游戏成绩）。")
            lines.append("如需手工释放，可编辑：")
            lines.append("   %s" % S.used_ids_path())
            messagebox.showinfo("编号台账", "\n".join(lines))
            self.set_status("已用编号 %d 个；下一个可用 %s" % (len(ids), nxt))

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("读取台账失败", str(e)),
                                   self.set_status("读取台账失败：%s" % e)))

    def _refresh_installed_batch_button(self):
        try:
            n = len(self.installed_grid.checked_items())
        except Exception:                                    # noqa: BLE001
            n = 0
        try:
            self.btn_batch_uninstall_all.configure(text="批量卸载(%d)" % n)
        except Exception:                                    # noqa: BLE001
            pass

    def batch_uninstall_installed(self):
        items = self.installed_grid.checked_items()
        if not items:
            messagebox.showinfo("提示", "请先勾选要卸载的谱面（卡片左上角方框）。")
            return
        todo = []
        for it in items:
            mid = it.get("music_id") or it.get("installed_music_id") or it.get("mid")
            if mid:
                todo.append((mid, it.get("title") or ("id=%s" % mid)))
        if not todo:
            messagebox.showinfo("提示", "勾选的条目里没有可卸载的 music_id。")
            return
        if not messagebox.askyesno(
                "批量卸载",
                "将从游戏里卸载 %d 个自制谱：\n  %s\n\n"
                "卸载会按安装记录逐项回滚（曲表 / 包表 / 文件），确定继续？"
                % (len(todo), "、".join(n for _, n in todo[:5])
                   + ("…" if len(todo) > 5 else ""))):
            return
        self._run_batch_uninstall(todo)

    # ===================== 登录 =====================
    def _build_account(self, root):
        card = ttk.Frame(root, style="Card.TFrame")
        card.pack(fill="x", padx=16, pady=16)
        tk.Label(card, text="Malody 账号登录", bg=C["bg2"], fg=C["accent"],
                 font=(UI_FONT, 12, "bold")).grid(row=0, column=0, columnspan=2,
                                             sticky="w", padx=12, pady=(12, 4))
        tk.Label(card, text="账号 / 邮箱", bg=C["bg2"], fg=C["fg"]).grid(
            row=1, column=0, sticky="e", padx=8, pady=6)
        self.var_user = tk.StringVar()
        ttk.Entry(card, textvariable=self.var_user, width=36).grid(
            row=1, column=1, sticky="w", pady=6)
        tk.Label(card, text="密码", bg=C["bg2"], fg=C["fg"]).grid(
            row=2, column=0, sticky="e", padx=8, pady=6)
        self.var_psw = tk.StringVar()
        self.psw_entry = ttk.Entry(card, textvariable=self.var_psw, width=36,
                                   show="\u2022")
        self.psw_entry.grid(row=2, column=1, sticky="w", pady=6)
        tk.Label(card, text="密码只在本地即时转 MD5，绝不落盘、不写日志。",
                 bg=C["bg2"], fg=C["fg_dim"]).grid(
            row=3, column=1, sticky="w", pady=(0, 6))
        btns = ttk.Frame(card, style="Card.TFrame")
        btns.grid(row=4, column=1, sticky="w", pady=10)
        ttk.Button(btns, text="登录", style="Accent.TButton",
                   command=self.do_login).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="退出登录 / 清除凭据", style="Danger.TButton",
                   command=self.do_logout).pack(side="left", padx=(0, 8))
        self.btn_open_log = ttk.Button(btns, text="打开登录日志",
                                       command=self.open_login_log)
        self.btn_open_log.pack(side="left")

        info = ttk.Frame(root)
        info.pack(fill="both", expand=True, padx=16)
        tk.Label(info, text="本机凭据文件（只含 uid/凭证，不含密码）:",
                 bg=C["bg"], fg=C["fg_dim"]).pack(anchor="w")
        self.var_creds_path = tk.StringVar(value=S.creds_path())
        ttk.Entry(info, textvariable=self.var_creds_path,
                  state="readonly").pack(fill="x", pady=4)
        tk.Label(info, text="登录诊断日志（已脱敏：只记长度/状态码/原始响应，"
                            "绝不含密码与其 MD5）:",
                 bg=C["bg"], fg=C["fg_dim"]).pack(anchor="w")
        self.var_login_log_path = tk.StringVar(value=S.login_log_path())
        ttk.Entry(info, textvariable=self.var_login_log_path,
                  state="readonly").pack(fill="x", pady=4)
        self.login_log = tk.Text(info, height=10, bg=C["bg2"], fg=C["fg"],
                                 relief="flat", wrap="word")
        self.login_log.pack(fill="both", expand=True, pady=6)

    # ===================== 通用 =====================
    def set_status(self, text):
        self.status.set(text)

    def on_error(self, e, prefix):
        self.set_status("%s：%s" % (prefix, e))
        messagebox.showerror(prefix, str(e))

    def refresh_login_state(self):
        c = S.load_credentials()
        if not c:
            self.login_state.set("未登录（下载需要登录）")
        elif not S.store_key_of(c):
            self.login_state.set("已登录 uid=%s，但缺商店凭证：请重新登录"
                                 % c.get("uid"))
        else:
            self.login_state.set("已登录 uid=%s（%s）"
                                 % (c.get("uid"), c.get("name") or "-"))

    def refresh_game_dir(self):
        g = S.auto_detect_game()
        self.game_dir = g or None
        if g:
            self.var_game.set("游戏目录: %s" % g)
        else:
            self.var_game.set("未找到游戏目录（可在【高级】菜单手动指定）")

    @staticmethod
    def _safe_name(s):
        return "".join(ch if ch.isalnum() else "_" for ch in str(s))[-64:] or "cover"

    @staticmethod
    def _photo(path):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            im.thumbnail((COVER, COVER))
            return ImageTk.PhotoImage(im)
        except Exception:                                # noqa: BLE001
            return None

    def load_cover_spec(self, spec, callback):
        """spec: ("local", path) / ("url", url, key) / None。callback(PhotoImage|None)。"""
        if not spec:
            callback(None)
            return
        kind = spec[0]
        if kind == "local":
            path = spec[1] if len(spec) > 1 else None
            callback(self._photo(path) if path and os.path.isfile(path) else None)
            return
        if kind == "url":
            url, key = spec[1], spec[2]
            if key in self._cover_cache:
                callback(self._cover_cache[key])
                return

            def work():
                dest = os.path.join(S.cache_dir(self.cfg), "covers", "%s.img" % key)
                self.client.fetch_cover(url, dest)
                return dest

            def done(path):
                ph = self._photo(path)
                self._cover_cache[key] = ph
                callback(ph)

            self.async_.run(work, done, lambda _e: callback(None))
            return
        callback(None)

    # ---- 谱库浏览行为（**增量**：先拉一批，翻页不够再拉下一批）----
    # 数据流：all_rows(已加载) → apply_filter → filtered_all → _render_page → rows(本页渲染)
    def reload_page(self):
        try:
            self.page_size = int(self.var_count.get())
        except Exception:                                # noqa: BLE001
            self.page_size = 30
        self.page = 0
        self.all_rows = []
        self.filtered_all = []
        self.session = None
        self.has_more = True
        self._loading = False
        self._pending_page = None
        self._ever_loaded = False
        self._render_page()
        self._load_batch(first=True)

    def change_page(self, delta):
        npg = self.page + delta
        if npg < 0:
            return
        if self._loading:
            self.set_status("正在拉取中，请稍候再翻页…")
            return
        # 目标页还没加载到 → 再拉一批。**不要先改 page**：万一拉取失败或到底了，
        # 页码已经跳走会让界面显示错位（看起来像"重复/跳页"）。
        # 记到 _pending_page，等 done() 里数据到位后再切页。
        if (npg + 1) * self.page_size > len(self.filtered_all) and self.has_more:
            self._pending_page = npg
            self._load_batch()
            return
        maxp = max(1, (len(self.filtered_all) + self.page_size - 1) // self.page_size)
        if npg >= maxp:
            self.set_status("已经是最后一页了（已加载 %d 条）" % len(self.filtered_all))
            return
        self.page = npg
        self._render_page()

    def on_pad_only_toggle(self):
        """切换「只要纯 Pad(ub)」：写回 config（下次启动仍生效）并重新拉取。"""
        want = bool(self.var_pad_only.get())
        try:
            cfg = dict(self.cfg)
            cfg["store_pad_only"] = want
            S.save_config(cfg)
            self.cfg = S.load_config()
        except Exception as ex:                              # noqa: BLE001
            self.set_status("保存筛选设置失败（不影响本次浏览）：%s" % ex)
        self.reload_page()

    def _load_batch(self, first=False):
        """拉**下一批**（商店 1 页 + 公开各 status 1 页），追加后重新渲染当前页。

        不一次拉全库：公开接口 30 条/页 + 限速 ~1 req/s，且 `total` 字段是假的，
        所以按需增量拉取 —— 打开界面只发 5 个请求（商店 1 页 + 公开 4×1 页）。

        ⚠️ **必须防重入**：Async.run 每次都会新开工作线程，用户快速连点「下一页」
        会并发调用 `session.next_batch()`，而 BrowseSession 的 `_seen` / `_anon_page`
        是普通属性、没有并发保护 —— 两次拉取互相踩就会出现**重复条目**。
        """
        if self._loading:
            self.set_status("正在拉取中，请稍候…")
            return
        if not first and not self.has_more:
            self.set_status("已经到底了：公开接口没有更多条目")
            return
        # ⚠️ Tk 变量只能在**主线程**读：在后台 work() 里访问 self.var_pad_only
        # 会与主线程的 Tk 事件循环争 Tcl 锁（主线程紧密 update 时直接死锁）。
        pad_only = bool(self.var_pad_only.get())
        self.set_status("正在拉取一批（商店 1 页 + 公开 4×1 页）…")
        self._loading = True

        def work():
            if self.session is None:
                self.session = S.BrowseSession(
                    self.client, mode=S.MODE_PAD, pad_only=pad_only)
            # 首次只拉 1 页：每页要 4 个公开请求（限速 ~1 req/s），拉 2 页要
            # 近 40 秒才出内容。先快速把首屏铺出来，之后每批拉 2 页保证翻页快。
            return self.session.next_batch(pages=1 if first else None)

        def done(res):
            self._loading = False
            self._ever_loaded = True
            new, more = res
            self.all_rows.extend(new)
            self.has_more = more
            p = self.session.parts if self.session else {}
            self.source_label = "商店 %d + 公开 %d（已加载 %d%s）" % (
                p.get("store", 0), p.get("anon", 0), len(self.all_rows),
                "" if more else "，已到底")
            # 若这次拉取是为了跳到更后面的页，等数据到位后再改页码
            if self._pending_page is not None:
                self.page = self._pending_page
                self._pending_page = None
            self.apply_filter(reset_page=False)
            self.set_status("本批 +%d 条 → %s" % (len(new), self.source_label))

        def err(e):
            self._loading = False
            self._ever_loaded = True      # 失败也算"尝试过"，否则会一直显示"拉取中"
            self._pending_page = None
            self.on_error(e, "拉取列表失败")

        self.async_.run(work, done, err)

    def _render_page(self):
        arr = self.filtered_all
        maxp = max(1, (len(arr) + self.page_size - 1) // self.page_size)
        self.page = max(0, min(self.page, maxp - 1))
        self.rows = arr[self.page * self.page_size:(self.page + 1) * self.page_size]
        # 空列表有两种含义，文案要分开：
        #   · 还没拉到过任何一批 → 是"正在拉取"，不是"没有曲"
        #   · 拉过但确实是空的 / 过滤后为空 → 才是"没有匹配的谱面"
        if not arr:
            if not self._ever_loaded:
                empty = "正在拉取谱面列表，请稍候…（首次约几秒）"
            elif self.var_filter.get().strip():
                empty = "没有匹配「%s」的谱面。" % self.var_filter.get().strip()
            else:
                empty = "没有拉到谱面。可点【刷新】重试。"
        else:
            empty = ""
        self.browse_grid.set_items(
            self.rows,
            badge_of=self._browse_badge,
            cover_of=self._browse_cover_spec,
            empty_text=empty)
        self.var_page.set("%d / %d（已加载 %d 条%s）"
                          % (self.page + 1, maxp, len(arr),
                             "" if self.has_more else "，到底"))

    def apply_filter(self, reset_page=True):
        kw = self.var_filter.get()
        # 对**已加载**的全部条目过滤（增量模式下 = 已经拉到的所有页）
        self.filtered_all = S.local_filter(self.all_rows, kw)
        self.filtered = self.filtered_all
        if reset_page:
            self.page = 0
        self._render_page()
        self.set_status("过滤 '%s' → %d 条（已加载 %d 条）"
                        % (kw, len(self.filtered_all), len(self.all_rows)))

    @staticmethod
    def _browse_badge(it):
        v = it.get("version")
        return _short_text(v, 12) if v else None

    def _browse_cover_spec(self, it):
        url = S.cover_url(it.get("sid") or it.get("id"), it.get("cover"), self.cfg)
        if not url:
            return None
        return ("url", url, self._safe_name(it.get("id") or url))

    def on_browse_select(self, item, _idx=None):
        self.selected_row = item
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", "SID: %s\nCID: %s\n标题: %s\n艺人: %s\n难度: %s\n作者: %s\n"
                                  "模式: %s\n\n（下载会自动挑 EXT 谱面，其次 ADV/BSC）\n" % (
            item.get("sid"), item.get("cid") or item.get("id"),
            item.get("title"), item.get("artist"),
            item.get("version"), item.get("username") or item.get("creator"),
            S.mode_names(item.get("mode"))))
        self.detail.configure(state="disabled")

    def _prog(self, pct, name):
        self.progress.configure(value=pct)
        self.set_status("下载中 %d%% — %s" % (pct, name))

    def download_selected(self):
        it = self.selected_row
        if not it:
            messagebox.showinfo("提示", "请先在左侧点选一张谱面卡片。")
            return
        # 重要：商店列表条目只有 **sid（歌曲）**，没有 cid（谱面）；
        # 匿名列表的 `id` 才是 cid。所以商店条目必须先去 /store/charts 挑一个谱面。
        sid = it.get("sid")
        cid = it.get("cid") or (None if sid else it.get("id"))
        if not S.is_logged_in():
            if not messagebox.askyesno(
                    "需要登录",
                    "下载需要登录（游客身份会返回 code=-3 无权限）。\n现在去【登录】页登录？"):
                return
            self.notebook.select(self.tab_account)
            return
        self.set_status("正在准备下载 sid=%s …" % sid)
        self.progress.configure(value=0, maximum=100)

        def work():
            resolved = None
            if cid is None:
                if not sid:
                    raise S.StoreError(-2, "这个条目既没有 cid 也没有 sid，无法下载")
                # 挑谱面：优先 EXT，其次 ADV / BSC
                resolved = self.client.pick_cid(sid)
                self.async_.q.put((self.set_status,
                                   ("已选谱面 %s（cid=%s）"
                                    % ((resolved.get("chart") or {}).get("version"),
                                       resolved.get("cid")),), {}))
            use_cid = (resolved or {}).get("cid") or cid

            def prog(i, n, name):
                self.async_.q.put((self._prog,
                                   (int(100 * i / max(1, n)), name), {}))
            res = self.client.download_chart(use_cid, progress=prog)
            res["cid"] = use_cid
            res["chart"] = (resolved or {}).get("chart")
            return res

        def done(res):
            self.progress.configure(value=100)
            self.set_status("下载完成：%s" % res["dir"])
            ch = res.get("chart") or {}
            messagebox.showinfo(
                "下载完成",
                "谱面：%s（cid=%s）\n已下载到：\n%s\n\n文件：\n%s\n\n可到【已下载】页点【一键安装】。"
                % (ch.get("version") or "-", res.get("cid"), res["dir"],
                   "\n".join("  %s  (%d B, md5 %s)"
                             % (f["name"], f["size"], "OK" if f["ok"] else "FAIL")
                             for f in res["files"])))
            try:
                self.notebook.select(self.tab_downloaded)
            except tk.TclError:
                pass

        def err(e):
            if isinstance(e, S.StoreError) and e.code == -3:
                messagebox.showwarning(
                    "无法下载（code=-3）",
                    "服务器返回 code=-3：未登录 / 无下载权限。\n"
                    "请先在【登录】页用 Malody 账号登录后重试。\n"
                    "（部分谱面可能还需要金币或已被作者限制下载。）\n\n数据: %s" % e.detail)
            elif isinstance(e, S.StoreError) and e.code == -1000:
                messagebox.showwarning(
                    "鉴权失败（code=-1000）",
                    "%s\n\n若是旧版凭据（只有登录 token、没有商店凭证），"
                    "请到【登录】页重新登录一次以补全商店凭证。" % e.message)
            else:
                messagebox.showerror("下载失败", str(e))
            self.set_status("下载失败：%s" % e)

        self.async_.run(work, done, err)

    # ---- 批量下载（谱库浏览 -> 本地）----
    def _refresh_browse_batch_button(self):
        try:
            n = len(self.browse_grid.checked_items())
        except Exception:                                    # noqa: BLE001
            n = 0
        try:
            self.btn_batch_download.configure(text="批量下载(%d)" % n)
        except Exception:                                    # noqa: BLE001
            pass

    def batch_download_browse(self):
        items = self.browse_grid.checked_items()
        if not items:
            messagebox.showinfo("提示", "请先勾选要下载的谱面（卡片左上角方框）。")
            return
        if not S.is_logged_in():
            if not messagebox.askyesno(
                    "需要登录",
                    "批量下载需要登录（游客身份会返回 code=-3 无权限）。\n现在去【登录】页登录？"):
                return
            self.notebook.select(self.tab_account)
            return
        titles = [it.get("title") or ("id=%s" % (it.get("cid") or it.get("id")))
                  for it in items]
        if not messagebox.askyesno(
                "批量下载",
                "将下载 %d 个谱面：\n  %s\n\n"
                "  · 逐个串行下载，会先挑谱面（优先 EXT）再拉文件\n"
                "  · 商店条目需要多一次接口调用，整批会偏慢\n\n确定继续？"
                % (len(items), "、".join(titles[:5]) + ("…" if len(titles) > 5 else ""))):
            return
        self.set_status("开始批量下载 %d 个…" % len(items))
        self.progress.configure(value=0, maximum=len(items))

        def work():
            ok, fail = [], []
            for i, it in enumerate(items, 1):
                name = it.get("title") or ("id=%s" % (it.get("cid") or it.get("id")))
                # 商店条目只有 sid，匿名条目 id 才是 cid
                sid = it.get("sid")
                cid = it.get("cid") or (None if sid else it.get("id"))
                self.async_.q.put((self.set_status,
                                   ("批量下载 %d/%d：%s"
                                    % (i, len(items), name),), {}))
                try:
                    if cid is None:
                        if not sid:
                            raise S.StoreError(-2, "该条目既没有 cid 也没有 sid")
                        resolved = self.client.pick_cid(sid)
                        use_cid = (resolved or {}).get("cid")
                    else:
                        use_cid = cid

                    def prog(_i, _n, _nm, _nm2=name):
                        self.async_.q.put((self.set_status,
                                           ("%s：%s" % (_nm2, _nm),), {}))
                    res = self.client.download_chart(use_cid, progress=prog)
                    ok.append((name, res.get("dir")))
                except Exception as ex:                      # noqa: BLE001
                    detail = getattr(ex, "detail", "") or ""
                    fail.append((name, ("%s %s" % (ex, detail)).strip()))
                self.async_.q.put((self.progress.configure, (), {"value": i}))
            return {"ok": ok, "fail": fail}

        def done(res):
            self.progress.configure(value=len(items))
            ok, fail = res["ok"], res["fail"]
            self.set_status("批量下载完成：成功 %d，失败 %d" % (len(ok), len(fail)))
            lines = []
            if ok:
                lines.append("成功 %d 个：" % len(ok))
                for n, d in ok[:12]:
                    lines.append("   · %s  →  %s" % (n, d))
                if len(ok) > 12:
                    lines.append("   … 还有 %d 个" % (len(ok) - 12))
            if fail:
                lines.append("")
                lines.append("失败 %d 个：" % len(fail))
                for n, e in fail[:12]:
                    lines.append("   · %s：%s" % (n, str(e)[:90]))
                if len(fail) > 12:
                    lines.append("   … 还有 %d 个" % (len(fail) - 12))
            lines.append("")
            lines.append("可到【已下载】页批量安装。")
            messagebox.showinfo("批量下载完成", "\n".join(lines))
            try:
                self.reload_downloaded()
            except Exception:                                # noqa: BLE001
                pass

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("批量下载失败", str(e)),
                                   self.set_status("批量下载失败：%s" % e)))

    # ---- 已下载页行为 ----
    def on_downloaded_select(self, item, _idx=None):
        self.selected_downloaded = item

    def reload_downloaded(self):
        self.set_status("正在读取已下载谱面…")

        def work():
            return S.list_downloaded()

        def done(items):
            self.downloaded_items = items
            self.downloaded_grid.set_items(
                items,
                footers=self._footer_downloaded,
                badge_of=lambda c: _short_text(c.get("version"), 12)
                if c.get("version") else None,
                cover_of=lambda c: (("local", c.get("cover"))
                                    if c.get("cover") else None),
                empty_text="还没有已下载的谱面。去【谱库浏览】页下载吧。")
            self.set_status("已下载 %d 个谱面" % len(items))

        self.async_.run(work, done,
                        lambda e: self.set_status("读取已下载失败：%s" % e))

    def _footer_downloaded(self, item, parent):
        err = item.get("error")
        mid = item.get("installed_music_id")
        if err:
            tk.Label(parent, text="⚠ " + _short_text(err, 60), bg=C["bg2"],
                     fg=C["danger"], font=(UI_FONT, 8), wraplength=CARD_W - 24,
                     justify="left", anchor="w").pack(fill="x")
            return
        if mid:
            tk.Label(parent, text="已装 id=%s" % mid, bg=C["bg2"], fg=C["accent2"],
                     font=(UI_FONT, 8)).pack(anchor="w")
        # 一行两个按钮：【一键安装】/【卸载】 + 【删除本地】
        row = tk.Frame(parent, bg=C["bg2"])
        row.pack(fill="x")
        if mid:
            ttk.Button(row, text="卸载", style="Danger.TButton",
                       command=lambda m=mid: self.uninstall_card(m)).pack(
                side="left", fill="x", expand=True)
        else:
            ttk.Button(row, text="一键安装", style="Accent.TButton",
                       command=lambda it=item: self.one_click_install_card(
                           it)).pack(side="left", fill="x", expand=True)
        # 【删除】= 只清本地下载文件（不同于"卸载"= 从游戏移除）
        ttk.Button(row, text="删除", style="Danger.TButton",
                   command=lambda it=item: self.delete_card(it)).pack(
            side="left", fill="x", expand=True, padx=(4, 0))

    def delete_card(self, item):
        """删除本地下载的谱面文件。若已装进游戏，**提示一并卸载**再删。"""
        folder = item.get("dir")
        if not folder:
            messagebox.showinfo("提示", "该卡片缺少下载目录信息。")
            return
        title = item.get("title") or os.path.basename(folder)
        mid = item.get("installed_music_id")

        def do_delete():
            if not messagebox.askyesno(
                    "删除本地谱面",
                    "将从磁盘删除《%s》的下载文件：\n\n  %s\n\n确定删除？"
                    % (title, folder)):
                return

            def work():
                return S.delete_downloaded(folder, force=bool(mid))

            def done(res):
                mb = res.get("freed", 0) / 1048576.0
                self.set_status("已删除《%s》，释放 %.1f MB" % (title, mb))
                self.reload_downloaded()
                try:
                    self.reload_installed()
                except Exception:                            # noqa: BLE001
                    pass

            def err(e):
                messagebox.showerror("删除失败", str(e))
                self.set_status("删除失败：%s" % e)

            self.set_status("正在删除《%s》…" % title)
            self.async_.run(work, done, err)

        # 已装进游戏 → 先问要不要一并卸载（否则游戏里会留下无效曲目）
        if mid:
            if messagebox.askyesno(
                    "该谱面已装进游戏",
                    "《%s》已经装进游戏（music_id=%s）。\n\n"
                    "【是】= 先从游戏卸载，再删除本地文件（推荐）\n"
                    "【否】= 取消，什么都不做\n\n"
                    "（只想删本地文件、保留游戏里那条的话，"
                    "请到【已装自制谱】页单独操作）" % (title, mid)):
                self.set_status("正在卸载《%s》…" % title)

                def work():
                    S.uninstall_music(mid)
                    return S.delete_downloaded(folder, force=True)

                def done(res):
                    mb = res.get("freed", 0) / 1048576.0
                    self.set_status("已卸载并删除《%s》，释放 %.1f MB" % (title, mb))
                    self.reload_downloaded()
                    try:
                        self.reload_installed()
                    except Exception:                        # noqa: BLE001
                        pass

                def err(e):
                    messagebox.showerror("卸载/删除失败", str(e))
                    self.set_status("卸载/删除失败：%s" % e)

                self.async_.run(work, done, err)
            return
        do_delete()

    # ---- 本地导入：谱面 / 官方更新包 ----
    def _data_dir(self):
        return getattr(S, "DATA_DIR", os.path.dirname(os.path.abspath(__file__)))

    def _require_game(self):
        """确保有可用的游戏目录，没有就引导用户指定。"""
        game = self.game_dir or None
        if not game:
            try:
                game = S.auto_detect_game()
            except Exception:                                # noqa: BLE001
                game = None
        if not game:
            if messagebox.askyesno(
                    "还没指定游戏目录",
                    "找不到 jubeat 的 contents 目录。\n\n现在手动指定一次？"):
                self.choose_game_dir()
                game = self.game_dir
        if not game:
            self.set_status("已取消：没有游戏目录")
        return game

    def open_import_dir(self):
        if I is None:
            messagebox.showinfo("提示", "缺少 ub_import.py 模块。")
            return
        d = I.import_dir(self._data_dir())
        self._open_path(d)
        self.set_status("把谱面或更新包放进这个文件夹，再点【文件 → 扫描导入目录】")

    def scan_import_dir(self):
        if I is None:
            messagebox.showinfo("提示", "缺少 ub_import.py 模块。")
            return
        d = I.import_dir(self._data_dir())
        found = I.classify_paths([d])
        self._handle_found(found, hint="导入目录：%s" % d)

    def _handle_found(self, found, hint=""):
        """把扫描结果交给用户确认，然后执行。"""
        charts, packs = found.get("charts") or [], found.get("packs") or []
        if not charts and not packs:
            messagebox.showinfo(
                "没有发现可导入的文件",
                "%s\n\n支持：\n  · 谱面 .mc / .mcz\n  · 官方更新包 .7z / .zip"
                % hint)
            return
        lines = []
        if charts:
            lines.append("谱面 %d 个：" % len(charts))
            for c in charts[:12]:
                lines.append("   · %s" % os.path.basename(c))
            if len(charts) > 12:
                lines.append("   … 还有 %d 个" % (len(charts) - 12))
        if packs:
            if lines:
                lines.append("")
            lines.append("更新包 %d 个：" % len(packs))
            for p in packs[:8]:
                lines.append("   · %s" % os.path.basename(p))
            if len(packs) > 8:
                lines.append("   … 还有 %d 个" % (len(packs) - 8))
        if not messagebox.askyesno("确认导入", "\n".join(lines) + "\n\n继续？"):
            return
        # 更新包需要逐个走"扫描 → 确认 → 应用"，所以分开处理
        if charts:
            self._install_units(I.plan_units(charts, self._data_dir()), charts)
        for p in packs:
            self._apply_pack_interactive(p)

    def import_charts(self):
        if I is None:
            messagebox.showinfo("提示", "缺少 ub_import.py 模块。")
            return
        paths = filedialog.askopenfilenames(
            title="选择谱面文件（可多选；建议连同音源/封面放同一目录）",
            filetypes=[("Malody 谱面", "*.mc *.mcz"), ("所有文件", "*.*")])
        if not paths:
            return
        found = I.classify_paths(list(paths))
        self._handle_found(found, hint="已选文件")

    def import_pack(self):
        paths = filedialog.askopenfilenames(
            title="选择 jubeat 官方更新包",
            filetypes=[("更新包", "*.7z *.zip"), ("所有文件", "*.*")])
        if not paths:
            return
        for p in paths:
            self._apply_pack_interactive(p)

    # ---- 更新包：扫描 → 确认 → 应用 ----
    def _apply_pack_interactive(self, pack_path):
        if UU is None:
            messagebox.showinfo("提示", "缺少 ub_updater.py 模块。")
            return
        game = self._require_game()
        if not game:
            return
        self.set_status("正在扫描更新包…")

        def work():
            return UU.do_scan(pack_path)

        def done(scan):
            marks = scan.get("deletion_markers") or {}
            n_mark = sum(len(v) for v in marks.values())
            ids = scan.get("affected_song_ids") or []
            dlls = scan.get("new_dlls") or []
            msg = ("更新包：%s\n\n"
                   "条目总数：%d\n"
                   "  普通文件：%d\n"
                   "  谱面：%d\n"
                   "  贴图：%d\n"
                   % (os.path.basename(pack_path), scan.get("entries", 0),
                      scan.get("normal_files", 0), scan.get("charts", 0),
                      scan.get("textures", 0)))
            if dlls:
                msg += ("\n⚠ 含官方新版 DLL %d 个：\n%s\n"
                        "  应用后 DLL 补丁会失效（偏移变了），稍后会问你是否自动重新打补丁。\n"
                        % (len(dlls),
                           "\n".join("    · %s" % d["rel"] for d in dlls[:4])))
            if n_mark:
                msg += ("\n⚠ 含删除标记 %d 个（涉及 %d 首曲目 id）\n"
                        "  应用时会先备份；是否同时执行删除？\n"
                        % (n_mark, len(ids)))
                msg += "\n选【是】= 应用并执行删除\n选【否】= 只应用，跳过删除\n"
                ans = messagebox.askyesnocancel("应用更新包", msg)
                if ans is None:
                    return
                allow_delete = bool(ans)
            else:
                msg += "\n游戏目录：%s\n\n是否应用？" % game
                if not messagebox.askyesno("应用更新包", msg):
                    return
                allow_delete = False

            self.set_status("正在应用更新包（已备份）…")

            def work2():
                try:
                    return UU.do_apply(pack_path, game,
                                       dry_run=False, allow_delete=allow_delete)
                except SystemExit as ex:                     # do_apply 用 SystemExit 报错
                    raise RuntimeError(str(ex))

            def done2(res):
                plan = (res or {}).get("plan") or res or {}
                n_apply = len(plan.get("apply") or [])
                n_skip = len(plan.get("skip_marker") or [])
                self.set_status("更新包应用完成（写入 %d 项）" % n_apply)
                extra = ""
                if dlls:
                    extra = "\n\n⚠ 包含新版 DLL：%s" % "、".join(
                        d["rel"] for d in dlls[:3])
                messagebox.showinfo(
                    "更新包已应用",
                    "《%s》应用完成。\n\n写入 %d 项\n跳过删除标记 %d 项\n\n"
                    "备份在 ub_updater 工作目录的 backup 下，可回退。%s"
                    % (os.path.basename(pack_path), n_apply, n_skip, extra))
                if dlls:
                    self._ask_new_dll(game, dlls)

            def err2(e):
                messagebox.showerror("应用更新包失败", str(e))
                self.set_status("应用更新包失败：%s" % e)

            self.async_.run(work2, done2, err2)

        self.async_.run(work, done,
                        lambda e: messagebox.showerror("扫描更新包失败", str(e)))

    def _ask_new_dll(self, game, dlls):
        """官方新版 DLL 的后续处理：装成 jb_org.dll 并**自动重新打补丁**。"""
        rel = dlls[0]["rel"]
        src = os.path.join(game, rel.replace("/", os.sep))
        if not os.path.exists(src):
            messagebox.showwarning(
                "找不到新版 DLL",
                "应用后没能找到 %s。\n请手工按包内《应用说明.txt》处理。" % src)
            return
        if not messagebox.askyesno(
                "发现官方新版 DLL",
                "包里带了官方新版 %s。\n\n"
                "它已释放到：\n  %s\n\n"
                "游戏实际加载的是 contents\\jb_org.dll，而这个新版 DLL 是官方原版，\n"
                "**DLL 补丁（跳过教程 / 计时器冻结等）会失效**（官方重编译后偏移全变）。\n\n"
                "现在自动处理吗？\n"
                "  1) 备份现有 jb_org.dll\n"
                "  2) 用新版覆盖\n"
                "  3) 用特征码重新定位 4 个补丁站点并打补丁\n\n"
                "（只在这些站点**全部唯一命中**时才会写出补丁；\n"
                "  失败则保留官方原版并告诉你，不会乱打）\n\n"
                "⚠️ 不会碰 contents\\jubeat.dll（omnimix 挂载模块）。" % (rel, src)):
            self.set_status("已跳过新版 DLL 的补丁处理")
            return
        self.set_status("正在处理新版 DLL（备份 + 打补丁）…")

        def work():
            return UU.apply_new_dll(src, game, target_rel="jb_org.dll", patch=True)

        def done(res):
            if res.get("error"):
                self.set_status("DLL 处理未完成：%s" % res["error"])
                messagebox.showwarning(
                    "DLL 处理未完成",
                    "%s\n\n目标：%s\n备份：%s\n\n"
                    "建议按包内《应用说明.txt》人工核对。"
                    % (res["error"], res.get("target"), res.get("backup")))
                return
            self.set_status("DLL 已更新并打好补丁 ✅")
            messagebox.showinfo(
                "DLL 处理完成",
                "已更新 %s 并重新打好补丁。\n\n"
                "  补丁站点：%s%s\n"
                "  备份：%s"
                % (os.path.basename(res["target"] or ""),
                   res.get("sites"),
                   "（原本已是补丁版，无需重打）" if res.get("already") else "",
                   res.get("backup") or "（无，原文件不存在）"))

        def err(e):
            self.set_status("DLL 处理失败：%s" % e)
            messagebox.showerror("DLL 处理失败", str(e))

        self.async_.run(work, done, err)

    # ---- 批量安装谱面 ----
    def _install_units(self, units, chart_paths):
        if not units:
            return
        game = self._require_game()
        if not game:
            return
        titles = [u["title"] for u in units]
        head = "、".join(titles[:4]) + ("…" if len(titles) > 4 else "")
        if not messagebox.askyesno(
                "安装自制谱",
                "将安装 %d 个谱面：\n  %s\n\n"
                "  · 自动选空闲 music_id、只新增不覆盖\n"
                "  · 优先用谱面自带音源，没有则用下载的封面/占位\n\n"
                "确定继续？（每个谱面编码音源约需十秒）" % (len(units), head)):
            return

        def work():
            done, failed = [], []
            for idx, u in enumerate(units, 1):
                self.async_.q.put((self.set_status,
                                   ("安装 %d/%d：%s" % (idx, len(units), u["title"]),), {}))

                def prog(_i, msg, _t=u["title"]):
                    self.async_.q.put((self.set_status, ("%s：%s" % (_t, msg),), {}))
                try:
                    res = S.one_click_install(u["unit_dir"], progress=prog)
                    mid = ((res or {}).get("plan") or {}).get("music_id")
                    done.append((u["title"], mid))
                except Exception as ex:                      # noqa: BLE001
                    failed.append((u["title"], str(ex)))
            return {"done": done, "failed": failed}

        def done(res):
            try:
                I.cleanup_stage(self._data_dir())
            except Exception:                                # noqa: BLE001
                pass
            d, f = res["done"], res["failed"]
            self.set_status("导入完成：成功 %d，失败 %d" % (len(d), len(f)))
            lines = []
            if d:
                lines.append("成功 %d 个：" % len(d))
                for t, mid in d:
                    lines.append("   · %s  →  music_id=%s" % (t, mid))
            if f:
                lines.append("")
                lines.append("失败 %d 个：" % len(f))
                for t, e in f:
                    lines.append("   · %s：%s" % (t, e[:80]))
            messagebox.showinfo("导入完成",
                                "\n".join(lines) + "\n\n可在【已装自制谱】页查看。")
            try:
                self.reload_installed()
            except Exception:                                # noqa: BLE001
                pass

        self.async_.run(work, done,
                        lambda e: messagebox.showerror("导入失败", str(e)))

    def one_click_install_card(self, item):
        folder = item.get("dir")
        if not folder:
            messagebox.showinfo("提示", "该卡片缺少下载目录信息。")
            return
        title = item.get("title") or os.path.basename(folder)
        if not messagebox.askyesno(
                "一键安装",
                "将把《%s》装成游戏新曲：\n"
                "  · 自动探测游戏目录、自动选空闲 id\n"
                "  · 自动用谱面自带音源、自动用下载的封面当曲绘\n"
                "  · 只新增、不覆盖\n\n确定继续？（编码音源约需十秒）" % title):
            return
        self.set_status("正在安装《%s》…（编码音源，请稍候）" % title)

        def work():
            def prog(_i, msg):
                self.async_.q.put((self.set_status,
                                   ("安装中：%s" % msg,), {}))
            return S.one_click_install(folder, progress=prog)

        def done(res):
            plan = (res or {}).get("plan") or {}
            mid = plan.get("music_id")
            sp = plan.get("slot_plan") or {}
            if sp and len({v["diff"] for v in sp.values()}) == 1:
                how = "三档同谱（谱面只含 1 个难度：%s）" % (
                    (sp.get("ext") or {}).get("version"))
            elif sp:
                how = "BSC=%s / ADV=%s / EXT=%s" % tuple(
                    (sp.get(k) or {}).get("version") for k in ("bsc", "adv", "ext"))
            else:
                how = "未分派"
            self.set_status("安装完成 music_id=%s" % mid)
            messagebox.showinfo(
                "安装完成",
                "《%s》已装成 music_id=%s。\n\n谱面分档：%s\n定数：Lv.%s\n\n"
                "可在【已装自制谱】页查看或卸载。"
                % (title, mid, how, plan.get("level")))
            self.reload_downloaded()

        def err(e):
            messagebox.showerror("安装失败", str(e))
            self.set_status("安装失败：%s" % e)

        self.async_.run(work, done, err)

    def uninstall_card(self, mid):
        if not messagebox.askyesno(
                "确认卸载",
                "将删除 music_id=%s 的文件并还原曲表/包表。\n继续？" % mid):
            return
        self.set_status("正在卸载 music_id=%s …" % mid)

        def work():
            return S.uninstall_music(mid)

        def done(res):
            removed = len((res or {}).get("removed", []) or [])
            self.set_status("已卸载 music_id=%s" % mid)
            messagebox.showinfo("卸载完成",
                                "已移除 %d 个文件并还原曲表。" % removed)
            self.reload_downloaded()
            self.reload_installed()

        def err(e):
            messagebox.showerror("卸载失败", str(e))
            self.set_status("卸载失败：%s" % e)

        self.async_.run(work, done, err)

    # ---- 已装页行为 ----
    def on_installed_select(self, item, _idx=None):
        self.selected_installed = item

    def reload_installed(self):
        self.set_status("正在读取已装清单…")

        def work():
            items = S.list_installed(self.game_dir)
            covers = {}
            try:
                for c in S.list_downloaded():
                    t = (c.get("title") or "").strip().lower()
                    if t and c.get("cover"):
                        covers.setdefault(t, c["cover"])
            except Exception:                            # noqa: BLE001
                pass
            return items, covers

        def done(res):
            items, covers = res
            self.installed_items = items
            self._installed_covers = covers
            self.installed_grid.set_items(
                items,
                footers=self._footer_installed,
                badge_of=lambda it: ("id=%s" % it.get("music_id"))
                if it.get("music_id") else None,
                cover_of=self._installed_cover_spec,
                empty_text="还没有装进游戏的自制谱。去【已下载】页一键安装吧。")
            self.set_status("已装自制曲 %d 首" % len(items))

        self.async_.run(work, done,
                        lambda e: self.set_status("读取清单失败：%s" % e))

    def _installed_cover_spec(self, it):
        t = (it.get("title") or "").strip().lower()
        cov = (self._installed_covers or {}).get(t)
        return ("local", cov) if cov else None

    def _footer_installed(self, item, parent):
        mid = item.get("music_id")
        ttk.Button(parent, text="卸载", style="Danger.TButton",
                   command=lambda m=mid: self.uninstall_card(m)).pack(fill="x")

    def open_backup_dir(self):
        d = (self.selected_installed or {}).get("backup_dir")
        if not d or not os.path.isdir(d):
            d = os.path.join(S.newsong_workdir(), "backup")
        if not os.path.isdir(d):
            messagebox.showinfo("提示", "未找到备份目录：%s" % d)
            return
        self._open_path(d)

    # ---- 游戏目录 ----
    def rediscover_game(self):
        g = S.auto_detect_game()
        if not g:
            messagebox.showinfo("重新探测游戏目录",
                                "未找到游戏目录。\n"
                                "请用【高级 → 手动指定游戏目录…】选择 contents。")
            self.refresh_game_dir()
            return
        S.set_game_dir(g)
        self.refresh_game_dir()
        self.set_status("已记住游戏目录：%s" % g)

    def choose_game_dir(self):
        p = filedialog.askdirectory(title="选择 jubeat 游戏目录（contents）")
        if p:
            S.set_game_dir(p)
            self.refresh_game_dir()
            self.set_status("已指定游戏目录：%s" % p)

    # ---- 登录 ----
    def do_login(self):
        name = self.var_user.get().strip()
        psw = self.var_psw.get()
        if not name or not psw:
            messagebox.showinfo("提示", "请输入账号和密码。")
            return
        self.login_log.insert("end", "正在登录 %s …\n" % name)
        self.set_status("正在登录…")

        def work():
            return self.client.login(name, psw)

        def done(res):
            S.save_credentials(res["uid"], res["key"], name=name,
                               store_key=res.get("storeKey"))
            self.var_psw.set("")                 # 立即清空密码框
            extra = ""
            if not res.get("storeKey"):
                extra = "（注意：服务端未返回商店凭证，下载可能失败，请稍后重试）"
            self.login_log.insert("end", "登录成功：uid=%s\n凭据已保存：%s\n%s\n"
                                         % (res["uid"], S.creds_path(), extra))
            self.refresh_login_state()
            self.set_status("登录成功 uid=%s" % res["uid"])

        def err(e):
            self.var_psw.set("")
            detail = getattr(e, "detail", None)
            self.login_log.insert("end", "登录失败：%s\n诊断日志：%s\n"
                                         % (e, S.login_log_path()))
            msg = str(e) + (("\n\n%s" % detail) if detail else "")
            messagebox.showerror("登录失败", msg)
            self.set_status("登录失败")

        self.async_.run(work, done, err)

    def open_login_log(self):
        """打开登录诊断日志（不存在则先建一个空文件）。"""
        p = S.login_log_path()
        if not os.path.exists(p):
            try:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "a", encoding="utf-8").close()
            except Exception as e:                       # noqa: BLE001
                messagebox.showinfo("打开失败", str(e))
                return
        self._open_path(p)

    def do_logout(self):
        if messagebox.askyesno("退出登录", "确定删除本机保存的 uid/凭证 凭据？\n%s"
                               % S.creds_path()):
            S.clear_credentials()
            self.login_log.insert("end", "已清除凭据。\n")
            self.refresh_login_state()
            self.set_status("已退出登录")

    # ---- 目录 ----
    def choose_proxy(self):
        """设置网络代理（默认留空 = 直连，适用于无代理环境）。"""
        cur = (self.cfg.get("proxy") or "").strip()
        hint = getattr(S, "DEFAULT_PROXY_HINT", "http://127.0.0.1:10808")
        msg = ("HTTP/HTTPS 代理地址：\n"
               "\n"
               "  · 留空 = 直连（默认，适用于无代理环境）\n"
               "  · 例：%s\n"
               "\n"
               "注意：国内私服 api.paimonnokoya.me 始终直连，不受此项影响。"
               % hint)
        val = simpledialog.askstring("网络代理设置", msg,
                                     initialvalue=cur, parent=self.root)
        if val is None:
            return                                      # 用户取消
        val = val.strip()
        self.cfg["proxy"] = val
        try:
            S.save_config(self.cfg)
        except Exception as ex:                          # noqa: BLE001
            messagebox.showerror("保存失败", str(ex))
            return
        # Client 持有同一个 cfg 引用 → 立即生效；再显式指一次以防被替换过
        try:
            self.client.cfg = self.cfg
        except Exception:                                # noqa: BLE001
            pass
        self.set_status("代理已%s"
                        % ("关闭（直连）" if not val else "设为 %s" % val))

    def choose_cache_dir(self):
        p = filedialog.askdirectory(title="选择下载缓存目录")
        if p:
            self.cfg["cache_dir"] = p
            S.save_config(self.cfg)
            self.set_status("缓存目录已改为 %s" % p)

    def open_download_dir(self):
        d = S.cache_dir(self.cfg)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:                                # noqa: BLE001
            pass
        self._open_path(d)

    def open_data_dir(self):
        self._open_path(S.DATA_DIR)

    def _open_path(self, p):
        try:
            if os.name == "nt":
                os.startfile(p)                          # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["xdg-open", p])
        except Exception as e:                           # noqa: BLE001
            messagebox.showinfo("打开失败", str(e))

    # ---- 弹窗 ----
    def show_disclaimer(self, first=False):
        win = tk.Toplevel(self.root)
        win.title("免责声明")
        win.configure(bg=C["bg"])
        win.transient(self.root)
        txt = tk.Text(win, width=78, height=20, bg=C["bg2"], fg=C["fg"],
                      relief="flat", wrap="word")
        txt.pack(fill="both", expand=True, padx=12, pady=12)
        txt.insert("end", DISCLAIMER)
        txt.configure(state="disabled")
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=12, pady=(0, 12))

        def accept():
            self.cfg["disclaimer_accepted"] = True
            S.save_config(self.cfg)
            win.destroy()

        if first:
            ttk.Button(bar, text="不同意并退出",
                       command=self.root.destroy).pack(side="left")
            ttk.Button(bar, text="同意并继续", style="Accent.TButton",
                       command=accept).pack(side="right")
        else:
            ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")
        return win

    def show_about(self):
        ver = getattr(U, "APP_VERSION", "?") if U else "(无自更新模块)"
        repo = getattr(U, "GITHUB_REPO", "") if U else ""
        messagebox.showinfo("关于", "jubeat 自制谱商店  v%s\n\n"
                                    "Malody 谱库 → 下载 → 装进 jubeat（只新增不覆盖）\n"
                                    "核心逻辑：ub_store.py  |  本界面：ub_store_gui.py\n"
                                    "安装引擎：ub_newsong.py（复用，未修改）\n"
                                    "自更新：ub_update.py%s"
                                    % (ver, ("\n源码：https://github.com/%s" % repo) if repo else ""))

    # ---- 自更新 ----
    def check_update(self, interactive=True):
        """检查 GitHub Releases 上的新版本。

        interactive=False（启动时静默检查）只在状态栏提示，不弹窗打断用户。
        """
        if U is None:
            if interactive:
                messagebox.showinfo("检查更新", "未找到自更新模块 ub_update.py。")
            return
        if interactive:
            self.set_status("正在检查更新…")
        proxy = (self.cfg.get("proxy") or "").strip() or None

        def work():
            return U.check(current=U.APP_VERSION, proxy=proxy)

        def done(info):
            if not info.get("ok"):
                if interactive:
                    messagebox.showwarning("检查更新",
                                           "检查失败：%s" % info.get("error"))
                else:
                    self.set_status("检查更新失败：%s" % info.get("error"))
                return
            if not info.get("newer"):
                if interactive:
                    messagebox.showinfo("检查更新",
                                        "当前已是最新版本（%s）。" % info["current"])
                else:
                    self.set_status("已是最新版本 %s" % info["current"])
                return
            # 有新版本
            notes = (info.get("notes") or "(无更新说明)").strip()
            if len(notes) > 600:
                notes = notes[:600] + "…"
            if not interactive:
                self.set_status("发现新版本 %s（当前 %s）—— 见【帮助 → 检查更新】"
                                % (info["latest"], info["current"]))
                return
            if info.get("error"):
                messagebox.showwarning("检查更新", info["error"])
                return
            if not messagebox.askyesno(
                    "发现新版本",
                    "当前：%s\n最新：%s\n\n%s\n\n是否现在下载并更新？（更新后会自动重启）"
                    % (info["current"], info["latest"], notes)):
                return
            self._do_update(info)

        self.async_.run(work, done,
                        lambda e: (messagebox.showerror("检查更新失败", str(e))
                                   if interactive
                                   else self.set_status("检查更新失败：%s" % e)))

    def _do_update(self, info):
        exe = U.current_exe()
        if not exe:
            messagebox.showinfo("无法自更新",
                                "当前以源码方式运行，请用 git pull 手动更新。")
            return
        dst = exe + ".new"
        self.set_status("正在下载 %s …" % info["asset_name"])
        proxy = (self.cfg.get("proxy") or "").strip() or None

        def work():
            def prog(done, total):
                if total:
                    self.async_.q.put((self.set_status,
                                       ("正在下载 %s … %d%%"
                                        % (info["asset_name"], 100 * done // total),), {}))
            U.download(info["asset_url"], dst, proxy=proxy, progress=prog)
            return dst

        def done(path):
            try:
                U.replace_self(path, restart=True)
            except Exception as ex:                          # noqa: BLE001
                messagebox.showerror("更新失败", "%s\n\n原程序未被修改。" % ex)
                return
            # 新进程已启动，本进程退出
            try:
                self.root.destroy()
            except Exception:                                # noqa: BLE001
                pass

        self.async_.run(work, done,
                        lambda e: messagebox.showerror("下载失败", str(e)))


# ---------------------------------------------------------------------------
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # 数据目录若刚从旧位置搬过来，先纠正 config.json 里的旧绝对路径
    try:
        S.migrate_legacy_data()
    except Exception:
        pass
    root = tk.Tk()
    app = StoreGUI(root)
    if "--selftest" in argv:
        # 无头自检：建窗、构造控件、立刻退出（不 mainloop）
        root.update_idletasks()
        print("selftest OK: tabs=%d" % len(app.notebook.tabs()))
        root.destroy()
        return 0
    if "--restore-ui-font" in argv:
        # 把会话界面字体恢复成系统默认（万一觉得改了系统外观不舒服）
        if FONT is not None:
            ok = FONT.restore_session_ui_font()
            print("restore session ui font: %s" % ("OK" if ok else "失败"))
        root.destroy()
        return 0
    if "--depcheck" in argv:
        # 依赖自检：确认打包进 exe 的关键第三方库都在（缺了会导致安装谱面失败）
        print("=== 关键依赖 ===")
        rows = []
        try:
            import kbinxml
            rows.append(("kbinxml", getattr(kbinxml, "__version__", "?")))
        except Exception as ex:                              # noqa: BLE001
            rows.append(("kbinxml", "**缺失**: %s" % ex))
        try:
            import lxml.etree as _le
            rows.append(("lxml", getattr(_le, "__version__", "?")))
        except Exception as ex:                              # noqa: BLE001
            rows.append(("lxml", "**缺失**: %s" % ex))
        try:
            import PIL
            rows.append(("Pillow", getattr(PIL, "__version__", "?")))
        except Exception as ex:                              # noqa: BLE001
            rows.append(("Pillow", "**缺失**: %s" % ex))
        for name, ver in rows:
            print("  %-12s %s" % (name, ver))
        # ffmpeg 是外部可执行文件（音源解码用），单独报告路径
        try:
            import ub_audio as _ua
            print("  %-12s %s" % ("ffmpeg", _ua.ffmpeg_exe()))
        except Exception as ex:                              # noqa: BLE001
            print("  %-12s **缺失**: %s" % ("ffmpeg", ex))
        print("=== 内部模块 ===")
        for mod, label in ((S, "ub_store"), (I, "ub_import"),
                           (UU, "ub_updater"), (U, "ub_update"),
                           (FONT, "ub_font")):
            print("  %-12s %s" % (label, "OK" if mod is not None else "**缺失**"))
        # ub_chart 的 KBinXML 是否真的可用（这是安装谱面的必需项）
        try:
            import ub_chart as _uc
            print("  %-12s %s" % ("KBinXML",
                                  "OK" if getattr(_uc, "KBinXML", None) else "**不可用**"))
        except Exception as ex:                              # noqa: BLE001
            print("  %-12s **导入失败**: %s" % ("ub_chart", ex))
        root.destroy()
        return 0
    if "--fontcheck" in argv:
        # 字体自检：确认自带字库已注册、且中文/日文字形都能渲染（宽度 > 0）。
        # 用来在"没有中文字体的日文系统"上快速验证界面方框问题是否已解决。
        print("font module : %s" % ("有" if FONT is not None else "缺失"))
        if FONT is not None:
            print("font file   : %s" % FONT.font_path())
            print("system cjk  : %s" % FONT.system_has_cjk_font())
            info = FONT.ensure_ui_font()
            print("registered  : %s" % info.get("family"))
            print("session ui  : %s  (%s)" % (info.get("session_ui"),
                                              info.get("session_ui_note")))
            print("applied     : %s" % FONT.apply_to_tk(root))
            try:
                import tkinter.font as tkfont
                f = tkfont.Font(root=root, family=info.get("family")
                                or "TkDefaultFont", size=14)
                bad = []
                for ch in "谱铺载设删转检查确认装导更新包曲名艺人高级帮助":
                    if f.measure(ch) <= 0:
                        bad.append(ch)
                for ch in "の漢あいうえお峠辻畑":
                    if f.measure(ch) <= 0:
                        bad.append(ch)
                print("cjk glyphs  : %s" % ("全部可渲染" if not bad
                                            else "缺字形 %s" % "".join(bad)))
            except Exception as ex:                          # noqa: BLE001
                print("glyph check : 失败 %s" % ex)
        root.destroy()
        return 0
    # 命令行带路径（把文件拖到 exe 图标上、或从"发送到"调用）→ 启动后直接导入
    extra = [a for a in argv if a and not a.startswith("-")]
    if extra and I is not None:
        def _auto_import():
            app._handle_found(I.classify_paths(extra),
                              hint="命令行传入 %d 项" % len(extra))
        try:
            root.after(700, _auto_import)
        except Exception:                                    # noqa: BLE001
            pass
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
