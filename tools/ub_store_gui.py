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
                 cover_loader=None, on_select=None):
        super().__init__(master)
        self.card_w = card_w
        self.card_h = card_h
        self.cover_size = cover_size
        self.cover_loader = cover_loader
        self.on_select = on_select
        self.cards = []
        self._cols = 0
        self._selected = -1

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
                                    bg=C["bg"], fg=C["fg_dim"], font=("", 11))

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
                                    bg=C["bg"], fg=C["fg_dim"], font=("", 11))
        self.cards = []
        self._selected = -1
        self._cols = 0
        for it in items:
            self.cards.append(self._make_card(it, footers, badge_of))

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

    def _make_card(self, item, footers, badge_of):
        f = tk.Frame(self.inner, bg=C["bg2"], width=self.card_w, height=self.card_h,
                     highlightbackground=C["border"], highlightthickness=2)
        f.grid_propagate(False)

        holder = tk.Frame(f, bg=C["bg3"], width=self.cover_size,
                          height=self.cover_size)
        holder.pack(pady=(10, 4))
        holder.pack_propagate(False)
        cover = tk.Label(holder, bg=C["bg3"], fg=C["fg_dim"], text="载入中…",
                         font=("", 9))
        cover.place(x=0, y=0, relwidth=1, relheight=1)
        badge = tk.Label(holder, bg=C["accent"], fg="#ffffff", font=("", 8, "bold"),
                         padx=5, pady=1)
        bt = badge_of(item) if badge_of else None
        if bt:
            badge.configure(text=str(bt))
            badge.place(relx=1.0, x=-4, y=4, anchor="ne")

        title = tk.Label(f, text=_short_text(item.get("title") or "(无标题)", 18),
                         bg=C["bg2"], fg=C["fg"], font=("", 10, "bold"),
                         wraplength=self.card_w - 18, justify="left", anchor="w")
        title.pack(fill="x", padx=9, pady=(4, 0))
        artist = tk.Label(f, text=_short_text(item.get("artist") or "-", 22),
                          bg=C["bg2"], fg=C["fg_dim"], font=("", 8),
                          wraplength=self.card_w - 18, justify="left", anchor="w")
        artist.pack(fill="x", padx=9)

        footer = tk.Frame(f, bg=C["bg2"])
        footer.pack(side="bottom", fill="x", padx=7, pady=7)
        if footers:
            footers(item, footer)

        card = {"frame": f, "cover": cover, "item": item, "photo": None}
        idx = len(self.cards)

        def on_click(_e=None, i=idx):
            self.select(i)

        for w in (f, holder, cover, title, artist):
            w.bind("<Button-1>", on_click)
        return card

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
        """启动后的收尾：清掉自更新遗留文件，按配置做一次静默版本检查。"""
        if U is None:
            return
        try:
            if U.cleanup_old():
                self.set_status("已清理上次更新留下的旧文件")
        except Exception:                                    # noqa: BLE001
            pass
        if not self.cfg.get("check_update_on_start", True):
            return
        # 延迟 2s，别和首屏加载抢网络
        try:
            self.root.after(2000, lambda: self.check_update(interactive=False))
        except Exception:                                    # noqa: BLE001
            pass

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
                     font=("", 12, "bold"))
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

        mid = ttk.Frame(root)
        mid.pack(fill="both", expand=True, padx=8)
        self.browse_grid = CardGrid(mid, cover_loader=self.load_cover_spec,
                                    on_select=self.on_browse_select)
        self.browse_grid.pack(side="left", fill="both", expand=True)

        side = ttk.Frame(mid, style="Card.TFrame", width=252)
        side.pack(side="right", fill="y", padx=(8, 0))
        side.pack_propagate(False)
        tk.Label(side, text="谱面详情", bg=C["bg2"], fg=C["accent"],
                 font=("", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 4))
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

        self.downloaded_grid = CardGrid(root, cover_loader=self.load_cover_spec,
                                        on_select=self.on_downloaded_select)
        self.downloaded_grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))

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

        self.installed_grid = CardGrid(root, cover_loader=self.load_cover_spec,
                                       on_select=self.on_installed_select)
        self.installed_grid.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # ===================== 登录 =====================
    def _build_account(self, root):
        card = ttk.Frame(root, style="Card.TFrame")
        card.pack(fill="x", padx=16, pady=16)
        tk.Label(card, text="Malody 账号登录", bg=C["bg2"], fg=C["accent"],
                 font=("", 12, "bold")).grid(row=0, column=0, columnspan=2,
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
        self._load_batch(first=True)

    def change_page(self, delta):
        npg = self.page + delta
        if npg < 0:
            return
        # 目标页还没加载到 → 再拉一批（异步）；否则本地翻页
        if (npg + 1) * self.page_size > len(self.filtered_all) and self.has_more:
            self.page = npg
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
        """
        if not first and not self.has_more:
            self.set_status("已经到底了：公开接口没有更多条目")
            return
        # ⚠️ Tk 变量只能在**主线程**读：在后台 work() 里访问 self.var_pad_only
        # 会与主线程的 Tk 事件循环争 Tcl 锁（主线程紧密 update 时直接死锁）。
        pad_only = bool(self.var_pad_only.get())
        self.set_status("正在拉取一批（商店 1 页 + 公开 4×1 页）…")

        def work():
            if self.session is None:
                self.session = S.BrowseSession(
                    self.client, mode=S.MODE_PAD, pad_only=pad_only)
            return self.session.next_batch()

        def done(res):
            new, more = res
            self.all_rows.extend(new)
            self.has_more = more
            p = self.session.parts if self.session else {}
            self.source_label = "商店 %d + 公开 %d（已加载 %d%s）" % (
                p.get("store", 0), p.get("anon", 0), len(self.all_rows),
                "" if more else "，已到底")
            self.apply_filter(reset_page=False)
            self.set_status("本批 +%d 条 → %s" % (len(new), self.source_label))

        self.async_.run(work, done, lambda e: self.on_error(e, "拉取列表失败"))

    def _render_page(self):
        arr = self.filtered_all
        maxp = max(1, (len(arr) + self.page_size - 1) // self.page_size)
        self.page = max(0, min(self.page, maxp - 1))
        self.rows = arr[self.page * self.page_size:(self.page + 1) * self.page_size]
        self.browse_grid.set_items(
            self.rows,
            badge_of=self._browse_badge,
            cover_of=self._browse_cover_spec,
            empty_text="没有匹配的谱面。")
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
                     fg=C["danger"], font=("", 8), wraplength=CARD_W - 24,
                     justify="left", anchor="w").pack(fill="x")
            return
        if mid:
            row = tk.Frame(parent, bg=C["bg2"])
            row.pack(fill="x")
            tk.Label(row, text="已装 id=%s" % mid, bg=C["bg2"], fg=C["accent2"],
                     font=("", 8)).pack(side="left")
            ttk.Button(row, text="卸载", style="Danger.TButton",
                       command=lambda m=mid: self.uninstall_card(m)).pack(side="right")
        else:
            ttk.Button(parent, text="一键安装", style="Accent.TButton",
                       command=lambda it=item: self.one_click_install_card(
                           it)).pack(fill="x")

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
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
