#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ub_store.py -- Malody 谱库客户端 + 下载 + 安装编排（Phase 4 核心逻辑）

纯 stdlib（urllib），可 import、带 CLI。供 ub_store_gui.py 调用；**GUI 不写业务逻辑**。

职责：
  * HTTP 客户端：可配代理（海外域）/直连（国内私服），限速 + 有限重试
  * Malody 谱库只读接口：匿名公开列表 / 游客 token / 登录 / 歌曲列表 / 谱面列表 / 详情
  * 下载：/api/store/download -> 逐文件下载 -> **校验 md5(hash)** -> 落本地缓存
  * 凭据：落 uid/key(登录 token)/storeKey(商店凭证)（**明文密码绝不落盘、绝不写日志/打印**）
  * 安装编排：复用（不修改）ub_newsong.py 的 make_plan / install / uninstall
  * 已装清单：本地 manifest + 兼容读取 ub_newsong 的 state_*.json

安全：
  * 本文件不含任何硬编码账号 / 密码 / token。
  * login() 接收明文密码仅用于本地即时 md5，不打印、不落盘、不进入异常消息。
  * 登录诊断写 %LOCALAPPDATA%\\ub_store\\login_debug.log（源码模式落 DATA_DIR）；
    **只记长度/状态码/原始响应**，写前对明文密码、psw 的 md5、以及响应中
    回显的 token/tokenStore/key 等凭证值做全文擦除（绝不落盘）。

