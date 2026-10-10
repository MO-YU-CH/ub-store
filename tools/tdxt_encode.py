#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tdxt_encode.py -- PNG <-> jubeat TDXT 纹理编码器 + PXET 图集写回

Phase 2：把自定义 PNG（曲绘 / 曲名文本条）编码成游戏能读的 TDXT 纹理，
并可写回 PXET 图集（新增或替换同名条目）。

四个 CLI 子命令：
    encode    --png <图> --out <tdxt文件> [--fmt <格式>] [--fit <WxH>]
              [--stretch] [--hf3 <n>] [--plain|--compress]
    replace   --atlas <pxet图集> --name <贴图名> --png <图> --out <输出>
              [--fmt <格式>] [--fit <WxH>] [--stretch] [--hf3 <n>]
    roundtrip --file <真实tdxt或pxet> [--png-out <目录>] [--fmt <格式>]
    title     --text <曲名> [--marker <[自制]>] --slot mini|wide --out <png>
              [--font <ttf>] [--size <pt>] [--x <px>] [--y <px>]

可被 GUI 直接 import 的函数：
    encode_png_to_tdxt(png_path, fmt=None, fit=None, stretch=False, hf3=None, plain=True) -> bytes
    encode_image_to_tdxt(pil_image, fmt=None, fit=None, ...) -> bytes
    replace_in_atlas(atlas_path, name, png_path, out_path, ...) -> dict
    add_to_atlas(...)  /  replace_bytes_in_atlas(atlas_bytes, name, tdxt_bytes, ...) -> bytes
    roundtrip_tdxt(tdxt_bytes) -> dict   / roundtrip_file(path) -> dict
    decode_tdxt(tdxt_bytes) -> PIL.Image    (本地实现，无 omnisrc 依赖)
    make_title_png(text, slot='mini', marker='', font=None, size=None, ...) -> PIL.Image
    read_pxet(path)/write_pxet(path, base, items)   (与 omnisrc/merge_pxet.py 规则一致)

算法与容器格式来源（均为公开 GitHub 仓库 / 本项目已实测结论，仅作技术参考）：
  * 容器/LZSS: Swan416ya/Jubeat2Malody-GUI  texbin_extractor.py
  * TDXT 头/像素格式: DragonMinded/bemaniutils  bemani/format/tdxt.py
  * DXT: DragonMinded/bemaniutils  bemani/format/dxt.py
  * PXET 写回规则: 本项目 omnisrc/merge_pxet.py（实测建立）

依赖：仅 stdlib + Pillow（已装）。
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 常量：像素格式
# ---------------------------------------------------------------------------
FMT_INFO = {
    0x01: ('gray',      1.0,  False),    # 实测：曲名条/占位图也用此格式
    0x0B: ('565',       2.0,  False),
    0x0E: ('bgr888',    3.0,  False),
    0x10: ('bgra8888',  4.0,  True),     # <- jubeat 主用格式（曲绘/曲名条），无损
    0x11: ('idx4',      0.5,  False),
    0x13: ('a1r5g5b5',  2.0,  False),
    0x15: ('argb8888',  4.0,  True),
    0x16: ('dxt1',      0.5,  False),
    0x18: ('dxt3',      1.0,  False),
    0x1A: ('dxt5',      1.0,  False),    # 实测：tm/ 等 UI 贴图在用
    0x1F: ('a4r4g4b4',  2.0,  False),
    0x20: ('bgra',      4.0,  True),
}
FMT_BY_NAME = {
    'gray': 0x01, 'l8': 0x01, 'grayscale': 0x01,
    '565': 0x0B, 'rgb565': 0x0B,
    'bgr': 0x0E, 'bgr888': 0x0E, 'rgb888': 0x0E,
    'bgra': 0x10, 'bgra8888': 0x10, 'rgba': 0x10, 'rgba8888': 0x10,
    'idx4': 0x11, 'pal4': 0x11,
    'a1r5g5b5': 0x13, '1555': 0x13,
    'argb': 0x15, 'argb8888': 0x15,
    'dxt1': 0x16, 'bc1': 0x16,
    'dxt3': 0x18, 'bc2': 0x18,
    'dxt5': 0x1A, 'bc3': 0x1A,
    'a4r4g4b4': 0x1F, '4444': 0x1F,
}
TDXT_MAGIC = b'TDXT'
HEADER_SIZE = 0x40

SLOT_SIZE = {
    'bnr_big': (320, 320), 'bnr': (160, 160), 'idx': (324, 40),
    'idx_mini': (160, 24), 'mini': (160, 24), 'wide': (324, 40),
}


def _pixels(img):
    """兼容新旧 Pillow 的逐像素接口。"""
    return img.get_flattened_data() if hasattr(img, 'get_flattened_data') else img.getdata()


def resolve_fmt(fmt) -> int:
    if fmt is None:
        return 0x10
    if isinstance(fmt, int):
        return fmt
    s = str(fmt).strip().lower()
    if s in FMT_BY_NAME:
        return FMT_BY_NAME[s]
    return int(s, 0)


def payload_size(w, h, fmt) -> int:
    n = int(w * h * FMT_INFO[fmt][1])
    if fmt == 0x11:
        n += 0x14 + 64
    return n


# ---------------------------------------------------------------------------
# TDXT 头
# ---------------------------------------------------------------------------
def build_tdxt_header(w, h, fmt, hf3=0x81, fmtflags=None,
                      flags1=0x00010000, flags2=0x00010100) -> bytes:
    """构造 64 字节小端 TDXT 头（与真实文件实测字段一致）。
       fmtflags 完整 u32：实测高位恒为 0x112210，低字节才是像素格式 -> 默认 0x11221000|fmt。"""
    if fmtflags is None:
        fmtflags = 0x11221000 | (fmt & 0xFF)
    raw_len = HEADER_SIZE + payload_size(w, h, fmt)
    hdr = bytearray(HEADER_SIZE)
    struct.pack_into('<4sIIIHHIII', hdr, 0,
                     TDXT_MAGIC, flags1, flags2, raw_len, w, h, fmtflags & 0xFFFFFFFF, 0, 0)
    struct.pack_into('<I', hdr, 0x2C, hf3 & 0xFFFFFFFF)
    return bytes(hdr)


def parse_tdxt_header(tdxt):
    if tdxt[:4] not in (b'TDXT', b'TXDT'):
        return None
    fix = struct.unpack_from('<I', tdxt, 8)[0] == 0x00010100
    end = '<' if fix else '>'
    r32 = lambda o: struct.unpack_from(end + 'I', tdxt, o)[0]
    r16 = lambda o: struct.unpack_from(end + 'H', tdxt, o)[0]
    fmt = (tdxt[0x14] if fix else tdxt[0x17])
    return dict(fix=fix, flags1=r32(0x04), flags2=r32(0x08), raw_len=r32(0x0C),
                w=r16(0x10), h=r16(0x12), fmtflags=r32(0x14), fmt=fmt,
                zero1=r32(0x18), zero2=r32(0x1C), hf3=r32(0x2C))


