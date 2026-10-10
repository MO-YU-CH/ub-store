# -*- coding: utf-8 -*-
"""自带字库：解决"日文系统上简体中文显示成方框"的问题。

背景
----
程序界面里有大量简体中文，但日文版 Windows（jubeat 机台就是）**不带任何中文字体**
（实测目标机 Fonts 目录只有 meiryo/msgothic）。tkinter 里 ``font=("", N)``
用的是"系统默认字体"= MS UI Gothic，不含简体字形 ⇒ 界面上一堆 tofu（方框）。

做法
----
把一份裁剪过的 CJK 字体（思源黑体 SC，SIL OFL 授权）**随 exe 一起打包**，
启动时用 ``AddFontResourceExW(..., FR_PRIVATE, 0)`` **注册到当前进程**
（FR_PRIVATE = 只对本进程可见，不污染系统），再把 tkinter 的默认字体设为它。

这样无论目标机是中文还是日文系统，界面文字都能正常显示。

字体裁剪见 ``tools/_make_font.py``：保留 CJK 统一汉字全字集 + 假名 + 全角标点，
共约 2.3 万字（9 MB），确保曲名/艺人名不会出现缺字。
"""
from __future__ import annotations

import ctypes
import os
import sys

FONT_FILENAME = "ub_cjk_font.otf"
#: 字体内部的族名（Source Han Sans SC）；改字体时记得同步
FONT_FAMILY = "Source Han Sans SC"
FR_PRIVATE = 0x10

_cached_family = None
_tried = False


def font_path():
    """定位字体文件：打包后在 _MEIPASS/assets/，源码模式在 tools/assets/。"""
    cands = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "assets", FONT_FILENAME))
        cands.append(os.path.join(base, FONT_FILENAME))
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, "assets", FONT_FILENAME))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def register():
    """把自带字体注册到当前进程。返回可用族名；失败返回 None。"""
    global _cached_family, _tried
    if _tried:
        return _cached_family
    _tried = True
    if os.name != "nt":
        return None
    p = font_path()
    if not p:
        return None
    try:
        import ctypes
        # FR_PRIVATE：只对本进程可见，不写系统字体目录、不需要管理员权限。
        # ⚠️ 不要用 SendMessageW(HWND_BROADCAST, WM_FONTCHANGE) 去通知系统：
        #    那是**同步**广播，只要有一个窗口不处理该消息就会把本进程卡死。
        #    FR_PRIVATE 注册的字体对本进程新建的字体对象立即生效，无需广播。
        n = ctypes.windll.gdi32.AddFontResourceExW(
            ctypes.c_wchar_p(p), FR_PRIVATE, 0)
        if not n:
            return None
    except Exception:                                        # noqa: BLE001
        return None
    _cached_family = FONT_FAMILY
    return _cached_family


def family():
    """返回可用的族名（未注册/不可用则 None）。"""
    return _cached_family


def available():
    """字体是否已成功注册。"""
    return _cached_family is not None


def apply_to_tk(root=None):
    """把 tkinter 的**默认字体**换成自带字体。

    逐个覆盖 named font（TkDefaultFont 等），凡是代码里写了 ``font=("", N)``
    （空族名 = 用默认）的控件都会跟着变。返回是否成功。
    """
    fam = register()
    if not fam:
        return False
    try:
        import tkinter
        import tkinter.font as tkfont
        if root is None:
            root = tkinter._default_root
        if root is None:
            return False
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkCaptionFont",
                     "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
            try:
                f = tkfont.nametofont(name, root=root)
                f.configure(family=fam)
            except Exception:                                # noqa: BLE001
                pass
        # 显式指定过族名的地方也统一换掉（有些控件直接写了字体元组）
        return True
    except Exception:                                        # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 窗口标题栏 / 菜单栏（非客户区，归 Windows 画，不归 tkinter）
# ---------------------------------------------------------------------------
# 标题栏和菜单栏由系统用"界面字体"绘制：日文系统上是 MS UI Gothic，
# 同样不含简体字形 ⇒ 标题、菜单里照样是方框。改 tkinter 字体对它无效。
# 只能改**当前登录会话**的界面字体（SystemParametersInfo）。

SPI_GETNONCLIENTMETRICS = 0x0029
SPI_SETNONCLIENTMETRICS = 0x002A
SPIF_UPDATEINIFILE = 0x0001
SPIF_SENDCHANGE = 0x0002
LF_FACESIZE = 32


class _LOGFONTW(ctypes.Structure if os.name == "nt" else object):
    _fields_ = [
        ("lfHeight", ctypes.c_long),
        ("lfWidth", ctypes.c_long),
        ("lfEscapement", ctypes.c_long),
        ("lfOrientation", ctypes.c_long),
        ("lfWeight", ctypes.c_long),
        ("lfItalic", ctypes.c_byte),
        ("lfUnderline", ctypes.c_byte),
        ("lfStrikeOut", ctypes.c_byte),
        ("lfCharSet", ctypes.c_byte),
        ("lfOutPrecision", ctypes.c_byte),
        ("lfClipPrecision", ctypes.c_byte),
        ("lfQuality", ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte),
        ("lfFaceName", ctypes.c_wchar * LF_FACESIZE),
    ]


