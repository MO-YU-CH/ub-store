#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ub_newsong.py -- 把自制 Malody 谱面「注册成游戏里的新曲」（Phase 3）

铁律：**只新增、不覆盖**。除「曲表 / 包表」这两张必须登记的表外，不改动游戏里
任何既有文件；两张表的改动也只允许「新增一行 / 新增一条记录」。

复用（不修改）：
  * tools/ub_chart.py   -> _msc.ifs 容器读写 + Malody 解析 + eve 转换
  * tools/ub_updater.py -> TLKP 包表 _tlkp_rows / _tlkp_build
  * tools/tdxt_encode.py-> PNG -> TDXT 编码 + 单曲图集(PXET)重建（可选，缺省时降级）

五个子命令：
  list-ids  --game <目录> [--base 90001036] [--count 5] [--json]
  plan      --mcz <f> --game <目录> [--parent <id>] [--audio-from <id>]
            [--title <t>] [--reading <r>] [--jacket <png>] [--diff N]
            [--slot ext] [--level N] [--music-id <id>] [--out <plan.json>]
  install   --plan <plan.json> [--apply] [--workdir <dir>]
  uninstall --music-id <id> --game <目录> [--workdir <dir>] [--stamp <s>]
  verify    --game <目录> [--json]
  group-tex --game <目录> --music-id <id> [--title <t>] [--artist <a>]
            [--marker "[自制]"] [--jacket <160x160 png>] [--atlas <name>]
            [--apply] [--workdir <dir>] [--json]
            （补注册小曲绘/曲名条 BNR_ID/IDX_ID/IDX_MINI_ID 到「已加载」的分组图集；
              uninstall 会按侧车记录自动撤销）