def default_hf3(w, h) -> int:
    """新条目 hf3 缺省值（实测 320x320=0x81；文本条/160 方图多为 0x83）。
       游戏解码器忽略该字段；replace 时沿用被替换条目的原值。"""
    return 0x81 if (w >= 256 and h >= 256) else 0x83


# ---------------------------------------------------------------------------
# LZSS 压缩（与 lzss_decompress 精确互逆；大图慢，默认走明文路径）
# ---------------------------------------------------------------------------
def lzss_compress(data, max_chain=32) -> bytes:
    """返回 LZSS 流（不含 8 字节记录头）。可被 lzss_decompress 精确还原。
       注意：匹配长度上限为「源区间到写指针的距离」，避免重叠导致的自引用歧义
       （重叠拷贝虽可行，但对不可压数据收益极小，这里取更简单且可证正确的做法）。
       大图较慢 —— 默认走明文路径。"""
    n = len(data)
    window = bytearray(4096)
    win = 4078
    out = bytearray()
    heads = {}
    i = 0
    while i < n:
        ctrl = 0
        toks = bytearray()
        for b in range(8):
            if i >= n:
                break
            best_len = 0
            best_off = 0
            if i + 3 <= n:
                pos = heads.get(bytes(data[i:i + 3]), -1)
                chain = 0
                while pos >= 0 and chain < max_chain:
                    chain += 1
                    maxd = (win - pos) & 0xFFF        # 不重叠：源区间须全在写指针之前
                    lim = min(18, maxd, n - i)
                    ln = 0
                    while ln < lim and window[(pos + ln) & 0xFFF] == data[i + ln]:
                        ln += 1
                    if ln > best_len:
                        best_len = ln
                        best_off = pos
                        if ln == 18:
                            break
                    break
            if best_len >= 3:
                toks.append(best_off & 0xFF)
                # 高半字节 = 偏移 bit8..11；低半字节 = 长度-3（与解码器一致）
                toks.append((((best_off >> 8) & 0x0F) << 4) | ((best_len - 3) & 0x0F))
                for k in range(best_len):
                    window[win] = window[(best_off + k) & 0xFFF]
                    if i + k + 3 <= n:
                        heads[bytes(data[i + k:i + k + 3])] = win
                    win = (win + 1) & 0xFFF
                i += best_len
            else:
                ctrl |= (1 << b)
                lit = data[i]
                window[win] = lit
                if i + 3 <= n:
                    heads[bytes(data[i:i + 3])] = win
                win = (win + 1) & 0xFFF
                toks.append(lit)
                i += 1
        out.append(ctrl)
        out += toks
    return bytes(out)


def make_pxet_record(tdxt, compress=False) -> bytes:
    """[BE32 解压后大小][BE32 压缩后大小][负载]；comp==0 表示负载为明文 TDXT。"""
    if compress:
        comp = lzss_compress(tdxt)
        if len(comp) < len(tdxt):
            return struct.pack('>II', len(tdxt), len(comp)) + comp
    return struct.pack('>II', len(tdxt), 0) + tdxt


# ---------------------------------------------------------------------------
# 像素编码：RGBA bytes -> 各 fmt 负载
# ---------------------------------------------------------------------------
def _luma(r, g, b):
    return (r * 299 + g * 587 + b * 114) // 1000


#: fmt=0x10/0x20 的通道序开关。
#: 实测结论：**按 RGBA 原样写**才正确（与底层 encode_pixels 一致）。
#: 若哪天发现需要 R/B 交换，设环境变量 UB_TEX_SWAP_RB=1 即可全局切换。
_SWAP_RB = None


def _swap_rb() -> bool:
    global _SWAP_RB
    if _SWAP_RB is None:
        _SWAP_RB = os.environ.get('UB_TEX_SWAP_RB', '').strip() in ('1', 'true', 'yes', 'on')
    return _SWAP_RB


def encode_pixels(rgba, w, h, fmt) -> bytes:
    n = w * h
    if fmt in (0x10, 0x20):
        return bytes(rgba)
    if fmt == 0x15:
        out = bytearray(n * 4)
        for i in range(n):
            r, g, b, a = rgba[i * 4:i * 4 + 4]
            out[i * 4:i * 4 + 4] = bytes((a, r, g, b))
        return bytes(out)
    if fmt == 0x0E:
        out = bytearray(n * 3)
        for i in range(n):
            r, g, b, a = rgba[i * 4:i * 4 + 4]
            out[i * 3:i * 3 + 3] = bytes((b, g, r))
        return bytes(out)
    if fmt == 0x01:
        return bytes(_luma(rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2]) for i in range(n))
    if fmt == 0x0B:
        out = bytearray(n * 2)
        for i in range(n):
            r, g, b, a = rgba[i * 4:i * 4 + 4]
            struct.pack_into('<H', out, i * 2, ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3))
        return bytes(out)
    if fmt == 0x13:
        out = bytearray(n * 2)
        for i in range(n):
            r, g, b, a = rgba[i * 4:i * 4 + 4]
            v = ((1 if a >= 128 else 0) << 15) | ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)
            struct.pack_into('<H', out, i * 2, v)
        return bytes(out)
    if fmt == 0x1F:
        out = bytearray(n * 2)
        for i in range(n):
            r, g, b, a = rgba[i * 4:i * 4 + 4]
            v = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
            struct.pack_into('<H', out, i * 2, v)
        return bytes(out)
    if fmt == 0x11:
        return _encode_idx4(rgba, w, h)
    if fmt in (0x16, 0x18, 0x1A):
        return _encode_dxt(rgba, w, h, fmt)
    raise ValueError('不支持的编码格式 fmt=0x%02X' % fmt)


def _encode_idx4(rgba, w, h) -> bytes:
    n = w * h
    im = Image.frombytes('RGBA', (w, h), rgba).convert('RGB')
    q = im.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:48]
    idx = list(q.getdata())
    npx = n // 2
    packed = bytearray(npx)
    for i in range(n):
        nib = idx[i] & 0x0F
        bi = i // 2
        if i % 2 == 0:
            packed[bi] = (packed[bi] & 0xF0) | nib
        else:
            packed[bi] = (packed[bi] & 0x0F) | (nib << 4)
    pal_raw = bytearray(64)
    for k in range(16):
        r = pal[k * 3] if k * 3 < len(pal) else 0
        g = pal[k * 3 + 1] if k * 3 + 1 < len(pal) else 0
        b = pal[k * 3 + 2] if k * 3 + 2 < len(pal) else 0
        pal_raw[k * 4:k * 4 + 4] = bytes((b, g, r, 255))
    return bytes(packed) + b'\x00' * 0x14 + bytes(pal_raw)


# ---- DXT ---------------------------------------------------------------
def _q565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _exp565(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _pal4(c0, c1):
    r0, g0, b0 = _exp565(c0)
    r1, g1, b1 = _exp565(c1)
    return [(r0, g0, b0), (r1, g1, b1),
            ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
            ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3)]


def _nudge565(c, d):
    if d == 0:
        return c
    r = max(0, min(31, ((c >> 11) & 0x1F) + d))
    g = max(0, min(63, ((c >> 5) & 0x3F) + d))
    b = max(0, min(31, (c & 0x1F) + d))
    return (r << 11) | (g << 5) | b


