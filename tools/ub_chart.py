#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ub_chart.py -- Malody(.mc/.mcz) -> jubeat(.eve) -> _msc.ifs 转换与容器读写工具

Phase 1：只处理谱面，不改动音频成员。

三个 CLI 子命令：
    inspect --ifs <file>                                 列出成员 + dump 谱面前若干行
    convert  --mcz <file> --ifs <file> --out <file>      转换并回写谱面成员
             [--diff N] [--slot ext|adv|bsc] [--member NAME] [--hold-policy split|straighten]
    verify   --ifs <file>                                 自检容器头部 / 成员表 / 长度

同时导出可被 GUI 直接调用的函数：
    open_ifs / inspect_ifs / verify_ifs
    parse_malody_ifs / parse_mcz / parse_mc
    chart_to_eve
    convert(mcz_path, ifs_path, out_path, ...)

依赖：仅 stdlib + kbinxml（随 `pip install ifstools` 一起安装）。
容器格式（实测反解，非猜测）：
    0x00  u32 签名 0x6CAD8F89
    0x04  u16 flags            (bit0=有时间戳, bit1=有 MD5) -> 实测恒为 3
    0x06  u16 flags ^ 0xFFFF   (恒为 0xFFFC，随 flags 变)
    0x08  u32 unix 时间戳
    0x0C  u32 manifest.mem_size （kbinxml 内存尺寸，参与结构，需重算）
    0x10  u32 manifest_end      （=数据区起点，随 manifest 长度变，16 字节对齐）
    0x14  16B md5(manifest 区)  （manifest_md5，需重算）
    0x24  ...  kbinxml 二进制 XML 清单（"imgfs" 树：_info_ + 每个成员一个节点）
    manifest_end ...  数据区（成员数据，成员之间 16 字节对齐）
清单中成员节点形如 <bsc_Eeve __type="3s32">offset size time</bsc_Eeve>，
名字用 Node 转义（'_E'->'.', '_A'->' ', ...）。_info_ 节点保存
md5(data区) 与 len(data区)。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # kbinxml 是 ifstools 的依赖；缺失时给出明确错误
    from kbinxml import KBinXML
except Exception:  # pragma: no cover
    KBinXML = None

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
SIG = 0x6CAD8F89
HEADER_SIZE = 0x24
DEFAULT_MANIFEST_END = 0xD0  # 旧版/多成员包；仅用于说明，实际值读 0x10
FLAG_MANIFEST_HAS_TIMESTAMPS = 1
FLAG_HAS_MD5 = 2
FLAG_UNCOMPRESSED_MANIFEST = 4

# eve 命令（值即排序用的次键，来自 jubeatools Command 枚举）
CMD_END, CMD_MEASURE, CMD_HAKU, CMD_PLAY, CMD_LONG, CMD_TEMPO = 1, 2, 3, 4, 5, 6
CMD_NAME = {CMD_END: "END", CMD_MEASURE: "MEASURE", CMD_HAKU: "HAKU",
            CMD_PLAY: "PLAY", CMD_LONG: "LONG", CMD_TEMPO: "TEMPO"}

# jubeat LONG 尾巴方向 -> value 位域（jubeatools DIRECTION_TO_VALUE）
#   0=DOWN 1=UP 2=RIGHT 3=LEFT
DIR_DOWN, DIR_UP, DIR_RIGHT, DIR_LEFT = 0, 1, 2, 3
# 外推向量（注意 song.py 的 y 轴向下；命名沿用 jubeatools，内部自洽）
OUTWARD_VECTOR = {DIR_UP: (0, 1), DIR_DOWN: (0, -1), DIR_LEFT: (1, 0), DIR_RIGHT: (-1, 0)}

# 名字转义表（ifstools Node.escapes）
_ESCAPES = {' ': '_A', '$': '_B', '+': '_C', '-': '_D', '.': '_E',
            ':': '_F', '@': '_G', '~': '_H', '_': '__'}
_FIX = {v[1]: k for k, v in _ESCAPES.items()}
_SPECIAL_NAMES = ('_info_', '_super_')


def fix_name(n: str) -> str:
    """把清单里的转义名还原为真实成员名（bsc_Eeve -> bsc.eve）。"""
    if n in _SPECIAL_NAMES:
        return n
    out = n
    start = 0
    while True:
        idx = out.find('_', start)
        if idx == -1:
            break
        key = out[idx + 1]
        if idx == 0 and key.isdigit():
            new = key
        else:
            new = _FIX.get(key)
        if new is None:
            raise ValueError("非法转义成员名: %r" % n)
        out = out[:idx] + new + out[idx + 2:]
        start = idx + 1
    return out


# ----------------------------------------------------------------------------
# 小节/拍 -> 分数拍
# ----------------------------------------------------------------------------
def beat_to_fraction(b: Sequence[int]) -> Fraction:
    """Malody beat=[小节, 拍, 分母]  ->  总拍数（Fraction）。

    与 jubeatools 一致：b[0] + b[1]/b[2]。
    """
    if not isinstance(b, (list, tuple)) or len(b) != 3:
        raise ValueError("beat 字段必须是长度 3 的数组: %r" % (b,))
    return Fraction(int(b[0])) + Fraction(int(b[1]), int(b[2]))