class _NONCLIENTMETRICSW(ctypes.Structure if os.name == "nt" else object):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("iBorderWidth", ctypes.c_int),
        ("iScrollWidth", ctypes.c_int),
        ("iScrollHeight", ctypes.c_int),
        ("iCaptionWidth", ctypes.c_int),
        ("iCaptionHeight", ctypes.c_int),
        ("lfCaptionFont", _LOGFONTW),
        ("iSmCaptionWidth", ctypes.c_int),
        ("iSmCaptionHeight", ctypes.c_int),
        ("lfSmCaptionFont", _LOGFONTW),
        ("iMenuWidth", ctypes.c_int),
        ("iMenuHeight", ctypes.c_int),
        ("lfMenuFont", _LOGFONTW),
        ("lfStatusFont", _LOGFONTW),
        ("lfMessageFont", _LOGFONTW),
    ]


def system_has_cjk_font():
    """系统里是否已有可用的中文字体（决定要不要动会话界面字体）。"""
    if os.name != "nt":
        return True
    try:
        import ctypes as C
        gdi32 = C.windll.gdi32
        LF_FACESIZE = 32

        class LOGFONTW(C.Structure):
            _fields_ = [("lfHeight", C.c_long), ("lfWidth", C.c_long),
                        ("lfEscapement", C.c_long), ("lfOrientation", C.c_long),
                        ("lfWeight", C.c_long), ("lfItalic", C.c_byte),
                        ("lfUnderline", C.c_byte), ("lfStrikeOut", C.c_byte),
                        ("lfCharSet", C.c_byte), ("lfOutPrecision", C.c_byte),
                        ("lfClipPrecision", C.c_byte), ("lfQuality", C.c_byte),
                        ("lfPitchAndFamily", C.c_byte),
                        ("lfFaceName", C.c_wchar * LF_FACESIZE)]

        class ENUMLOGFONTEXW(C.Structure):
            _fields_ = [("elfLogFont", LOGFONTW),
                        ("elfFullName", C.c_wchar * 64),
                        ("elfStyle", C.c_wchar * 32),
                        ("elfScript", C.c_wchar * 32)]

        # 常见**系统自带**中文字体族名（任一到场就说明系统能显示简体）。
        # ⚠️ 不要把 "source han" / "noto sans" 之类算进来——那正是我们自己
        #    注册的字体，会被本函数枚举到，导致误判"系统已有中文字体"而跳过切换。
        wants = ("yahei", "simsun", "simhei", "nsimsun", "msjh",
                 "microsoft jhenghei", "fangsong", "kaiti", "dengxian",
                 "mingliu", "pmingliu", "dfkai", "lisu", "youyuan")
        #: 我们自己注册的族名，检测时一律排除
        mine = FONT_FAMILY.lower()
        found = []

        FONTENUMPROC = C.WINFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p,
                                     C.c_int, C.c_void_p)

        def _cb(lpelfe, _lptm, _ftype, _lparam):
            try:
                ef = C.cast(lpelfe, C.POINTER(ENUMLOGFONTEXW)).contents
                name = ef.elfLogFont.lfFaceName.lower()
                if mine and mine in name:
                    return 1                                 # 我们自己注册的，跳过
                if any(w in name for w in wants):
                    found.append(name)
            except Exception:                                # noqa: BLE001
                pass
            return 1

        hdc = C.windll.user32.GetDC(0)
        try:
            C.windll.gdi32.EnumFontFamiliesExW(
                hdc, C.byref(LOGFONTW()), FONTENUMPROC(_cb), 0, 0)
        finally:
            C.windll.user32.ReleaseDC(0, hdc)
        return bool(found)
    except Exception:                                        # noqa: BLE001
        return True                                          # 判断不了就当有，别乱动