def _sse4(c0, c1, colors):
    pal = _pal4(c0, c1)
    s = 0
    for cr, cg, cb in colors:
        best = 1 << 30
        for pr, pg, pb in pal:
            dr, dg, db = cr - pr, cg - pg, cb - pb
            e = dr * dr + dg * dg + db * db
            if e < best:
                best = e
        s += best
    return s


# DXT1 4 色模式的线性权：code0->c0, code1->c1, code2->(2c0+c1)/3, code3->(c0+2c1)/3
_LS_W = ((1.0, 0.0), (0.0, 1.0), (2.0 / 3.0, 1.0 / 3.0), (1.0 / 3.0, 2.0 / 3.0))


def _cluster_fit(colors, iters=8):
    """Squish 风格 cluster fit：把每个像素按最近色分配权重后，对 (c0,c1) 做最小二乘。
       返回浮点 (c0, c1)，每分量 (r,g,b)。"""
    # 初值：主轴投影极值
    n = len(colors)
    mr = sum(c[0] for c in colors) / n
    mg = sum(c[1] for c in colors) / n
    mb = sum(c[2] for c in colors) / n
    srr = sgg = sbb = srg = srb = sgb = 0.0
    for r, g, b in colors:
        dr, dg, db = r - mr, g - mg, b - mb
        srr += dr * dr; sgg += dg * dg; sbb += db * db
        srg += dr * dg; srb += dr * db; sgb += dg * db
    vx, vy, vz = 1.0, 1.0, 1.0
    for _ in range(8):
        nx = srr * vx + srg * vy + srb * vz
        ny = srg * vx + sgg * vy + sgb * vz
        nz = srb * vx + sgb * vy + sbb * vz
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        vx, vy, vz = nx / ln, ny / ln, nz / ln
    t = [c[0] * vx + c[1] * vy + c[2] * vz for c in colors]
    c0 = list(map(float, colors[t.index(max(t))]))
    c1 = list(map(float, colors[t.index(min(t))]))
    for _ in range(iters):
        pal = [c0, c1,
               [(2 * c0[k] + c1[k]) / 3 for k in range(3)],
               [(c0[k] + 2 * c1[k]) / 3 for k in range(3)]]
        s00 = s01 = s11 = 0.0
        b0 = [0.0, 0.0, 0.0]
        b1 = [0.0, 0.0, 0.0]
        for cr, cg, cb in colors:
            best = 1 << 30
            bi = 0
            for pi, pc in enumerate(pal):
                dr, dg, db = cr - pc[0], cg - pc[1], cb - pc[2]
                e = dr * dr + dg * dg + db * db
                if e < best:
                    best = e
                    bi = pi
            w0, w1 = _LS_W[bi]
            s00 += w0 * w0; s01 += w0 * w1; s11 += w1 * w1
            b0[0] += w0 * cr; b0[1] += w0 * cg; b0[2] += w0 * cb
            b1[0] += w1 * cr; b1[1] += w1 * cg; b1[2] += w1 * cb
        det = s00 * s11 - s01 * s01
        if abs(det) < 1e-9:
            break
        nc0 = [(s11 * b0[k] - s01 * b1[k]) / det for k in range(3)]
        nc1 = [(s00 * b1[k] - s01 * b0[k]) / det for k in range(3)]
        moved = max(abs(nc0[k] - c0[k]) + abs(nc1[k] - c1[k]) for k in range(3))
        c0, c1 = nc0, nc1
        if moved < 0.5:
            break
    q = lambda c: (max(0, min(255, int(round(c[0])))),
                   max(0, min(255, int(round(c[1])))),
                   max(0, min(255, int(round(c[2])))))
    return q(c0), q(c1)


def _fit_endpoints(colors):
    """挑一对 565 端点使 4 色近似 SSE 最小。
       候选：cluster fit（Squish 风格最小二乘）/ PCA 极值 / 通道包围盒，取最优后微调。"""
    if all(c == colors[0] for c in colors):
        v = _q565(*colors[0])
        return v, v
    cands = []
    fc0, fc1 = _cluster_fit(colors)
    cands.append((_q565(*fc0), _q565(*fc1)))
    n = len(colors)
    mr = sum(c[0] for c in colors) / n
    mg = sum(c[1] for c in colors) / n
    mb = sum(c[2] for c in colors) / n
    srr = sgg = sbb = srg = srb = sgb = 0.0
    for r, g, b in colors:
        dr, dg, db = r - mr, g - mg, b - mb
        srr += dr * dr; sgg += dg * dg; sbb += db * db
        srg += dr * dg; srb += dr * db; sgb += dg * db
    vx, vy, vz = 1.0, 1.0, 1.0
    for _ in range(8):
        nx = srr * vx + srg * vy + srb * vz
        ny = srg * vx + sgg * vy + sgb * vz
        nz = srb * vx + sgb * vy + sbb * vz
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        vx, vy, vz = nx / ln, ny / ln, nz / ln
    ax, ay, az = vx, vy, vz
    t = [c[0] * ax + c[1] * ay + c[2] * az for c in colors]
    cands.append((_q565(*colors[t.index(max(t))]), _q565(*colors[t.index(min(t))])))
    lo = (min(c[0] for c in colors), min(c[1] for c in colors), min(c[2] for c in colors))
    hi = (max(c[0] for c in colors), max(c[1] for c in colors), max(c[2] for c in colors))
    cands.append((_q565(*hi), _q565(*lo)))
    best = None
    for a, b in cands:
        if a < b:
            a, b = b, a
        s = _sse4(a, b, colors)
        if best is None or s < best[0]:
            best = (s, a, b)
    s, a, b = best
    # ★ 逐通道坐标下降：分别优化 c0/c1 的 r(0..31)/g(0..63)/b(0..31) 六个坐标
    def unpack(c):
        return [(c >> 11) & 0x1F, (c >> 5) & 0x3F, c & 0x1F]

    def pack(v):
        return (v[0] << 11) | (v[1] << 5) | v[2]

    v0, v1 = unpack(a), unpack(b)
    maxv = (31, 63, 31)
    span = 3
    for _ in range(6):
        improved = False
        for v in (v0, v1):
            for ch in range(3):
                base = v[ch]
                lo_, hi_ = max(0, base - span), min(maxv[ch], base + span)
                bv, bs = base, s
                for nv in range(lo_, hi_ + 1):
                    if nv == base:
                        continue
                    v[ch] = nv
                    na, nb = pack(v0), pack(v1)
                    if na < nb:
                        na, nb = nb, na
                    s2 = _sse4(na, nb, colors)
                    if s2 < bs:
                        bs, bv = s2, nv
                    v[ch] = base
                if bv != base:
                    v[ch] = bv
                    s, improved = bs, True
        if not improved:
            break
    a, b = pack(v0), pack(v1)
    if a < b:
        a, b = b, a
    return a, b


def _block_rgb(rgba, w, h, bx, by):
    out = []
    for j in range(4):
        yy = by + j
        for i in range(4):
            xx = bx + i
            if xx < w and yy < h:
                o = (yy * w + xx) * 4
                out.append((rgba[o], rgba[o + 1], rgba[o + 2]))
            else:
                out.append((0, 0, 0))
    return out