鉴权要点（与 Malody 社区前端一致，community_index.js 的 nt/We 请求助手）：
  * **商店接口**（/store/*、/skin/*）的 query `key` 用**商店凭证 storeKey**（即登录响应
    的 `tokenStore`）；**其它接口**（如 /community/*）用登录 token（`token`）。
  * 缺失凭证时服务端返回 code=-1000；本模块在发请求前先本地校验并给可操作提示。
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import zipfile
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ---------------------------------------------------------------------------
# 常量 / 默认配置
# ---------------------------------------------------------------------------
DEFAULT_API_BASE = "https://api.mugzone.net/api"
DEFAULT_LEGACY_BASE = "http://m.mugzone.net"
DEFAULT_COVER_BASE = "http://cni.machart.top"
DEFAULT_PROXY = ""                 # 默认**不使用代理**（直连）；需要时在【高级 → 网络代理设置…】里手动填
DEFAULT_PROXY_HINT = "http://127.0.0.1:10808"   # 仅作为对话框里的示例提示，不写进默认配置
# 国内私服：必须直连（不走代理）
DEFAULT_DIRECT_HOSTS = ["api.paimonnokoya.me", "127.0.0.1", "localhost"]
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_RETRY = 3
DEFAULT_MIN_INTERVAL = 1.0        # 秒，请求之间最小间隔（限速）
# 客户端版本：与 Malody 社区前端一致（community_index.js 里 dm="6.3.22"，
# 前端对跨域接口会带 Malody: <clientVersion> 头）
DEFAULT_CLIENT_VERSION = "6.3.22"

MODE_PAD = 4                      # Malody 模式号：Pad = jubeat（16 格）
MASK_PAD = 1 << MODE_PAD          # 歌曲 mode 位掩码中 Pad 位

# 数据目录（便携模式）：一律放在**程序所在目录**下，便于整包搬走。
#   exe 模式  -> <exe 所在目录>\ub_store_data
#   源码模式  -> <仓库>\tmp\ub_store        （测试用；可用 UB_STORE_DATA 覆盖）
# 旧的 %LOCALAPPDATA%\ub_store 会在首次运行时自动迁移过来（见 migrate_legacy_data）。
DATA_DIR_ENV = "UB_STORE_DATA"
DATA_DIR_NAME = "ub_store_data"


def _exe_dir():
    if getattr(sys, "frozen", False):
        try:
            return os.path.dirname(os.path.abspath(sys.executable))
        except Exception:
            return None
    return None


def legacy_data_roots():
    """历史上用过的数据目录（用于一次性迁移）。"""
    out = []
    la = os.environ.get("LOCALAPPDATA")
    if la:
        out.append(os.path.join(la, "ub_store"))
        out.append(os.path.join(la, "ub_store", DATA_DIR_NAME))
    out.append(os.path.join(os.path.dirname(_HERE), "tmp", "ub_store"))
    out.append(os.path.join(os.path.expanduser("~"), "ub_store"))
    return out


def _data_root() -> str:
    ov = os.environ.get(DATA_DIR_ENV)
    if ov:
        return os.path.abspath(ov)
    exed = _exe_dir()
    if exed:
        cand = os.path.join(exed, DATA_DIR_NAME)
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except Exception:
            # 程序目录不可写（只读介质/权限）→ 回退到用户目录
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            fallback = os.path.join(base, "ub_store")
            os.makedirs(fallback, exist_ok=True)
            return fallback
    return os.path.join(os.path.dirname(_HERE), "tmp", "ub_store")


DATA_DIR = _data_root()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
CRED_PATH = os.path.join(DATA_DIR, "credentials.json")
MANIFEST_PATH = os.path.join(DATA_DIR, "installed.json")
CACHE_DIR = os.path.join(DATA_DIR, "cache")


def _under(child: str, parent: str) -> bool:
    try:
        c = os.path.abspath(child).lower().rstrip("\\/")
        p = os.path.abspath(parent).lower().rstrip("\\/")
        return c == p or c.startswith(p + os.sep)
    except Exception:
        return False


def fix_stale_config_paths() -> dict:
    """纠正 config.json 里残留的、指向**旧数据目录**的绝对路径。

    背景：数据目录从 %LOCALAPPDATA%\\ub_store 改到程序同目录后，直接搬过来的
    config.json 里的 `cache_dir` 仍指向旧路径 —— 会导致「已下载」列表读不到，
    并且以后的下载又被写回 C 盘。这里只纠正**确实指向旧目录或已不存在**的那种。
    """
    try:
        if not os.path.exists(CONFIG_PATH):
            return {}
        with open(CONFIG_PATH, encoding="utf-8") as f:
            disk = json.load(f)
        if not isinstance(disk, dict):
            return {}
    except Exception:
        return {}

    changed = {}
    cd = disk.get("cache_dir")
    if cd and not _under(cd, DATA_DIR):
        legacy = any(_under(cd, r) for r in legacy_data_roots())
        if legacy or not os.path.isdir(cd):
            disk["cache_dir"] = CACHE_DIR
            changed["cache_dir"] = {"from": cd, "to": CACHE_DIR}
    if changed:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(disk, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return changed


def migrate_legacy_data(verbose=False) -> list:
    """把旧数据目录里的关键文件搬到当前 DATA_DIR（只补缺失项，不覆盖）。
       返回迁移说明列表。"""
    moved = []
    mine = os.path.abspath(DATA_DIR)
    for src in legacy_data_roots():
        src = os.path.abspath(src)
        if not os.path.isdir(src) or src == mine:
            continue
        for name in ("config.json", "credentials.json", "installed.json"):
            s = os.path.join(src, name)
            d = os.path.join(mine, name)
            if os.path.exists(s) and not os.path.exists(d):
                try:
                    os.makedirs(mine, exist_ok=True)
                    shutil.copy2(s, d)
                    moved.append("%s  <-  %s" % (d, s))
                except Exception:
                    pass
        sc = os.path.join(src, "cache")
        dc = os.path.join(mine, "cache")
        if os.path.isdir(sc) and not os.path.isdir(dc):
            try:
                shutil.copytree(sc, dc)
                moved.append("%s  <-  %s" % (dc, sc))
            except Exception:
                pass
    # 搬完顺手把 config.json 里的旧路径纠正掉（含「目标目录已有 config」的情形）
    fixed = fix_stale_config_paths()
    for k, v in fixed.items():
        moved.append("纠偏 %s: %s -> %s" % (k, v["from"], v["to"]))
    if verbose:
        for m in moved:
            print("迁移: " + m)
    return moved

# 登录诊断日志：每次登录尝试追加一行；单文件超过 1MB 轮转为 .1。
# 冻结模式（dist\ub_store.exe）下 DATA_DIR = %LOCALAPPDATA%\ub_store，
# 因此该文件即 %LOCALAPPDATA%\ub_store\login_debug.log。
LOG_NAME = "login_debug.log"
LOG_MAX_BYTES = 1024 * 1024


def login_log_path() -> str:
    """诊断日志路径（跟随当前 DATA_DIR，便于测试与冻结模式定位）。"""
    return os.path.join(DATA_DIR, LOG_NAME)

# 接口 code 释义（来源：Malody_Store_API README_CN + 社区前端 i18n + 实测）
CODES = {
    0: "成功",
    -1: "参数错误或校验失败",
    -2: "目标不存在 / 该谱面无可下载文件（下载）；尝试次数过多或用户名已存在（注册）",
    -3: "未登录或账号密码错误（下载/需鉴权接口表示无权限；登录表示账号或密码错误）",
    -4: "账号被封禁",
    -5: "设备或 IP 被封禁 / 邮箱不合法",
    -6: "密码格式不合法（需要 32 位 MD5）",
    -7: "设备被封禁",
    -1000: "鉴权失败：uid/凭证缺失或无效",
}


def code_text(code) -> str:
    try:
        c = int(code)
    except Exception:
        return str(code)
    return "%s(code=%d)" % (CODES.get(c, "未知返回码"), c)


# 登录接口专用文案。来源：Malody 社区前端 i18n（community_index.js 的
# auth.error.* 中文串）+ 实测。注意 CODES[-3] 是**通用**语义（下载等鉴权接口也
# 用它表示“无权限”），而登录场景下 -3 明确是「账号或密码错误」，故单列。
LOGIN_CODES = {
    -1: "参数错误或校验失败",
    -2: "请求被拒绝：尝试次数过多或用户名已存在",
    -3: "账号或密码错误",
    -4: "账号被封禁",
    -5: "设备或 IP 被封禁 / 邮箱不合法",
    -6: "密码格式不合法（需要 32 位 MD5）",
    -7: "设备被封禁",
}


def login_code_text(code) -> str:
    try:
        c = int(code)
    except Exception:
        return str(code)
    return LOGIN_CODES.get(c, "登录失败(code=%s)" % c)


class StoreError(Exception):
    """带接口 code 的业务异常。"""

    def __init__(self, code, message="", detail=None):
        self.code = code
        self.message = message or code_text(code)
        self.detail = detail
        super().__init__("[%s] %s" % (code, self.message))


class BrowseSession:
    """「商店 + 公开」双来源**增量**浏览会话（打开界面只发 5 个请求，翻页时再拉下一批）。

    每次 `next_batch()` 只拉一小批：
      * 商店：**1 页**（20 条，游标翻页，记住 `next` 游标）
      * 公开：**每个 status 1 页**（各 30 条，`page` 递增）
    合并 + 按「标题+艺人」去重后返回**新增**条目；两来源都取尽时 has_more=False。

    为什么要增量：公开接口 30 条/页 + 限速 ~1 req/s，一次拉全库要几十秒到几分钟；
    而且 `total` 字段是假的（无法预知到底有多少），所以按需翻页最合适。
    """

    def __init__(self, client, mode=MODE_PAD, pad_only=False, statuses=(0, 1, 2, 3),
                 include_store=True, include_anon=True, uid=None, key=None):
        self.c = client
        self.mode = mode
        self.pad_only = pad_only
        self.statuses = tuple(statuses)
        self.include_store = include_store
        self.include_anon = include_anon
        self.uid, self.key = uid, key
        self._store_cursor = 0
        self._store_more = bool(include_store)
        self._anon_page = {st: 0 for st in self.statuses}
        self._anon_more = bool(include_anon)
        self._seen = set()
        self.batches = 0
        self.scanned = 0
        self.parts = {"store": 0, "anon": 0}

    @staticmethod
    def _key_of(it):
        return ("%s|%s" % (it.get("title") or "", it.get("artist") or "")).strip().lower()

    def has_more(self):
        return bool(self._store_more or self._anon_more)

    #: 每次 next_batch() 默认拉几「页」。
    #: 用户要求 2 —— 一次多拉一页，翻页时更少触发"再拉一批"的等待。
    PAGES_PER_BATCH = 2

    def next_batch(self, pages=None):
        """拉下一批，返回 (新增条目列表, 是否还有更多)。

        `pages` 默认取 `PAGES_PER_BATCH`（2），即一次拉两页：
        商店 2 页 + 每个 status 各 2 页。这样连续翻页时基本不用等网络。
        """
        pages = self.PAGES_PER_BATCH if pages is None else max(1, int(pages))
        new = []
        for _ in range(pages):
            if not self.has_more():
                break
            before = len(new)
            new.extend(self._next_one_page())
            # 这一页一条新的都没拿到，且已到底 → 再拉也是白费
            if len(new) == before and not self.has_more():
                break
        self.batches += 1
        return new, self.has_more()

    def _next_one_page(self):
        """拉一「页」：商店 1 页（游标）+ 公开各 status 1 页。返回新增条目。

        合并后按 `_key_of`（title|artist）去重，只回新增的。
        """
        new = []
        # --- 商店：1 页（游标） ---
        if self.include_store and self._store_more:
            try:
                r = self.c.store_list(word="", mode=self.mode,
                                      from_=self._store_cursor,
                                      uid=self.uid, key=self.key)
            except Exception:                # pragma: no cover
                r = {}
            data = list(r.get("data") or [])
            self.scanned += len(data)
            for it in data:
                if self.pad_only and not is_pad_only(it):
                    continue
                it = dict(it)
                it["__src"] = "store"
                k = self._key_of(it)
                if k in self._seen:
                    continue
                self._seen.add(k)
                new.append(it)
                self.parts["store"] += 1
            nxt = r.get("next")
            self._store_more = bool(r.get("hasMore") and data and nxt
                                    and nxt != self._store_cursor)
            if nxt:
                self._store_cursor = nxt
        # --- 公开：每个 status 各 1 页 ---
        if self.include_anon:
            got_any = False
            for st in self.statuses:
                pg = self._anon_page[st]
                try:
                    r = self.c.legacy_list(status=st, mode=self.mode,
                                           count=30, page=pg)
                except Exception:            # pragma: no cover
                    continue
                data = list(r.get("list") or [])
                self._anon_page[st] = pg + 1
                self.scanned += len(data)
                if data:
                    got_any = True
                for it in data:
                    it = dict(it)
                    it["cid"] = it.get("id")     # 匿名条目的 id 就是 cid
                    it["__src"] = "anon"
                    k = self._key_of(it)
                    if k in self._seen:
                        continue
                    self._seen.add(k)
                    new.append(it)
                    self.parts["anon"] += 1
            self._anon_more = got_any
        return new


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def default_config() -> dict:
    return {
        "api_base": DEFAULT_API_BASE,
        "legacy_base": DEFAULT_LEGACY_BASE,
        "cover_base": DEFAULT_COVER_BASE,
        "proxy": DEFAULT_PROXY,
        "direct_hosts": list(DEFAULT_DIRECT_HOSTS),
        "timeout": DEFAULT_TIMEOUT,
        "max_retry": DEFAULT_MAX_RETRY,
        "min_interval": DEFAULT_MIN_INTERVAL,
        "cache_dir": CACHE_DIR,
        "game": "",              # 记住游戏目录（自动探测到后写入，之后一键安装不再问路径）
        # 自制曲的版本分类实验开关（留空 = 沿用蓝本曲的 version）。
        # 曲表 <version> 是 16 位十六进制位掩码；官方 base 里 bit15(8000)=beyond the Ave.，
        # 往下 bit14/13… 是历代版本。想给自制曲单独一档时改这里，需实机重启验证。
        "custom_version_mask": "",
        "custom_grouping_category": "",
        # 自制谱 music_id 起始段，默认 **90009000**。
        # 规则（来自 2026-10-10 实机事故，改动务必谨慎）：
        #   官方 id = 世代*1000000 + 段内序号，当前世代 90 用到的序号只到 1035。
        #   ① **必须留在 90 段内**：曾经挪到 99900000（99 段），
        #      结果游戏登录时 W:EXCEPTION: avs-sort.c 刷屏 24070 次后闪退。
        #   ② **段内偏移要够大**：紧跟官方尾部的 90001036 迟早被官方新曲撞号。
        #   90009000 同时满足：段号=90，偏移=9000（离官方 1035 很远）。
        # 非法值会被 ub_newsong.safe_base() 自动纠正，不必担心改错。
        "custom_id_base": 90009000,
        # 自制曲的曲名前缀：装完自动把「小曲绘 / 曲名条」注册进分组图集时，
        # 曲名会渲染成「<前缀> <曲名>」以便一眼认出哪些是自制谱。留空 = 不加前缀。
        "custom_title_marker": "[自制]",
        # 自制曲的「五十音假名读音前缀」（写进 music_info 的 name_string）。
        # 必须是合法 CP932 假名/汉字（ASCII 会被游戏判 Invalid name string）。
        # 作用：游戏内按假名搜「ジセイ」就能筛出自制谱。留空 = 不加前缀。
        "custom_reading_prefix": "ジセイ",
        # 谱库浏览是否**只显示纯 Pad（= jubeat）**歌曲。
        # True  -> 只保留 mode 位掩码恰好为 16 的（滤掉 48/17/249… 这类「多模式」歌）
        # False -> 显示全部含 Pad 的歌曲
        "store_pad_only": True,
        # 浏览「公开谱面（匿名接口）」时每个 status 最多翻几页。
        # 该接口 30 条/页且 total 字段不可信，翻页较慢（~1 req/s）⇒ 用页数控制耗时。
        # 10 页 × 4 个 status ≈ 1200 条上限，约 40 秒。
        "browse_anon_pages": 10,
        # 启动时是否静默检查 GitHub 上的新版本（只在状态栏提示，不弹窗）。
        # 如需完全离线运行，把它设为 false。
        "check_update_on_start": True,
    }


def load_config() -> dict:
    cfg = default_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            disk = json.load(f)
        if isinstance(disk, dict):
            cfg.update({k: v for k, v in disk.items() if v is not None})
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    base = default_config()
    base.update({k: v for k, v in (cfg or {}).items()})
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(base, f, ensure_ascii=False, indent=2)
    return CONFIG_PATH


def cache_dir(cfg=None) -> str:
    d = (cfg or load_config()).get("cache_dir") or CACHE_DIR
    return d


# ---------------------------------------------------------------------------
# 凭据（落 uid/key(登录 token)/storeKey(商店凭证)；绝不存密码）
#
# Malody 登录会返回两个凭证：`token`（社区接口用）与 `tokenStore`（商店接口用）。
# 社区前端把它们归一成 {uid, key, storeKey}（community_index.js 的 Ai 写入 localStorage）：
#   key = token ?? tokenStore，storeKey = tokenStore
# 商店接口（/store/*、/skin/*）的 query `key` 必须填 **storeKey**，否则回 code=-1000。
# ---------------------------------------------------------------------------
def creds_path() -> str:
    return CRED_PATH


def save_credentials(uid, key, name=None, store_key=None) -> str:
    """保存凭据。key=登录 token；store_key=商店凭证(登录响应的 tokenStore)。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    rec = {"uid": uid, "key": key, "storeKey": store_key, "name": name,
           "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(CRED_PATH, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(CRED_PATH, 0o600)
    except Exception:
        pass
    return CRED_PATH


def load_credentials():
    try:
        with open(CRED_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("uid") is not None and d.get("key"):
            return d
    except Exception:
        pass
    return None


def store_key_of(creds) -> str:
    """从凭据里取商店凭证（兼容旧字段名 tokenStore）。无则返回空串。"""
    if not isinstance(creds, dict):
        return ""
    return creds.get("storeKey") or creds.get("tokenStore") or ""


def has_store_credential() -> bool:
    """本机凭据是否含商店凭证（旧版凭据只有 uid/key，需重新登录补全）。"""
    return bool(store_key_of(load_credentials()))


def clear_credentials():
    for p in (CRED_PATH,):
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    return not os.path.exists(CRED_PATH)


def is_logged_in() -> bool:
    return load_credentials() is not None


# ---------------------------------------------------------------------------
# 杂项
# ---------------------------------------------------------------------------
def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_entry_matches(blob: bytes, expect: str) -> bool:
    """blob 若是 zip 且其中某文件条目 md5 == expect，返回 True。

    用于 .mc：服务端把 .mc 压成 zip 存储，声明的 hash 是**解压后** .mc 的 md5。
    """
    if not expect or blob[:4] != b"PK\x03\x04":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                if hashlib.md5(z.read(info)).hexdigest() == expect:
                    return True
    except Exception:                                    # noqa: BLE001
        return False
    return False


# ---------------------------------------------------------------------------
# 登录诊断日志（可诊断、不泄密）
# ---------------------------------------------------------------------------
def _scrub(text, secrets) -> str:
    """把敏感值从文本里整段擦除（先长后短，避免子串残留）。"""
    s = "" if text is None else str(text)
    for sec in sorted((x for x in secrets if x), key=len, reverse=True):
        s = s.replace(sec, "[REDACTED]")
    return s


def log_login_attempt(record: dict, secrets=()) -> str:
    """追加一条登录诊断到 login_debug.log（追加 + 单文件 >1MB 轮转）。

    record 只放**非敏感摘要**（URL/长度/状态码/原始响应体）；写入前再对
    secrets（明文密码、psw 的 md5，以及响应回显的 token/tokenStore/key/storeKey）
    做全文擦除 —— 即使服务端把值回显在响应里，也绝不落盘。任何失败都静默忽略，
    绝不影响登录本身。
    """
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        line = _scrub(json.dumps(record, ensure_ascii=False), secrets)
        path = login_log_path()
        try:
            if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except Exception:                                # noqa: BLE001
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
        return path
    except Exception:                                    # noqa: BLE001
        return ""


def _cred_secrets(res, *extra):
    """收集需要在日志里擦除的敏感值：明文密码/md5 + 响应回显的凭证值。"""
    secrets = [x for x in extra if x]
    if isinstance(res, dict):
        for f in ("token", "tokenStore", "storeKey", "key", "psw"):
            v = res.get(f)
            if isinstance(v, str) and v:
                secrets.append(v)
    return tuple(secrets)


def has_mode(mask, mode: int = MODE_PAD) -> bool:
    """歌曲 mode 字段是位掩码：判断是否含某模式。"""
    try:
        return bool(int(mask) & (1 << mode))
    except Exception:
        return False


def is_pad_only(item) -> bool:
    """歌曲是否**只**支持 Pad（= jubeat）—— 即 mode 位掩码恰好等于 MASK_PAD(16)。

    实测（2026-10-09）：商店 `/store/list` 的 `mode` 是位掩码，
    135 首里 54 首为纯 `16`，其余是「多模式」歌（48=PAD|TAIKA、17=PAD|KEY、
    249、80、57、113、528、25、761、24…）。只保留 `==16` 即为「只要 ub 纯 Pad 谱面」。
    """
    try:
        return int(item.get("mode") or 0) == MASK_PAD
    except Exception:
        return False


def mode_names(mask) -> str:
    names = {0: "Key", 1: "Key", 2: "Slide", 3: "Catch", 4: "Pad", 5: "Taiko",
             6: "Ring", 7: "Cube", 8: "Live", 9: "Solo"}
    try:
        m = int(mask)
    except Exception:
        return str(mask)
    out = [v for k, v in sorted(names.items()) if m & (1 << k)]
    return "/".join(out) if out else str(mask)


def cover_url(sid, cover_field=None, cfg=None) -> str:
    """把 cover 字段（可能是绝对/相对/空）规整成可用 URL。"""
    cfg = cfg or load_config()
    base = (cfg.get("cover_base") or DEFAULT_COVER_BASE).rstrip("/")
    if cover_field:
        cf = str(cover_field)
        if cf.startswith("http://") or cf.startswith("https://"):
            return cf
        if cf.startswith("/"):
            return base + cf
    if sid:
        return "%s/cover/%s!small" % (base, sid)
    return ""


def local_filter(items, keyword):
    """本地关键词过滤：标题 / 艺人 / 作者(username|creator)。空关键词返回原列表。"""
    if not keyword:
        return list(items)
    kw = keyword.strip().lower()
    if not kw:
        return list(items)
    out = []
    for it in items:
        hay = " ".join(str(it.get(k, "")) for k in
                       ("title", "artist", "username", "creator", "version"))
        if kw in hay.lower():
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------
class Client:
    def __init__(self, config=None, quiet=True):
        self.cfg = config or load_config()
        self.quiet = quiet
        self._last_req = 0.0
        self._lock = threading.Lock()
        self._cookies = CookieJar()
        self._log = []
        self._last_resp = {}           # 最近一次 HTTP 响应（url/status/text），供诊断

    # ---- 内部 ----
    def _should_bypass_proxy(self, host: str) -> bool:
        for h in (self.cfg.get("direct_hosts") or []):
            if host == h or host.endswith("." + h):
                return True
        return False

    def _opener(self, url: str):
        host = urllib.parse.urlsplit(url).hostname or ""
        proxy = self.cfg.get("proxy")
        handlers = [urllib.request.HTTPCookieProcessor(self._cookies)]
        if proxy and not self._should_bypass_proxy(host):
            handlers.append(urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(*handlers)

    def _throttle(self):
        iv = float(self.cfg.get("min_interval") or 0)
        if iv <= 0:
            return
        with self._lock:
            dt = time.time() - self._last_req
            if dt < iv:
                time.sleep(iv - dt)
            self._last_req = time.time()

    def request(self, url, method="GET", body=None, headers=None,
                retries=None, raw=False):
        """发请求。返回: raw=True -> (bytes, headers) 否则解析后的 JSON(dict)。"""
        retries = self.cfg.get("max_retry", DEFAULT_MAX_RETRY) if retries is None else retries
        timeout = self.cfg.get("timeout", DEFAULT_TIMEOUT)
        data = None
        hdrs = {
            "User-Agent": "ub_store/1.0 (+jubeat custom chart installer)",
            "Accept": "*/*",
            "Malody": DEFAULT_CLIENT_VERSION,
        }
        if headers:
            hdrs.update(headers)
        if body is not None:
            ctype = hdrs.get("Content-Type") or hdrs.get("content-type") or ""
            if "x-www-form-urlencoded" in ctype.lower():
                # 与社区前端一致：POST 体是 URL 编码表单（不是 JSON）
                data = urllib.parse.urlencode(body).encode("utf-8")
            else:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json; charset=utf-8")

        last_exc = None
        for attempt in range(max(1, int(retries))):
            self._throttle()
            try:
                req = urllib.request.Request(url, data=data, headers=hdrs,
                                             method=method)
                opener = self._opener(url)
                with opener.open(req, timeout=timeout) as resp:
                    blob = resp.read()
                    if raw:
                        return blob, dict(resp.headers)
                    text = blob.decode("utf-8", "replace")
                    self._last_resp = {"url": url,
                                       "status": getattr(resp, "status", None),
                                       "text": text}
                    try:
                        return json.loads(text)
                    except Exception as e:
                        raise StoreError(-1000, "返回不是合法 JSON", text[:300]) from e
            except urllib.error.HTTPError as e:
                body_text = ""
                try:
                    body_text = e.read().decode("utf-8", "replace")
                except Exception:                        # noqa: BLE001
                    pass
                self._last_resp = {"url": url, "status": e.code, "text": body_text}
                last_exc = StoreError(-1000, "HTTP %s" % e.code, url)
            except urllib.error.URLError as e:
                last_exc = StoreError(-1000, "网络不可达: %s" % e.reason, url)
            except StoreError:
                raise
            except Exception as e:                       # noqa: BLE001
                last_exc = StoreError(-1000, "请求异常: %s" % e, url)
            if attempt + 1 < max(1, int(retries)):
                time.sleep(min(2.0 * (attempt + 1), 5.0))
        raise last_exc or StoreError(-1000, "请求失败", url)

    def _url(self, path, params=None):
        base = (self.cfg.get("api_base") or DEFAULT_API_BASE).rstrip("/")
        url = base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _api(self, path, params=None, method="GET", body=None, headers=None):
        return self.request(self._url(path, params), method=method, body=body,
                            headers=headers)

    @staticmethod
    def _ok(res):
        """校验 code==0；否则抛 StoreError（带中文释义）。"""
        if not isinstance(res, dict):
            raise StoreError(-1000, "返回结构异常", str(res)[:200])
        code = res.get("code", 0)
        if code != 0:
            raise StoreError(code)
        return res

    # ---- 公开只读 ----
    def legacy_list(self, status=0, mode=MODE_PAD, count=30, page=0):
        """匿名公开谱面列表（无需 token）。返回 {total, list:[...]}。"""
        base = (self.cfg.get("legacy_base") or DEFAULT_LEGACY_BASE).rstrip("/")
        url = base + "/page/chart/filter?" + urllib.parse.urlencode(
            {"status": status, "mode": mode, "count": count, "page": page})
        res = self.request(url)
        self._ok(res)
        data = res.get("data") or {}
        return {"total": int(data.get("total") or 0),
                "list": list(data.get("list") or [])}

    def guest_token(self):
        """游客 token（可拉列表/详情，**不能下载**）。"""
        res = self._api("/web/auth/guest/wt")
        self._ok(res)
        return {"uid": res.get("uid"), "key": res.get("key")}

    def legacy_list_all(self, status=0, mode=MODE_PAD, max_pages=None,
                        max_items=None, progress=None):
        """把匿名公开列表按 `page` 递增**取全**。

        该接口的坑（实测）：
          * **忽略 `count`**，恒 30 条/页；
          * `total` 与实际返回**不一致**（可能出现「累计条数 > total」），
            因此不能用 total 当循环终止条件，只能「某页没有新条目就停」；
          * 页间可能重叠 ⇒ 按 `id`(=cid) 去重。
        """
        max_pages = 60 if max_pages is None else max_pages
        max_items = 2000 if max_items is None else max_items
        out, seen, pages = [], set(), 0
        while pages < max_pages and len(out) < max_items:
            r = self.legacy_list(status=status, mode=mode, count=30, page=pages)
            data = list(r.get("list") or [])
            fresh = 0
            for it in data:
                k = it.get("id")
                if k in seen:
                    continue
                seen.add(k)
                out.append(it)
                fresh += 1
            pages += 1
            if progress:
                try:
                    progress(pages, len(out))
                except Exception:        # pragma: no cover
                    pass
            if not data or fresh == 0:
                break
        return {"total": len(out), "data": out, "pages": pages, "raw_total": None}

    def login(self, name, psw, ver=1):
        """登录。psw 为**明文**，此处立即 md5；不打印、不落盘。

        返回 {uid, key, token, storeKey}：
          * key      = `token`（社区接口用；与前端 Ai 的 key 一致）
          * storeKey = `tokenStore`（**商店接口** /store/* 用）

        请求格式以 Malody 社区前端为准（community_index.js）：
          POST /account/login/wt  body=name&psw=<32位小写md5>&ver=1
          Content-Type: application/x-www-form-urlencoded（**非** JSON）
        成功判据：uid && (token || tokenStore)。
        """
        if not name or not psw:
            raise StoreError(-1, "账号或密码为空")
        psw_md5 = md5_hex(psw)                       # 小写 32 位（与前端一致）
        url = self._url("/account/login/wt")         # 登录接口无 query
        body = {"name": name, "psw": psw_md5, "ver": ver}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        rec = {"event": "login", "url": url.split("?")[0],
               "name_len": len(name), "psw_len": len(psw_md5), "ver": ver}

        try:
            res = self._api("/account/login/wt", method="POST", body=body,
                            headers=headers)
        except StoreError as ex:
            last = self._last_resp or {}
            rec.update({"http": last.get("status"), "resp": last.get("text") or "",
                        "code": None, "error": str(ex)[:200]})
            log_login_attempt(rec, secrets=(psw, psw_md5))
            raise

        last = self._last_resp or {}
        code = res.get("code", 0) if isinstance(res, dict) else None
        try:                                             # 容忍 "0" 这类字符串 code
            code = int(code)
        except (TypeError, ValueError):
            pass
        rec.update({"http": last.get("status"), "resp": last.get("text") or "",
                    "code": code})
        # 响应里若回显了凭证（token/tokenStore/key），一并擦除后再落盘
        secrets = _cred_secrets(res, psw, psw_md5)
        log_login_attempt(rec, secrets=secrets)

        if not isinstance(res, dict):
            raise StoreError(-1000, "登录返回结构异常", str(res)[:200])
        if code != 0:
            raw = _scrub(json.dumps(res, ensure_ascii=False)[:500], (psw, psw_md5))
            raise StoreError(code, login_code_text(code),
                             "服务端原始响应：%s" % raw)

        uid = res.get("uid")
        key = res.get("token") or res.get("tokenStore") or res.get("key")
        if not uid or not key:
            fields = ",".join(sorted(res.keys()))
            raw = _scrub(json.dumps(res, ensure_ascii=False)[:500], (psw, psw_md5))
            # 用 -1000（协议/鉴权异常）而非 -3，避免再出现「-3 与登录成功并列」
            # 的自相矛盾提示。
            raise StoreError(
                -1000,
                "登录成功但未返回凭据（响应缺少 uid/凭证字段；实际字段名：%s）"
                % (fields or "（空对象）"),
                "服务端原始响应：%s" % raw)
        return {"uid": uid, "key": key, "token": res.get("token"),
                "storeKey": res.get("storeKey") or res.get("tokenStore")}

    # ---- 鉴权解析（商店接口用 storeKey；社区接口用 token）----
    def _store_auth(self, uid=None, key=None):
        """返回 (uid, key) 供 **商店接口** 使用：key 必须是商店凭证 storeKey。

        显式传入 key 时按其值（供测试/高级用法）；否则从本机凭据解析，
        缺 storeKey（多为旧版凭据）时给出可操作提示，而非等服务器回 -1000。
        """
        if key is not None:
            if uid is None:
                uid = (load_credentials() or {}).get("uid")
            return uid, key
        c = load_credentials()
        if not c:
            raise StoreError(-1000, "未登录：请先在【登录】页登录后再下载")
        sk = store_key_of(c)
        if not sk:
            raise StoreError(
                -1000,
                "商店凭证缺失（本机凭据只有登录 token）：请重新登录一次以获取商店凭证")
        return c.get("uid"), sk

    def _community_auth(self, uid=None, key=None):
        """返回 (uid, key) 供 **社区接口** 使用：key 是登录 token。"""
        if key is not None:
            if uid is None:
                uid = (load_credentials() or {}).get("uid")
            return uid, key
        c = load_credentials()
        if not c:
            raise StoreError(-1000, "未登录：请先在【登录】页登录后再使用该功能")
        return c.get("uid"), c.get("key")

    def store_list(self, word="", mode=MODE_PAD, from_=0, uid=None, key=None):
        """歌曲列表（登录后支持 word 搜索，按标题 LIKE）。商店接口 -> 用 storeKey。"""
        uid, key = self._store_auth(uid, key)
        res = self._api("/store/list", {"word": word or "", "mode": mode,
                                        "from": from_, "uid": uid, "key": key,
                                        "api": 1})
        self._ok(res)
        return res

    def store_charts(self, sid, mode=MODE_PAD, from_=0, uid=None, key=None):
        """歌曲谱面列表。商店接口 -> 用 storeKey。"""
        uid, key = self._store_auth(uid, key)
        res = self._api("/store/charts", {"sid": sid, "mode": mode,
                                          "from": from_, "uid": uid, "key": key,
                                          "api": 1})
        self._ok(res)
        return res

    # ---- 游标翻页取全（否则列表只显示第一页，看起来「不全」）----
    STORE_PAGE_LIMIT = 200      # 安全上限：最多翻多少页
    STORE_ITEM_LIMIT = 5000     # 安全上限：最多取多少条

    def store_list_all(self, word="", mode=MODE_PAD, uid=None, key=None,
                       max_pages=None, max_items=None, progress=None,
                       pad_only=False):
        """把 /store/list 的**全库**取完（游标翻页）。

        关键实测事实：
          * `/store/list` **每页只回 20 条**（不是一次性全给）；
          * 翻页靠响应里的 `hasMore` + `next`，而 **`next` 是游标（时间戳），不是偏移量**；
            必须下一轮把 `from=next` 传回去；传 `from=<页码*20>` 会直接得到 0 条。
        ⇒ 只调一次 store_list 就只能拿到第一页 20 条 —— 这就是「谱面列表获取不全」的根因。

        pad_only=True 时只保留**纯 Pad**（`mode == MASK_PAD`，即 16）的歌曲 —— 即「只要 ub 谱面」，
        会把「多模式」歌（48/17/249/… 含 Pad 但不止 Pad）一并滤掉。

        progress(pages, got) 每翻一页回调一次（got = 过滤后保留的条数）。
        返回 {total, data, pages, scanned}；total 即实际保留下来的条数。
        """
        uid, key = self._store_auth(uid, key)
        max_pages = self.STORE_PAGE_LIMIT if max_pages is None else max_pages
        max_items = self.STORE_ITEM_LIMIT if max_items is None else max_items
        out, seen, from_, pages, scanned = [], set(), 0, 0, 0
        while pages < max_pages and len(out) < max_items:
            res = self.store_list(word=word, mode=mode, from_=from_,
                                  uid=uid, key=key)
            data = list(res.get("data") or [])
            fresh = 0
            for it in data:
                scanned += 1
                if pad_only and not is_pad_only(it):
                    continue
                k = it.get("sid") or it.get("id")
                if k in seen:            # 游标页偶有重叠，去重兜底
                    continue
                seen.add(k)
                out.append(it)
                fresh += 1
            pages += 1
            if progress:
                try:
                    progress(pages, len(out))
                except Exception:        # pragma: no cover
                    pass
            nxt = res.get("next")
            if (not res.get("hasMore") or not data or not nxt
                    or nxt == from_):
                break
            from_ = nxt
        return {"total": len(out), "data": out, "pages": pages,
                "scanned": scanned}

    # ---- 双来源合并浏览（商店 + 公开）----
    # 实测（2026-10-09）：
    #   * 商店 `/store/list` 的 Pad 目录只有 **135 首**（hasMore=False 确实到底）；
    #   * 匿名 `/page/chart/filter` 才是大头：30 条/页、**`total` 字段是假的**（说 53 却远不止，
    #     翻 12 页仍有新条目），且 4 个 status 是**不同子集**（status=1 与其它零重叠）；
    #   * 两个来源**几乎不重叠**（匿名 30 条里只有 1 条在商店 135 里）。
    #   ⇒ 只看商店会以为「jubeat 自制谱只有 135 首」，实际公开库里多得多。
    # 匿名源较慢（30 条/页 + 限速 ~1 req/s），故用 anon_pages 限定每个 status 最多翻几页。
    def browse_all(self, mode=MODE_PAD, pad_only=False, anon_pages=10,
                   statuses=(0, 1, 2, 3), include_store=True, include_anon=True,
                   progress=None, uid=None, key=None):
        """合并「商店谱面」与「公开谱面（匿名）」两个来源，返回去重后的统一列表。

        进度回调 progress(tag, pages, got)：tag ∈ {'store', 'anon:0'…}。
        uid/key 显式传入时用于商店接口（否则取本地凭据；凭据缺失则跳过商店来源）。
        返回 {total, data, parts:{'store':n,'anon':n}, scanned, pages}
        """
        collected, scanned, pages = [], 0, 0
        parts = {"store": 0, "anon": 0}

        def _say(tag):
            def cb(pg, got):
                if progress:
                    try:
                        progress(tag, pg, got)
                    except Exception:        # pragma: no cover
                        pass
            return cb

        if include_store and has_store_credential():
            try:
                r = self.store_list_all(mode=mode, pad_only=pad_only,
                                        progress=_say("store"), uid=uid, key=key)
                for it in r["data"]:
                    it = dict(it)
                    it["__src"] = "store"
                    collected.append(it)
                parts["store"] = len(r["data"])
                scanned += r.get("scanned") or 0
                pages += r.get("pages") or 0
            except Exception:                # pragma: no cover
                pass

        if include_anon:
            seen_anon = set()
            for st in statuses:
                try:
                    r = self.legacy_list_all(status=st, mode=mode,
                                             max_pages=anon_pages,
                                             max_items=anon_pages * 30,
                                             progress=_say("anon:%d" % st))
                except Exception:            # pragma: no cover
                    continue
                fresh = 0
                for it in r["data"]:
                    k = it.get("id")
                    if k in seen_anon:
                        continue
                    seen_anon.add(k)
                    it = dict(it)
                    it["cid"] = it.get("id")      # 匿名条目的 id 就是 cid
                    it["__src"] = "anon"
                    collected.append(it)
                    fresh += 1
                parts["anon"] += fresh
                scanned += r.get("total") or 0
                pages += r.get("pages") or 0

        # 跨来源按「标题+艺人」去重，同曲优先保留商店条目
        uniq, idx = [], {}
        for it in sorted(collected, key=lambda x: 0 if x.get("__src") == "store" else 1):
            key = ("%s|%s" % (it.get("title") or "", it.get("artist") or "")).strip().lower()
            if key in idx:
                continue
            idx[key] = len(uniq)
            uniq.append(it)
        return {"total": len(uniq), "data": uniq, "parts": parts,
                "scanned": scanned, "pages": pages}

    def chart_info(self, cid, uid=None, key=None):
        """谱面详情（社区接口 -> 用登录 token）。"""
        uid, key = self._community_auth(uid, key)
        res = self._api("/community/chart/info", {"cid": cid, "uid": uid,
                                                  "key": key, "api": 1})
        self._ok(res)
        return res

    # ---- 歌曲 -> 谱面 cid（下载前必须做这一步）----
    # 商店 `/store/list` 只给 `sid`（歌曲），**下载要的是 `cid`（谱面）**；
    # 旧代码直接读条目里的 `id`，而商店条目根本没有 `id` 字段（只有匿名列表才有）
    # ⇒ 拿到 None ⇒ 点下载必然报错。必须先 `/store/charts` 取谱面列表再挑一个。
    CHART_PREFER = ("EXT", "EXTREME", "ADV", "ADVANCED", "ADVANCE",
                    "BSC", "BASIC", "HOLD")

    @classmethod
    def _diff_rank(cls, version, prefer=None) -> int:
        """把 `version`（如 'EXT Lv.16' / 'EXTREME Lv.23' / 'BSC LV.5'）映射成偏好序。

        首词命中 prefer 越靠前越发优先；未识别的一律排最后（并保持原顺序稳定）。
        """
        prefer = prefer or cls.CHART_PREFER
        head = str(version or "").strip().split()[0].upper() if str(version or "").strip() else ""
        for i, k in enumerate(prefer):
            if head == k:
                return i
        return len(prefer)

    def pick_cid(self, sid, uid=None, key=None, prefer=None, chart=None):
        """给商店歌曲挑一个可下载的谱面 cid。

        prefer 缺省按 CHART_PREFER（EXT → ADV → BSC → HOLD）；
        chart 传具体 cid 时直接用那个。
        返回 {"cid", "chart", "charts", "picked_rank"}；无谱面则抛 StoreError(-2)。
        """
        charts = list(self.store_charts(sid, mode=MODE_PAD, uid=uid, key=key)
                      .get("data") or [])
        if not charts:
            raise StoreError(-2, "该歌曲在商店里没有可下载的谱面（sid=%s）" % sid,
                             {"sid": sid})
        if chart is not None:
            for c in charts:
                if str(c.get("cid")) == str(chart):
                    return {"cid": c.get("cid"), "chart": c, "charts": charts,
                            "picked_rank": None}
            raise StoreError(-2, "指定的谱面 cid=%s 不属于该歌曲" % chart, {"sid": sid})
        ordered = sorted(charts, key=lambda c: (self._diff_rank(c.get("version"), prefer),
                                                -(c.get("level") or 0)))
        best = ordered[0]
        return {"cid": best.get("cid"), "chart": best, "charts": charts,
                "picked_rank": self._diff_rank(best.get("version"), prefer)}

    # ---- 下载 ----
    def download_meta(self, cid, uid=None, key=None):
        """取下载清单（不落盘）。商店接口 -> 用 storeKey；未登录/无商店凭证本地即拦。"""
        uid, key = self._store_auth(uid, key)
        res = self._api("/store/download", {"cid": cid, "uid": uid, "key": key,
                                            "api": 1})
        code = res.get("code", 0)
        if code != 0:
            raise StoreError(code)
        items = res.get("items") or []
        if not items:
            raise StoreError(-2, "该谱面没有可下载文件")
        return {"items": items, "sid": res.get("sid"), "cid": res.get("cid")}

    def download_chart(self, cid, dest_dir=None, progress=None, uid=None, key=None):
        """下载某谱面的全部文件到缓存目录，逐个校验 md5(hash)。

        注意：Malody Store 的 **.mc 是压缩成 zip 存储的**（README「谱面上传/综述」：
        mc 会被压成 zip 再上传），因此下载项声明的 hash 是**解压后 .mc** 的 md5，
        与「下载到的 zip 字节」的 md5 不同。故校验时先比对原始字节，再回退比对
        zip 内条目（仅 .mc 这类情形）。落盘仍是原始字节（zip），安装管线可直接解析。

        progress: callable(done, total, name) 可选。
        返回 {"dir", "files":[{name,path,hash,md5,ok,size}], "sid", "cid"}。
        """
        meta = self.download_meta(cid, uid=uid, key=key)
        dest_dir = os.path.abspath(dest_dir or os.path.join(
            cache_dir(self.cfg), str(cid)))
        os.makedirs(dest_dir, exist_ok=True)
        if not os.access(dest_dir, os.W_OK):
            raise StoreError(-1, "缓存目录不可写：%s" % dest_dir)
        items = meta["items"]
        files = []
        for i, it in enumerate(items):
            name = os.path.basename(str(it.get("name") or "chart_%d.bin" % i))
            url = it.get("file")
            expect = (it.get("hash") or "").lower()
            if not url:
                raise StoreError(-1, "下载项缺少 file 地址：%s" % name)
            path = os.path.join(dest_dir, name)
            if progress:
                progress(i, len(items), name)
            blob, _hdrs = self.request(url, raw=True)
            got = hashlib.md5(blob).hexdigest()
            ok = (got == expect) if expect else True
            if not ok and _zip_entry_matches(blob, expect):
                ok = True                    # 服务端把 .mc 压成了 zip
            if not ok:
                # 重下一次（限一次），仍不符即报错
                blob2, _ = self.request(url, raw=True)
                got2 = hashlib.md5(blob2).hexdigest()
                if got2 == expect or _zip_entry_matches(blob2, expect):
                    blob, got, ok = blob2, got2, True
            if not ok:
                raise StoreError(-1, "文件 md5 校验失败：%s（期望 %s，实际 %s）"
                                 % (name, expect, got))
            with open(path, "wb") as f:
                f.write(blob)
            files.append({"name": name, "path": path, "hash": expect,
                          "md5": got, "ok": ok, "size": len(blob)})
        if progress:
            progress(len(items), len(items), "完成")
        return {"dir": dest_dir, "files": files,
                "sid": meta.get("sid"), "cid": meta.get("cid")}

    def fetch_cover(self, url, dest_path):
        blob, hdrs = self.request(url, raw=True)
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(blob)
        return {"path": dest_path, "bytes": len(blob),
                "content_type": (hdrs or {}).get("Content-Type", "")}