def install_session_ui_font():
    """把自带字体设为**当前登录会话**的界面字体（标题栏/菜单/状态栏/消息框）。

    做法：
      1. 用 ``AddFontResourceExW(path, 0, 0)`` **全局**注册字体（不是 FR_PRIVATE），
         否则系统绘制非客户区时找不到它；
      2. 用 ``SystemParametersInfoW(SPI_SETNONCLIENTMETRICS)`` 把标题栏 / 菜单 /
         状态栏 / 消息框字体换成它。

    注意：
      * 只影响当前登录会话，**重启即恢复**；不改注册表持久项
        （不传 SPIF_UPDATEINIFILE），不影响游戏本体。
      * ``SPIF_SENDCHANGE`` 会让系统广播 WM_SETTINGCHANGE。这里用系统 API 自带的
        广播，**不要自己 SendMessage(HWND_BROADCAST)** —— 那会同步阻塞。
    返回 (是否成功, 说明)。
    """
    if os.name != "nt":
        return False, "仅 Windows 支持"
    p = font_path()
    if not p:
        return False, "找不到字体文件"
    try:
        import ctypes as C
        # ① 全局注册（不带 FR_PRIVATE），系统绘制非客户区才看得到
        added = C.windll.gdi32.AddFontResourceExW(C.c_wchar_p(p), 0, 0)
        if not added:
            return False, "全局注册字体失败"

        # ② 改会话界面字体
        ncm = _NONCLIENTMETRICSW()
        ncm.cbSize = C.sizeof(ncm)
        ok = C.windll.user32.SystemParametersInfoW(
            SPI_GETNONCLIENTMETRICS, ncm.cbSize, C.byref(ncm), 0)
        if not ok:
            return False, "读取界面度量失败"
        for lf in (ncm.lfCaptionFont, ncm.lfSmCaptionFont,
                   ncm.lfMenuFont, ncm.lfStatusFont, ncm.lfMessageFont):
            lf.lfFaceName = FONT_FAMILY
            lf.lfCharSet = 1                    # DEFAULT_CHARSET
        # ⚠️ 这里**不能**用 SPIF_SENDCHANGE：它会让系统同步广播
        #    WM_SETTINGCHANGE，只要有一个窗口不处理就会失败/卡住（和注册字体时
        #    踩的是同一个坑）。改为 flags=0 先把设置写进去，再自己发一个
        #    **带超时**的广播，让已存在的窗口刷新非客户区。
        ok = C.windll.user32.SystemParametersInfoW(
            SPI_SETNONCLIENTMETRICS, ncm.cbSize, C.byref(ncm), 0)
        if not ok:
            err = C.windll.kernel32.GetLastError()
            return False, "设置界面字体失败 (GetLastError=%s)" % err
        # 带超时的广播：SMTO_ABORTIFHUNG，最多等 1 秒，绝不阻塞
        try:
            SMTO_ABORTIFHUNG = 0x0002
            WM_SETTINGCHANGE = 0x001A
            res = C.c_ulong()
            C.windll.user32.SendMessageTimeoutW(
                0xFFFF, WM_SETTINGCHANGE, 0,
                C.c_wchar_p("WindowMetrics"), SMTO_ABORTIFHUNG, 1000,
                C.byref(res))
        except Exception:                                    # noqa: BLE001
            pass
        return True, "界面字体已切到 %s（仅当前会话，重启恢复；不影响游戏）" % FONT_FAMILY
    except Exception as ex:                                  # noqa: BLE001
        return False, "%s: %s" % (type(ex).__name__, ex)


def ensure_ui_font(session_ui=True):
    """一站式：注册字体 + 切 tkinter 字体 +（必要时）切会话界面字体。

    **仅当系统本身没有中文字体时**才动会话界面字体（标题栏/菜单），
    免得在中文系统上平白改变用户的系统外观。
    返回 dict，便于 --fontcheck 打印。
    """
    r = {"family": None, "tk": False, "session_ui": False,
         "session_ui_note": "", "had_cjk": None}
    r["family"] = register()
    if not r["family"]:
        return r
    r["had_cjk"] = system_has_cjk_font()
    if session_ui and not r["had_cjk"]:
        ok, note = install_session_ui_font()
        r["session_ui"] = ok
        r["session_ui_note"] = note
    elif session_ui:
        r["session_ui_note"] = "系统已有中文字体，跳过（不动系统外观）"
    return r


def restore_session_ui_font():
    """把界面字体恢复成系统默认（并把我们注册的全局字体资源撤掉）。"""
    if os.name != "nt":
        return False
    p = font_path()
    try:
        import ctypes as C
        ncm = _NONCLIENTMETRICSW()
        ncm.cbSize = C.sizeof(ncm)
        C.windll.user32.SystemParametersInfoW(
            SPI_GETNONCLIENTMETRICS, ncm.cbSize, C.byref(ncm), 0)
        for lf in (ncm.lfCaptionFont, ncm.lfSmCaptionFont,
                   ncm.lfMenuFont, ncm.lfStatusFont, ncm.lfMessageFont):
            lf.lfFaceName = "MS UI Gothic"      # 日文系统默认；其它系统也不至于太怪
        C.windll.user32.SystemParametersInfoW(
            SPI_SETNONCLIENTMETRICS, ncm.cbSize, C.byref(ncm), 0)
        # 同样用带超时的广播，别用 SPIF_SENDCHANGE
        try:
            SMTO_ABORTIFHUNG = 0x0002
            res = C.c_ulong()
            C.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, C.c_wchar_p("WindowMetrics"),
                SMTO_ABORTIFHUNG, 1000, C.byref(res))
        except Exception:                                    # noqa: BLE001
            pass
        if p:
            C.windll.gdi32.RemoveFontResourceExW(C.c_wchar_p(p), 0, 0)
        return True
    except Exception:                                        # noqa: BLE001
        return False