def _block_alpha(rgba, w, h, bx, by):
    out = []
    for j in range(4):
        yy = by + j
        for i in range(4):
            xx = bx + i
            out.append(rgba[(yy * w + xx) * 4 + 3] if (xx < w and yy < h) else 0)
    return out


def _indices_for(c0, c1, colors):
    pal = _pal4(c0, c1)
    idx = 0
    for k, (cr, cg, cb) in enumerate(colors):
        best = 1 << 30
        bi = 0
        for pi, (pr, pg, pb) in enumerate(pal):
            dr, dg, db = cr - pr, cg - pg, cb - pb
            e = dr * dr + dg * dg + db * db
            if e < best:
                best = e
                bi = pi
        idx |= (bi & 3) << (2 * k)
    return idx


def _alpha_block5(al):
    a0 = max(al)
    a1 = min(al)
    if a0 == a1:
        return bytes((a0, a1, 0, 0, 0, 0, 0, 0))
    lv = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
    bits = 0
    for k in range(16):
        best = 1 << 30
        bi = 0
        for pi, v in enumerate(lv):
            e = abs(al[k] - v)
            if e < best:
                best = e
                bi = pi
        bits |= (bi & 7) << (3 * k)
    return bytes((a0, a1)) + bits.to_bytes(6, 'little')


def _encode_dxt(rgba, w, h, fmt):
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            colors = _block_rgb(rgba, w, h, bx, by)
            c0, c1 = _fit_endpoints(colors)
            if c0 < c1:
                c0, c1 = c1, c0
            if c0 == c1:
                c1 = _nudge565(c1, -1) if c0 > 0 else c0
            color_block = struct.pack('<HHI', c0, c1, _indices_for(c0, c1, colors))
            if fmt == 0x16:
                out += color_block
            elif fmt == 0x18:
                al = _block_alpha(rgba, w, h, bx, by)
                a = 0
                for k in range(16):
                    a |= ((al[k] >> 4) & 0xF) << (4 * k)
                out += struct.pack('<Q', a) + color_block
            else:
                out += _alpha_block5(_block_alpha(rgba, w, h, bx, by)) + color_block
    return bytes(out)


# ---------------------------------------------------------------------------
# PNG/Image -> TDXT
# ---------------------------------------------------------------------------
def _fit_image(img, fit, stretch):
    if not fit:
        return img
    w, h = fit
    if stretch:
        return img.resize((w, h), Image.LANCZOS)
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    nw, nh = max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))
    rs = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return rs.crop((left, top, left + w, top + h))


def encode_image_to_tdxt(img, fmt=None, fit=None, stretch=False, hf3=None,
                         flags1=0x00010000, flags2=0x00010100, fmtflags=None) -> bytes:
    """把 PIL 图像编码为完整 TDXT 字节流（默认 fmt=0x10 BGRA，无损）。"""
    fmt = resolve_fmt(fmt)
    img = _fit_image(img.convert('RGBA'), fit, stretch)
    w, h = img.size
    if w <= 0 or h <= 0:
        raise ValueError('非法尺寸 %r' % (img.size,))
    if fmt in (0x16, 0x18, 0x1A) and (w % 4 or h % 4):
        raise ValueError('DXT 要求宽高为 4 的倍数，当前 %dx%d' % (w, h))
    if fmt == 0x11 and (w * h) % 2:
        raise ValueError('idx4 要求像素总数为偶数')
    rgba = img.tobytes()                      # Pillow RGBA = R,G,B,A
    if fmt in (0x10, 0x20):
        # 🔴 通道序：fmt=0x10 按 **RGBA** 原样写。
        #    曾在这里做 R/B 交换（当 BGRA 处理），实机上新曲绘出现
        #    「原本红色的部分变蓝」——正是 R/B 颠倒的典型表现。
        #    底层 encode_pixels(0x10) 一直是原样返回，这里与它保持一致。
        #    若某台机器上反而需要交换，把环境变量 UB_TEX_SWAP_RB=1 即可（见 _swap_rb）。
        if _swap_rb():
            payload = bytes(b for i in range(0, len(rgba), 4)
                            for b in (rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))
        else:
            payload = bytes(rgba)
    else:
        payload = encode_pixels(rgba, w, h, fmt)
    if hf3 is None:
        hf3 = default_hf3(w, h)
    return build_tdxt_header(w, h, fmt, hf3, fmtflags, flags1, flags2) + payload


def encode_png_to_tdxt(png_path, fmt=None, fit=None, stretch=False, hf3=None,
                       fmtflags=None) -> bytes:
    return encode_image_to_tdxt(Image.open(png_path), fmt, fit, stretch, hf3,
                                fmtflags=fmtflags)


# ---------------------------------------------------------------------------
# TDXT 解码（自包含；逻辑与 omnisrc/tdxt_decode.py 一致）
# ---------------------------------------------------------------------------
def lzss_decompress(blob) -> bytes:
    if len(blob) < 8:
        return blob
    decomp_size = struct.unpack_from('>I', blob, 0)[0]
    comp_size = struct.unpack_from('>I', blob, 4)[0]
    if comp_size == 0:
        return blob[8:8 + decomp_size]
    avail = len(blob) - 8
    if comp_size > avail:
        comp_size = avail
    comp = blob[8:8 + comp_size]
    window = bytearray(4096)
    out = bytearray(decomp_size)
    ci = di = 0
    win = 4078
    ctrl = bits = 0
    while True:
        if bits == 0:
            if ci >= comp_size:
                break
            ctrl = comp[ci]; ci += 1; bits = 8
        if ctrl & 0x01:
            if ci >= comp_size:
                break
            out[di] = window[win] = comp[ci]
            di += 1; win = (win + 1) & 0xFFF; ci += 1
            if di >= decomp_size:
                break
        else:
            if di >= decomp_size - 1:
                break
            off = (((comp[ci + 1] & 0xF0) << 4) | comp[ci]) & 0xFFF
            ln = (comp[ci + 1] & 0x0F) + 3
            ci += 2
            if di + ln > decomp_size:
                ln = decomp_size - di
            while ln > 0:
                out[di] = window[win] = window[off]
                di += 1; win = (win + 1) & 0xFFF; off = (off + 1) & 0xFFF
                ln -= 1
        ctrl >>= 1; bits -= 1
    return bytes(out)