# ---------------------------------------------------------------------------
# 已装清单
# ---------------------------------------------------------------------------
def _load_manifest() -> dict:
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("items", []), list):
            return d
    except Exception:
        pass
    return {"items": []}


def _write_manifest(m: dict) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    return MANIFEST_PATH


def manifest_path() -> str:
    return MANIFEST_PATH


def add_installed(record: dict) -> dict:
    m = _load_manifest()
    rec = dict(record)
    rec.setdefault("installed_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    m["items"] = [x for x in m["items"]
                  if int(x.get("music_id", -1)) != int(rec.get("music_id", -2))]
    m["items"].append(rec)
    m["items"].sort(key=lambda x: int(x.get("music_id", 0)))
    _write_manifest(m)
    return rec


def remove_installed(music_id) -> bool:
    m = _load_manifest()
    before = len(m["items"])
    m["items"] = [x for x in m["items"]
                  if int(x.get("music_id", -1)) != int(music_id)]
    _write_manifest(m)
    return len(m["items"]) != before


def list_installed(game=None) -> list:
    """合并 manifest 与 ub_newsong 的 state_*.json（后者更权威，含备份信息）。"""
    out = {}
    for it in _load_manifest()["items"]:
        try:
            out[int(it["music_id"])] = dict(it)
        except Exception:
            pass
    if game:
        try:
            import ub_newsong as N
            dirs = []
            for d in (newsong_workdir(), N.default_workdir()):
                if d not in dirs:
                    dirs.append(d)
            for wd in dirs:
                if not os.path.isdir(wd):
                    continue
                for fn in os.listdir(wd):
                    mm = re.match(r"state_(\d+)\.json$", fn)
                    if not mm:
                        continue
                    mid = int(mm.group(1))
                    try:
                        st = json.load(open(os.path.join(wd, fn), encoding="utf-8"))
                    except Exception:
                        continue
                    base = out.get(mid, {})
                    out[mid] = {
                        "music_id": mid,
                        "title": (st.get("plan") or {}).get("title") or base.get("title"),
                        "mcz": (st.get("plan") or {}).get("mcz") or base.get("mcz"),
                        "game": st.get("game") or base.get("game"),
                        "installed_at": st.get("installed_at") or base.get("installed_at"),
                        "backup_dir": os.path.join(st.get("workdir", ""), "backup",
                                                   st.get("stamp", "")),
                        "restore_cmd": ("python tools/ub_newsong.py uninstall "
                                        "--music-id %d --game \"%s\"" % (mid, st.get("game", ""))),
                        "state_file": os.path.join(wd, fn),
                    }
        except Exception:
            pass
    return [out[k] for k in sorted(out)]