def _to_fraction(v) -> Fraction:
    if isinstance(v, Fraction):
        return v
    if isinstance(v, Decimal):
        return Fraction(v)
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        return Fraction(Decimal(str(v)))
    return Fraction(v)


# ----------------------------------------------------------------------------
# 时间映射（与 jubeatools TimeMap.from_beats 等价）
# ----------------------------------------------------------------------------
class TimeMap:
    """拍(fractions of beats) -> 秒 的映射。ticks = round(秒 * 300)。"""

    def __init__(self, bpm_at_beat: Sequence[Tuple[Fraction, Fraction]],
                 offset_seconds: Fraction = Fraction(0),
                 offset_beats: Fraction = Fraction(0)):
        if not bpm_at_beat:
            raise ValueError("时间信息为空：至少需要一个 BPM 事件")
        evs = sorted(((_to_fraction(b), _to_fraction(bpm)) for b, bpm in bpm_at_beat),
                     key=lambda x: x[0])
        for i in range(1, len(evs)):
            if evs[i][0] == evs[i - 1][0]:
                raise ValueError("同一拍上出现多个 BPM: %s" % (evs[i][0],))
        # 先假设第一个事件发生在 0 秒
        self.changes: List[Tuple[Fraction, Fraction, Fraction]] = []  # (beat, seconds, bpm)
        cur = Fraction(0)
        fb, fbpm = evs[0]
        self.changes.append((fb, cur, fbpm))
        for (pb, pbpm), (cb, cbpm) in zip(evs, evs[1:]):
            cur += Fraction(60) * (cb - pb) / pbpm
            self.changes.append((cb, cur, cbpm))
        # 应用偏移
        unshifted = self._raw_seconds_at(_to_fraction(offset_beats))
        shift = _to_fraction(offset_seconds) - unshifted
        self.changes = [(b, s + shift, bpm) for (b, s, bpm) in self.changes]
        self.changes.sort(key=lambda x: x[0])

    def _raw_seconds_at(self, beat: Fraction) -> Fraction:
        prev = self.changes[0]
        for ch in self.changes:
            if ch[0] <= beat:
                prev = ch
            else:
                break
        b, s, bpm = prev
        return s + Fraction(60) * (beat - b) / bpm

    def seconds_at(self, beat: Fraction) -> Fraction:
        return self._raw_seconds_at(_to_fraction(beat))

    def ticks_at(self, beat: Fraction) -> int:
        # Fraction.__round__ 为 banker's rounding，与 jubeatools 的 round() 行为一致
        return round(self.seconds_at(beat) * 300)


# ----------------------------------------------------------------------------
# _msc.ifs 容器
# ----------------------------------------------------------------------------
@dataclass
class Member:
    packed_name: str          # 清单里的转义名，如 bsc_Eeve
    name: str                 # 还原名，如 bsc.eve
    offset: int               # 相对数据区的偏移
    size: int
    time: Optional[int]
    elem: Any                 # lxml 元素（就地改写）
    data: bytes               # 原始字节；convert 时替换

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()


