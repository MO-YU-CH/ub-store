# -*- coding: utf-8 -*-
"""本地导入：把用户自备的文件接进现有安装链路。

支持两类来源
------------
1. **谱面文件**（``.mc`` / ``.mcz``，可连同同目录的音源与封面）
   → 交给 `ub_store.one_click_install` 安装。
2. **官方更新包**（``.7z`` / ``.zip``）
   → 交给 `ub_updater.do_scan` / `do_apply` 处理。

两个关键点
----------
* **一个谱面一个目录**：`ub_store.one_click_install` 是按"目录"工作、且
  `_pick_mc()` 在同一目录里只会挑最大的那个谱面。所以当用户给的目录里有**多个**
  谱面时，本模块会为每个谱面建**暂存目录**（复制谱面 + 同目录的音源/封面），
  避免互相覆盖；只有一个谱面时直接用原目录，零拷贝。
* **投放目录**（`import_dir()`）：用户可以不做任何选择，直接把文件丢进这个固定
  目录，程序扫描即可。省去"每次都要点开文件对话框"的麻烦。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil

# 谱面 / 音源 / 封面 / 更新包 的扩展名
MC_EXTS = (".mc", ".mcz")
AUDIO_EXTS = (".ogg", ".mp3", ".wav", ".m4a", ".flac", ".aac", ".opus")
COVER_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
PACK_EXTS = (".7z", ".zip")

#: 投放目录名（相对数据目录）
IMPORT_DIR_NAME = "import"
#: 暂存目录名（相对数据目录）
STAGE_DIR_NAME = "import_staged"


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------
def import_dir(data_dir: str, create: bool = True) -> str:
    """用户"投放目录"：把文件丢进去，程序扫描即可。"""
    d = os.path.join(data_dir, IMPORT_DIR_NAME)
    if create:
        try:
            os.makedirs(d, exist_ok=True)
            # 放个说明文件，免得用户不知道该干什么
            readme = os.path.join(d, "把谱面或更新包放这里.txt")
            if not os.path.exists(readme):
                with open(readme, "w", encoding="utf-8") as f:
                    f.write(
                        "把下列文件直接放进这个文件夹，然后在程序里点"
                        "【文件 → 扫描导入目录】：\n\n"
                        "  · 谱面文件：  .mc / .mcz\n"
                        "  · 音源（可选）：.ogg / .mp3 / .wav …\n"
                        "  · 封面（可选）：.jpg / .png …\n"
                        "  · 官方更新包：.7z / .zip\n\n"
                        "如果谱面和音源放在一起，程序会自动配对。\n"
                        "也可以直接把这些文件拖到程序窗口上。\n"
                    )
        except OSError:
            pass
    return d


def stage_root(data_dir: str) -> str:
    d = os.path.join(data_dir, STAGE_DIR_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def find_charts_in_dir(folder: str, recursive: bool = False) -> list:
    """列出目录里的谱面文件（绝对路径）。"""
    out = []
    if not os.path.isdir(folder):
        return out
    if recursive:
        for dp, _dn, fn in os.walk(folder):
            for f in fn:
                if _ext(f) in MC_EXTS:
                    out.append(os.path.join(dp, f))
    else:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if os.path.isfile(p) and _ext(f) in MC_EXTS:
                out.append(p)
    out.sort()
    return out


def find_packs_in_dir(folder: str, recursive: bool = False) -> list:
    """列出目录里的更新包（绝对路径）。"""
    out = []
    if not os.path.isdir(folder):
        return out
    it = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
    for dp, _dn, fn in it:
        for f in fn:
            p = os.path.join(dp, f)
            if os.path.isfile(p) and _ext(f) in PACK_EXTS:
                out.append(p)
    out.sort()
    return out


def classify_paths(paths) -> dict:
    """把"用户给的任意路径列表"分成谱面与更新包（目录会展开一层）。"""
    charts, packs = [], []
    for p in paths or []:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            charts += find_charts_in_dir(p)
            packs += find_packs_in_dir(p)
        elif os.path.isfile(p):
            e = _ext(p)
            if e in MC_EXTS:
                charts.append(p)
            elif e in PACK_EXTS:
                packs.append(p)
        # 其它类型一律忽略（音源/封面由谱面所在目录自动带上）
    return {"charts": sorted(set(charts)), "packs": sorted(set(packs))}


# ---------------------------------------------------------------------------
# 暂存
# ---------------------------------------------------------------------------
def _safe_name(text: str, limit: int = 40) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text or "")
    text = text.strip(" .") or "chart"
    return text[:limit]


def chart_unit_dir(mc_path: str, data_dir: str, force_stage: bool = False) -> str:
    """给一个谱面确定"交给安装器使用的目录"。

    * 同目录里只有这一个谱面，且不需要强制暂存 → **直接用原目录**（零拷贝）。
    * 否则 → 建一个暂存目录，把谱面连同同目录的音源/封面复制进去。
    """
    mc_path = os.path.abspath(mc_path)
    src_dir = os.path.dirname(mc_path)
    siblings = find_charts_in_dir(src_dir)
    if not force_stage and len(siblings) <= 1:
        return src_dir

    base = _safe_name(os.path.splitext(os.path.basename(mc_path))[0])
    tag = hashlib.md5(mc_path.encode("utf-8", "replace")).hexdigest()[:6]
    dst = os.path.join(stage_root(data_dir), "%s_%s" % (base, tag))
    try:
        os.makedirs(dst, exist_ok=True)
    except OSError:
        return src_dir                      # 建不出来就退回原目录

    try:
        shutil.copy2(mc_path, os.path.join(dst, os.path.basename(mc_path)))
    except OSError:
        return src_dir
    # 把同目录的候选音源/封面一并带上（只复制，不动原文件）
    for f in os.listdir(src_dir):
        if _ext(f) not in AUDIO_EXTS + COVER_EXTS:
            continue
        try:
            shutil.copy2(os.path.join(src_dir, f), os.path.join(dst, f))
        except OSError:
            pass
    return dst


def plan_units(chart_paths, data_dir: str) -> list:
    """把谱面列表转成装配单元：[{mc, unit_dir, staged, title}]。"""
    out = []
    for mc in chart_paths:
        sib = find_charts_in_dir(os.path.dirname(mc))
        staged = len(sib) > 1
        unit = chart_unit_dir(mc, data_dir)
        out.append({
            "mc": mc,
            "unit_dir": unit,
            "staged": staged and unit != os.path.dirname(mc),
            "title": os.path.splitext(os.path.basename(mc))[0],
        })
    return out


def cleanup_stage(data_dir: str) -> None:
    """清掉暂存目录（安装完/取消后调用）。"""
    d = os.path.join(data_dir, STAGE_DIR_NAME)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