# ---------------------------------------------------------------------------
# 安装编排（复用 ub_newsong，不修改）
# ---------------------------------------------------------------------------
def _newsong():
    import ub_newsong as N
    return N


def newsong_workdir() -> str:
    """安装状态/备份的落盘目录。冻结模式下用持久目录（源码模式沿用 ub_newsong 默认）。"""
    if getattr(sys, "frozen", False):
        return os.path.join(DATA_DIR, "newsong")
    try:
        return _newsong().default_workdir()
    except Exception:
        return os.path.join(DATA_DIR, "newsong")


# ---------------------------------------------------------------------------
# 卡片视图：已下载清单 / 一键安装 / 一键卸载
# ---------------------------------------------------------------------------
CARD_COVER_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def _pick_mc(folder: str):
    """在下载目录里挑出谱面文件（.mc / .mcz；商店下载的 .mc 实际是 zip）。"""
    if not os.path.isdir(folder):
        return None
    cands = [f for f in os.listdir(folder)
             if os.path.splitext(f)[1].lower() in (".mc", ".mcz")]
    if not cands:
        return None
    cands.sort(key=lambda f: os.path.getsize(os.path.join(folder, f)), reverse=True)
    return os.path.join(folder, cands[0])


def _pick_cover(folder: str):
    if not os.path.isdir(folder):
        return None
    cands = [f for f in os.listdir(folder)
             if os.path.splitext(f)[1].lower() in CARD_COVER_EXTS]
    if not cands:
        return None
    cands.sort(key=lambda f: os.path.getsize(os.path.join(folder, f)), reverse=True)
    return os.path.join(folder, cands[0])