class MiscIfs:
    """jubeat `_msc.ifs` 容器（Konami IFS / kbinxml 清单）。

    忠实重打包：未修改时逐字节还原原文件（见 verify 报告）。
    """

    def __init__(self, path: Optional[str] = None, data: Optional[bytes] = None):
        if KBinXML is None:
            raise RuntimeError("缺少 kbinxml 库，请先执行: pip install ifstools")
        if path is not None:
            with open(path, "rb") as f:
                data = f.read()
        if data is None:
            raise ValueError("必须提供 path 或 data")
        self.raw = data
        self._parse(data)

    # ---- 解析 ----
    def _parse(self, raw: bytes) -> None:
        if len(raw) < HEADER_SIZE:
            raise ValueError("文件过短，不是 IFS 容器")
        self.signature = struct.unpack_from(">I", raw, 0x00)[0]
        if self.signature != SIG:
            raise ValueError("签名不符：0x%08X != 0x%08X" % (self.signature, SIG))
        self.flags = struct.unpack_from(">H", raw, 0x04)[0]
        self.flags_not = struct.unpack_from(">H", raw, 0x06)[0]
        self.time = struct.unpack_from(">I", raw, 0x08)[0]
        self.tree_size_field = struct.unpack_from(">I", raw, 0x0C)[0]
        self.manifest_end = struct.unpack_from(">I", raw, 0x10)[0]
        self.manifest_md5 = raw[0x14:0x24]
        if not (HEADER_SIZE <= self.manifest_end <= len(raw)):
            raise ValueError("manifest_end 越界: 0x%X (file=0x%X)" % (self.manifest_end, len(raw)))
        manifest_raw = raw[HEADER_SIZE:self.manifest_end]
        self._manifest_raw = manifest_raw
        self.manifest_encoding = manifest_raw[2] if len(manifest_raw) > 3 else 0x80
        self._kb = KBinXML(manifest_raw)
        self.root = self._kb.xml_doc
        if self.root.tag != "imgfs":
            raise ValueError("清单根节点不是 imgfs: %r" % self.root.tag)
        self.data = raw[self.manifest_end:]
        self.members: List[Member] = []
        self._collect(self.root)
        for m in self.members:
            if m.offset < 0 or m.offset + m.size > len(self.data):
                raise ValueError("成员 %s 越界: off=0x%X size=%d data=0x%X"
                                 % (m.name, m.offset, m.size, len(self.data)))
        self._tail = self._compute_tail()

    def _collect(self, elem) -> None:
        for child in elem.iterchildren():
            t = child.get("__type")
            if t in ("2s32", "3s32"):
                parts = [int(x) for x in (child.text or "").split()]
                if len(parts) == 2:
                    off, size = parts
                    tm = None
                elif len(parts) == 3:
                    off, size, tm = parts
                else:
                    raise ValueError("成员节点字段数异常: %r" % child.text)
                nm = fix_name(child.tag)
                self.members.append(Member(
                    packed_name=child.tag, name=nm, offset=off, size=size, time=tm,
                    elem=child, data=self.data[off:off + size]))
            elif t is None:
                # 文件夹 或 _info_ / _super_
                self._collect(child)

    def _compute_tail(self) -> bytes:
        """数据区中位于所有成员（含 16 字节对齐填充）之后的残余字节。

        正常包没有残余；这里仅在确有残余时原样保留，避免误删。
        """
        end = 0
        for m in sorted(self.members, key=lambda x: x.offset):
            end = m.offset + m.size
            end += (-m.size) % 16
        if end < len(self.data):
            return self.data[end:]
        return b""

    # ---- 便捷访问 ----
    def get_member(self, name: str) -> Optional[Member]:
        low = name.lower()
        for m in self.members:
            if m.name.lower() == low:
                return m
        return None

    def audio_members(self) -> List[Member]:
        out = []
        for m in self.members:
            if m.ext in (".bin", ".bmp") or m.data[:4] == b"BMP\x00":
                out.append(m)
        return out

    def chart_members(self) -> List[Member]:
        return [m for m in self.members if m.ext == ".eve"]

    # ---- 重打包（忠实） ----
    def to_bytes(self, set_time: Optional[int] = None) -> bytes:
        blob = bytearray()
        for m in self.members:
            data = m.data
            m.offset = len(blob)
            m.size = len(data)
            if m.time is not None:
                m.elem.text = "%d %d %d" % (m.offset, m.size, m.time)
            else:
                m.elem.text = "%d %d" % (m.offset, m.size)
            blob += data
            pad = (-len(data)) % 16
            if pad:
                blob += b"\x00" * pad
        # 保留原数据区末尾多出来的残余字节（正常包为空）
        blob += self._tail

        # 更新 _info_
        info = self.root.find("_info_")
        if info is not None:
            md5e = info.find("md5")
            sizee = info.find("size")
            if md5e is not None:
                md5e.text = hashlib.md5(bytes(blob)).hexdigest()
            if sizee is not None:
                sizee.text = str(len(blob))

        # 重新编码清单
        man = bytearray(self._kb.to_binary(compressed=True))
        man[2] = self.manifest_encoding
        man[3] = 0xFF ^ self.manifest_encoding
        pad = (-(HEADER_SIZE + len(man))) % 16
        if pad:
            man += b"\x00" * pad
        mem_size = self._kb.mem_size
        manifest_end = HEADER_SIZE + len(man)

        head = bytearray(HEADER_SIZE)
        struct.pack_into(">I", head, 0x00, SIG)
        struct.pack_into(">H", head, 0x04, self.flags)
        struct.pack_into(">H", head, 0x06, self.flags ^ 0xFFFF)
        struct.pack_into(">I", head, 0x08, self.time if set_time is None else set_time)
        struct.pack_into(">I", head, 0x0C, mem_size)
        struct.pack_into(">I", head, 0x10, manifest_end)
        head[0x14:0x24] = hashlib.md5(bytes(man)).digest()
        return bytes(head) + bytes(man) + bytes(blob)

    def save(self, path: str, set_time: Optional[int] = None) -> None:
        with open(path, "wb") as f:
            f.write(self.to_bytes(set_time=set_time))


# ----------------------------------------------------------------------------
# eve 文本
# ----------------------------------------------------------------------------
def _dump_event(t: int, cmd: int, value: int) -> str:
    return "%8d,%-8s,%8d" % (t, CMD_NAME[cmd], value)


def parse_eve(text: bytes) -> List[Tuple[int, int, int]]:
    """把 eve 文本解析成 [(ticks, cmd, value)]（用于回读校验）。"""
    name2cmd = {v: k for k, v in CMD_NAME.items()}
    events = []
    for line in text.replace(b"\r\n", b"\n").split(b"\n"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(b",")]
        if len(parts) < 3:
            continue
        try:
            t = int(parts[0])
        except ValueError:
            continue
        cmd = name2cmd.get(parts[1].decode("ascii", "replace"))
        if cmd is None:
            continue
        events.append((t, cmd, int(parts[2])))
    return events


# ----------------------------------------------------------------------------
# Malody 解析
# ----------------------------------------------------------------------------
@dataclass
class MNote:
    time: Fraction
    index: int
    end_time: Optional[Fraction] = None
    end_index: Optional[int] = None

    @property
    def is_long(self) -> bool:
        return self.end_time is not None


