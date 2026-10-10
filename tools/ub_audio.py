#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ub_audio.py -- jubeat `_msc.ifs` 音频成员（Konami BMP / OKI4S ADPCM）编解码

用途：让新装的**自制谱使用它自带的音源**，而不是借已有曲的音频。

格式（实测自 90001036/90000122 等真机样本）：
    Konami BMP 头 32 字节：
      0x00  "BMP\\0"
      0x04  >I  num_samples   （每声道采样帧数；立体声时 == ADPCM 字节数）
      0x08  >I  loop_start    （游戏内样本样本多为 0 = 不循环）
      0x0C  >I  loop_end
      0x10  <H  channels (2)     0x12 <H bits (16)
      0x14  >I  sample_rate (48000)
      0x18  8 字节 0
      0x20  OKI4S ADPCM 数据
    文件总长 = 0x20 + len(adpcm)

OKI4S 步进（与 vgmstream coding_OKI4S 一致）：
    编码值 code(0..15)：bit0=+1档 bit1=+2档 bit2=+4档，bit3=符号
    step = STEP_SIZES[step_index] << 4
    delta = step>>3 (+ step>>2 if bit0) (+ step>>1 if bit1) (+ step if bit2)，bit3 取负
    step_index += STEX[code]，钳到 0..48；输出样本钳到 int16
    → 因此 code 的幅度档位恒为 (2+4k)*S, k=0..7（S=STEP_SIZES[idx]），可**解析求最优 code**，
      不必逐样本 16 路暴力搜索。