def chart_card(folder: str) -> dict:
    """读一个下载目录，产出卡片视图需要的字段（不联网）。"""
    folder = os.path.abspath(folder)
    cid = os.path.basename(folder)
    card = {"cid": cid if cid.isdigit() else None, "dir": folder,
            "mc": None, "audio": None, "cover": _pick_cover(folder),
            "title": os.path.basename(folder), "artist": "", "version": "",
            "charts": [], "notes": 0, "size": 0, "error": None,
            "preview_ms": None, "installed_music_id": None}
    try:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if os.path.isfile(p):
                card["size"] += os.path.getsize(p)
    except Exception:
        pass
    mc = _pick_mc(folder)
    card["mc"] = mc
    if mc:
        try:
            N = _newsong()
            charts = N.UC.parse_malody(mc)
            if charts:
                ch = charts[0]
                card["title"] = ch.title or card["title"]
                card["artist"] = ch.artist or ""
                card["version"] = ch.version or ""
                card["charts"] = [{"version": c.version or "",
                                   "notes": len(c.notes)} for c in charts]
                card["notes"] = len(ch.notes)
                card["preview_ms"] = ch.preview_ms
                if getattr(N, "UA", None) is not None:
                    got = N.UA.load_chart_audio(mc, title=ch.title,
                                                hint=ch.audio_hint, meta_only=True)
                    card["audio"] = got[0] if got else None
        except Exception as ex:
            card["error"] = str(ex)
    # 是否已经装进游戏（按标题匹配已装清单）
    try:
        for rec in list_installed():
            if (rec.get("title") or "").strip().lower() == (card["title"] or "").strip().lower():
                card["installed_music_id"] = rec.get("music_id")
                break
    except Exception:
        pass
    return card