def _dxt_decode(w, h, data, kind):
    buf = [bytes((0, 0, 0, 255))] * (w * h)

    def rgb(p):
        r = (p >> 11) & 0x1F; g = (p >> 5) & 0x3F; b = p & 0x1F
        return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))

    step = 8 if kind == 1 else 16
    pos = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            if pos + step > len(data):
                return b''.join(buf)
            chunk = data[pos:pos + step]; pos += step
            if kind == 1:
                c0, c1, ct = struct.unpack('<HHI', chunk); acode = None
            elif kind == 3:
                acode = ('e', struct.unpack('<Q', chunk[:8])[0])
                c0, c1, ct = struct.unpack('<HHI', chunk[8:16])
            else:
                a0, a1, ac0, ac1, c0, c1, ct = struct.unpack('<BBHIHHI', chunk)
                acode = ('i', a0, a1, (ac1 << 16) | ac0)
            for j in range(4):
                for i in range(4):
                    code = (ct >> (2 * (4 * j + i))) & 3
                    r0, g0, b0 = rgb(c0); r1, g1, b1 = rgb(c1)
                    if code == 0:
                        col = (r0, g0, b0)
                    elif code == 1:
                        col = (r1, g1, b1)
                    elif code == 2:
                        col = ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3) if c0 > c1 \
                            else ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2)
                    else:
                        col = ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3) if c0 > c1 \
                            else (0, 0, 0)
                    if kind == 1:
                        al = 255
                    elif kind == 3:
                        al = ((acode[1] >> (4 * (4 * j + i))) & 0xF) * 17
                    else:
                        a0, a1, abits = acode[1], acode[2], acode[3]
                        c = (abits >> (3 * (4 * j + i))) & 7
                        if c == 0:
                            al = a0
                        elif c == 1:
                            al = a1
                        elif a0 > a1:
                            al = ((8 - c) * a0 + (c - 1) * a1) // 7
                        elif c == 6:
                            al = 0
                        elif c == 7:
                            al = 255
                        else:
                            al = ((6 - c) * a0 + (c - 1) * a1) // 5
                    xx, yy = bx + i, by + j
                    if xx < w and yy < h:
                        buf[yy * w + xx] = bytes((col[0], col[1], col[2], al))
    return b''.join(buf)


