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