关键事实（实测，见 ub_新曲贴图不显示_修复报告.md / ub_曲绘缺失_最终定位与修复.md）：
  * _msc.ifs 分组目录 = 'd' + str(music_id // 10)   （90001035 -> d9000103）
  * 单曲大曲绘图集 = tex_l44_bnr_big_id<id>.bin（PXET，内含条目 BNR_BIG_ID<id>）
  * 对应模型桩     = mdl_l44_bnr_big_id<id>.bin（60B，所有曲完全一致，可直接克隆）
  * 包表行 = (包名 'L44_BNR_BIG_ID<id>', 'tex_l44_bnr_big_id<id>.bin', 'mdl_l44_bnr_big_id<id>.bin')
  * 游戏用 data/d3/package/alldata.bin 把「贴图名 -> 包」解析出来（故默认写基础包表）
  * 曲表契约：块 id 必须 0..N-1 连续；<data_num> 必须 == 最终块数
  * 小曲绘/曲名条不在单曲图集里：它们在**分组图集** tex_l44*_bnr_j_*.bin 内，
    且只有「开机实际加载的那一批」（本机 55 个）里的贴图才找得到 —— 见 group-tex
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ub_chart as UC          # noqa: E402
import ub_updater as UU        # noqa: E402

try:
    import tdxt_encode as T    # noqa: E402
except Exception:              # pragma: no cover
    T = None

try:
    import ub_audio as UA      # noqa: E402
except Exception:              # pragma: no cover
    UA = None

DEFAULT_BASE = 90001036        # 当前 9xxx 自制段的最大占用 90001035 之后
DEFAULT_LEVEL = 1              # 自制谱定数固定 1（用户 2026-10-10 定；Malody meta 无 level）
MUSIC_BLOCK_RE = re.compile(r'<data __type="void" id="\d+">.*?</data>', re.S)
MID_RE = re.compile(r'<music_id __type="s32">(\d+)</music_id>')


# ============================================================================
# 路径
# ============================================================================
def game_paths(game: str) -> dict:
    g = os.path.abspath(game)
    return {
        "game": g,
        "music_table_official": os.path.join(g, "data", "music_info", "music_info.xml"),
        "music_table_overlay": os.path.join(g, "data_mods", "omnimix", "music_info", "music_omni.xml"),
        "pkg_base": os.path.join(g, "data", "d3", "package", "alldata.bin"),
        "pkg_overlay_alldata": os.path.join(g, "data_mods", "omnimix", "d3", "package", "alldata.bin"),
        "pkg_overlay_allomni": os.path.join(g, "data_mods", "omnimix", "d3", "package", "allomni.bin"),
        "ifs_base": os.path.join(g, "data", "ifs_pack"),
        "ifs_overlay": os.path.join(g, "data_mods", "omnimix", "ifs_pack"),
        "model_base": os.path.join(g, "data", "d3", "model"),
        "model_overlay": os.path.join(g, "data_mods", "omnimix", "d3", "model"),
    }


def music_table_path(game: str) -> str:
    p = game_paths(game)
    if os.path.exists(p["music_table_overlay"]):
        return p["music_table_overlay"]
    if os.path.exists(p["music_table_official"]):
        return p["music_table_official"]
    raise FileNotFoundError("找不到曲表（music_omni.xml / music_info.xml）：%s" % game)


def pkg_table_path(game: str, which: str = "base") -> str:
    """which: base | overlay | allomni | auto"""
    p = game_paths(game)
    if which == "base":
        return p["pkg_base"]
    if which == "overlay":
        if os.path.exists(p["pkg_overlay_alldata"]):
            return p["pkg_overlay_alldata"]
        return p["pkg_overlay_allomni"]
    if which == "allomni":
        return p["pkg_overlay_allomni"]
    for cand in (p["pkg_overlay_alldata"], p["pkg_base"], p["pkg_overlay_allomni"]):
        if os.path.exists(cand):
            return cand
    return p["pkg_base"]


def group_dir(music_id: int) -> str:
    return "d%d" % (music_id // 10)


def new_ifs_path(game: str, music_id: int) -> str:
    return os.path.join(game_paths(game)["ifs_overlay"], group_dir(music_id),
                        "%d_msc.ifs" % music_id)


def new_tex_path(game: str, music_id: int) -> str:
    return os.path.join(game_paths(game)["model_overlay"],
                        "tex_l44_bnr_big_id%d.bin" % music_id)


def new_mdl_path(game: str, music_id: int) -> str:
    return os.path.join(game_paths(game)["model_overlay"],
                        "mdl_l44_bnr_big_id%d.bin" % music_id)


def pkg_row_for(music_id: int):
    return ("L44_BNR_BIG_ID%d" % music_id,
            "tex_l44_bnr_big_id%d.bin" % music_id,
            "mdl_l44_bnr_big_id%d.bin" % music_id)


# ============================================================================
# 占用扫描
# ============================================================================
def table_ids(path: str):
    if not os.path.exists(path):
        return set()
    txt = open(path, encoding="utf-8", errors="replace").read()
    return {int(x) for x in MID_RE.findall(txt)}


def _ifs_ids(root: str):
    out = set()
    if not os.path.isdir(root):
        return out
    for d in os.listdir(root):
        sub = os.path.join(root, d)
        if not os.path.isdir(sub):
            continue
        for f in os.listdir(sub):
            m = re.match(r"^(\d+)_msc\.ifs$", f)
            if m:
                out.add(int(m.group(1)))
    return out


def _tex_ids(root: str):
    out = set()
    if not os.path.isdir(root):
        return out
    for f in os.listdir(root):
        m = re.search(r"^tex_l44_bnr_big_id(\d+)\.bin$", f)
        if m:
            out.add(int(m.group(1)))
    return out


def scan_occupied(game: str) -> dict:
    p = game_paths(game)
    src = {
        "music_info.xml": table_ids(p["music_table_official"]),
        "music_omni.xml": table_ids(p["music_table_overlay"]),
        "ifs_pack(data)": _ifs_ids(p["ifs_base"]),
        "ifs_pack(data_mods)": _ifs_ids(p["ifs_overlay"]),
        "tex(data)": _tex_ids(p["model_base"]),
        "tex(data_mods)": _tex_ids(p["model_overlay"]),
    }
    union = set()
    for s in src.values():
        union |= s
    return {"sources": src, "union": union}


def check_id_free(game: str, music_id: int):
    """返回 (是否可用, [占用原因...])"""
    occ = scan_occupied(game)
    reasons = []
    for name, ids in occ["sources"].items():
        if music_id in ids:
            reasons.append("已被 %s 占用" % name)
    mid = music_id
    for path in (new_ifs_path(game, mid), new_tex_path(game, mid), new_mdl_path(game, mid)):
        if os.path.exists(path):
            reasons.append("目标文件已存在：%s" % path)
    return (len(reasons) == 0), reasons


def recommend_ids(game: str, base: int = DEFAULT_BASE, count: int = 5):
    occ = scan_occupied(game)["union"]
    out = []
    i = base
    while len(out) < count and i < base + 100000:
        if i not in occ:
            out.append(i)
        i += 1
    return out


# ============================================================================
# 曲表读写
# ============================================================================
def music_blocks(txt: str):
    return MUSIC_BLOCK_RE.findall(txt)


def music_block_ids(txt: str):
    return [int(x) for x in re.findall(r'<data __type="void" id="(\d+)">', txt)]


def find_block_by_music_id(txt: str, music_id: int):
    for b in music_blocks(txt):
        m = MID_RE.search(b)
        if m and int(m.group(1)) == music_id:
            return b
    return None


def _set_field(block: str, tag: str, value, typ: str = "s32") -> str:
    pat = re.compile(r'(<%s __type="[^"]+">)(.*?)(</%s>)' % (re.escape(tag), re.escape(tag)), re.S)
    if pat.search(block):
        return pat.sub(lambda m: m.group(1) + str(value) + m.group(3), block, count=1)
    return block.replace("</data>", "<%s __type=\"%s\">%s</%s>\n</data>" % (tag, typ, value, tag), 1)


def _set_attr_id(block: str, new_id: int) -> str:
    return re.sub(r'^<data __type="void" id="\d+">',
                  '<data __type="void" id="%d">' % new_id, block, count=1)


def hex_cp932(text: str) -> str:
    try:
        return text.encode("cp932").hex()
    except Exception:
        return text.encode("ascii", "replace").hex()


def build_music_block(template: str, *, block_id: int, music_id: int, parent_id: int,
                      is_hold: int, title: str, reading: str, level: int,
                      bpm_max, version=None, grouping_category=None, pack_id=None) -> str:
    """以 template 块为蓝本，覆盖必要字段，产出新块文本。"""
    b = template
    b = _set_attr_id(b, block_id)
    b = _set_field(b, "music_id", music_id)
    b = _set_field(b, "parent_id", parent_id)
    b = _set_field(b, "is_hold", int(is_hold))
    b = _set_field(b, "name_string", hex_cp932(reading or title), "str")
    lv = int(round(float(level)))
    for slot in ("bsc", "adv", "ext"):
        b = _set_field(b, "level_%s" % slot, lv, "u8")
        b = _set_field(b, "detail_level_%s" % slot, float(level), "float")
    if bpm_max:
        b = _set_field(b, "bpm_max", bpm_max, "float")
    if version is not None:
        b = _set_field(b, "version", version, "str")
    if grouping_category is not None:
        b = _set_field(b, "grouping_category", grouping_category, "str")
    if pack_id is not None:
        b = _set_field(b, "pack_id", pack_id, "s16")
    return b


def add_music_record(txt: str, block: str):
    """在末尾插入块；遵守两条契约。返回 (新文本, info)。"""
    blocks = music_blocks(txt)
    ids = music_block_ids(txt)
    if sorted(ids) != list(range(len(ids))):
        raise ValueError("曲表契约违规：现有块 id 不是 0..N-1 连续（%d 块）" % len(ids))
    n = len(blocks)
    block = _set_attr_id(block, n)
    i = txt.rfind("</data>")
    if i < 0:
        raise ValueError("曲表结构异常：找不到 </data>")
    tail = txt[i + len("</data>"):]
    if "</body>" not in tail or "</music_data>" not in tail:
        raise ValueError("曲表结构异常：尾部闭合标签不完整")
    cut = i + len("</data>")
    out = txt[:cut] + "\n" + block + txt[cut:]
    out = re.sub(r"(<data_num __type=\"s32\">)\d+(</data_num>)",
                 lambda m: m.group(1) + str(n + 1) + m.group(2), out, count=1)
    return out, {"action": "add", "block_id": n, "new_data_num": n + 1}


def remove_music_record(txt: str, music_id: int):
    """按 music_id 摘除块，并把块 id 重排为 0..N-2 连续。返回 (新文本, info)。"""
    blocks = music_blocks(txt)
    keep = []
    removed = 0
    for b in blocks:
        m = MID_RE.search(b)
        if m and int(m.group(1)) == music_id:
            removed += 1
            continue
        keep.append(b)
    if removed == 0:
        raise ValueError("曲表里找不到 music_id=%d 的记录" % music_id)
    keep = [_set_attr_id(b, k) for k, b in enumerate(keep)]
    head_end = txt.find("<body")
    head = txt[:head_end]
    body_end_tag = txt.rfind("</data>")
    tail = txt[body_end_tag + len("</data>"):]
    out = head + "<body __type=\"void\">\n" + "\n".join(keep) + "\n" + tail
    out = re.sub(r"(<data_num __type=\"s32\">)\d+(</data_num>)",
                 lambda m: m.group(1) + str(len(keep)) + m.group(2), out, count=1)
    return out, {"action": "remove", "removed_blocks": removed, "new_data_num": len(keep)}


# ============================================================================
# 包表读写（复用 ub_updater 的 TLKP 解析/序列化）
# ============================================================================
def _pkg_key(name: str) -> str:
    return name.replace("_", " ")


def pkg_rows(path: str):
    return UU._tlkp_rows(open(path, "rb").read())


def pkg_add_row(path: str, row) -> tuple:
    rows, hdr = pkg_rows(path)
    rows = [r for r in rows if r[0] != row[0]]
    keys = [_pkg_key(r[0]) for r in rows]
    i = bisect.bisect_left(keys, _pkg_key(row[0]))
    rows.insert(i, tuple(row))
    return UU._tlkp_build(rows, hdr), {"action": "add", "name": row[0], "at": i,
                                       "rows": len(rows)}


def pkg_remove_row(path: str, name: str) -> tuple:
    rows, hdr = pkg_rows(path)
    keep = [r for r in rows if r[0] != name]
    removed = len(rows) - len(keep)
    if removed == 0:
        raise ValueError("包表里找不到行 %s" % name)
    return UU._tlkp_build(keep, hdr), {"action": "remove", "name": name,
                                       "removed": removed, "rows": len(keep)}


# ============================================================================
# 新 IFS / 贴图 构建
# ============================================================================
def _md5_file(p: str) -> str:
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_chart_level(mcz: str, diff: int):
    """尽力从 .mc 的 meta.level 取等级；取不到返回 None。

    注意：**当前安装流程不再使用它**——自制谱定数按用户要求固定为 1
    （实测 Malody 谱面 meta 里普遍没有 `level` 键，探测必然回落，无意义）。
    保留此函数仅供诊断/统计。
    """
    try:
        charts = UC.parse_mcz(mcz)
        name, raw = charts[diff]
        doc = json.loads(raw.decode("utf-8-sig"))
        lv = (doc.get("meta") or {}).get("level")
        return int(lv) if lv is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 难度分档（用户 2026-10-10 决定）
#   · 只有 1 个难度  -> 三档同谱（bsc / adv / ext 都写它）
#   · 有多个难度     -> 不三档同谱：从**难到易**依次填 ext -> adv -> bsc
#   · 定数（level）   -> 自制谱固定 1，不再探测
# ---------------------------------------------------------------------------
SLOT_ORDER = ("bsc", "adv", "ext")

# 难度名 -> 档位。按「先前缀、后整串包含」两轮匹配。
DIFF_HINTS = (
    ("bsc", ("bsc", "basic", "easy", "beginner", "light", "novice")),
    ("adv", ("adv", "advanced", "advance", "medium", "normal", "hard")),
    ("ext", ("ext", "extreme", "expert", "challenge", "master", "insane", "another", "oni")),
)


def classify_slot(chart):
    """按谱面难度名（meta.version）猜档位，返回 'bsc'/'adv'/'ext'；猜不出 None。"""
    text = (getattr(chart, "version", "") or "").strip().lower()
    if not text:
        return None
    for slot, keys in DIFF_HINTS:
        if any(text.startswith(k) for k in keys):
            return slot
    for slot, keys in DIFF_HINTS:
        if any(k in text for k in keys):
            return slot
    return None


def slot_of_member(member_name: str):
    """容器成员名 -> 档位（`ext.eve` -> 'ext'）。认不出返回 None。"""
    n = (member_name or "").lower()
    for s in SLOT_ORDER:
        if s in n:
            return s
    return None


def plan_slot_map(charts, slot=None) -> dict:
    """决定「档位 -> charts 下标」的映射。

    排序依据：优先难度名（bsc < adv < ext），其次音符数（少=易），最后原顺序。
    返回如 {"ext": 2, "adv": 1, "bsc": 0}；`slot` 显式指定时只映射该档。
    """
    n = len(charts)
    if n <= 0:
        return {}
    if slot:
        return {slot: 0}
    if n == 1:
        return {s: 0 for s in SLOT_ORDER}

    rank = {"bsc": 0, "adv": 1, "ext": 2}

    def sort_key(i):
        ch = charts[i]
        s = classify_slot(ch)
        return (rank[s] if s else 1.5, len(ch.notes or []), i)

    order = sorted(range(n), key=sort_key)      # 易 -> 难（原下标）
    used = order[-3:] if n > 3 else order       # 多于 3 档：取最难的 3 个
    easy = used[0]
    return {
        "ext": used[-1],
        "adv": used[-2] if len(used) >= 2 else easy,
        "bsc": used[-3] if len(used) >= 3 else easy,
    }


def build_new_ifs(src_ifs: str, mcz: str, diff: int, slots,
                  out_path: str, hold_policy: str = "split",
                  own_audio: bool = False, audio_bytes=None, audio_label=None,
                  preview_ms=None, on_progress=None, slot_map=None) -> dict:
    """克隆 src_ifs -> out_path，替换谱面成员。

    own_audio=True 时**同时把音频成员换成谱面自带音源**（默认 48kHz 立体声、
    10.0s 预览，起点取谱面 meta.preview）；否则音频/预览成员逐字节保持（借音源）。

    slot_map: {档位: charts 下标}，如 {"ext": 2, "adv": 1, "bsc": 0}。
      给定时**每个档位写各自难度的谱面**（多难度不再三档同谱）；
      不给时所有成员都写 `diff` 那一份（= 三档同谱）。
    """
    charts = UC.parse_malody(mcz)
    if not charts:
        raise ValueError("mcz 内没有可用谱面")
    if not (0 <= diff < len(charts)):
        raise ValueError("--diff 越界：%d（共 %d 个）" % (diff, len(charts)))
    chart = charts[diff]

    def eve_of(d: int):
        if not (0 <= d < len(charts)):
            raise ValueError("slot_map 指向越界的难度下标：%d（共 %d 个）"
                             % (d, len(charts)))
        return UC.chart_to_eve(charts[d], hold_policy=hold_policy)

    c = UC.MiscIfs(path=src_ifs)
    src_audio = {m.name: hashlib.md5(m.data).hexdigest() for m in c.audio_members()}
    replaced = []
    slot_used = {}
    cache = {}
    for m in c.chart_members():
        if slots and m.name.lower() not in slots:
            continue
        d = diff
        if slot_map:
            s = slot_of_member(m.name)
            if s in slot_map:
                d = int(slot_map[s])
        if d not in cache:
            cache[d] = eve_of(d)
        eve, _st = cache[d]
        m.data = eve
        replaced.append(m.name)
        slot_used[m.name] = {"diff": d, "version": charts[d].version,
                             "notes": len(charts[d].notes or [])}
    if not replaced:
        raise ValueError("目标容器里没有匹配的谱面成员（slot=%s）；可选：%s"
                         % (slots, ", ".join(m.name for m in c.chart_members())))

    # 主档（用于报告/统计）：优先 ext，其次传入的 diff
    main_d = diff
    if slot_map and "ext" in slot_map:
        main_d = int(slot_map["ext"])
    if main_d not in cache:
        cache[main_d] = eve_of(main_d)
    st = cache[main_d][1]


    audio_info = None
    if own_audio:
        if UA is None:
            raise RuntimeError("缺少 tools/ub_audio.py —— 无法写入自带音源")
        if audio_bytes is None:
            got = UA.load_chart_audio(mcz, title=chart.title, hint=chart.audio_hint)
            if got is None:
                raise ValueError("谱面里找不到自带音源（%s）；如要借音源请关掉自带音源开关"
                                 % os.path.dirname(os.path.abspath(mcz)))
            audio_label, audio_bytes = got
        pm = preview_ms if preview_ms is not None else (chart.preview_ms or 0)
        if on_progress:
            on_progress(0.05, "解码自带音源…")
        pcm = UA.decode_to_pcm(audio_bytes, rate=48000, channels=2)
        frames = UA.pcm_frames(pcm, 2)
        pv = UA.make_preview(pcm, 2, 48000, pm)
        if on_progress:
            on_progress(0.20, "编码 OKI4S（%d 帧 / %.1fs）…" % (frames, frames / 48000.0))
        rep = UA.replace_ifs_audio(c, pcm, pv, rate=48000, channels=2)
        stt = UA.pcm_stats(pcm)
        audio_info = {"own": True, "label": audio_label, "preview_ms": int(pm),
                      "frames": frames, "seconds": frames / 48000.0,
                      "rms": round(stt["rms"], 1), "peak": stt["peak"],
                      "main": rep["main"], "preview": rep.get("preview")}
        if on_progress:
            on_progress(0.90, "重打包 IFS…")

    blob = c.to_bytes()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(blob)

    back = UC.MiscIfs(path=out_path)
    new_audio = {m.name: hashlib.md5(m.data).hexdigest() for m in back.audio_members()}
    if on_progress:
        on_progress(1.0, "完成")
    return {
        "out": out_path, "replaced": replaced, "src_ifs": src_ifs,
        "src_audio_md5": src_audio, "new_audio_md5": new_audio,
        "audio_identical": src_audio == new_audio,
        "audio_source": audio_info or {"own": False,
                                       "label": "借用 %s 的音频" % src_ifs},
        "chart": {"title": chart.title, "artist": chart.artist,
                  "version": chart.version, "notes": len(chart.notes)},
        "stats": st.as_dict(),
        # 每个谱面成员实际用了哪个难度（多难度分档时各档不同）
        "slots": slot_used,
        "slot_map": ({k: int(v) for k, v in slot_map.items()} if slot_map else None),
    }


def _load_placeholder_font(size=24):
    from PIL import ImageFont
    for cand in (r"C:\Windows\Fonts\meiryo.ttc", r"C:\Windows\Fonts\msgothic.ttc",
                 r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                pass
    return ImageFont.load_default()


def build_new_tex(src_tex: str, music_id: int, jacket_png, out_path: str) -> dict:
    """克隆单曲图集，把唯一条目改名为 BNR_BIG_ID<id> 并换成新图。"""
    if T is None:
        raise RuntimeError("缺少 tools/tdxt_encode.py —— 无法编码贴图；"
                           "请先交付该模块，或省略 --jacket 并加 --no-tex")
    base = T.read_pxet(src_tex)
    if not base["items"]:
        raise ValueError("源图集没有条目：%s" % src_tex)
    oh = T.parse_tdxt_header(T.lzss_decompress(base["items"][0]["blob"])) or {}
    w, h, fmt, hf3 = oh.get("w", 320), oh.get("h", 320), oh.get("fmt", 0x10), oh.get("hf3", 0x81)
    if jacket_png:
        tdxt = T.encode_png_to_tdxt(jacket_png, fmt=fmt, fit=(w, h), hf3=hf3)
        used = jacket_png
        placeholder = False
    else:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (w, h), (24, 26, 34, 255))
        d = ImageDraw.Draw(img)
        font = _load_placeholder_font(max(18, w // 14))
        txt = "CUSTOM\n%d" % music_id
        try:
            d.multiline_text((w // 12, h // 2 - 20), txt, fill=(240, 240, 245, 255),
                             font=font, spacing=8)
        except Exception:
            d.text((w // 12, h // 2 - 20), txt, fill=(240, 240, 245, 255))
        tdxt = T.encode_image_to_tdxt(img, fmt=fmt, hf3=hf3)
        used = "(placeholder)"
        placeholder = True
    entry = "BNR_BIG_ID%d" % music_id
    new_item = dict(name=entry, hash=T.name_hash(entry),
                    flag=base["items"][0]["flag"],
                    blob=T.make_pxet_record(tdxt))
    blob = T.rebuild_pxet(base, [new_item])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(blob)
    return {"out": out_path, "entry": entry, "src_tex": src_tex,
            "size_px": (w, h), "fmt": fmt, "from": used,
            "placeholder": placeholder, "bytes": len(blob)}


def clone_mdl(src_mdl: str, music_id: int, out_path: str) -> dict:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    shutil.copy2(src_mdl, out_path)
    return {"out": out_path, "src_mdl": src_mdl, "bytes": os.path.getsize(out_path)}


# ============================================================================
# 分组图集小贴图注册（BNR_ID / IDX_ID / IDX_MINI_ID）
#
# 铁律（见 .workbuddy/memory/2026-10-05.md「PMAN f14」「分组图集加载上限」两节）：
#   1) 分组图集必须位于 mod 目录才会被加载；开机只加载**固定的一批**（本机 55 个）
#      —— 所以**不要新增图集文件**，而是往「已被加载」的图集里追加条目。
#   2) PMAN 头 f14 必须按最终条目数重算（由 tdxt_encode.rebuild_pxet 负责）。
#   3) 纹理表/texIndex 按名字升序、PMAN 条目按 hash 升序、clen 4 字节对齐。
#   4) **绝不改写单曲图集** tex_l44_bnr_big_id<id>.bin（内部结构有严格要求，改写即失效）。
#
# 选图集策略：优先挑「已加载 + 含同段最大且小于本曲 id 的 BNR_ID」的那一个
#   （90001036 -> tex_l44fo_bnr_j_09_021.bin，它已收录到 90001035）。
# ============================================================================
GROUP_ATLAS_FALLBACK = ("tex_l44fo_bnr_j_09_021.bin", "tex_l44av_bnr_j_11_018.bin")
_LOG_ATLAS_RE = re.compile(
    r"layeredfs:\s*Using\s+.*?/(tex_[A-Za-z0-9_]*?bnr_j[A-Za-z0-9_]*?\.bin)")


def loaded_group_atlases(game: str) -> list:
    """从游戏日志解析**实际被加载**的分组图集名（权威来源：layeredfs 命中记录）。
       日志缺失时返回空列表（调用方退回内置兜底名单）。"""
    log = os.path.join(game_paths(game)["game"], "log.txt")
    if not os.path.exists(log):
        return []
    try:
        txt = open(log, encoding="utf-8", errors="replace").read()
    except Exception:
        return []
    out = []
    for m in _LOG_ATLAS_RE.finditer(txt):
        n = m.group(1)
        if n not in out:
            out.append(n)
    return out


def _atlas_bnr_ids(path: str):
    """返回 (base, [int id, ...])，只取 BNR_ID<n> 形式的条目。"""
    base = T.read_pxet(path)
    ids = []
    for it in base["items"]:
        m = re.fullmatch(r"BNR_ID(\d+)", it["name"])
        if m:
            ids.append(int(m.group(1)))
    return base, ids


def pick_group_atlas(game: str, music_id: int, prefer=None) -> dict:
    """挑一个「必定被加载」的分组图集来追加贴图。返回 {name, path, anchor}。"""
    root = game_paths(game)["model_overlay"]
    cand = []
    if prefer:
        cand.append(os.path.basename(prefer))
    for n in loaded_group_atlases(game):
        if n not in cand:
            cand.append(n)
    for n in GROUP_ATLAS_FALLBACK:
        if n not in cand:
            cand.append(n)

    best_below, best_below_id = None, -1
    best_above, best_above_id = None, None
    for n in cand:
        p = os.path.join(root, n)
        if not os.path.exists(p):
            continue
        try:
            _b, ids = _atlas_bnr_ids(p)
        except Exception:
            continue
        below = [i for i in ids if i < music_id]
        above = [i for i in ids if i > music_id]
        if below and max(below) > best_below_id:
            best_below_id, best_below = max(below), n
        if above:
            mn = min(above)
            if best_above_id is None or mn < best_above_id:
                best_above_id, best_above = mn, n
    name = best_below or best_above
    if name is None:
        raise FileNotFoundError("找不到可用的分组图集（mod 目录：%s）；"
                                "请用 --atlas 显式指定" % root)
    return {"name": name, "path": os.path.join(root, name),
            "anchor": best_below_id if best_below else best_above_id,
            "loaded": name in loaded_group_atlases(game)}


def _sibling_style(base, rx: str):
    """在已有条目里找一个匹配 rx 的同族条目，返回 (fmt, hf3, fmtflags, flag)；无则 None。
       用于让新条目沿用官方同族贴图的像素格式（最大保真）。"""
    for it in base["items"]:
        if re.fullmatch(rx, it["name"]):
            try:
                h = T.parse_tdxt_header(T.lzss_decompress(it["blob"]))
            except Exception:
                continue
            if h:
                return (h.get("fmt"), h.get("hf3"), h.get("fmtflags"), it["flag"])
    return None


def _make_small_jacket(music_id: int, wh):
    """没有提供小曲绘 PNG 时生成的占位方图。"""
    from PIL import Image, ImageDraw
    w, h = wh
    img = Image.new("RGBA", (w, h), (24, 26, 34, 255))
    d = ImageDraw.Draw(img)
    font = _load_placeholder_font(max(12, w // 9))
    try:
        d.multiline_text((6, max(2, h // 2 - 15)), "CUSTOM\n%d" % music_id,
                         fill=(240, 240, 245, 255), font=font, spacing=3)
    except Exception:
        d.text((6, max(2, h // 2 - 8)), str(music_id), fill=(240, 240, 245, 255))
    return img


def _derive_small_jacket(game, music_id: int, wh):
    """从本曲的大曲绘图集 tex_l44_bnr_big_id<id>.bin 解码并缩放到 wh。
       成功返回 PIL 图，失败返回 None（调用方退回占位图）。"""
    p = new_tex_path(game, music_id)
    if not os.path.exists(p):
        return None
    try:
        base = T.read_pxet(p)
        if not base["items"]:
            return None
        raw = T.lzss_decompress(base["items"][0]["blob"])
        img = T.decode_tdxt(raw)
        return T._fit_image(img.convert("RGBA"), wh, False)
    except Exception:
        return None


def register_group_tex(game, music_id: int, title: str, jacket=None, artist=None,
                       marker="", atlas=None, apply=False, workdir=None) -> dict:
    """把 BNR_ID / IDX_ID / IDX_MINI_ID<id> 追加进一个「已被加载」的分组图集。
       只新增条目：若同名已存在则跳过（不覆盖）。apply=False 时仅预览。"""
    if T is None:
        raise RuntimeError("缺少 tools/tdxt_encode.py —— 无法编码贴图")

    got = pick_group_atlas(game, music_id, atlas)
    path, name = got["path"], got["name"]
    base = T.read_pxet(path)
    exist = {it["name"] for it in base["items"]}

    from PIL import Image
    specs = [
        ("BNR_ID%d" % music_id, r"BNR_ID\d+", (160, 160)),
        ("IDX_ID%d" % music_id, r"IDX_ID\d+", (324, 40)),
        ("IDX_MINI_ID%d" % music_id, r"IDX_MINI_ID\d+", (160, 24)),
    ]
    todo = [(n, rx, wh) for (n, rx, wh) in specs if n not in exist]
    skipped = [n for (n, _rx, _wh) in specs if n in exist]

    details = []
    for entry, rx, (w, h) in todo:
        st = _sibling_style(base, rx)
        fmt = st[0] if (st and st[0] is not None) else 0x10
        hf3 = st[1] if (st and st[1] is not None) else None
        fmtflags = st[2] if st else None
        flag = st[3] if st else 0

        if entry.startswith("BNR_ID"):
            img, src = None, None
            if jacket:
                img = T._fit_image(Image.open(jacket).convert("RGBA"), (w, h), False)
                src = jacket
            if img is None:
                img = _derive_small_jacket(game, music_id, (w, h))
                if img is not None:
                    src = "derive:%s" % os.path.basename(new_tex_path(game, music_id))
            if img is None:
                img = _make_small_jacket(music_id, (w, h))
                src = "(placeholder)"
            tdxt = T.encode_image_to_tdxt(img, fmt=fmt, hf3=hf3, fmtflags=fmtflags)
        else:
            wide = entry.startswith("IDX_ID")
            img = T.make_title_png(title, slot=("wide" if wide else "mini"),
                                   marker=marker,
                                   artist=(artist if wide else None))
            tdxt = T.encode_image_to_tdxt(img, fmt=fmt, hf3=hf3, fmtflags=fmtflags)
            src = "title:%s(%dx%d)" % (title, w, h)

        item = dict(name=entry, hash=T.name_hash(entry), flag=flag,
                    blob=T.make_pxet_record(tdxt, False))
        base["items"].append(item)
        details.append({"entry": entry, "fmt": hex(fmt), "size": "%dx%d" % (w, h),
                        "from": src, "bytes": len(item["blob"])})

    out = {"atlas": path, "atlas_name": name, "anchor": got["anchor"],
           "atlas_loaded": got["loaded"],
           "n_before": len(base["items"]) - len(details), "n_after": len(base["items"]),
           "size_before": os.path.getsize(path),
           "entries": details, "skipped_existing": skipped, "apply": bool(apply)}
    if not details:
        out["action"] = "noop"
        return out

    blob = T.rebuild_pxet(base, base["items"])
    out["size_after"] = len(blob)
    if not apply:
        out["action"] = "dry-run"
        return out

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = path + ".bak_" + stamp
    shutil.copy2(path, bak)
    with open(path, "wb") as f:
        f.write(blob)

    chk = T.read_pxet(path)
    n = len(chk["items"])
    out.update({
        "action": "written", "backup": bak,
        "md5": hashlib.md5(blob).hexdigest(),
        "verify": {
            "n": n,
            "f14_file": hex(chk["f14"]),
            "f14_expect": hex(T.pman_hash_param(n)),
            "f14_ok": chk["f14"] == T.pman_hash_param(n),
            "present": sorted(it["name"] for it in chk["items"]
                              if it["name"].endswith(str(music_id))),
            "byte_exact_rebuild": T.rebuild_pxet(chk, chk["items"]) == blob,
        },
    })

    # 侧车记录：让 uninstall 能精确撤销这次追加
    wd = os.path.abspath(workdir or default_workdir())
    os.makedirs(wd, exist_ok=True)
    side = os.path.join(wd, "group_tex_%d.json" % music_id)
    with open(side, "w", encoding="utf-8") as fh:
        json.dump({"music_id": music_id, "atlas": path,
                   "entries": [e["entry"] for e in details],
                   "backup": bak, "stamp": stamp}, fh, ensure_ascii=False, indent=1)
    out["sidecar"] = side
    return out


def unregister_group_tex(game, music_id: int, workdir=None) -> dict:
    """撤销 register_group_tex 追加的条目（按侧车记录精确删除；可重复调用）。"""
    wd = os.path.abspath(workdir or default_workdir())
    side = os.path.join(wd, "group_tex_%d.json" % music_id)
    if not os.path.exists(side):
        return {"skipped": "无侧车记录 %s" % side}
    rec = json.load(open(side, encoding="utf-8"))
    path = rec["atlas"]
    if not os.path.exists(path):
        return {"skipped": "图集不存在 %s" % path}
    base = T.read_pxet(path)
    drop = set(rec["entries"])
    present = {it["name"] for it in base["items"]}
    gone = sorted(drop & present)
    if not gone:
        return {"removed": [], "atlas": path}
    keep = [it for it in base["items"] if it["name"] not in drop]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = path + ".bak_unreg_" + stamp
    shutil.copy2(path, bak)
    with open(path, "wb") as f:
        f.write(T.rebuild_pxet(base, keep))
    chk = T.read_pxet(path)
    os.remove(side)
    return {"removed": gone, "atlas": path, "backup": bak,
            "n": len(chk["items"]),
            "f14_ok": chk["f14"] == T.pman_hash_param(len(chk["items"])),
            "sidecar_removed": side}


def find_source_assets(game: str, src_music_id: int):
    """为借源曲找到它现有的 ifs / tex / mdl（data 或 data_mods 皆可）。"""
    p = game_paths(game)
    ifs = None
    for root in (p["ifs_base"], p["ifs_overlay"]):
        cand = os.path.join(root, group_dir(src_music_id), "%d_msc.ifs" % src_music_id)
        if os.path.exists(cand):
            ifs = cand
            break
    tex = mdl = None
    for root in (p["model_overlay"], p["model_base"]):
        t = os.path.join(root, "tex_l44_bnr_big_id%d.bin" % src_music_id)
        m = os.path.join(root, "mdl_l44_bnr_big_id%d.bin" % src_music_id)
        if tex is None and os.path.exists(t):
            tex = t
        if mdl is None and os.path.exists(m):
            mdl = m
    return {"ifs": ifs, "tex": tex, "mdl": mdl}


def any_source_with_assets(game: str, limit=4000):
    """兜底：找一个既有 ifs 的曲作为音频源。"""
    p = game_paths(game)
    for root in (p["ifs_overlay"], p["ifs_base"]):
        if not os.path.isdir(root):
            continue
        for d in sorted(os.listdir(root)):
            sub = os.path.join(root, d)
            if not os.path.isdir(sub):
                continue
            for f in sorted(os.listdir(sub)):
                m = re.match(r"^(\d+)_msc\.ifs$", f)
                if m:
                    return int(m.group(1))
    return None


# ============================================================================
# 哈希清单（零覆盖证明用）
# ============================================================================
def hash_tree(root: str, exclude=()):
    ex = set(os.path.normpath(x) for x in exclude)
    out = {}
    for dp, _dn, fns in os.walk(root):
        for f in fns:
            full = os.path.join(dp, f)
            if os.path.normpath(full) in ex:
                continue
            rel = os.path.relpath(full, root).replace("\\", "/")
            st = os.stat(full)
            out[rel] = {"md5": _md5_file(full), "size": st.st_size}
    return out


def diff_manifests(before: dict, after: dict):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}


# ============================================================================
# plan
# ============================================================================
def make_plan(game, mcz, parent=None, audio_from=None, title=None, reading=None,
              jacket=None, diff=0, slot=None, level=None, music_id=None,
              hold_policy="split", pkg_which="base", no_tex=False,
              own_audio=None, preview_ms=None,
              version_mask=None, grouping_category=None, reading_prefix=None):
    p = game_paths(game)
    if not os.path.isdir(p["game"]):
        raise FileNotFoundError("游戏目录不存在：%s" % game)
    if not os.path.exists(music_table_path(game)):
        raise FileNotFoundError("游戏目录结构不符：缺少曲表")
    if audio_from is None:
        audio_from = parent
    if audio_from is None:
        audio_from = any_source_with_assets(game)
        if audio_from is None:
            raise ValueError("无法确定音频来源：请显式指定 --audio-from <已有曲id>")
    src = find_source_assets(game, audio_from)
    if not src["ifs"]:
        raise ValueError("借源曲 %d 不存在可用的 *_msc.ifs" % audio_from)

    charts = UC.parse_malody(mcz)
    if not charts:
        raise ValueError("mcz 里没有可用谱面：%s" % mcz)
    if not (0 <= diff < len(charts)):
        raise ValueError("--diff 越界：%d（共 %d 个）" % (diff, len(charts)))
    chart = charts[diff]

    # ---- 自带音源判定 ----
    # 借源曲只用来提供「容器模板」（成员名/顺序）；音频可以换成谱面自带的。
    audio_label = None
    if own_audio is None:
        if UA is not None:
            got = None
            try:
                got = UA.load_chart_audio(mcz, title=chart.title, hint=chart.audio_hint)
            except Exception:
                got = None
            own_audio = bool(got)
            if got:
                audio_label = got[0]
        else:
            own_audio = False
    if own_audio:
        if UA is None:
            raise RuntimeError("缺少 tools/ub_audio.py —— 无法使用自带音源")
        if audio_label is None:
            got = UA.load_chart_audio(mcz, title=chart.title, hint=chart.audio_hint)
            if got is None:
                raise ValueError("谱面里找不到自带音源；如要借音源请显式 own_audio=False")
            audio_label = got[0]
    if preview_ms is None:
        preview_ms = chart.preview_ms if chart.preview_ms is not None else 0

    # 定数（level）：自制谱**固定 1**（用户 2026-10-10 定）。
    # 实测 Malody 谱面 meta 里普遍没有 `level` 键（4/4 首都没有），
    # 探测只会回落成 1，没有意义，故不再探测。显式传 level 仍可覆盖。
    if level is None:
        level = DEFAULT_LEVEL

    if music_id is None:
        music_id = recommend_ids(game, DEFAULT_BASE, 1)[0]
    free, reasons = check_id_free(game, music_id)
    if not free:
        raise ValueError("music_id=%d 不可用：%s" % (music_id, "；".join(reasons)))

    title = title or chart.title or ("CUSTOM %d" % music_id)
    reading = reading or title
    # 五十音假名读音（name_string）前缀：必须是合法 CP932 假名/汉字，
    # 否则游戏会打 W:music_db: Invalid name string。
    # 注意：**不加半角空格当分隔符**（半角 ASCII 可能被判非法），直接拼接。
    # 典型值 'ジセイ'（= 自制）→ 读音变成 'ジセイ<原读音>'，按假名搜「ジセイ」即可筛出。
    if reading_prefix:
        reading = ("%s%s" % (reading_prefix, reading)).strip()
    try:
        bpm_max = float(max(bpm for _b, bpm in chart.bpm_at_beat))
    except Exception:
        bpm_max = None

    slots = None
    if slot:
        slots = {"%s.eve" % slot}
    # 多难度分档：单难度 -> 三档同谱；多难度 -> 各档写各自难度。
    # 显式 --slot 时不做分派（保持「只写该槽 + 用 --diff 指定难度」的原语义）。
    slot_map = None if slot else (plan_slot_map(charts) or None)

    new_files = [{
        "kind": "ifs",
        "path": new_ifs_path(game, music_id),
        "from": src["ifs"],
        "desc": ("克隆容器 + 替换谱面成员 + **写入谱面自带音源**（%s）" % audio_label)
                if own_audio else
                "克隆借源曲容器，仅替换谱面成员（音频/预览逐字节保持）",
    }]
    if not no_tex:
        if not src["tex"] and not jacket:
            raise ValueError("借源曲 %d 没有单曲图集可作模板；请指定 --no-tex 或换 --audio-from"
                             % audio_from)
        if src["tex"]:
            new_files.append({
                "kind": "tex",
                "path": new_tex_path(game, music_id),
                "from": src["tex"],
                "desc": "单曲大曲绘图集（条目 BNR_BIG_ID%d）" % music_id,
            })
            if src["mdl"]:
                new_files.append({
                    "kind": "mdl",
                    "path": new_mdl_path(game, music_id),
                    "from": src["mdl"],
                    "desc": "模型桩（60B，全曲一致，直接克隆）",
                })

    tbl = music_table_path(game)
    pk = pkg_table_path(game, pkg_which)
    table_changes = [{
        "kind": "music", "path": tbl, "action": "add",
        "detail": "新增一条 <data> 记录 music_id=%d parent_id=%d is_hold=%d"
                  % (music_id, parent or 0, 1 if parent else 0),
    }]
    if not no_tex and src["tex"]:
        table_changes.append({
            "kind": "pkg", "path": pk, "action": "add",
            "detail": "新增一行 %s" % (pkg_row_for(music_id)[0],),
        })

    plan = {
        "game": p["game"],
        "music_id": music_id,
        "title": title,
        "reading": reading,
        "level": int(round(float(level))),
        "bpm_max": bpm_max,
        "parent_id": parent or 0,
        "is_hold": 1 if parent else 0,
        "source_music_id": audio_from,
        "mcz": os.path.abspath(mcz),
        "jacket": os.path.abspath(jacket) if jacket else None,
        "diff": diff,
        "slot": slot,
        # {档位: charts 下标}；None = 不分派（三档同谱 / 显式 --slot）
        "slot_map": slot_map,
        # 便于展示：档位 -> 该档实际使用的难度名与音符数
        "slot_plan": ({s: {"diff": int(d),
                           "version": charts[int(d)].version,
                           "notes": len(charts[int(d)].notes or [])}
                       for s, d in (slot_map or {}).items()}),
        "charts_count": len(charts),
        "hold_policy": hold_policy,
        "own_audio": bool(own_audio),
        "audio_label": audio_label,
        "preview_ms": int(preview_ms or 0),
        "version_mask": version_mask or None,          # 覆盖曲表 <version>（如 4000）
        "grouping_category": grouping_category,        # 覆盖 <grouping_category>
        "pkg_table": pk,
        "music_table": tbl,
        "no_tex": bool(no_tex),
        "new_files": new_files,
        "table_changes": table_changes,
        "touched_existing": [{"path": c["path"], "why": "必须登记（曲表/包表）"}
                             for c in table_changes],
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    return plan


# ============================================================================
# install / uninstall
# ============================================================================
def default_workdir():
    return os.path.join(os.path.dirname(_HERE), "tmp", "ub_newsong")


def _backup(root, stamp, path):
    rel = os.path.relpath(path, os.path.splitdrive(path)[0] + os.sep).replace("\\", "/")
    dst = os.path.join(root, "backup", stamp, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(path, dst)
    return dst


def install(plan, apply=False, workdir=None, on_progress=None):
    workdir = os.path.abspath(workdir or default_workdir())
    game = plan["game"]
    mid = plan["music_id"]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    state_path = os.path.join(workdir, "state_%d.json" % mid)

    # ---- 前置校验 ----
    free, reasons = check_id_free(game, mid)
    if not free:
        raise ValueError("拒绝安装：music_id=%d 不可用（%s）" % (mid, "；".join(reasons)))
    if os.path.exists(state_path):
        raise ValueError("拒绝安装：已存在安装状态 %s（如需重装请先 uninstall）" % state_path)
    for f in plan["new_files"]:
        if os.path.exists(f["path"]):
            raise ValueError("拒绝安装：目标文件已存在 %s" % f["path"])
        if f.get("from") and not os.path.exists(f["from"]):
            raise ValueError("拒绝安装：来源文件不存在 %s" % f["from"])
    for c in plan["table_changes"]:
        if not os.path.exists(c["path"]):
            raise ValueError("拒绝安装：表文件不存在 %s" % c["path"])

    steps = []
    steps.append("新增 %d 个文件" % len(plan["new_files"]))
    steps.append("修改 %d 张表" % len(plan["table_changes"]))
    result = {"apply": bool(apply), "stamp": stamp, "steps": steps,
              "new_files": [f["path"] for f in plan["new_files"]],
              "state_path": state_path}
    if not apply:
        result["dry_run"] = True
        return result

    os.makedirs(workdir, exist_ok=True)
    backups = {}

    # ---- 1. 备份两张表 ----
    for c in plan["table_changes"]:
        backups[c["path"]] = _backup(workdir, stamp, c["path"])

    # ---- 2. 写新文件 ----
    written = []
    details = {}
    for f in plan["new_files"]:
        if f["kind"] == "ifs":
            details["ifs"] = build_new_ifs(
                f["from"], plan["mcz"], plan["diff"],
                ({"%s.eve" % plan["slot"]} if plan.get("slot") else None),
                f["path"], plan["hold_policy"],
                own_audio=bool(plan.get("own_audio")),
                preview_ms=plan.get("preview_ms"),
                on_progress=on_progress,
                slot_map=plan.get("slot_map"))
        elif f["kind"] == "tex":
            details["tex"] = build_new_tex(f["from"], mid, plan.get("jacket"), f["path"])
        elif f["kind"] == "mdl":
            details["mdl"] = clone_mdl(f["from"], mid, f["path"])
        written.append(f["path"])

    # ---- 3. 改表 ----
    tbl_changes = []
    for c in plan["table_changes"]:
        if c["kind"] == "music":
            txt = open(c["path"], encoding="utf-8", errors="replace").read()
            tmpl = find_block_by_music_id(txt, plan["source_music_id"])
            if tmpl is None:
                tmpl = music_blocks(txt)[0]
            block = build_music_block(
                tmpl, block_id=len(music_blocks(txt)), music_id=mid,
                parent_id=plan["parent_id"], is_hold=plan["is_hold"],
                title=plan["title"], reading=plan["reading"], level=plan["level"],
                bpm_max=plan.get("bpm_max"),
                version=plan.get("version_mask"),
                grouping_category=plan.get("grouping_category"))
            out, info = add_music_record(txt, block)
            with open(c["path"], "w", encoding="utf-8", newline="") as fh:
                fh.write(out)
            tbl_changes.append({"kind": "music", "path": c["path"], **info})
        elif c["kind"] == "pkg":
            blob, info = pkg_add_row(c["path"], pkg_row_for(mid))
            with open(c["path"], "wb") as fh:
                fh.write(blob)
            tbl_changes.append({"kind": "pkg", "path": c["path"], **info})

    state = {"music_id": mid, "game": game, "stamp": stamp, "workdir": workdir,
             "new_files": [{"path": f["path"], "kind": f["kind"]} for f in plan["new_files"]],
             "backups": backups, "table_changes": tbl_changes,
             "plan": plan, "installed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)

    result.update({"written": written, "table_changes": tbl_changes,
                   "backups": backups, "details": details})
    result["restore_cmd"] = ("python tools/ub_newsong.py uninstall --music-id %d --game \"%s\""
                             % (mid, game))
    return result


def uninstall(game, music_id, workdir=None, stamp=None):
    workdir = os.path.abspath(workdir or default_workdir())
    state_path = os.path.join(workdir, "state_%d.json" % music_id)
    if not os.path.exists(state_path):
        raise FileNotFoundError("找不到安装状态 %s（无法安全还原；请手动核对）" % state_path)
    state = json.load(open(state_path, encoding="utf-8"))

    removed = []
    for f in state["new_files"]:
        p = f["path"]
        if os.path.exists(p):
            os.remove(p)
            removed.append(p)
    # 清理空的分组目录（仅当为空）
    d = os.path.dirname(new_ifs_path(game, music_id))
    if os.path.isdir(d) and not os.listdir(d):
        os.rmdir(d)

    restored = []
    for path, bak in state["backups"].items():
        if os.path.exists(bak):
            shutil.copy2(bak, path)
            restored.append(path)

    os.remove(state_path)
    # 顺带撤销分组图集里追加的小贴图（若有侧车记录；失败不影响主流程）
    gtx = {}
    try:
        gtx = unregister_group_tex(game, music_id, workdir=workdir)
    except Exception as ex:                                   # pragma: no cover
        gtx = {"error": str(ex)}
    return {"music_id": music_id, "removed": removed, "restored": restored,
            "state_removed": state_path, "group_tex": gtx}


# ============================================================================
# verify
# ============================================================================
def verify(game, as_json=False):
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # 曲表
    try:
        mt = music_table_path(game)
        txt = open(mt, encoding="utf-8", errors="replace").read()
        try:
            ET.fromstring(txt)
            chk("曲表 XML 可解析", True, mt)
        except Exception as e:
            chk("曲表 XML 可解析", False, str(e))
        ids = music_block_ids(txt)
        nblocks = len(music_blocks(txt))
        chk("曲表块 id 连续 0..N-1", ids == list(range(len(ids))),
            "块数=%d, id范围=%s" % (nblocks, (min(ids), max(ids)) if ids else None))
        m = re.search(r"<data_num __type=\"s32\">(\d+)</data_num>", txt)
        dn = int(m.group(1)) if m else -1
        chk("data_num == 块数", dn == nblocks, "data_num=%d 块数=%d" % (dn, nblocks))
        mids = [int(x) for x in MID_RE.findall(txt)]
        chk("music_id 无重复", len(mids) == len(set(mids)),
            "%d 个 / 唯一 %d" % (len(mids), len(set(mids))))
    except Exception as e:
        chk("曲表校验", False, str(e))

    # 包表
    for label, path in (("基础包表 alldata.bin", game_paths(game)["pkg_base"]),
                        ("覆盖包表 allomni.bin", game_paths(game)["pkg_overlay_allomni"]),
                        ("覆盖包表 alldata.bin", game_paths(game)["pkg_overlay_alldata"])):
        if not os.path.exists(path):
            continue
        try:
            buf = open(path, "rb").read()
            rows, hdr = UU._tlkp_rows(buf)
            rebuilt = UU._tlkp_build(rows, hdr)
            chk("包表可解析 %s" % label, True, "%d 行" % len(rows))
            chk("包表 round-trip 一致 %s" % label, rebuilt == buf,
                "重打包 %d B vs 原 %d B" % (len(rebuilt), len(buf)))
            key = [_pkg_key(r[0]) for r in rows]
            chk("包表按游戏键升序 %s" % label, key == sorted(key))
        except Exception as e:
            chk("包表校验 %s" % label, False, str(e))

    ok = all(o for _, o, _ in checks)
    res = {"game": os.path.abspath(game), "ok": ok,
           "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]}
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print("== verify %s" % res["game"])
        for n, o, d in checks:
            print("  [%s] %s%s" % ("PASS" if o else "FAIL", n, ("  (%s)" % d) if d else ""))
        print("== 结果: %s" % ("全部通过" if ok else "存在失败项"))
    return res


# ============================================================================
# CLI
# ============================================================================
def cmd_list_ids(args):
    occ = scan_occupied(args.game)
    rec = recommend_ids(args.game, args.base, args.count)
    if args.json:
        print(json.dumps({
            "game": os.path.abspath(args.game),
            "counts": {k: len(v) for k, v in occ["sources"].items()},
            "union_count": len(occ["union"]),
            "max_id": max(occ["union"]) if occ["union"] else None,
            "recommended": rec,
            "recommend_base": args.base,
        }, ensure_ascii=False, indent=1))
        return 0
    print("== 已占用 music_id 扫描 %s" % os.path.abspath(args.game))
    for k, v in occ["sources"].items():
        print("  %-22s %5d 个" % (k, len(v)))
    print("  %-22s %5d 个" % ("并集(union)", len(occ["union"])))
    if occ["union"]:
        print("  全局最大 id: %d" % max(occ["union"]))
    print("-- 推荐未占用号段（起于 --base=%d）--" % args.base)
    for i, r in enumerate(rec):
        print("  #%d  %d   (分组目录 %s)" % (i + 1, r, group_dir(r)))
    return 0


def cmd_plan(args):
    plan = make_plan(
        args.game, args.mcz, parent=args.parent, audio_from=args.audio_from,
        title=args.title, reading=args.reading, jacket=args.jacket, diff=args.diff,
        slot=args.slot, level=args.level, music_id=args.music_id,
        hold_policy=args.hold_policy, pkg_which=args.pkg, no_tex=args.no_tex,
        own_audio=(False if args.borrow_audio else (True if args.own_audio else None)),
        preview_ms=args.preview_ms,
        version_mask=args.version_mask, grouping_category=args.grouping_category,
        reading_prefix=args.reading_prefix)
    out = args.out
    if out is None:
        out = os.path.join(default_workdir(), "plans", "plan_%d.json" % plan["music_id"])
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print("== 安装计划（未改动游戏目录）==")
    print("  music_id      : %d   (分组目录 %s)" % (plan["music_id"], group_dir(plan["music_id"])))
    print("  标题 / 难度   : %s / Lv.%s" % (plan["title"], plan["level"]))
    _sp = plan.get("slot_plan") or {}
    if _sp:
        if len({v["diff"] for v in _sp.values()}) == 1:
            print("  谱面分档      : 三档同谱（谱面只含 1 个难度：%s）"
                  % (_sp.get("ext") or {}).get("version"))
        else:
            print("  谱面分档      : BSC=%s(%d)  ADV=%s(%d)  EXT=%s(%d)"
                  % tuple(x for k in ("bsc", "adv", "ext") for x in
                          ((_sp.get(k) or {}).get("version"),
                           (_sp.get(k) or {}).get("notes"))))
    else:
        print("  谱面分档      : 不分派（--slot %s，统一用 --diff %d）"
              % (plan.get("slot"), plan.get("diff")))
    print("  parent_id     : %d   is_hold=%d" % (plan["parent_id"], plan["is_hold"]))
    print("  容器来源曲     : %d" % plan["source_music_id"])
    if plan.get("own_audio"):
        print("  音源          : **谱面自带** —— %s" % plan.get("audio_label"))
        print("                  预览起点 %d ms（长 10.0s）" % plan.get("preview_ms", 0))
    else:
        print("  音源          : 借用 %d 的音频（谱面未提供自带音源）" % plan["source_music_id"])
    print("-- 将新增的文件 --")
    for f in plan["new_files"]:
        print("  + [%s] %s" % (f["kind"], f["path"]))
        print("      %s" % f.get("desc", ""))
    print("-- 将修改的表（仅新增记录）--")
    for c in plan["table_changes"]:
        print("  ~ [%s] %s   %s" % (c["kind"], c["path"], c["detail"]))
    print("-- 将被触碰的既有文件（除上述两张表之外）--")
    extra = [t for t in plan["touched_existing"]
             if t["path"] not in [c["path"] for c in plan["table_changes"]]]
    print("  %s" % ("（无）" if not extra else extra))
    print("  计划已写入：%s" % out)
    print("  安装：python tools/ub_newsong.py install --plan \"%s\" --apply" % out)
    return 0


def cmd_install(args):
    plan = json.load(open(args.plan, encoding="utf-8"))
    res = install(plan, apply=args.apply, workdir=args.workdir)
    if res.get("dry_run"):
        print("== DRY RUN（未写盘）==")
        for s in res["steps"]:
            print("  " + s)
        for p in res["new_files"]:
            print("  + %s" % p)
        print("  加 --apply 才会真正写入")
        return 0
    print("== 安装完成 (stamp=%s) ==" % res["stamp"])
    for p in res["written"]:
        print("  + %s" % p)
    for c in res["table_changes"]:
        print("  ~ [%s] %s  %s" % (c["kind"], c["path"], {k: v for k, v in c.items()
                                                          if k not in ("kind", "path")}))
    d = res.get("details", {})
    if "ifs" in d:
        print("  音频继承: %s" % d["ifs"]["audio_identical"])
    if "tex" in d:
        print("  贴图条目: %s (%s)" % (d["tex"]["entry"], d["tex"]["from"]))
    print("  备份目录: %s" % os.path.join(res.get("workdir", default_workdir()),
                                          "backup", res["stamp"]))
    print("  还原命令: %s" % res["restore_cmd"])
    return 0


def cmd_uninstall(args):
    res = uninstall(args.game, args.music_id, workdir=args.workdir, stamp=args.stamp)
    print("== 卸载 music_id=%d ==" % res["music_id"])
    for p in res["removed"]:
        print("  - %s" % p)
    for p in res["restored"]:
        print("  ~ 还原表 %s" % p)
    g = res.get("group_tex") or {}
    if g.get("removed"):
        print("  ~ 撤销分组图集条目 %s（%s）" % (", ".join(g["removed"]),
                                                os.path.basename(g["atlas"])))
        print("    备份 %s" % g.get("backup"))
    elif g.get("skipped"):
        print("  = 分组图集：%s" % g["skipped"])
    elif g.get("error"):
        print("  !! 分组图集撤销失败：%s" % g["error"])
    print("  已移除状态文件: %s" % res["state_removed"])
    return 0


def cmd_verify(args):
    res = verify(args.game, as_json=args.json)
    return 0 if res["ok"] else 1


def cmd_group_tex(args):
    res = register_group_tex(
        args.game, args.music_id, title=args.title or ("CUSTOM %d" % args.music_id),
        jacket=args.jacket, artist=args.artist, marker=args.marker or "",
        atlas=args.atlas, apply=args.apply, workdir=args.workdir)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print("== 分组图集小贴图注册 music_id=%d ==" % args.music_id)
    print("  目标图集 : %s%s" % (res["atlas_name"],
                                 "" if res.get("atlas_loaded") else "  (!! 未在日志中出现，可能未被加载)"))
    print("  锚点 id  : %s（同段最大但不大于本曲）" % res["anchor"])
    print("  条数     : %d -> %d" % (res["n_before"], res["n_after"]))
    print("  大小     : %d -> %s B" % (res["size_before"], res.get("size_after", "?")))
    for e in res["entries"]:
        print("  + %-24s %-7s %-9s %8d B   %s"
              % (e["entry"], e["size"], e["fmt"], e["bytes"], e["from"]))
    if res["skipped_existing"]:
        print("  = 已存在（跳过）: %s" % ", ".join(res["skipped_existing"]))
    if res["action"] == "dry-run":
        print("  [DRY-RUN] 加 --apply 才真正写入")
    elif res["action"] == "written":
        v = res["verify"]
        print("  已写入：%s" % res["atlas"])
        print("  备份  ：%s" % res["backup"])
        print("  校验  ：n=%d f14=%s(%s) 回读重建字节一致=%s"
              % (v["n"], v["f14_file"], "OK" if v["f14_ok"] else "MISMATCH",
                 v["byte_exact_rebuild"]))
        print("  已含  ：%s" % ", ".join(v["present"]))
        print("  —— 重启游戏后生效")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="ub_newsong.py",
                                description="把自制谱面注册成游戏新曲（只新增，不覆盖）")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("list-ids", help="列出已占用 music_id 并推荐空段")
    a.add_argument("--game", required=True)
    a.add_argument("--base", type=int, default=DEFAULT_BASE)
    a.add_argument("--count", type=int, default=5)
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_list_ids)

    b = sub.add_parser("plan", help="生成安装计划（不改动游戏目录）")
    b.add_argument("--mcz", required=True, help=".mcz 或 .mc")
    b.add_argument("--game", required=True)
    b.add_argument("--parent", type=int, default=None, help="HOLD 谱：基础曲 id")
    b.add_argument("--audio-from", type=int, default=None, help="借音频的曲 id")
    b.add_argument("--title", default=None)
    b.add_argument("--reading", default=None, help="假名读音（name_string）")
    b.add_argument("--reading-prefix", default=None,
                   help="五十音读音前缀（须为 CP932 假名/汉字），如 ジセイ；写进 name_string")
    b.add_argument("--jacket", default=None, help="320x320 曲绘 PNG")
    b.add_argument("--diff", type=int, default=0, help="mcz 中第 N 个难度")
    b.add_argument("--slot", default=None, choices=["bsc", "adv", "ext"],
                   help="只替换该难度成员（默认替换容器内全部 .eve）")
    b.add_argument("--level", type=float, default=None)
    b.add_argument("--music-id", type=int, default=None)
    b.add_argument("--hold-policy", default="split", choices=["split", "straighten"])
    b.add_argument("--pkg", default="base", choices=["base", "overlay", "allomni", "auto"],
                   help="写哪张包表（默认 base=data/d3/package/alldata.bin，游戏用它解析贴图）")
    b.add_argument("--no-tex", action="store_true", help="不新增贴图/包表行")
    g = b.add_mutually_exclusive_group()
    g.add_argument("--own-audio", action="store_true",
                   help="强制使用谱面自带音源（默认：谱面带音源时自动启用）")
    g.add_argument("--borrow-audio", action="store_true",
                   help="强制借用借源曲的音频（默认自动判断）")
    b.add_argument("--preview-ms", type=int, default=None,
                   help="预览起点毫秒（默认取谱面 meta.preview）")
    b.add_argument("--version-mask", default=None,
                   help="覆盖曲表 <version> 位掩码（十六进制字符串，如 4000）；默认沿用蓝本曲")
    b.add_argument("--grouping-category", default=None,
                   help="覆盖曲表 <grouping_category>；默认沿用蓝本曲")
    b.add_argument("--out", default=None)
    b.set_defaults(func=cmd_plan)

    c = sub.add_parser("install", help="执行安装")
    c.add_argument("--plan", required=True)
    c.add_argument("--apply", action="store_true")
    c.add_argument("--workdir", default=None)
    c.set_defaults(func=cmd_install)

    d = sub.add_parser("uninstall", help="卸载并还原到安装前")
    d.add_argument("--music-id", type=int, required=True)
    d.add_argument("--game", required=True)
    d.add_argument("--workdir", default=None)
    d.add_argument("--stamp", default=None)
    d.set_defaults(func=cmd_uninstall)

    e = sub.add_parser("verify", help="校验曲表/包表契约")
    e.add_argument("--game", required=True)
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_verify)

    f = sub.add_parser("group-tex",
                       help="补注册小曲绘/曲名条（BNR_ID/IDX_ID/IDX_MINI_ID）到已加载的分组图集")
    f.add_argument("--game", required=True)
    f.add_argument("--music-id", type=int, required=True)
    f.add_argument("--title", default=None, help="曲名（默认 CUSTOM <id>）")
    f.add_argument("--artist", default=None, help="艺人/作者（仅画进 IDX_ID 第二行）")
    f.add_argument("--marker", default="", help="曲名**前缀**标记，如 [自制]（渲染为「[自制] 曲名」）")
    f.add_argument("--jacket", default=None,
                   help="小曲绘 PNG（160x160）；省略则自动从本曲大曲绘缩放派生，再不行才用占位图")
    f.add_argument("--atlas", default=None, help="强制指定目标图集文件名")
    f.add_argument("--apply", action="store_true")
    f.add_argument("--workdir", default=None, help="侧车记录目录（uninstall 用它精确撤销）")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=cmd_group_tex)
    return p


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as ex:
        print("ERROR: %s" % ex, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