def downloaded_dirs() -> list:
    """缓存目录下所有『下载产物』目录（含 .mc 的一级子目录）。"""
    root = cache_dir()
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if not os.path.isdir(p) or name == "covers":
            continue
        if _pick_mc(p):
            out.append(p)
    return out


def list_downloaded() -> list:
    return [chart_card(d) for d in downloaded_dirs()]


def delete_downloaded(folder: str, force: bool = False) -> dict:
    """删除**本地下载目录**（清缓存，不碰游戏）。

    与 `uninstall_music` 的区别：
      * **卸载** = 从游戏里移除（回滚曲表/包表/文件）
      * **删除** = 只清掉 `<数据目录>/cache/<cid>/` 这份下载产物

    🔴 安全约束（防止误删）：
      1. 目标必须是**真实存在的目录**
      2. 必须位于 `cache_dir()` **之下**（用 realpath 比较，防 `..` 穿越）
      3. **不能是 `cache_dir()` 本身**，也不能是它的父级
    已装进游戏的谱面默认**拒绝**删除（`force=True` 才允许，且返回提示，
    因为游戏里还会留着那条曲子）。

    返回 {folder, freed, installed_music_id, warns}
    """
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise StoreError(-1, "目录不存在：%s" % folder)

    root = os.path.realpath(cache_dir())
    target = os.path.realpath(folder)
    if target == root:
        raise StoreError(-1, "拒绝删除缓存根目录本身")
    if os.path.commonpath([root, target]) != root:
        raise StoreError(-1, "拒绝删除缓存目录之外的路径：%s" % folder)
    if target.startswith(root + os.sep) is False and target != root:
        raise StoreError(-1, "目标不在缓存目录内：%s" % folder)

    # 该目录是否已装进游戏？已装则先要求卸载（避免游戏里留孤儿条目）
    card = chart_card(folder)
    mid = card.get("installed_music_id")
    if mid and not force:
        raise StoreError(-1, (
            "《%s》已经装进游戏（music_id=%s）。\n"
            "请先【卸载】再删除，否则游戏里会留下一条无效曲目。"
            % (card.get("title") or os.path.basename(folder), mid)))

    # 统计大小
    freed = 0
    for dirpath, _dirnames, filenames in os.walk(folder):
        for f in filenames:
            try:
                freed += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass

    shutil.rmtree(folder)
    warns = []
    if mid:
        warns.append("游戏里仍保留 music_id=%s 的条目（本次是强制删除）" % mid)
    return {"folder": folder, "freed": freed,
            "installed_music_id": mid, "warns": warns}