@dataclass
class MChart:
    title: str
    artist: str
    version: str          # 难度名（meta.version）
    mode: int
    bpm_at_beat: List[Tuple[Fraction, Fraction]]
    notes: List[MNote]
    bgm_offset_ms: int = 0
    bgm_beat: Fraction = Fraction(0)
    source: str = ""
    preview_ms: Optional[int] = None    # meta.preview（毫秒）= 官方预览起点
    background: str = ""                # meta.background（曲绘文件名）
    audio_hint: str = ""                # meta.song.file（若谱面显式给出音源文件名）


def _load_json(text: str) -> dict:
    # parse_float=Decimal：避免 108.09 之类的浮点误差（与 jubeatools 的 simplejson 一致）
    return json.loads(text, parse_float=Decimal)


def parse_mc(raw: bytes, source: str = "") -> MChart:
    """解析单个 .mc（JSON）。mode 必须为 4 (Pad = jubeat)。"""
    try:
        doc = _load_json(raw.decode("utf-8-sig"))
    except Exception as e:
        raise ValueError("无法解析 .mc JSON (%s): %s" % (source, e))
    if not isinstance(doc, dict):
        raise ValueError("非法 .mc：顶层不是对象 (%s)" % source)
    meta = doc.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("非法 .mc：缺少 meta 字段 (%s)" % source)
    mode = meta.get("mode")
    if mode is None:
        raise ValueError("非法 .mc：meta.mode 缺失 (%s)" % source)
    if int(mode) != 4:
        raise ValueError("不是 Malody Pad(jubeat) 谱面：meta.mode=%s（应为 4）(%s)" % (mode, source))

    song = meta.get("song") or {}
    title = song.get("title") or ""
    artist = song.get("artist") or ""
    version = meta.get("version") or ""
    preview_ms = None
    if meta.get("preview") is not None:
        try:
            preview_ms = int(meta["preview"])
        except Exception:
            preview_ms = None
    background = meta.get("background") or ""
    audio_hint = song.get("file") or song.get("audio") or meta.get("file") or ""

    tinfo = doc.get("time") or []
    if not isinstance(tinfo, list) or not tinfo:
        raise ValueError("非法 .mc：time 为空（至少需要一个 BPM）(%s)" % source)
    bpm_at_beat = []
    for ev in tinfo:
        if not isinstance(ev, dict) or "beat" not in ev or "bpm" not in ev:
            raise ValueError("非法 .mc：time 事件缺字段 (%s)" % source)
        bpm_at_beat.append((beat_to_fraction(ev["beat"]), _to_fraction(ev["bpm"])))

    notes: List[MNote] = []
    bgm_offset = 0
    bgm_beat = Fraction(0)
    found_bgm = False
    for ev in (doc.get("note") or []):
        if not isinstance(ev, dict):
            continue
        if "sound" in ev:  # 音效/BGM 事件
            if int(ev.get("type", 0)) == 1 and not found_bgm:
                bgm_offset = int(ev.get("offset") or 0)
                bgm_beat = beat_to_fraction(ev["beat"]) if "beat" in ev else Fraction(0)
                found_bgm = True
            continue
        if "beat" not in ev or "index" not in ev:
            continue
        idx = int(ev["index"])
        if not 0 <= idx <= 15:
            raise ValueError("非法 .mc：index 越界 %d (%s)" % (idx, source))
        t = beat_to_fraction(ev["beat"])
        if "endbeat" in ev and ev.get("endbeat") is not None:
            ei = int(ev.get("endindex", idx))
            if not 0 <= ei <= 15:
                raise ValueError("非法 .mc：endindex 越界 %d (%s)" % (ei, source))
            notes.append(MNote(time=t, index=idx,
                               end_time=beat_to_fraction(ev["endbeat"]), end_index=ei))
        else:
            notes.append(MNote(time=t, index=idx))

    return MChart(title=title, artist=artist, version=version, mode=int(mode),
                  bpm_at_beat=bpm_at_beat, notes=notes,
                  bgm_offset_ms=bgm_offset, bgm_beat=bgm_beat, source=source,
                  preview_ms=preview_ms, background=background,
                  audio_hint=audio_hint)


def parse_mcz(path: str) -> List[Tuple[str, bytes]]:
    """从 .mcz(ZIP) 取出所有 .mc。返回 [(内部路径, 字节)]，按路径排序。

    兼容三种命名：
      1) 明确的 `*.mc`（jubeatools 等工具导出）
      2) Malody 原生包里的 `0/mc`（无扩展名，basename 恰为 "mc"）
      3) 兜底：内容像 JSON（以 '{' 开头且含 "note"/"meta"）
    """
    if not zipfile_is_zip(path):
        # 直接当 .mc 处理
        with open(path, "rb") as f:
            return [(os.path.basename(path), f.read())]
    out: List[Tuple[str, bytes]] = []
    with zipfile.ZipFile(path) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        picked = [i for i in infos if i.filename.lower().endswith(".mc")]
        if not picked:
            picked = [i for i in infos
                      if os.path.basename(i.filename).lower() == "mc"]
        if not picked:
            sniffed = []
            for i in infos:
                try:
                    head = z.read(i)[:4096]
                except Exception:
                    continue
                s = head.lstrip(b"\xef\xbb\xbf \t\r\n")
                if s[:1] == b"{" and (b'"note"' in head or b'"meta"' in head):
                    sniffed.append(i)
            picked = sniffed
        for info in picked:
            out.append((info.filename, z.read(info)))
    out.sort(key=lambda x: x[0])
    if not out:
        raise ValueError(".mcz 内没有找到任何 .mc 文件：%s" % path)
    return out


