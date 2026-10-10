#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ub_version.py -- jubeat「版本分类」（theme_info）注册表读写

背景（实测）：
  * 版本/主题注册表 = `<游戏根>/data/theme_info/theme_info.xml`
    - SHIFT_JIS、单引号属性、每块字段各占一行
    - 字段：theme_id(s8) / pos_index(s8) / theme_type(u16) / theme_version(u16) / name_j(str)
    - 本机现状：49 块（块 id 1..49），pos_index 0..48，theme_version 0..10（9 被跳过）
      最后一位 pos_index=48 是「アベニュー」= beyond the Ave.（theme_id=49）
    - 头部 <data_num> 写的是 20，与 49 块**不一致**（官方原样如此）→ 默认**保持不动**，
      避免猜测语义；如确认游戏按 data_num 截断，可用 --set-data-num 显式改。
  * 曲表里每首歌的 `<version>` 是 16 位十六进制位掩码（本机 1613 首共 18 种取值，
    bits 0..15 全部被占用；bits 0..13 对应 14 个正式 AC 版本，bit13=0x2000 即
    beyond the Ave.）。
  * 分组图集（tex_l44*_bnr_j_<grp>_<n>.bin）的**加载名单由游戏固定**（本机 55 个），
    实测 tex_l44av_bnr_j_11_019/020 虽在包表与 mod 目录里也从未被加载 →
    **新建分组图集不会被加载**，贴图仍必须放进已加载的图集里。

本工具只做「注册表」层面的增删，**不碰任何贴图/包表**，并自动备份，可一键回退。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time

ENCODING = "shift_jis"
BLOCK_RE = re.compile(r"<data __type='void' id='\d+'>.*?</data>\n", re.S)


# ---------------------------------------------------------------------------
def theme_info_path(game: str) -> str:
    return os.path.join(os.path.abspath(game), "data", "theme_info", "theme_info.xml")


def _read_raw(path: str) -> str:
    with open(path, "rb") as f:
        return f.read().decode(ENCODING, errors="replace")


def _g(block: str, tag: str) -> str:
    m = re.search(r"<%s __type='[^']*'>\s*([^<]*?)\s*</%s>" % (tag, tag), block, re.S)
    return m.group(1) if m else ""


def read_themes(game_or_path: str) -> list:
    path = game_or_path if game_or_path.lower().endswith(".xml") else theme_info_path(game_or_path)
    txt = _read_raw(path)
    out = []
    for m in BLOCK_RE.finditer(txt):
        blk = m.group(0)
        out.append({
            "block_id": int(re.search(r"id='(\d+)'", blk).group(1)),
            "theme_id": int(_g(blk, "theme_id") or -1),
            "pos_index": int(_g(blk, "pos_index") or -1),
            "theme_type": int(_g(blk, "theme_type") or 0),
            "theme_version": int(_g(blk, "theme_version") or 0),
            "name_j": _g(blk, "name_j"),
            "raw": blk,
        })
    out.sort(key=lambda d: d["pos_index"])
    return out


def next_theme_slots(themes: list) -> dict:
    """给出「紧接着最后一位」的新槽位建议值。"""
    return {
        "block_id": max(t["block_id"] for t in themes) + 1,
        "theme_id": max(t["theme_id"] for t in themes) + 1,
        "pos_index": max(t["pos_index"] for t in themes) + 1,
        "theme_version": max(t["theme_version"] for t in themes) + 1,
    }


def _make_block(block_id, theme_id, pos_index, theme_type, theme_version, name_j) -> str:
    name_j.encode(ENCODING)          # 提前校验可编码
    return (
        "<data __type='void' id='%d'>\n"
        "<theme_id __type='s8'>\n%d\n</theme_id>\n"
        "<pos_index __type='s8'>\n%d\n</pos_index>\n"
        "<theme_type __type='u16'>\n%d\n</theme_type>\n"
        "<theme_version __type='u16'>\n%d\n</theme_version>\n"
        "<name_j __type='str'>\n%s\n</name_j>\n"
        "</data>\n" % (block_id, theme_id, pos_index, theme_type, theme_version, name_j)
    )


def _backup(path: str) -> str:
    bak = path + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, bak)
    return bak


# ---------------------------------------------------------------------------
def add_theme(game: str, name_j: str, theme_version=None, theme_id=None,
              pos_index=None, theme_type=0, apply=False, set_data_num=False) -> dict:
    """在 theme_info.xml 末尾追加一条版本/主题（按 name_j 幂等）。"""
    path = theme_info_path(game)
    if not os.path.exists(path):
        raise FileNotFoundError("找不到 theme_info.xml：%s" % path)
    themes = read_themes(path)
    slots = next_theme_slots(themes)

    for t in themes:
        if t["name_j"] == name_j:
            return {"action": "exists", "path": path, "name_j": name_j,
                    "theme_version": t["theme_version"], "pos_index": t["pos_index"],
                    "theme_id": t["theme_id"]}

    tid = int(theme_id if theme_id is not None else slots["theme_id"])
    pos = int(pos_index if pos_index is not None else slots["pos_index"])
    tver = int(theme_version if theme_version is not None else slots["theme_version"])

    for t in themes:
        if t["theme_id"] == tid:
            raise ValueError("theme_id=%d 已被「%s」占用" % (tid, t["name_j"]))
        if t["pos_index"] == pos:
            raise ValueError("pos_index=%d 已被「%s」占用" % (pos, t["name_j"]))

    blk = _make_block(slots["block_id"], tid, pos, int(theme_type), tver, name_j)
    txt = _read_raw(path)
    if "</body>" not in txt:
        raise ValueError("theme_info.xml 结构异常：缺少 </body>")
    new_txt = txt.replace("</body>", blk + "</body>", 1)

    info = {"action": "dry-run", "path": path, "name_j": name_j,
            "theme_id": tid, "pos_index": pos, "theme_version": tver,
            "theme_type": theme_type, "block_id": slots["block_id"],
            "n_before": len(themes), "n_after": len(themes) + 1,
            "data_num_before": _data_num(txt)}
    if set_data_num:
        new_txt = _set_data_num(new_txt, len(themes) + 1)
        info["data_num_after"] = len(themes) + 1
    if not apply:
        return info

    info["backup"] = _backup(path)
    with open(path, "wb") as f:
        f.write(new_txt.encode(ENCODING))
    back = read_themes(path)
    info["action"] = "written"
    info["verify"] = {
        "n": len(back),
        "present": [t["name_j"] for t in back if t["name_j"] == name_j],
        "max_pos_index": max(t["pos_index"] for t in back),
        "max_theme_id": max(t["theme_id"] for t in back),
        "data_num": _data_num(_read_raw(path)),
    }
    return info