def default_reading(title: str) -> str:
    """曲表 `name_string` 的默认值 = **跟着真实曲名**（用户要求：得看得出自己玩的是什么）。

    背景：`name_string` 只是**五十音排序用的读音**，不是显示名（显示名是预渲染贴图）。
    早前为了"让自制曲在排序里聚堆"而统一成 ジサクフ —— 已按要求**作废**。
    纯 ASCII 标题会让游戏打一条 `W:music_db: Invalid name string` 警告，但不影响使用；
    若要消除该警告，可在 config.json 里给 `custom_reading` 指定假名读音。
    """
    t = (title or "").strip()
    return t or "CUSTOM"


def set_game_dir(path: str) -> str:
    """记住游戏目录（下次一键安装不再问）。"""
    path = os.path.abspath(path)
    cfg = load_config()
    cfg["game"] = path
    save_config(cfg)
    return path


def one_click_install(folder: str, game=None, music_id=None, own_audio=True,
                      level=None, reading=None, register_small=True,
                      version_mask=None, grouping_category=None,
                      id_base=None, progress=None) -> dict:
    """把某个已下载目录一键装成游戏新曲。

    自动完成：探测游戏目录 / 选空闲 music_id / 用自带音源 / 用下载的封面当曲绘
              / 补小曲绘与曲名条 / 登记曲表与包表。
    version_mask / grouping_category 留空时读 config.json 里的同名配置
    （便于实机试「自制谱」独立版本档，不用改代码）。
    返回 {"plan": <计划>, "result": <安装结果>}。
    """
    N = _newsong()
    card = chart_card(folder)
    if not card.get("mc"):
        raise StoreError(-1, "该下载目录里没有谱面文件：%s" % folder)
    game = game or auto_detect_game()
    if not game:
        raise StoreError(-1, "找不到游戏目录（contents）。请先手动指定一次。")
    if not _looks_like_game(game):
        raise StoreError(-1, "游戏目录不对（缺少 spice.exe / data_mods）：%s" % game)

    cfg = load_config()
    if version_mask is None:
        version_mask = (cfg.get("custom_version_mask") or "").strip() or None
    if grouping_category is None:
        grouping_category = (cfg.get("custom_grouping_category") or "").strip() or None

    if music_id is None:
        # 自制谱 id 段：优先用调用方指定 → 再 config.json 的 custom_id_base
        # → 最后 N.DEFAULT_BASE。默认 90009000（90 段内、偏移够大）。
        # 非法起点会被 recommend_ids() 内的 safe_base() 自动纠正。
        base = (id_base
                or int(cfg.get("custom_id_base") or 0)
                or N.DEFAULT_BASE)
        music_id = N.recommend_ids(game, base, 1)[0]
    else:
        # 手动指定时也要校验：跨段会让游戏闪退，贴官方尾部会撞号
        prob = N.id_problem(music_id)
        if prob:
            raise StoreError(-1, "指定的 music_id 不可用：%s" % prob)
    if not reading:
        reading = default_reading(card.get("title"))

    if progress:
        progress(0, "生成安装计划…")
    plan = N.make_plan(game, card["mc"], jacket=card.get("cover"),
                       music_id=music_id, reading=reading, level=level,
                       own_audio=bool(own_audio),
                       version_mask=version_mask,
                       grouping_category=grouping_category,
                       reading_prefix=(cfg.get("custom_reading_prefix") or "").strip() or None)
    if progress:
        progress(1, "安装中（编码音源/贴图）…")
    res = run_install(plan, apply=True, workdir=newsong_workdir(),
                      register_small=register_small,
                      artist=(card.get("artist") or None))
    set_game_dir(game)
    return {"plan": plan, "result": res, "card": card}


def uninstall_music(music_id, game=None, workdir=None) -> dict:
    game = game or auto_detect_game()
    if not game:
        raise StoreError(-1, "找不到游戏目录（contents）")
    return run_uninstall(game, music_id, workdir=workdir)


def _looks_like_game(p: str) -> bool:
    try:
        return (os.path.isfile(os.path.join(p, "spice.exe"))
                and os.path.isdir(os.path.join(p, "data_mods")))
    except Exception:
        return False


def auto_detect_game() -> str:
    """自动找游戏目录（contents 根）。顺序：
       1) config.json 里记住的 game
       2) 程序目录 / 数据目录 的上层目录（便携部署时 contents 就在旁边）
       3) 常见盘符下的常见位置
       4) 浅扫 D..H 盘一级目录（找 *\\contents\\spice.exe），限时避免卡界面
    """
    cfg = load_config()
    saved = (cfg or {}).get("game")
    if saved and _looks_like_game(saved):
        return saved

    here = _exe_dir() or os.path.dirname(_HERE)
    cands = []
    base = here
    for _ in range(4):
        cands.append(os.path.join(base, "contents"))
        cands.append(base)
        base = os.path.dirname(base)
    cands += [r"<GAME_ROOT>", r"<GAME_ROOT>",
              r"<GAME_ROOT>", r"<ARCADE_ROOT>",
              r"D:\jubeat\contents", r"F:\ub\contents", r"C:\JUBEAT\contents"]
    for p in cands:
        if p and _looks_like_game(p):
            return os.path.abspath(p)

    # 浅扫（只到二级，跳过系统目录）
    import string
    skip = {"$recycle.bin", "system volume information", "windows", "program files",
            "program files (x86)", "programdata", "users", "recovery", "perflogs"}
    for d in string.ascii_uppercase[3:11]:          # D..K
        root = "%s:\\" % d
        if not os.path.isdir(root):
            continue
        try:
            for a in os.listdir(root):
                pa = os.path.join(root, a)
                if not os.path.isdir(pa) or a.lower() in skip:
                    continue
                if _looks_like_game(pa):
                    return os.path.abspath(pa)
                try:
                    for b in os.listdir(pa):
                        pb = os.path.join(pa, b)
                        if os.path.isdir(pb) and _looks_like_game(pb):
                            return os.path.abspath(pb)
                except Exception:
                    pass
        except Exception:
            pass
    return ""


def game_defaults():
    """兼容旧调用：返回自动探测到的游戏目录（找不到返回空串）。"""
    return auto_detect_game()


def recommend_music_ids(game, base=None, count=1):
    N = _newsong()
    return N.recommend_ids(game, base or N.DEFAULT_BASE, count)


def build_install_plan(game, mcz, audio_from=None, title=None, reading=None,
                       jacket=None, diff=0, slot=None, level=None, music_id=None,
                       hold_policy="split", no_tex=False):
    """生成安装计划（不改动游戏目录）。"""
    N = _newsong()
    return N.make_plan(game, mcz, parent=None, audio_from=audio_from, title=title,
                       reading=reading, jacket=jacket, diff=diff, slot=slot,
                       level=level, music_id=music_id, hold_policy=hold_policy,
                       no_tex=no_tex)


def run_install(plan, apply=True, workdir=None, register_small=True, artist=None):
    """执行安装。返回 ub_newsong.install 的结果字典。

    register_small=True 时，装完顺带把 **小曲绘 / 曲名条**
    （BNR_ID / IDX_ID / IDX_MINI_ID）注册进「已被游戏加载」的分组图集 ——
    否则新曲虽然在曲表里，但选曲格的曲名文字与小曲绘会缺失。
    曲名会按 config 的 `custom_title_marker`（默认 '[自制]'）加上**前缀**。
    该步骤失败不影响主安装（记录在 res['group_tex']['error']）。"""
    N = _newsong()
    wd = os.path.abspath(workdir or newsong_workdir())
    res = N.install(plan, apply=apply, workdir=wd)
    if apply and register_small and not res.get("dry_run"):
        try:
            marker = (load_config().get("custom_title_marker") or "").strip()
            res["group_tex"] = N.register_group_tex(
                plan["game"], plan["music_id"], plan.get("title") or "",
                jacket=None, artist=artist, marker=marker,
                apply=True, workdir=wd)
        except Exception as ex:                                 # pragma: no cover
            res["group_tex"] = {"error": str(ex)}
    return res