def decode_tdxt(tdxt):
    h = parse_tdxt_header(tdxt)
    if not h:
        return None
    w, ht, fmt = h['w'], h['h'], h['fmt']
    if w <= 0 or ht <= 0:
        return None
    size = h['raw_len'] - HEADER_SIZE
    bmp = tdxt[HEADER_SIZE:HEADER_SIZE + size]
    if fmt == 0x01:
        return Image.frombytes('L', (w, ht), bmp[:w * ht]).convert('RGBA')
    if fmt == 0x0B:
        out = bytearray(w * ht * 4)
        for i in range(w * ht):
            p = struct.unpack_from('<H', bmp, i * 2)[0]
            r = ((p >> 11) & 0x1F) << 3; g = ((p >> 5) & 0x3F) << 2; b = (p & 0x1F) << 3
            out[i * 4:i * 4 + 4] = bytes((r | (r >> 5), g | (g >> 6), b | (b >> 5), 255))
        return Image.frombytes('RGBA', (w, ht), bytes(out))
    if fmt == 0x0E:
        return Image.frombytes('RGB', (w, ht), bmp[:w * ht * 3], 'raw', 'BGR').convert('RGBA')
    if fmt == 0x10:
        # 与 encode 保持一致：默认按 RGBA 解释（见 _swap_rb 的说明）
        order = 'BGRA' if _swap_rb() else 'RGBA'
        return Image.frombytes('RGBA', (w, ht), bmp[:w * ht * 4], 'raw', order)
    if fmt == 0x11:
        npx = w * ht // 2
        px = bytearray(bmp[:npx])
        for i in range(len(px)):
            b = px[i]
            px[i] = ((b & 0x0F) << 4) | ((b & 0xF0) >> 4)
        pal_raw = bmp[npx + 0x14:npx + 0x14 + 64]
        pal = []
        for i in range(0, 64, 4):
            b, g, r, a = pal_raw[i:i + 4]
            pal.append((r, g, b, a if a else 255))
        out = bytearray(w * ht * 4)
        for i in range(w * ht):
            byte = px[i // 2]
            nib = byte >> 4 if i % 2 == 0 else byte & 0x0F
            r, g, b, a = pal[nib]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
        return Image.frombytes('RGBA', (w, ht), bytes(out))
    if fmt == 0x13:
        out = bytearray(w * ht * 4)
        for i in range(w * ht):
            p = struct.unpack_from('<H', bmp, i * 2)[0]
            a = 255 if (p >> 15) & 1 else 0
            r = ((p >> 0) & 0x1F) << 3; g = ((p >> 5) & 0x1F) << 3; b = ((p >> 10) & 0x1F) << 3
            out[i * 4:i * 4 + 4] = bytes((r | (r >> 5), g | (g >> 5), b | (b >> 5), a))
        return Image.frombytes('RGBA', (w, ht), bytes(out))
    if fmt == 0x15:
        return Image.frombytes('RGBA', (w, ht), bmp[:w * ht * 4], 'raw', 'ARGB')
    if fmt == 0x16:
        return Image.frombytes('RGBA', (w, ht), _dxt_decode(w, ht, bmp, 1), 'raw', 'RGBA')
    if fmt == 0x18:
        return Image.frombytes('RGBA', (w, ht), _dxt_decode(w, ht, bmp, 3), 'raw', 'RGBA')
    if fmt == 0x1A:
        return Image.frombytes('RGBA', (w, ht), _dxt_decode(w, ht, bmp, 5), 'raw', 'RGBA')
    if fmt == 0x1F:
        out = bytearray(w * ht * 4)
        for i in range(w * ht):
            p = struct.unpack_from('<H', bmp, i * 2)[0]
            b = ((p >> 0) & 0xF) << 4; g = ((p >> 4) & 0xF) << 4
            r = ((p >> 8) & 0xF) << 4; a = ((p >> 12) & 0xF) << 4
            out[i * 4:i * 4 + 4] = bytes((r | (r >> 4), g | (g >> 4), b | (b >> 4), a | (a >> 4)))
        return Image.frombytes('RGBA', (w, ht), bytes(out))
    if fmt == 0x20:
        return Image.frombytes('RGBA', (w, ht), bmp[:w * ht * 4], 'raw', 'BGRA')
    return None


# ---------------------------------------------------------------------------
# PXET 容器读写（与 omnisrc/merge_pxet.py 规则一致；自包含、无 chdir 副作用）
# ---------------------------------------------------------------------------
def pxet_entries(data):
    """返回 [(name, flag, rec_len, blob_off)]（直接解析原文件，不重建）。"""
    if data[:4] != b'PXET':
        return []
    cnt = struct.unpack_from('<I', data, 0x14)[0]
    pman = struct.unpack_from('<I', data, 0x34)[0]
    tt = struct.unpack_from('<I', data, 0x3C)[0]
    names_by_texidx = {}
    n2 = struct.unpack_from('<I', data, pman + 0x10)[0]
    base = pman + 0x1C
    for i in range(n2):
        _h, ti, so = struct.unpack_from('<3I', data, base + i * 12)
        s = pman + so
        e = data.find(b'\x00', s)
        names_by_texidx[ti] = data[s:e if e > 0 else s + 64].decode('latin-1', 'replace')
    out = []
    for i in range(cnt):
        o = tt + i * 12
        fl, ln, off = struct.unpack_from('<3I', data, o)
        out.append((names_by_texidx.get(i, 'entry_%d' % i), fl, ln, off))
    return out


def pman_hash_param(n):
    """PMAN 头第 6 字段 f14：m=floor(log2(n)), f14=((2^m-1)<<16)|2^(m-1)；n<=1 -> 0。"""
    if n <= 1:
        return 0
    m = n.bit_length() - 1
    return ((1 << m) - 1) << 16 | (1 << (m - 1))


def read_pxet(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'PXET', path
    ntex = struct.unpack_from('<I', d, 0x14)[0]
    pman = struct.unpack_from('<I', d, 0x34)[0]
    tt = struct.unpack_from('<I', d, 0x3C)[0]
    magic, clen, f8, fc, n, f14, f18 = struct.unpack_from('<7I', d, pman)
    assert magic == 0x4E414D50, 'PMAN magic'
    items = []
    for i in range(n):
        hh, ti, no = struct.unpack_from('<3I', d, pman + 0x1C + 12 * i)
        e = d.index(b'\0', pman + no)
        nm = d[pman + no:e].decode('latin-1')
        fl, ln, off = struct.unpack_from('<3I', d, tt + 12 * ti)
        items.append(dict(name=nm, hash=hh, flag=fl, blob=d[off:off + ln], texIndex=ti))
    return dict(hdr=d[:0x40], f8=f8, fc=fc, f14=f14, f18=f18, ntex=ntex, items=items)


def rebuild_pxet(base, items) -> bytes:
    """按规则重建 PXET：纹理表/texIndex 按名字升序；PMAN 条目按 hash 升序；
       f14 按 n 重算；clen 4 字节对齐；blob 之间 4 字节对齐。返回完整字节。"""
    n = len(items)
    items = sorted(items, key=lambda it: it['name'])
    order = sorted(range(n), key=lambda i: items[i]['hash'])
    names_sorted = [it['name'] for it in items]
    nb = b''.join(nm.encode('latin-1') + b'\0' for nm in names_sorted)
    name_off_of = {}
    pos = 0x1C + 12 * n
    for nm in names_sorted:
        name_off_of[nm] = pos
        pos += len(nm) + 1
    pman_size = pos + ((4 - pos % 4) % 4)

    tt_off = 0x40 + pman_size
    blob_start = tt_off + 12 * n
    offs = []
    cur = blob_start
    for it in items:
        offs.append(cur)
        cur += (len(it['blob']) + 3) // 4 * 4
    total = cur

    hdr = bytearray(base['hdr'])
    struct.pack_into('<I', hdr, 0x0C, total)
    struct.pack_into('<I', hdr, 0x14, n)
    struct.pack_into('<I', hdr, 0x1C, blob_start)
    struct.pack_into('<I', hdr, 0x34, 0x40)
    struct.pack_into('<I', hdr, 0x3C, tt_off)

    pman = bytearray()
    pman += struct.pack('<7I', 0x4E414D50, pman_size, base['f8'], base['fc'], n,
                        pman_hash_param(n), base['f18'])
    for i in order:
        it = items[i]
        pman += struct.pack('<3I', it['hash'], i, name_off_of[it['name']])
    pman += nb
    pman += b'\0' * (pman_size - pos)

    tt = bytearray()
    for i, it in enumerate(items):
        tt += struct.pack('<3I', it['flag'], len(it['blob']), offs[i])

    body = bytearray()
    for i, it in enumerate(items):
        body += it['blob']
        body += b'\0' * ((4 - len(it['blob']) % 4) % 4)

    out = bytes(hdr) + bytes(pman) + bytes(tt) + bytes(body)
    assert len(out) == total, (len(out), total)
    return out


def write_pxet(path, base, items) -> int:
    out = rebuild_pxet(base, items)
    open(path, 'wb').write(out)
    return len(out)


# ---------------------------------------------------------------------------
# 纹理名哈希（jubeat.dll 0x101558F0 的 1:1 复刻，见 omnisrc/jb_hash.py）
#   多项式 0x4C11DB7，初值 0，每字符消费低 6 位，MSB-first 移位
# ---------------------------------------------------------------------------
def name_hash(name: str) -> int:
    M = 0xFFFFFFFF
    POLY = 0x4C11DB7
    b = name.encode('latin-1') + b'\x00'
    eax = 0
    i = 0
    c = b[0]
    if c == 0:
        return 0
    while True:
        for k in range(6):
            sh = (eax << 1) & M
            bit = (c >> k) & 1
            s = (eax >> 31) & 1
            eax = ((sh | bit) ^ (POLY if s else 0)) & M
        i += 1
        c = b[i]
        if c == 0:
            return eax


# ---------------------------------------------------------------------------
# 高层 API：图集写回
# ---------------------------------------------------------------------------
def _entry_hf3(blob):
    try:
        h = parse_tdxt_header(lzss_decompress(blob))
        return h['hf3'] if h else None
    except Exception:
        return None


def modify_atlas(atlas_path, name, tdxt_bytes, out_path=None, compress=False) -> dict:
    """把已构造好的 tdxt_bytes 作为记录写进图集（同名替换 / 新名新增）。返回报告 dict。
       替换时沿用被替换条目的 hash / flag；新增时用 name_hash(name) / flag=0。"""
    base = read_pxet(atlas_path)
    items = base['items']
    idx = next((i for i, it in enumerate(items) if it['name'] == name), None)
    action = 'replace' if idx is not None else 'add'
    if idx is not None:
        it = items[idx]
        new_item = dict(name=name, hash=it['hash'], flag=it['flag'],
                        blob=make_pxet_record(tdxt_bytes, compress))
        items[idx] = new_item
    else:
        new_item = dict(name=name, hash=name_hash(name), flag=0,
                        blob=make_pxet_record(tdxt_bytes, compress))
        items.append(new_item)
    out = out_path or atlas_path
    size = write_pxet(out, base, items)
    th = parse_tdxt_header(tdxt_bytes) or {}
    return dict(action=action, out=out, n_before=len(base['items']), n_after=len(items),
                size=size, entry_bytes=len(new_item['blob']),
                hash=new_item['hash'], hf3=th.get('hf3'),
                fmt=th.get('fmt'), size_px=(th.get('w'), th.get('h')))


def replace_in_atlas(atlas_path, name, png_path, out_path, fmt=None, fit=None,
                     stretch=False, hf3=None, compress=False, fmtflags=None) -> dict:
    """读 PNG -> 编码为 TDXT -> 写回图集（新增或替换同名）。
       若图集已有同名条目：默认对齐它的 尺寸/fmt/hf3/fmtflags（最大保真）。"""
    base = read_pxet(atlas_path)
    it = next((x for x in base['items'] if x['name'] == name), None)
    oh = None
    if it and it['blob']:
        oh = parse_tdxt_header(lzss_decompress(it['blob']))
    fmt_i = resolve_fmt(fmt) if fmt is not None else (oh['fmt'] if oh else 0x10)
    if fit is None and oh:
        fit = (oh['w'], oh['h'])
    if hf3 is None and oh:
        hf3 = oh['hf3']
    if fmtflags is None and oh and fmt_i == oh['fmt']:
        fmtflags = oh['fmtflags']
    tdxt = encode_png_to_tdxt(png_path, fmt_i, fit, stretch, hf3, fmtflags)
    rep = modify_atlas(atlas_path, name, tdxt, out_path, compress)
    return rep


# ---------------------------------------------------------------------------
# 往返验证
# ---------------------------------------------------------------------------
def _psnr(a_img, b_img) -> float:
    a = a_img.convert('RGBA')
    b = b_img.convert('RGBA')
    if a.size != b.size:
        return 0.0
    pa = a.tobytes()
    pb = b.tobytes()
    se = 0
    m = 0
    # 比较 R,G,B 通道（忽略 alpha，DXT 不改 alpha 语义）
    for i in range(0, len(pa), 4):
        for c in range(3):
            d = pa[i + c] - pb[i + c]
            se += d * d
        m += 3
    if se == 0:
        return float('inf')
    import math
    mse = se / m
    return 10.0 * math.log10(255.0 * 255.0 / mse)


def roundtrip_tdxt(tdxt, fmt=None) -> dict:
    """解码 -> 重新编码 -> 与原字节比较。"""
    h = parse_tdxt_header(tdxt)
    if not h:
        return dict(ok=False, reason='非 TDXT')
    img = decode_tdxt(tdxt)
    if img is None:
        return dict(ok=False, reason='不支持的格式 fmt=0x%02X' % h['fmt'], fmt=h['fmt'])
    use_fmt = resolve_fmt(fmt) if fmt is not None else h['fmt']
    use_ff = h['fmtflags'] if use_fmt == h['fmt'] else None
    re = encode_image_to_tdxt(img, use_fmt, None, False, h['hf3'], h['flags1'],
                              h['flags2'], use_ff)
    re_img = decode_tdxt(re)
    psnr = _psnr(img, re_img) if re_img else 0.0
    n = min(len(tdxt), len(re))
    ndiff = sum(1 for i in range(n) if tdxt[i] != re[i]) + abs(len(tdxt) - len(re))
    return dict(ok=True, fmt=h['fmt'], w=h['w'], h=h['h'],
                same_fmt=(use_fmt == h['fmt']),
                byte_equal=(tdxt == re), n_bytes_diff=ndiff,
                len_orig=len(tdxt), len_re=len(re),
                psnr=psnr, lossless=FMT_INFO.get(h['fmt'], (0, 0, False))[2])


def roundtrip_file(path, fmt=None, png_out=None) -> dict:
    d = open(path, 'rb').read()
    if png_out:
        os.makedirs(png_out, exist_ok=True)
    if d[:4] == b'PXET':
        res = []
        for nm, fl, ln, off in pxet_entries(d):
            blob = d[off:off + ln]
            tdxt = lzss_decompress(blob)
            r = roundtrip_tdxt(tdxt, fmt)
            r['name'] = nm
            r['declen'] = len(tdxt)
            res.append(r)
            if png_out:
                im = decode_tdxt(tdxt)
                if im:
                    im.save(os.path.join(png_out, nm + '.png'))
        return dict(kind='pxet', name=os.path.basename(path), entries=res)
    is_raw_tdxt = d[:4] in (b'TDXT', b'TXDT')
    # 独立记录：[BE32 解压后][BE32 压缩后][负载]，负载解出后应为 TDXT
    is_record = (len(d) >= 12) and (d[8:12] in (b'TDXT', b'TXDT')
                                    or struct.unpack_from('>I', d, 0)[0] > 64)
    if is_raw_tdxt or is_record:
        tdxt = d if is_raw_tdxt else lzss_decompress(d)
        r = roundtrip_tdxt(tdxt, fmt)
        if png_out:
            im = decode_tdxt(tdxt)
            if im:
                im.save(os.path.join(png_out, os.path.basename(path) + '.png'))
        return dict(kind='tdxt', name=os.path.basename(path), entries=[r])
    return dict(kind='unknown', name=os.path.basename(path), entries=[])


# ---------------------------------------------------------------------------
# 曲名文本条
# ---------------------------------------------------------------------------
# 实测标定（对齐真实 IDX_MINI_ID* / IDX_ID* 条，见 tmp/tdxt_work/fit_title.py）：
#   真实 IDX_MINI(160x24): 白字+透明底，不透明像素≈200-420，墨迹 bbox 起始 x≈18-19，
#                           y∈[6..16]（字高≈10-11px）。多字体×字号网格搜索：
#                           dfgothw5.ttc@13 x=17 与真实掩码 IoU≈0.61（最高）；
#                           df-chubutomarugothic-w7.ttc@15 次之(0.60)。
#   真实 IDX_ID(324x40): 两行——曲名行 y∈[4..19]（高≈16px），艺术家行 y∈[25..35]（高≈11px），
#                         起笔 x≈4。-> 曲名 @18 y≈1、艺术家 @13 y≈24。
FONT_DIR = os.environ.get('JUBEAT_FONT_DIR') or r'<GAME_ROOT>\data\font'
DEFAULT_FONT = os.path.join(FONT_DIR, 'dfgothw5.ttc')
DEFAULT_SIZE = {'mini': 13, 'wide': 18}
DEFAULT_POS = {'mini': (17, 4), 'wide': (4, 1)}
ARTIST_SIZE = 13
ARTIST_POS = {'mini': None, 'wide': (4, 24)}

#: 曲名文本条要渲染中文（如 marker "[自制]"），必须用**带 CJK 的字体**渲染，
#: 否则会画成一串白框。按优先级查找：
#:   ① 游戏自己的日文字体 <游戏根>/data/font/dfgothw5.ttc（首选，风格最贴合）
#:   ② 环境变量 JUBEAT_FONT_DIR 指定的目录
#:   ③ 打包自带的思源黑体（assets/ub_cjk_font.otf）—— 保证任何机器都能渲染
_CJK_FALLBACKS = ('ub_cjk_font.otf', 'ub_cjk_font.ttf')


def game_font(game=None) -> str:
    """返回用于渲染曲名文本条的字体文件路径（找不到返回 None）。

    🔴 曾经把路径硬编码成开发机的 `F:\\jubeat_pack_src\\...`，在实机上必然找不到，
    于是回退到 arial —— arial 没有汉字，`[自制]` 就被画成一串白框。
    """
    cands = []
    if game:
        cands.append(os.path.join(game, 'data', 'font', 'dfgothw5.ttc'))
    if os.environ.get('JUBEAT_FONT_DIR'):
        cands.append(os.path.join(FONT_DIR, 'dfgothw5.ttc'))
    cands.append(DEFAULT_FONT)
    here = os.path.dirname(os.path.abspath(__file__))
    for name in _CJK_FALLBACKS:
        cands.append(os.path.join(here, 'assets', name))
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        for name in _CJK_FALLBACKS:
            cands.append(os.path.join(meipass, 'assets', name))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _load_font(fp, pt):
    """加载字体；fp 为空/加载失败时依次尝试 CJK 兜底字体，最后才用 PIL 默认。"""
    tried = []
    for cand in (fp, game_font()):
        if cand and cand not in tried:
            tried.append(cand)
            try:
                return ImageFont.truetype(cand, pt)
            except Exception:                                # noqa: BLE001
                pass
    for name in ('arial.ttf', 'Arial.ttf'):
        try:
            return ImageFont.truetype(name, pt)
        except Exception:                                    # noqa: BLE001
            pass
    return ImageFont.load_default()


def make_title_png(text, slot='mini', marker='', artist=None, font=None, size=None,
                   x=None, y=None, color=(255, 255, 255, 255), autofit=True) -> Image.Image:
    """生成白字 + 透明底的曲名文本条 PNG。
       mini=160x24（单行曲名）、wide=324x40（曲名 + 可选艺术家第二行）。
       marker（如 '[自制]'）会以空格接到曲名**之前**（前缀，曲名后置会看不清归属）。
       autofit=True 时，若文字超宽会自动缩小字号以适配（否则会被画布裁掉）。"""
    W, H = SLOT_SIZE[slot]
    full = text if not marker else '%s %s' % (marker, text)
    fp = font or DEFAULT_FONT
    pt = size or DEFAULT_SIZE.get(slot, 11)
    dx, dy = DEFAULT_POS.get(slot, (2, 3))
    if x is not None:
        dx = x
    if y is not None:
        dy = y

    def _fits(f, s):
        try:
            bb = f.getbbox(full)
            return (bb[2] - bb[0]) <= W - dx
        except Exception:
            return True

    f = _load_font(fp, pt)
    if autofit:
        while pt > 6 and not _fits(f, pt):
            pt -= 1
            f = _load_font(fp, pt)
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((dx, dy), full, font=f, fill=color)
    if slot == 'wide' and artist:
        ap = ARTIST_SIZE
        ax, ay = ARTIST_POS.get(slot) or (dx, dy)
        if x is not None and slot == 'wide':
            ax = x
        fa = _load_font(fp, ap)
        if autofit:
            while ap > 5 and not (fa.getbbox(artist)[2] - fa.getbbox(artist)[0]) <= W - ax:
                ap -= 1
                fa = _load_font(fp, ap)
        if ARTIST_POS.get(slot) is None:
            try:
                ay = dy + f.getbbox(full)[3] + 1
            except Exception:
                ay = dy + pt + 2
        d.text((ax, ay), artist, font=fa, fill=color)
    img.info['used_size'] = pt
    img.info['used_pos'] = (dx, dy)
    return img


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_fit(s):
    if not s:
        return None
    s = s.lower().replace('x', ' ').replace('*', ' ')
    a, b = s.split()
    return (int(a), int(b))


def main(argv=None):
    ap = argparse.ArgumentParser(description='jubeat TDXT 纹理编码器 + PXET 图集写回')
    sub = ap.add_subparsers(dest='cmd', required=True)

    pe = sub.add_parser('encode', help='PNG -> TDXT 文件')
    pe.add_argument('--png', required=True)
    pe.add_argument('--out', required=True)
    pe.add_argument('--fmt', default=None)
    pe.add_argument('--fit', default=None)
    pe.add_argument('--stretch', action='store_true')
    pe.add_argument('--hf3', type=lambda v: int(v, 0), default=None)
    pe.add_argument('--compress', action='store_true')
    pe.add_argument('--slot', choices=list(SLOT_SIZE), default=None)

    pr = sub.add_parser('replace', help='把 PNG 写回 PXET 图集（新增/替换同名）')
    pr.add_argument('--atlas', required=True)
    pr.add_argument('--name', required=True)
    pr.add_argument('--png', required=True)
    pr.add_argument('--out', required=True)
    pr.add_argument('--fmt', default=None)
    pr.add_argument('--fit', default=None)
    pr.add_argument('--stretch', action='store_true')
    pr.add_argument('--hf3', type=lambda v: int(v, 0), default=None)
    pr.add_argument('--compress', action='store_true')

    pt = sub.add_parser('roundtrip', help='解码->重编码->逐字节比较')
    pt.add_argument('--file', required=True)
    pt.add_argument('--png-out', default=None)
    pt.add_argument('--fmt', default=None)

    pti = sub.add_parser('title', help='生成曲名文本条 PNG')
    pti.add_argument('--text', required=True)
    pti.add_argument('--marker', default='')
    pti.add_argument('--artist', default=None)
    pti.add_argument('--slot', choices=['mini', 'wide'], default='mini')
    pti.add_argument('--out', required=True)
    pti.add_argument('--font', default=None)
    pti.add_argument('--size', type=int, default=None)
    pti.add_argument('--x', type=int, default=None)
    pti.add_argument('--y', type=int, default=None)

    a = ap.parse_args(argv)

    if a.cmd == 'encode':
        fit = _parse_fit(a.fit) or (SLOT_SIZE[a.slot] if a.slot else None)
        tdxt = encode_png_to_tdxt(a.png, a.fmt, fit, a.stretch, a.hf3)
        h = parse_tdxt_header(tdxt)
        open(a.out, 'wb').write(tdxt)
        rec = make_pxet_record(tdxt, a.compress)
        print('encode: %s -> %s  fmt=0x%02X %dx%d hf3=%d  tdxt=%dB  record=%dB%s'
              % (a.png, a.out, h['fmt'], h['w'], h['h'], h['hf3'], len(tdxt), len(rec),
                 ' (LZSS)' if a.compress else ' (plain)'))
    elif a.cmd == 'replace':
        fit = _parse_fit(a.fit)
        rep = replace_in_atlas(a.atlas, a.name, a.png, a.out, a.fmt, fit, a.stretch,
                               a.hf3, a.compress)
        print('replace[%s]: %s  条目 %d -> %d  产出 %d B  fmt=0x%02X %dx%d hf3=%s'
              % (rep['action'], rep['out'], rep['n_before'], rep['n_after'], rep['size'],
                 rep['fmt'], rep['size_px'][0], rep['size_px'][1], rep['hf3']))
    elif a.cmd == 'roundtrip':
        r = roundtrip_file(a.file, a.fmt, a.png_out)
        if r['kind'] == 'pxet':
            ok = sum(1 for e in r['entries'] if e.get('byte_equal'))
            print('roundtrip PXET %s：%d 条，逐字节一致 %d 条' % (r['name'], len(r['entries']), ok))
            for e in r['entries']:
                print('   %-22s fmt=0x%02X %dx%d  same_fmt=%s byte_equal=%s diff=%d PSNR=%.2f%s'
                      % (e.get('name'), e.get('fmt', 0), e.get('w', 0), e.get('h', 0),
                         e.get('same_fmt'), e.get('byte_equal'), e.get('n_bytes_diff', -1),
                         e.get('psnr', 0), ' 无损' if e.get('lossless') else ' 有损'))
        else:
            for e in r['entries']:
                print('roundtrip %s：fmt=0x%02X %dx%d byte_equal=%s PSNR=%.2f'
                      % (r['name'], e.get('fmt', 0), e.get('w', 0), e.get('h', 0),
                         e.get('byte_equal'), e.get('psnr', 0)))
    elif a.cmd == 'title':
        img = make_title_png(a.text, a.slot, a.marker, a.artist, a.font, a.size, a.x, a.y)
        img.save(a.out)
        op = sum(1 for p in _pixels(img) if p[3] > 0)
        tr = 100.0 * (1 - op / (img.width * img.height))
        print('title: %s  %dx%d  不透明像素=%d (透明率 %.1f%%)  font=%s size=%d%s x,y=%s'
              % (a.out, img.width, img.height, op, tr,
                 os.path.basename(a.font or DEFAULT_FONT), img.info['used_size'],
                 '(autofit)' if (a.size is None or img.info['used_size'] != (a.size or 0)) else '',
                 img.info['used_pos']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