def zipfile_is_zip(path: str) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except Exception:
        return False


def parse_malody(path: str) -> List[MChart]:
    """读取 .mcz 或 .mc，返回其中每一个难度谱面。"""
    charts = []
    for name, raw in parse_mcz(path):
        charts.append(parse_mc(raw, source=name))
    return charts


# ----------------------------------------------------------------------------
# 转换：Malody -> eve
# ----------------------------------------------------------------------------
@dataclass
class ConvertStats:
    notes_in: int = 0
    taps: int = 0
    longs_total: int = 0
    longs_kept: int = 0
    holds_split: int = 0          # split 策略下降级为 tap 的 hold 数
    holds_straightened: int = 0   # straighten 策略下被强行拉直的 hold 数
    holds_zero_length: int = 0    # 起点==终点，退化为 tap
    duplicates: int = 0
    end_tick: int = 0
    eve_bytes: int = 0
    duration_ticks: int = 0
    policy: str = "split"

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _long_value(duration_ticks: int, length: int, direction: int, position: int) -> int:
    return (duration_ticks << 8) + (length << 6) + (direction << 4) + position


def _clamp_len(start_idx: int, direction: int, want: int) -> int:
    sx, sy = start_idx % 4, start_idx // 4
    vx, vy = OUTWARD_VECTOR[direction]
    limit = 3
    if vx > 0:
        limit = 3 - sx
    elif vx < 0:
        limit = sx
    elif vy > 0:
        limit = 3 - sy
    elif vy < 0:
        limit = sy
    return max(0, min(want, limit, 3))


def chart_to_eve(chart: MChart, hold_policy: str = "split",
                 line_sep: str = "\r\n", trailing_sep: bool = True,
                 apply_bgm_offset: bool = False,
                 bpm_round_snap: int = 0) -> Tuple[bytes, ConvertStats]:
    """把 MChart 转成 eve 文本字节。

    hold_policy:
      'split'      -- 所有 hold 都降级为头部的 tap（安全，默认）
      'straighten' -- 直线且长度 1~3 的 hold 保留为 LONG；斜线/超长的强行拉直
    """
    if hold_policy not in ("split", "straighten"):
        raise ValueError("未知 hold 策略: %r" % hold_policy)
    st = ConvertStats(policy=hold_policy)

    offset_seconds = Fraction(0)
    offset_beats = Fraction(0)
    if apply_bgm_offset:
        offset_seconds = Fraction(-chart.bgm_offset_ms, 1000)
        offset_beats = chart.bgm_beat

    tmap = TimeMap(chart.bpm_at_beat, offset_seconds, offset_beats)

    events: List[Tuple[int, int, int]] = []

    # --- 音符事件 ---
    seen = set()
    last_note_beat = Fraction(0)
    for n in chart.notes:
        st.notes_in += 1
        last_note_beat = max(last_note_beat, n.time, n.end_time if n.end_time else n.time)
        ticks = tmap.ticks_at(n.time)
        if not n.is_long:
            st.taps += 1
            key = (ticks, CMD_PLAY, n.index)
            if key in seen:
                st.duplicates += 1
            seen.add(key)
            events.append(key)
            continue

        st.longs_total += 1
        dur = tmap.ticks_at(n.end_time) - ticks
        sx, sy = n.index % 4, n.index // 4
        ex, ey = n.end_index % 4, n.end_index // 4

        if n.index == n.end_index or dur <= 0:
            st.holds_zero_length += 1
            st.taps += 1
            events.append((ticks, CMD_PLAY, n.index))
            continue

        straight = (sx == ex) or (sy == ey)
        length = None
        direction = None
        if straight:
            if sx == ex:
                dy = ey - sy
                direction = DIR_UP if dy > 0 else DIR_DOWN
                length = abs(dy)
            else:
                dx = ex - sx
                direction = DIR_LEFT if dx > 0 else DIR_RIGHT
                length = abs(dx)

        if hold_policy == "split":
            st.holds_split += 1
            st.taps += 1
            events.append((ticks, CMD_PLAY, n.index))
            continue

        # straighten 策略
        if not (straight and 1 <= length <= 3 and
                _clamp_len(n.index, direction, length) == length):
            # 需要拉直 / 截断
            st.holds_straightened += 1
            dx, dy = ex - sx, ey - sy
            if abs(dx) >= abs(dy):
                direction = DIR_LEFT if dx > 0 else DIR_RIGHT
                want = abs(dx) if dx != 0 else 1
            else:
                direction = DIR_UP if dy > 0 else DIR_DOWN
                want = abs(dy) if dy != 0 else 1
            length = _clamp_len(n.index, direction, want)
            if length <= 0:
                # 无法构成合法尾巴（贴边），退化为 tap
                st.taps += 1
                st.holds_zero_length += 1
                events.append((ticks, CMD_PLAY, n.index))
                continue
        else:
            st.longs_kept += 1

        events.append((ticks, CMD_LONG,
                       _long_value(dur, length, direction, n.index)))

    # --- 时间线事件 ---
    if bpm_round_snap > 0:
        def snap(fr: Fraction) -> Fraction:
            k = bpm_round_snap
            return Fraction(round(fr * k), k)
        bpm_evs = sorted({(snap(b), bpm) for b, bpm in chart.bpm_at_beat},
                         key=lambda x: x[0])
    else:
        bpm_evs = sorted(chart.bpm_at_beat, key=lambda x: x[0])

    for beat, bpm in bpm_evs:
        val = math.floor(Fraction(6 * 10 ** 7) / _to_fraction(bpm))
        if val == 0:
            raise ValueError("BPM 过大导致 TEMPO 值为 0: %s" % bpm)
        events.append((tmap.ticks_at(beat), CMD_TEMPO, val))

    # END：最后一个音符所在小节起点 + 8 拍（2 个小节）
    last = last_note_beat
    measure = last - (last % 4)
    end_beat = measure + 8
    end_tick = tmap.ticks_at(end_beat)
    st.end_tick = end_tick

    # MEASURE：每 4 拍
    b = Fraction(0)
    while b <= end_beat:
        events.append((tmap.ticks_at(b), CMD_MEASURE, 0))
        b += 4
    # HAKU：每 1 拍
    b = Fraction(0)
    while b <= end_beat + Fraction(1, 2):
        events.append((tmap.ticks_at(b), CMD_HAKU, 0))
        b += 1
    events.append((end_tick, CMD_END, 0))

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    lines = [_dump_event(t, c, v) for (t, c, v) in events]
    text = line_sep.join(lines)
    if trailing_sep:
        text += line_sep
    st.duration_ticks = max((e[0] for e in events), default=0)
    out = text.encode("ascii")
    st.eve_bytes = len(out)
    return out, st