自验：编码→参考解码 往返 SNR 见 `ub_audio.py roundtrip`
"""
from __future__ import annotations

import array
import math
import os
import shutil
import struct
import subprocess
import sys

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
STEP_SIZES = (
    16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66,
    73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658,
    724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552,
)
STEX_INDEXES = (-1, -1, -1, -1, 2, 4, 6, 8,
                -1, -1, -1, -1, 2, 4, 6, 8)

BMP_MAGIC = b"BMP\x00"
BMP_DATA_OFFSET = 0x20
DEFAULT_RATE = 48000
DEFAULT_CHANNELS = 2
PREVIEW_SECONDS = 10.0          # 实测官方 idx.bin 恒为 480000 帧 @48k = 10.0s
AUDIO_EXTS = (".ogg", ".mp3", ".wav", ".m4a", ".flac", ".aac", ".opus")

_FFMPEG_ENV = "UB_FFMPEG"


def is_audio_ext(name: str) -> bool:
    """按扩展名判断是否可能是音频。"""
    return os.path.splitext(name or "")[1].lower() in AUDIO_EXTS


def looks_like_audio(blob) -> bool:
    """按容器 magic 判断字节流是否像音频。

    为什么需要：Malody 商店下载的 `.mc` 是 **zip 包装**（外层 zip → 内层真正的谱面），
    内层成员名常常**就是曲名**（如 `Dadadadadadadadadada.mc`）。仅靠"文件名像不像"
    会把这个 JSON 谱面当成音源抓出来，送进 ffmpeg 就报
    `Invalid data found when processing input`。这里加一道内容兜底。
    """
    if not blob or len(blob) < 12:
        return False
    h = bytes(blob[:16])
    if h[:4] in (b"OggS", b"fLaC", b"RIFF", b"ADIF", b"MAC ", b"FORM", b"MThd"):
        return True
    if h[:3] == b"ID3":
        return True
    if h[4:8] == b"ftyp":                     # mp4 / m4a / mov 家族
        return True
    if h[0] == 0xFF and (h[1] & 0xE0) == 0xE0:   # MP3 / ADTS 帧同步
        return True
    return False



# ---------------------------------------------------------------------------
# 参考解码器（与游戏/ vgmstream 一致；逐位精确）
# ---------------------------------------------------------------------------
def _expand_nibble(byte_val: int, shift: int, hist: int, step_index: int):
    code = (byte_val >> shift) & 0xF
    step = STEP_SIZES[step_index] << 4
    delta = step >> 3
    if code & 1:
        delta += step >> 2
    if code & 2:
        delta += step >> 1
    if code & 4:
        delta += step
    if code & 8:
        delta = -delta
    h = hist + delta
    h = 32767 if h > 32767 else (-32768 if h < -32768 else h)
    ns = step_index + STEX_INDEXES[code]
    ns = 0 if ns < 0 else (48 if ns > 48 else ns)
    return h, ns


def decode_oki4s(adpcm: bytes, channels: int, num_samples: int,
                 on_progress=None) -> bytes:
    """OKI4S -> 交错 int16 LE PCM。channels 支持 1/2。"""
    if channels not in (1, 2):
        raise ValueError("不支持声道数 %r" % channels)
    if channels == 2:
        num_samples = min(num_samples, len(adpcm))
    else:
        num_samples = min(num_samples, len(adpcm) * 2)

    out = bytearray(num_samples * channels * 2)
    hists = [0, 0]
    steps = [0, 0]
    every = max(1, num_samples // 200)
    pack = struct.pack_into

    for i in range(num_samples):
        if channels == 2:
            bv = adpcm[i] if i < len(adpcm) else 0
            for ch in (0, 1):
                s, steps[ch] = _expand_nibble(bv, 4 if ch == 0 else 0,
                                              hists[ch], steps[ch])
                hists[ch] = s
                pack("<h", out, (i * 2 + ch) * 2, s)
        else:
            bv = adpcm[i // 2] if (i // 2) < len(adpcm) else 0
            s, steps[0] = _expand_nibble(bv, 0 if (i & 1) else 4, hists[0], steps[0])
            hists[0] = s
            pack("<h", out, i * 2, s)
        if on_progress and i % every == 0:
            on_progress(i / num_samples)
    if on_progress:
        on_progress(1.0)
    return bytes(out)


def parse_bmp(data: bytes):
    """解析 Konami BMP 头；非法返回 None。"""
    if len(data) < BMP_DATA_OFFSET or data[:4] != BMP_MAGIC:
        return None
    num_samples, loop_start, loop_end = struct.unpack(">III", data[4:16])
    channels = struct.unpack("<H", data[16:18])[0]
    bits = struct.unpack("<H", data[18:20])[0]
    rate = struct.unpack(">I", data[20:24])[0]
    if channels not in (1, 2) or rate == 0:
        return None
    adpcm = data[BMP_DATA_OFFSET:]
    if num_samples == 0:
        num_samples = len(adpcm) if channels == 2 else len(adpcm) * 2
    return {"num_samples": num_samples, "loop_start": loop_start,
            "loop_end": loop_end, "channels": channels, "bits": bits,
            "sample_rate": rate, "adpcm": adpcm,
            "pad_tail": len(adpcm) - (num_samples if channels == 2 else
                                      (num_samples + 1) // 2)}


def build_bmp(pcm: bytes, num_samples: int, channels: int = DEFAULT_CHANNELS,
              rate: int = DEFAULT_RATE, loop_start: int = 0,
              loop_end: int = 0, adpcm: bytes = None) -> bytes:
    """把 OKI4S ADPCM（或从 pcm 现场编码）组装成 Konami BMP。"""
    if adpcm is None:
        adpcm = encode_oki4s(pcm, channels, num_samples)
    head = bytearray(BMP_DATA_OFFSET)
    head[0:4] = BMP_MAGIC
    struct.pack_into(">III", head, 4, num_samples, loop_start, loop_end)
    struct.pack_into("<HH", head, 16, channels, 16)
    struct.pack_into(">I", head, 20, rate)
    return bytes(head) + adpcm


# ---------------------------------------------------------------------------
# 编码器：解析式最优 code（等价于逐样本 16 路搜索的最优解，但 O(1)）
# ---------------------------------------------------------------------------
_TABLE = None           # _TABLE[idx][|d|] -> k(0..7)，d = target-hist，|d|<=65535


def _code_table():
    """构造 (49 × 65536) 的最优档位表：k 最小化 ||d|-(2+4k)*S|。"""
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    lev = [2, 6, 10, 14, 18, 22, 26, 30]
    try:
        import numpy as np
        mags = np.arange(65536, dtype=np.int32)
        table = []
        for s in STEP_SIZES:
            lv = np.array([l * s for l in lev], dtype=np.int32)
            table.append(np.abs(mags[:, None] - lv[None, :]).argmin(axis=1).tolist())
        _TABLE = table
    except Exception:                                   # numpy 缺失：纯 Python 兜底
        table = []
        for s in STEP_SIZES:
            lv = [l * s for l in lev]
            table.append([min(range(8), key=lambda k: abs(m - lv[k]))
                          for m in range(65536)])
        _TABLE = table
    return _TABLE


def encode_oki4s(pcm: bytes, channels: int, num_samples: int,
                 on_progress=None) -> bytes:
    """交错 int16 LE PCM -> OKI4S 字节流（立体声时 1 字节/帧，ch0=高半字节）。"""
    if channels not in (1, 2):
        raise ValueError("不支持声道数 %r" % channels)
    tab = _code_table()
    SS, SX = STEP_SIZES, STEX_INDEXES
    every = max(1, num_samples // 200)

    if channels == 2:
        smp = array.array("h")
        smp.frombytes(pcm[: num_samples * 4])
        if sys.byteorder == "big":
            smp.byteswap()
        out = bytearray(num_samples)
        h0 = h1 = 0
        i0 = i1 = 0
        t0r = tab
        for i in range(num_samples):
            d = smp[2 * i] - h0
            if d >= 0:
                m = d if d <= 65535 else 65535
                c0 = t0r[i0][m]
            else:
                m = -d
                c0 = t0r[i0][m if m <= 65535 else 65535] | 8
            st = SS[i0] << 4
            dl = st >> 3
            if c0 & 1:
                dl += st >> 2
            if c0 & 2:
                dl += st >> 1
            if c0 & 4:
                dl += st
            if c0 & 8:
                dl = -dl
            v = h0 + dl
            h0 = 32767 if v > 32767 else (-32768 if v < -32768 else v)
            n = i0 + SX[c0]
            i0 = 0 if n < 0 else (48 if n > 48 else n)

            d = smp[2 * i + 1] - h1
            if d >= 0:
                m = d if d <= 65535 else 65535
                c1 = t0r[i1][m]
            else:
                m = -d
                c1 = t0r[i1][m if m <= 65535 else 65535] | 8
            st = SS[i1] << 4
            dl = st >> 3
            if c1 & 1:
                dl += st >> 2
            if c1 & 2:
                dl += st >> 1
            if c1 & 4:
                dl += st
            if c1 & 8:
                dl = -dl
            v = h1 + dl
            h1 = 32767 if v > 32767 else (-32768 if v < -32768 else v)
            n = i1 + SX[c1]
            i1 = 0 if n < 0 else (48 if n > 48 else n)

            out[i] = (c0 << 4) | c1
            if on_progress and i % every == 0:
                on_progress(i / num_samples)
        if on_progress:
            on_progress(1.0)
        return bytes(out)

    # 单声道：每字节 2 个样本（偶=高半字节）
    smp = array.array("h")
    smp.frombytes(pcm[: num_samples * 2])
    if sys.byteorder == "big":
        smp.byteswap()
    out = bytearray((num_samples + 1) // 2)
    h = 0
    idx = 0
    for i in range(num_samples):
        d = smp[i] - h
        if d >= 0:
            m = d if d <= 65535 else 65535
            c = tab[idx][m]
        else:
            m = -d
            c = tab[idx][m if m <= 65535 else 65535] | 8
        st = SS[idx] << 4
        dl = st >> 3
        if c & 1:
            dl += st >> 2
        if c & 2:
            dl += st >> 1
        if c & 4:
            dl += st
        if c & 8:
            dl = -dl
        v = h + dl
        h = 32767 if v > 32767 else (-32768 if v < -32768 else v)
        n = idx + SX[c]
        idx = 0 if n < 0 else (48 if n > 48 else n)
        out[i // 2] |= (c << 4) if (i & 1) == 0 else c
        if on_progress and i % every == 0:
            on_progress(i / num_samples)
    if on_progress:
        on_progress(1.0)
    return bytes(out)


def encode_oki4s_bruteforce(pcm: bytes, channels: int, num_samples: int) -> bytes:
    """逐样本 16 路暴力搜索（仅供测试比对，慢）。"""
    if channels != 2:
        raise ValueError("暴力版仅用于立体声测试")
    smp = array.array("h")
    smp.frombytes(pcm[: num_samples * 4])
    if sys.byteorder == "big":
        smp.byteswap()
    h = [0, 0]
    st = [0, 0]
    out = bytearray(num_samples)
    for i in range(num_samples):
        codes = [0, 0]
        for ch in (0, 1):
            tgt = smp[2 * i + ch]
            best, bc, bh, bn = 1 << 30, 0, h[ch], st[ch]
            for c in range(16):
                v, n = _expand_nibble(c, 0, h[ch], st[ch])
                e = abs(v - tgt)
                if e < best:
                    best, bc, bh, bn = e, c, v, n
            codes[ch] = bc
            h[ch], st[ch] = bh, bn
        out[i] = (codes[0] << 4) | codes[1]
    return bytes(out)


# ---------------------------------------------------------------------------
# 外部解码：任意音频 -> 48k/16bit/立体声 交错 PCM（走 ffmpeg）
# ---------------------------------------------------------------------------
def ffmpeg_exe() -> str:
    cands = [os.environ.get(_FFMPEG_ENV), r"<FFMPEG>",
             r"C:\ffmpeg\bin\ffmpeg.exe", shutil.which("ffmpeg")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError("找不到 ffmpeg；可用环境变量 %s 指定完整路径" % _FFMPEG_ENV)


def ffprobe_exe() -> str:
    f = ffmpeg_exe()
    p = os.path.join(os.path.dirname(f), "ffprobe.exe")
    if os.path.exists(p):
        return p
    w = shutil.which("ffprobe")
    if w:
        return w
    raise FileNotFoundError("找不到 ffprobe")


def probe_audio(path: str) -> dict:
    """返回 {codec, sample_rate, channels, duration}。"""
    exe = ffprobe_exe()
    out = subprocess.run(
        [exe, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels,duration",
         "-of", "default=noprint_wrappers=1:nokey=0", path],
        capture_output=True, text=True)
    d = {}
    for line in out.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    if "duration" not in d or not d.get("duration"):
        out2 = subprocess.run([exe, "-v", "error", "-show_entries",
                               "format=duration", "-of",
                               "default=noprint_wrappers=1:nokey=1", path],
                              capture_output=True, text=True)
        d["duration"] = out2.stdout.strip()
    return d


def decode_to_pcm(src, rate: int = DEFAULT_RATE,
                  channels: int = DEFAULT_CHANNELS) -> bytes:
    """任意音源（文件路径 或 音频字节）-> 交错 int16 LE PCM（默认 48000Hz 立体声）。"""
    exe = ffmpeg_exe()
    if isinstance(src, (bytes, bytearray)):
        cmd = [exe, "-v", "error", "-i", "pipe:0",
               "-f", "s16le", "-acodec", "pcm_s16le",
               "-ac", str(channels), "-ar", str(rate), "-"]
        p = subprocess.run(cmd, input=bytes(src), capture_output=True)
    else:
        cmd = [exe, "-v", "error", "-i", src,
               "-f", "s16le", "-acodec", "pcm_s16le",
               "-ac", str(channels), "-ar", str(rate), "-"]
        p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        detail = p.stderr.decode("utf-8", "replace")[:400]
        if isinstance(src, (bytes, bytearray)):
            where = "内存字节 %d B，头部 %s" % (len(src), bytes(src[:16]).hex())
        else:
            try:
                size = os.path.getsize(src)
            except OSError:
                size = -1
            where = "%s（%d B）" % (src, size)
        raise RuntimeError("ffmpeg 解码失败（来源：%s）: %s" % (where, detail))
    return p.stdout


def write_wav(path: str, pcm: bytes, rate: int = DEFAULT_RATE,
              channels: int = DEFAULT_CHANNELS) -> None:
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def pcm_frames(pcm: bytes, channels: int = 2) -> int:
    return len(pcm) // (2 * channels)


def pcm_stats(pcm: bytes) -> dict:
    """RMS / 峰值 / 非零比例（用于判断"不是静音"）。"""
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        a.byteswap()
    n = len(a)
    if not n:
        return {"n": 0, "rms": 0.0, "peak": 0, "nonzero": 0.0}
    s2 = 0
    peak = 0
    nz = 0
    for v in a:
        s2 += v * v
        av = -v if v < 0 else v
        if av > peak:
            peak = av
        if v:
            nz += 1
    return {"n": n, "rms": math.sqrt(s2 / n), "peak": peak, "nonzero": nz / n}


def slice_pcm(pcm: bytes, channels: int, start_frame: int, frames: int) -> bytes:
    b = 2 * channels
    s = max(0, start_frame) * b
    return pcm[s: s + frames * b]


def make_preview(pcm: bytes, channels: int, rate: int, start_ms: int,
                 seconds: float = PREVIEW_SECONDS) -> bytes:
    """按谱面 meta.preview（毫秒）切出固定长度预览；越界自动回退到末尾。"""
    total = pcm_frames(pcm, channels)
    want = int(round(seconds * rate))
    start = int(round(start_ms / 1000.0 * rate))
    if total <= want:
        return pcm
    if start < 0:
        start = 0
    if start + want > total:
        start = total - want
    return slice_pcm(pcm, channels, start, want)


def similarity(a: bytes, b: bytes):
    """返回 (SNR dB, 相关系数)。"""
    n = min(len(a), len(b)) // 2
    x = array.array("h")
    x.frombytes(a[: n * 2])
    y = array.array("h")
    y.frombytes(b[: n * 2])
    if sys.byteorder == "big":
        x.byteswap()
        y.byteswap()
    sig = err = num = 0
    sa = sb = 0
    for i in range(n):
        va, vb = x[i], y[i]
        sig += va * va
        d = va - vb
        err += d * d
        num += va * vb
        sa += va * va
        sb += vb * vb
    snr = 10 * math.log10(sig / err) if err else 999.0
    corr = num / math.sqrt(sa * sb) if sa and sb else 0.0
    return snr, corr


# ---------------------------------------------------------------------------
# 谱面目录里找自带音源
# ---------------------------------------------------------------------------
def find_audio_file(folder: str, title: str = None):
    """在（已解包的）谱面目录里找音源：优先与标题同名的音频，其次最大的音频。"""
    if not os.path.isdir(folder):
        return None
    cands = [f for f in os.listdir(folder)
             if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
    if not cands:
        return None
    if title:
        t = title.strip().lower()
        for f in cands:
            if os.path.splitext(f)[0].strip().lower() == t:
                return os.path.join(folder, f)
    cands.sort(key=lambda f: os.path.getsize(os.path.join(folder, f)), reverse=True)
    return os.path.join(folder, cands[0])


def find_audio_near(mc_or_mcz_path: str, title: str = None):
    """从谱面文件出发找音源：先看同目录，再看同名文件夹。"""
    d = os.path.dirname(os.path.abspath(mc_or_mcz_path))
    hit = find_audio_file(d, title)
    if hit:
        return hit
    stem = os.path.splitext(os.path.basename(mc_or_mcz_path))[0]
    for sub in (stem, os.path.basename(d)):
        p = os.path.join(d, sub)
        hit = find_audio_file(p, title)
        if hit:
            return hit
    return None


def load_chart_audio(chart_path: str, title: str = None, hint: str = None,
                     meta_only: bool = False):
    """从谱面文件（.mcz / 被当作 zip 的 .mc / 目录里的 .mc）取出自带音源。

    返回 (label, bytes)；找不到返回 None。meta_only=True 时只返回 (label, None)
    —— 用于列表展示，避免把几 MB 音频读进内存。
    查找顺序：
      1) 谱面显式提示的文件名（meta.song.file）
      2) ZIP 内部条目（.mcz 原生包 / Malody 商店下载的 .mc 其实是 zip）
      3) 谱面同目录 / 同名子目录下的音频文件
    """
    import zipfile

    def _candidates(names):
        """按优先级返回**音频候选**：hint 命中 → 曲名命中 → 其余按路径长度升序。

        关键修复：hint / title 匹配**只在音频扩展名候选内**进行。
        Malody 商店下载的 `.mc` 是 zip 包装（外层 zip → 内层真正的谱面），
        内层成员名常常**正好等于曲名**（如 `Dadadadadadadadadada.mc`）。
        若不过滤扩展名，"按曲名匹配"会把这个 JSON 谱面当音源抓出来，
        送进 ffmpeg 就报 `Invalid data found when processing input`。
        """
        cands = [n for n in names if is_audio_ext(n)]
        if not cands:
            return []
        out = []
        if hint:
            hs = os.path.basename(hint).strip().lower()
            out += [n for n in cands
                    if os.path.basename(n).strip().lower() == hs]
        if title:
            t = title.strip().lower()
            out += [n for n in cands
                    if os.path.splitext(os.path.basename(n))[0].strip().lower() == t]
        out += sorted(cands, key=len)
        seen, uniq = set(), []
        for n in out:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        return uniq

    # 1/2) ZIP 容器
    try:
        if os.path.exists(chart_path) and zipfile.is_zipfile(chart_path):
            with zipfile.ZipFile(chart_path) as z:
                infos = [i for i in z.infolist() if not i.is_dir()]
                for n in _candidates([i.filename for i in infos]):
                    if meta_only:
                        return (n, None)
                    try:
                        blob = z.read(n)
                    except Exception:
                        continue
                    # 内容兜底：扩展名像音频但内容不是（如被 zip 包了一层）就跳过
                    if looks_like_audio(blob):
                        return (n, blob)
    except Exception:
        pass

    # 3) 目录
    p = find_audio_near(chart_path, title)
    if p:
        if meta_only:
            return (p, None)
        with open(p, "rb") as f:
            return (p, f.read())
    return None


# ---------------------------------------------------------------------------
# 高层：给一个新曲 IFS 换上自带音源
# ---------------------------------------------------------------------------
def replace_ifs_audio(ifs_obj, main_pcm: bytes, preview_pcm: bytes = None,
                      rate: int = DEFAULT_RATE, channels: int = DEFAULT_CHANNELS):
    """把 IFS 里的音频成员换成自带音源（main -> bgm 类成员；preview -> idx 类成员）。
       返回改动报告。只改音频成员，谱面成员原样不动。"""
    audios = ifs_obj.audio_members()
    if not audios:
        raise ValueError("该 _msc.ifs 里没有音频成员")

    def _head_frames(m):
        return parse_bmp(m.data)["num_samples"] if m.data[:4] == BMP_MAGIC else -1

    # 按帧数区分主曲 / 预览（预览明显更短）
    audios = sorted(audios, key=_head_frames, reverse=True)
    main = audios[0]
    preview = audios[1] if len(audios) > 1 else None

    main_frames = pcm_frames(main_pcm, channels)
    main.data = build_bmp(main_pcm, main_frames, channels, rate, 0, 0)
    rep = {"main": {"member": main.name, "frames": main_frames,
                    "seconds": main_frames / float(rate),
                    "bytes": len(main.data)}}
    if preview is not None:
        pv = preview_pcm if preview_pcm is not None else make_preview(
            main_pcm, channels, rate, 0)
        pv_frames = pcm_frames(pv, channels)
        old = _head_frames(preview)
        preview.data = build_bmp(pv, pv_frames, channels, rate, 0, 0)
        rep["preview"] = {"member": preview.name, "frames": pv_frames,
                          "seconds": pv_frames / float(rate),
                          "bytes": len(preview.data), "old_frames": old}
    return rep


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cmd_probe(a):
    data = open(a.file, "rb").read()
    h = parse_bmp(data)
    if not h:
        print("不是 Konami BMP")
        return 1
    print("file        : %s (%d B)" % (a.file, len(data)))
    print("channels    : %d" % h["channels"])
    print("rate        : %d" % h["sample_rate"])
    print("num_samples : %d  (%.2f s)" % (h["num_samples"],
                                          h["num_samples"] / float(h["sample_rate"])))
    print("loop        : %d .. %d" % (h["loop_start"], h["loop_end"]))
    print("adpcm bytes : %d  pad_tail=%d" % (len(h["adpcm"]), h["pad_tail"]))
    return 0


def _cmd_decode(a):
    h = parse_bmp(open(a.file, "rb").read())
    if not h:
        print("不是 Konami BMP", file=sys.stderr)
        return 1
    pcm = decode_oki4s(h["adpcm"], h["channels"], h["num_samples"])
    write_wav(a.out, pcm, h["sample_rate"], h["channels"])
    st = pcm_stats(pcm)
    print("-> %s  %.2fs  RMS=%.0f peak=%d nonzero=%.1f%%"
          % (a.out, h["num_samples"] / float(h["sample_rate"]),
             st["rms"], st["peak"], st["nonzero"] * 100))
    return 0


def _cmd_encode(a):
    pcm = decode_to_pcm(a.src, rate=a.rate, channels=a.channels)
    n = pcm_frames(pcm, a.channels)
    blob = build_bmp(pcm, n, a.channels, a.rate, a.loop_start, a.loop_end)
    with open(a.out, "wb") as f:
        f.write(blob)
    print("-> %s  %d frames (%.2fs)  %d B  (PCM %d B)"
          % (a.out, n, n / float(a.rate), len(blob), len(pcm)))
    return 0


def _cmd_roundtrip(a):
    """自验：真实音频 -> 48k 立体声 -> OKI4S -> 解码，算 SNR / 相关系数。"""
    pcm = decode_to_pcm(a.src, rate=a.rate, channels=2)
    if a.seconds:
        pcm = pcm[: int(a.seconds * a.rate) * 4]
    n = pcm_frames(pcm, 2)
    enc = encode_oki4s(pcm, 2, n)
    dec = decode_oki4s(enc, 2, n)
    snr, corr = similarity(pcm, dec)
    print("源           : %s" % a.src)
    print("帧数         : %d (%.2f s)  PCM %d B" % (n, n / float(a.rate), len(pcm)))
    print("OKI4S 字节   : %d  (压缩比 %.2f%%)" % (len(enc), len(enc) * 100.0 / len(pcm)))
    print("往返 SNR     : %.1f dB   相关系数 %.4f" % (snr, corr))
    ok = corr > 0.95 and snr > 15
    print("判定         : %s" % ("达标" if ok else "不达标"))
    if a.out:
        write_wav(a.out, dec, a.rate, 2)
        print("试听文件     : %s" % a.out)
    return 0 if ok else 1


def _cmd_fastcheck(a):
    """校验快速解析式编码器与暴力搜索编码器是否给出同样的最优选择。"""
    pcm = decode_to_pcm(a.src, rate=a.rate, channels=2)
    n = min(pcm_frames(pcm, 2), int(a.seconds * a.rate))
    pcm = pcm[: n * 4]
    fast = encode_oki4s(pcm, 2, n)
    brute = encode_oki4s_bruteforce(pcm, 2, n)
    diff = sum(1 for i in range(n) if fast[i] != brute[i])
    snr_f, corr_f = similarity(pcm, decode_oki4s(fast, 2, n))
    snr_b, corr_b = similarity(pcm, decode_oki4s(brute, 2, n))
    print("帧数         : %d" % n)
    print("与暴力版不同 : %d / %d  (%.4f%%)" % (diff, n, diff * 100.0 / n))
    print("快速版 SNR   : %.2f dB  相关系数 %.6f" % (snr_f, corr_f))
    print("暴力版 SNR   : %.2f dB  相关系数 %.6f" % (snr_b, corr_b))
    return 0


def build_parser():
    import argparse
    p = argparse.ArgumentParser(prog="ub_audio.py",
                                description="Konami BMP / OKI4S 音频编解码")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("probe", help="解析 Konami BMP 头")
    a.add_argument("file")
    a.set_defaults(func=_cmd_probe)

    b = sub.add_parser("decode", help="Konami BMP -> WAV")
    b.add_argument("file")
    b.add_argument("--out", required=True)
    b.set_defaults(func=_cmd_decode)

    c = sub.add_parser("encode", help="任意音频 -> Konami BMP")
    c.add_argument("src")
    c.add_argument("--out", required=True)
    c.add_argument("--rate", type=int, default=DEFAULT_RATE)
    c.add_argument("--channels", type=int, default=2)
    c.add_argument("--loop-start", type=int, default=0)
    c.add_argument("--loop-end", type=int, default=0)
    c.set_defaults(func=_cmd_encode)

    d = sub.add_parser("roundtrip", help="编码->解码往返质量自验")
    d.add_argument("src")
    d.add_argument("--rate", type=int, default=DEFAULT_RATE)
    d.add_argument("--seconds", type=float, default=20.0)
    d.add_argument("--out", default=None)
    d.set_defaults(func=_cmd_roundtrip)

    e = sub.add_parser("fastcheck", help="快速编码器 vs 暴力搜索编码器一致性")
    e.add_argument("src")
    e.add_argument("--rate", type=int, default=DEFAULT_RATE)
    e.add_argument("--seconds", type=float, default=3.0)
    e.set_defaults(func=_cmd_fastcheck)
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