def remove_theme(game: str, name_j: str, apply=False) -> dict:
    path = theme_info_path(game)
    txt = _read_raw(path)
    themes = read_themes(path)
    hit = [t for t in themes if t["name_j"] == name_j]
    if not hit:
        return {"action": "not-found", "path": path, "name_j": name_j}
    new_txt = txt
    for t in hit:
        new_txt = new_txt.replace(t["raw"], "", 1)
    info = {"action": "dry-run", "path": path, "name_j": name_j,
            "removed": len(hit), "n_before": len(themes),
            "n_after": len(read_themes_raw(new_txt))}
    if not apply:
        return info
    info["backup"] = _backup(path)
    with open(path, "wb") as f:
        f.write(new_txt.encode(ENCODING))
    info["action"] = "written"
    info["verify"] = {"n": len(read_themes(path))}
    return info


def read_themes_raw(txt: str) -> list:
    return [{"name_j": _g(m.group(0), "name_j")} for m in BLOCK_RE.finditer(txt)]


def _data_num(txt: str) -> int:
    m = re.search(r"<data_num __type='s32'>\s*(\d+)\s*</data_num>", txt)
    return int(m.group(1)) if m else -1


def _set_data_num(txt: str, n: int) -> str:
    return re.sub(r"(<data_num __type='s32'>\s*)\d+(\s*</data_num>)",
                  lambda m: m.group(1) + str(n) + m.group(2), txt, count=1)


def restore(path: str, backup: str) -> str:
    shutil.copy2(backup, path)
    return path


# ---------------------------------------------------------------------------
def cmd_list(a):
    themes = read_themes(a.game)
    path = theme_info_path(a.game)
    txt = _read_raw(path)
    print("theme_info : %s" % path)
    print("块数        : %d   data_num=%d" % (len(themes), _data_num(txt)))
    print("%5s %5s %5s %6s %6s  %s" % ("bid", "tid", "pos", "type", "tver", "name_j"))
    for t in themes:
        print("%5d %5d %5d %6d %6d  %s" % (t["block_id"], t["theme_id"], t["pos_index"],
                                           t["theme_type"], t["theme_version"], t["name_j"]))
    print()
    print("下一个可用槽位:", next_theme_slots(themes))
    return 0


def cmd_add(a):
    r = add_theme(a.game, a.name, theme_version=a.theme_version, theme_id=a.theme_id,
                  pos_index=a.pos_index, theme_type=a.theme_type, apply=a.apply,
                  set_data_num=a.set_data_num)
    print("== 新增版本/主题 ==")
    for k in ("action", "name_j", "theme_id", "pos_index", "theme_version",
              "theme_type", "block_id", "n_before", "n_after",
              "data_num_before", "data_num_after", "backup"):
        if k in r:
            print("  %-16s %s" % (k, r[k]))
    if r.get("verify"):
        print("  校验            %s" % r["verify"])
    if r["action"] == "dry-run":
        print("  [DRY-RUN] 加 --apply 才写盘")
    return 0


def cmd_remove(a):
    r = remove_theme(a.game, a.name, apply=a.apply)
    print("== 移除版本/主题 ==")
    for k in ("action", "name_j", "removed", "n_before", "n_after", "backup"):
        if k in r:
            print("  %-16s %s" % (k, r[k]))
    if r["action"] == "dry-run":
        print("  [DRY-RUN] 加 --apply 才写盘")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="ub_version.py",
                                description="jubeat 版本分类（theme_info）注册表读写")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("list", help="列出所有版本/主题")
    a.add_argument("--game", required=True)
    a.set_defaults(func=cmd_list)

    b = sub.add_parser("add", help="新增一档版本/主题（默认追加到最后一位）")
    b.add_argument("--game", required=True)
    b.add_argument("--name", required=True, help="显示名（SHIFT_JIS 可编码，如 自作譜）")
    b.add_argument("--theme-version", type=int, default=None)
    b.add_argument("--theme-id", type=int, default=None)
    b.add_argument("--pos-index", type=int, default=None)
    b.add_argument("--theme-type", type=int, default=0)
    b.add_argument("--set-data-num", action="store_true",
                   help="同时把头部 data_num 改成新的块数（默认不动）")
    b.add_argument("--apply", action="store_true")
    b.set_defaults(func=cmd_add)

    c = sub.add_parser("remove", help="按显示名移除（回退用）")
    c.add_argument("--game", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--apply", action="store_true")
    c.set_defaults(func=cmd_remove)
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