# ----------------------------------------------------------------------------
# 高层函数（GUI 可直接调用）
# ----------------------------------------------------------------------------
def open_ifs(path: str) -> MiscIfs:
    return MiscIfs(path=path)


def inspect_ifs(path: str, eve_head_lines: int = 10) -> dict:
    c = MiscIfs(path=path)
    res = {
        "path": path,
        "size": len(c.raw),
        "flags": c.flags,
        "flags_not": c.flags_not,
        "time": c.time,
        "tree_size_field": c.tree_size_field,
        "manifest_end": c.manifest_end,
        "manifest_md5": c.manifest_md5.hex(),
        "data_size": len(c.data),
        "members": [],
    }
    audio = set(id(m) for m in c.audio_members())
    for m in c.members:
        d = {
            "packed_name": m.packed_name, "name": m.name,
            "offset": m.offset, "size": m.size, "time": m.time,
            "kind": "audio" if id(m) in audio else ("chart" if m.ext == ".eve" else "other"),
            "md5": hashlib.md5(m.data).hexdigest(),
            "head": m.data[:8].hex(" "),
        }
        if m.ext == ".eve" and eve_head_lines > 0:
            d["eve_head"] = [l.decode("ascii", "replace")
                             for l in m.data.split(b"\r\n")[:eve_head_lines]]
        res["members"].append(d)
    return res