def run_uninstall(game, music_id, workdir=None):
    N = _newsong()
    wd = os.path.abspath(workdir or newsong_workdir())
    state = os.path.join(wd, "state_%d.json" % music_id)
    if not os.path.exists(state):
        # 回退：早期用 CLI 装过的曲，状态在 ub_newsong 默认工作目录
        alt = os.path.abspath(N.default_workdir())
        if alt != wd and os.path.exists(os.path.join(alt, "state_%d.json" % music_id)):
            wd = alt
    res = N.uninstall(game, music_id, workdir=wd)
    remove_installed(music_id)
    return res


def verify_game(game):
    N = _newsong()
    return N.verify(game, as_json=False)


# ---------------------------------------------------------------------------
# 孤儿条目（曲表里有、文件却没了）—— 通常由"只删本地文件没卸载"造成
# ---------------------------------------------------------------------------
def find_orphans(game) -> list:
    N = _newsong()
    return N.find_orphans(game)


def cleanup_orphans(game, music_ids=None) -> dict:
    """清理孤儿曲表条目。music_ids 为 None 时先自己扫一遍。

    返回 {removed: [(id, 备份路径)], failed: [(id, 原因)], orphans: [...]}
    """
    N = _newsong()
    if music_ids is None:
        music_ids = [o["music_id"] for o in N.find_orphans(game)]
    removed, failed = [], []
    for mid in music_ids:
        try:
            info = N.cleanup_orphan(game, mid, backup=True)
            removed.append((mid, info.get("backup")))
        except Exception as ex:                              # noqa: BLE001
            failed.append((mid, str(ex)))
    return {"removed": removed, "failed": failed,
            "orphans": N.find_orphans(game)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_json(o):
    print(json.dumps(o, ensure_ascii=False, indent=1))


def cmd_config(args):
    cfg = load_config()
    if args.set:
        for kv in args.set:
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            if k in ("timeout", "max_retry"):
                v = int(v)
            elif k == "min_interval":
                v = float(v)
            elif k == "direct_hosts":
                v = [x for x in v.split(",") if x]
            cfg[k] = v
        print("已写入配置：%s" % save_config(cfg))
    _print_json({"config": load_config(), "config_path": CONFIG_PATH,
                 "creds_path": creds_path(), "cache_dir": cache_dir(cfg),
                 "login_log": login_log_path(),
                 "logged_in": is_logged_in()})


def cmd_list(args):
    c = Client()
    r = c.legacy_list(status=args.status, mode=args.mode, count=args.count,
                      page=args.page)
    items = local_filter(r["list"], args.filter)
    if args.json:
        _print_json({"total": r["total"], "shown": len(items), "list": items})
        return 0
    print("匿名公开谱面（mode=%d） total=%d 本页=%d 过滤后=%d"
          % (args.mode, r["total"], len(r["list"]), len(items)))
    for it in items:
        print("  cid=%-7s %-32s | %-28s | %-14s | %s"
              % (it.get("id"), (it.get("title") or "")[:32],
                 (it.get("artist") or "")[:28], (it.get("version") or ""),
                 it.get("username") or ""))
    return 0


def cmd_charts(args):
    c = Client()
    r = c.store_charts(args.sid, mode=args.mode, from_=args.page)
    _print_json(r)
    return 0


def cmd_info(args):
    _print_json(Client().chart_info(args.cid))
    return 0


def cmd_login(args):
    c = Client()
    psw = getpass.getpass("Malody 密码（不显示、不落盘）：")
    r = c.login(args.name, psw)
    psw = None
    save_credentials(r["uid"], r["key"], name=args.name,
                     store_key=r.get("storeKey"))
    print("登录成功：uid=%s，凭据已保存到 %s" % (r["uid"], creds_path()))
    return 0


def cmd_logout(args):
    print("已清除凭据：%s" % (creds_path() if clear_credentials() else "（无）"))
    return 0


def cmd_download(args):
    c = Client()

    def prog(i, n, name):
        print("  [%d/%d] %s" % (i + 1, n, name))

    r = c.download_chart(args.cid, dest_dir=args.dest, progress=prog)
    print("下载完成 -> %s" % r["dir"])
    for f in r["files"]:
        print("  + %s  %s  %d B" % (f["name"], "md5-OK" if f["ok"] else "md5-FAIL",
                                    f["size"]))
    return 0


def cmd_installed(args):
    _print_json({"installed": list_installed(args.game),
                 "manifest": manifest_path()})
    return 0


def cmd_install(args):
    plan = build_install_plan(
        args.game, args.mcz, audio_from=args.audio_from, title=args.title,
        reading=args.reading, jacket=args.jacket, diff=args.diff, slot=args.slot,
        level=args.level, music_id=args.music_id, hold_policy=args.hold_policy,
        no_tex=args.no_tex)
    print("计划 music_id=%d title=%s" % (plan["music_id"], plan["title"]))
    res = run_install(plan, apply=args.apply)
    if res.get("dry_run"):
        print("DRY RUN，加 --apply 才写盘")
        return 0
    add_installed({"music_id": plan["music_id"], "title": plan["title"],
                   "mcz": plan["mcz"], "game": plan["game"]})
    print("安装完成 stamp=%s" % res["stamp"])
    print("备份目录：%s" % os.path.join(res.get("workdir", ""), "backup",
                                        res["stamp"]))
    print("还原命令：%s" % res["restore_cmd"])
    return 0


def cmd_uninstall(args):
    res = run_uninstall(args.game, args.music_id)
    _print_json(res)
    return 0


def cmd_cover(args):
    c = Client()
    url = cover_url(args.sid, args.url)
    r = c.fetch_cover(url, args.out)
    print("封面 -> %s (%d B)" % (r["path"], r["bytes"]))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="ub_store.py",
                                description="Malody 谱库客户端 / 下载 / 安装编排")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("config", help="查看/修改配置")
    a.add_argument("--set", nargs="*", help="k=v，如 --set proxy=http://127.0.0.1:10808")
    a.set_defaults(func=cmd_config)

    b = sub.add_parser("list", help="匿名公开谱面列表（本地过滤）")
    b.add_argument("--status", type=int, default=0)
    b.add_argument("--mode", type=int, default=MODE_PAD)
    b.add_argument("--count", type=int, default=30)
    b.add_argument("--page", type=int, default=0)
    b.add_argument("--filter", default="")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_list)

    c = sub.add_parser("charts", help="歌曲的谱面列表")
    c.add_argument("--sid", required=True)
    c.add_argument("--mode", type=int, default=MODE_PAD)
    c.add_argument("--page", type=int, default=0)
    c.set_defaults(func=cmd_charts)

    d = sub.add_parser("info", help="谱面详情")
    d.add_argument("--cid", required=True)
    d.set_defaults(func=cmd_info)

    e = sub.add_parser("login", help="登录（密码用交互输入，不落盘）")
    e.add_argument("--name", required=True)
    e.set_defaults(func=cmd_login)

    f = sub.add_parser("logout", help="清除本地凭据")
    f.set_defaults(func=cmd_logout)

    g = sub.add_parser("download", help="下载谱面到缓存目录")
    g.add_argument("--cid", required=True)
    g.add_argument("--dest", default=None)
    g.set_defaults(func=cmd_download)

    h = sub.add_parser("installed", help="列出已装自制曲")
    h.add_argument("--game", default=None)
    h.set_defaults(func=cmd_installed)

    i = sub.add_parser("install", help="生成计划并安装（复用 ub_newsong）")
    i.add_argument("--mcz", required=True)
    i.add_argument("--game", required=True)
    i.add_argument("--audio-from", type=int, default=None)
    i.add_argument("--title", default=None)
    i.add_argument("--reading", default=None)
    i.add_argument("--jacket", default=None)
    i.add_argument("--diff", type=int, default=0)
    i.add_argument("--slot", default=None, choices=["bsc", "adv", "ext"])
    i.add_argument("--level", type=float, default=None)
    i.add_argument("--music-id", type=int, default=None)
    i.add_argument("--hold-policy", default="split", choices=["split", "straighten"])
    i.add_argument("--no-tex", action="store_true")
    i.add_argument("--apply", action="store_true")
    i.set_defaults(func=cmd_install)

    j = sub.add_parser("uninstall", help="卸载并还原")
    j.add_argument("--music-id", type=int, required=True)
    j.add_argument("--game", required=True)
    j.set_defaults(func=cmd_uninstall)

    k = sub.add_parser("cover", help="下载封面")
    k.add_argument("--sid", default=None)
    k.add_argument("--url", default=None)
    k.add_argument("--out", required=True)
    k.set_defaults(func=cmd_cover)
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        migrate_legacy_data()
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except StoreError as ex:
        print("ERROR: %s" % ex, file=sys.stderr)
        return 2
    except Exception as ex:                                # noqa: BLE001
        print("ERROR: %s" % ex, file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