def verify_ifs(path: str) -> dict:
    """自检容器。返回 {'ok': bool, 'checks': [(name, ok, detail)]}"""
    checks: List[Tuple[str, bool, str]] = []
    raw = open(path, "rb").read()

    def chk(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))

    if len(raw) < HEADER_SIZE:
        return {"ok": False, "checks": [("文件长度>=0x24", False, "%d" % len(raw))]}
    sig = struct.unpack_from(">I", raw, 0x00)[0]
    flags = struct.unpack_from(">H", raw, 0x04)[0]
    flags_not = struct.unpack_from(">H", raw, 0x06)[0]
    t = struct.unpack_from(">I", raw, 0x08)[0]
    tree = struct.unpack_from(">I", raw, 0x0C)[0]
    m_end = struct.unpack_from(">I", raw, 0x10)[0]
    man_md5 = raw[0x14:0x24]

    chk("签名 == 0x6CAD8F89", sig == SIG, "0x%08X" % sig)
    chk("flags ^ flags_not == 0xFFFF", (flags ^ flags_not) == 0xFFFF,
        "flags=%d (0x%04X) not=0x%04X" % (flags, flags, flags_not))
    chk("HAS_TIMESTAMPS 位", bool(flags & FLAG_MANIFEST_HAS_TIMESTAMPS), "flags=%d" % flags)
    chk("HAS_MD5 位", bool(flags & FLAG_HAS_MD5), "flags=%d" % flags)
    chk("manifest_end 在文件内且 16 对齐",
        HEADER_SIZE <= m_end <= len(raw) and m_end % 16 == 0,
        "0x%X (file=0x%X)" % (m_end, len(raw)))

    man_region = raw[HEADER_SIZE:m_end]
    chk("0x14 MD5 == md5(manifest 区)",
        hashlib.md5(man_region).digest() == man_md5,
        "stored=%s calc=%s" % (man_md5.hex(), hashlib.md5(man_region).hexdigest()))

    try:
        kb = KBinXML(man_region)
        ok_parse = True
        detail = "root=%s" % kb.xml_doc.tag
    except Exception as e:
        ok_parse = False
        detail = str(e)
    chk("manifest 可解析 (kbinxml)", ok_parse, detail)

    if ok_parse:
        chk("0x0C == manifest.mem_size", tree == kb.mem_size,
            "field=%d calc=%d" % (tree, kb.mem_size))
        info = kb.xml_doc.find("_info_")
        data_region = raw[m_end:]
        if info is not None:
            md5e = info.find("md5")
            sizee = info.find("size")
            chk("_info_.md5 == md5(数据区)",
                md5e is not None and md5e.text == hashlib.md5(data_region).hexdigest(),
                "stored=%s calc=%s" % (md5e.text if md5e is not None else None,
                                       hashlib.md5(data_region).hexdigest()))
            chk("_info_.size == len(数据区) == filesize-manifest_end",
                sizee is not None and int(sizee.text) == len(data_region),
                "stored=%s calc=%d" % (sizee.text if sizee is not None else None,
                                       len(data_region)))
        else:
            chk("存在 _info_ 节点", False, "未找到")

    # 成员表自洽
    try:
        c = MiscIfs(data=raw)
        bad_overlap = []
        aligned = True
        in_range = True
        ordered = sorted(c.members, key=lambda m: m.offset)
        for m in ordered:
            if m.offset % 16 != 0:
                aligned = False
            if m.offset + m.size > len(c.data):
                in_range = False
        for a, b in zip(ordered, ordered[1:]):
            if a.offset + a.size > b.offset:
                bad_overlap.append((a.name, b.name))
        chk("成员偏移均 16 字节对齐", aligned)
        chk("成员均在数据区内", in_range)
        chk("成员区间无重叠", not bad_overlap, str(bad_overlap))
        chk("成员按偏移排序 == 文档顺序", [m.name for m in c.members] == [m.name for m in ordered])
        chk("存在谱面成员(.eve)", len(c.chart_members()) > 0,
            ",".join(m.name for m in c.chart_members()))
    except Exception as e:
        chk("成员表解析", False, str(e))

    # 兼容旧报告里提到的 0x80/0x90 硬编码（仅在 manifest_end==0xD0 时成立）
    if m_end == DEFAULT_MANIFEST_END:
        chk("(旧口径) 0x80..0x90 == md5(data[0xD0:])",
            raw[0x80:0x90] == hashlib.md5(raw[DEFAULT_MANIFEST_END:]).digest(),
            "")

    return {"ok": all(ok for _, ok, _ in checks), "checks": checks}


def convert(mcz_path: str, ifs_path: str, out_path: str,
            diff: Optional[int] = None, slot: str = "ext",
            member: Optional[str] = None, hold_policy: str = "split",
            set_time: Optional[int] = None, apply_bgm_offset: bool = False,
            dry_run: bool = False) -> dict:
    """把 Malody 谱面转换后回写进 `_msc.ifs`。返回统计 dict。"""
    charts = parse_malody(mcz_path)
    if not charts:
        raise ValueError("没有可用的 Malody 谱面")
    if diff is not None:
        if not 0 <= diff < len(charts):
            raise ValueError("--diff 越界：%d（共 %d 个）" % (diff, len(charts)))
        chart = charts[diff]
    else:
        chart = charts[0]

    eve, st = chart_to_eve(chart, hold_policy=hold_policy,
                           apply_bgm_offset=apply_bgm_offset)

    ifs = MiscIfs(path=ifs_path)
    if member is not None:
        target = ifs.get_member(member)
    else:
        target = ifs.get_member("%s.eve" % slot)
    if target is None:
        raise ValueError("目标容器里找不到成员 %s（可选：%s）"
                         % (member or ("%s.eve" % slot),
                            ", ".join(m.name for m in ifs.chart_members())))

    audio_before = {m.name: hashlib.md5(m.data).hexdigest() for m in ifs.audio_members()}
    old_size = target.size
    target.data = eve

    result = {
        "mcz": mcz_path,
        "ifs": ifs_path,
        "out": out_path,
        "chart_slot": target.name,
        "source_version": chart.version,
        "title": chart.title,
        "artist": chart.artist,
        "old_eve_size": old_size,
        "new_eve_size": len(eve),
        "chart_count_in_mcz": len(charts),
        "diff_selected": diff if diff is not None else 0,
        "stats": st.as_dict(),
        "audio_md5_before": audio_before,
    }

    if dry_run:
        result["written"] = False
        return result

    blob = ifs.to_bytes(set_time=set_time)
    with open(out_path, "wb") as f:
        f.write(blob)

    # 回读校验
    back = MiscIfs(path=out_path)
    t2 = back.get_member(target.name)
    result["written"] = True
    result["audio_md5_after"] = {m.name: hashlib.md5(m.data).hexdigest()
                                 for m in back.audio_members()}
    result["audio_identical"] = (result["audio_md5_before"] == result["audio_md5_after"])
    result["readback_ok"] = (t2 is not None and t2.data == eve)
    result["readback_events"] = len(parse_eve(t2.data)) if t2 else 0
    return result


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _print_kv(k: str, v) -> None:
    print("  %-26s %s" % (k + ":", v))


def cmd_inspect(args) -> int:
    res = inspect_ifs(args.ifs, eve_head_lines=args.eve_lines)
    print("== 容器 %s" % res["path"])
    _print_kv("文件大小", res["size"])
    _print_kv("flags / ~flags", "%d / 0x%04X" % (res["flags"], res["flags_not"]))
    _print_kv("unix 时间戳", res["time"])
    _print_kv("0x0C manifest.mem_size", res["tree_size_field"])
    _print_kv("0x10 manifest_end", "0x%X" % res["manifest_end"])
    _print_kv("0x14 manifest_md5", res["manifest_md5"])
    _print_kv("数据区大小", res["data_size"])
    print("-- 成员 (%d) --" % len(res["members"]))
    print("  %-16s %-14s %-10s %-10s %-6s %s" % ("packed", "name", "offset", "size", "kind", "md5"))
    for m in res["members"]:
        print("  %-16s %-14s 0x%-8X %-10d %-6s %s%s"
              % (m["packed_name"], m["name"], m["offset"], m["size"], m["kind"],
                 m["md5"], "  head=%s" % m["head"] if m["kind"] != "chart" else ""))
    for m in res["members"]:
        if m.get("eve_head"):
            print("-- 谱面 %s 前 %d 行 --" % (m["name"], len(m["eve_head"])))
            for l in m["eve_head"]:
                print("   " + l)
    return 0


def cmd_verify(args) -> int:
    res = verify_ifs(args.ifs)
    print("== 自检 %s" % args.ifs)
    for name, ok, detail in res["checks"]:
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                               ("  (%s)" % detail) if detail else ""))
    print("== 结果: %s" % ("全部通过" if res["ok"] else "存在失败项"))
    return 0 if res["ok"] else 1


def cmd_convert(args) -> int:
    st = None
    res = convert(args.mcz, args.ifs, args.out, diff=args.diff, slot=args.slot,
                  member=args.member, hold_policy=args.hold_policy,
                  set_time=args.set_time, apply_bgm_offset=args.apply_bgm_offset,
                  dry_run=args.dry_run)
    print("== 转换 %s[%d] -> %s" % (res["mcz"], res["diff_selected"], res["chart_slot"]))
    _print_kv("曲名/作者", "%s / %s" % (res["title"], res["artist"]))
    _print_kv("难度名(meta.version)", res["source_version"] or "(空)")
    _print_kv("原谱面大小 -> 新", "%d -> %d" % (res["old_eve_size"], res["new_eve_size"]))
    print("-- 音符统计 --")
    for k, v in res["stats"].items():
        _print_kv(k, v)
    if res.get("written"):
        _print_kv("输出文件", res["out"])
        _print_kv("音频成员(前) ", json.dumps(res["audio_md5_before"], ensure_ascii=False))
        _print_kv("音频成员(后) ", json.dumps(res["audio_md5_after"], ensure_ascii=False))
        _print_kv("音频零改动", res["audio_identical"])
        _print_kv("谱面回读一致", res["readback_ok"])
        _print_kv("回读事件数", res["readback_events"])
    else:
        _print_kv("(dry-run，未写盘)", True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ub_chart.py",
                                description="Malody -> jubeat _msc.ifs 谱面转换 / 容器工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect", help="列出成员并 dump 谱面")
    pi.add_argument("--ifs", required=True)
    pi.add_argument("--eve-lines", type=int, default=10)
    pi.set_defaults(func=cmd_inspect)

    pv = sub.add_parser("verify", help="自检容器头部/成员表/长度")
    pv.add_argument("--ifs", required=True)
    pv.set_defaults(func=cmd_verify)

    pc = sub.add_parser("convert", help="转换 Malody 谱面并回写")
    pc.add_argument("--mcz", required=True, help=".mcz 或 .mc")
    pc.add_argument("--ifs", required=True, help="目标 _msc.ifs（只读，输出写到 --out）")
    pc.add_argument("--out", required=True)
    pc.add_argument("--diff", type=int, default=None, help="选择 .mcz 中的第 N 个难度")
    pc.add_argument("--slot", default="ext", choices=["bsc", "adv", "ext"],
                    help="替换哪个难度成员（默认 ext）")
    pc.add_argument("--member", default=None, help="直接指定成员名，如 ext.eve")
    pc.add_argument("--hold-policy", default="split", choices=["split", "straighten"])
    pc.add_argument("--set-time", type=int, default=None, help="覆写 0x08 时间戳")
    pc.add_argument("--apply-bgm-offset", action="store_true",
                    help="按 Malody 的 BGM offset 平移时间（默认忽略）")
    pc.add_argument("--dry-run", action="store_true")
    pc.set_defaults(func=cmd_convert)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
