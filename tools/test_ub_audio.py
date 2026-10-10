#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ub_audio.py -- Konami BMP / OKI4S 音频编解码回归测试

不依赖真机、不依赖 ffmpeg：全部用合成信号。
覆盖：
  * Konami BMP 头逐字段（0x20 偏移、num_samples、loop、channels、bits、rate、8B 零填充）
  * 参考解码器与编码器的往返质量（SNR / 相关系数）
  * 解析式快速编码器 vs 逐样本 16 路暴力搜索：质量不劣
  * save/load 闭环：decode(encode(x)) 的长度与统计一致
  * make_preview 的起点/长度/越界回退
  * slice_pcm 边界
  * parse_bmp 对非法输入的容忍
"""
import array
import math
import os
import struct
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ub_audio as UA          # noqa: E402

RATE = 48000


def _tone(n: int, freq=440.0, amp=12000, ch=2) -> bytes:
    """合成正弦（带一点立体声差异），返回交错 int16 LE。"""
    a = array.array("h")
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * freq * i / RATE))
        w = int(amp * 0.6 * math.sin(2 * math.pi * freq * 1.5 * i / RATE))
        a.append(max(-32768, min(32767, v)))
        a.append(max(-32768, min(32767, w)))
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _noise(n: int, seed=1) -> bytes:
    """伪随机（近似白噪声，考验编码器）。"""
    st = seed
    a = array.array("h")
    for _ in range(n * 2):
        st = (1103515245 * st + 12345) & 0x7FFFFFFF
        a.append((st >> 8 & 0xFFFF) - 32768)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


class TestKonamiBmp(unittest.TestCase):
    def test_build_and_parse_header(self):
        n = 1000
        pcm = _tone(n)
        blob = UA.build_bmp(pcm, n, channels=2, rate=48000, loop_start=0, loop_end=0)
        self.assertEqual(blob[:4], b"BMP\x00")
        self.assertEqual(len(blob), UA.BMP_DATA_OFFSET + n)      # 立体声 1 字节/帧
        self.assertEqual(blob[0x18:0x20], b"\x00" * 8)           # 8B 零填充
        h = UA.parse_bmp(blob)
        self.assertEqual(h["num_samples"], n)
        self.assertEqual(h["channels"], 2)
        self.assertEqual(h["bits"], 16)
        self.assertEqual(h["sample_rate"], 48000)
        self.assertEqual((h["loop_start"], h["loop_end"]), (0, 0))
        self.assertEqual(len(h["adpcm"]), n)

    def test_parse_rejects_garbage(self):
        self.assertIsNone(UA.parse_bmp(b""))
        self.assertIsNone(UA.parse_bmp(b"BMP\x00" + b"\x00" * 8))
        self.assertIsNone(UA.parse_bmp(b"XXXX" + b"\x00" * 64))

    def test_roundtrip_quality_tone(self):
        n = 48000                                   # 1 秒
        pcm = _tone(n)
        enc = UA.encode_oki4s(pcm, 2, n)
        dec = UA.decode_oki4s(enc, 2, n)
        self.assertEqual(len(enc), n)
        self.assertEqual(len(dec), len(pcm))
        snr, corr = UA.similarity(pcm, dec)
        self.assertGreater(snr, 20.0, "SNR 过低: %.1f" % snr)
        self.assertGreater(corr, 0.95, "相关系数过低: %.4f" % corr)

    def test_roundtrip_quality_noise(self):
        n = 24000
        pcm = _noise(n)
        enc = UA.encode_oki4s(pcm, 2, n)
        dec = UA.decode_oki4s(enc, 2, n)
        snr, corr = UA.similarity(pcm, dec)
        # 4bit ADPCM 对白噪声本来就差，这里只要求不崩、且明显优于"全静音"
        self.assertEqual(len(dec), len(pcm))
        self.assertGreater(corr, 0.5, "相关系数过低: %.4f" % corr)

    def test_fast_encoder_not_worse_than_bruteforce(self):
        n = 6000
        pcm = _tone(n, freq=300)
        fast = UA.encode_oki4s(pcm, 2, n)
        brute = UA.encode_oki4s_bruteforce(pcm, 2, n)
        sf, cf = UA.similarity(pcm, UA.decode_oki4s(fast, 2, n))
        sb, cb = UA.similarity(pcm, UA.decode_oki4s(brute, 2, n))
        # 允许极小差异（极值钳位导致），但质量不允许明显变差
        self.assertGreater(cf, cb - 0.01, "快速版相关系数明显偏低")
        self.assertGreater(sf, sb - 1.0, "快速版 SNR 明显偏低")

    def test_silence_stays_near_zero(self):
        """静音输入：OKI4S 最小步进是 ±2*STEP[0]=±32（官方文件同样如此，
           见真机 bgm.bin 开头 `00 88 00 88`），所以只能要求「接近静音」。"""
        n = 4800
        pcm = b"\x00" * (n * 4)
        enc = UA.encode_oki4s(pcm, 2, n)
        dec = UA.decode_oki4s(enc, 2, n)
        a = array.array("h")
        a.frombytes(dec)
        self.assertLessEqual(max(abs(v) for v in a), 64, "静音解码偏移过大")
        self.assertLess(UA.pcm_stats(dec)["rms"], 100.0)

    def test_encode_build_bmp_equals_decode_of_decode(self):
        """build_bmp 产出的字节，decode 后应与原 PCM 质量一致（闭环）。"""
        n = 12000
        pcm = _tone(n, freq=660)
        blob = UA.build_bmp(pcm, n, 2, RATE)
        h = UA.parse_bmp(blob)
        dec = UA.decode_oki4s(h["adpcm"], h["channels"], h["num_samples"])
        snr, corr = UA.similarity(pcm, dec)
        self.assertGreater(corr, 0.95)

    # ---- 预览 / 切片 ----
    def test_make_preview_start_and_len(self):
        n = 20 * RATE
        pcm = _tone(n, freq=200)
        pv = UA.make_preview(pcm, 2, RATE, start_ms=5000, seconds=10.0)
        self.assertEqual(UA.pcm_frames(pv, 2), 10 * RATE)
        want = UA.slice_pcm(pcm, 2, 5 * RATE, 10 * RATE)
        self.assertEqual(pv, want)

    def test_make_preview_clamps_to_tail(self):
        n = 12 * RATE                                   # 只有 12 秒
        pcm = _tone(n, freq=200)
        pv = UA.make_preview(pcm, 2, RATE, start_ms=20_000, seconds=10.0)
        self.assertEqual(UA.pcm_frames(pv, 2), 10 * RATE)
        # 起点被回退到 total-want = 2s
        self.assertEqual(pv, UA.slice_pcm(pcm, 2, 2 * RATE, 10 * RATE))

    def test_make_preview_shorter_than_want(self):
        n = 3 * RATE
        pcm = _tone(n, freq=200)
        pv = UA.make_preview(pcm, 2, RATE, start_ms=0, seconds=10.0)
        self.assertEqual(pv, pcm)

    def test_slice_boundaries(self):
        n = 1000
        pcm = _tone(n)
        self.assertEqual(len(UA.slice_pcm(pcm, 2, 0, 0)), 0)
        self.assertEqual(len(UA.slice_pcm(pcm, 2, -5, 10)), 10 * 4)
        self.assertEqual(UA.slice_pcm(pcm, 2, 0, n), pcm)

    def test_pcm_stats(self):
        n = 1000
        st = UA.pcm_stats(_tone(n))
        self.assertEqual(st["n"], n * 2)
        self.assertGreater(st["rms"], 1000)
        self.assertLessEqual(st["peak"], 32768)
        self.assertGreater(st["nonzero"], 0.9)
        z = UA.pcm_stats(b"\x00" * 400)
        self.assertEqual(z["rms"], 0.0)
        self.assertEqual(z["nonzero"], 0.0)


class TestChartAudioLookup(unittest.TestCase):
    """音源查找：目录 / zip 两种形态（不联网）。"""

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp(prefix="ubaud_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_find_audio_in_folder_by_title(self):
        d = os.path.join(self.root, "160392")
        os.makedirs(d)
        for nm in ("Calamity Fortune.ogg", "1788237285.mc", "Calamity Fortune.jpg"):
            open(os.path.join(d, nm), "wb").write(b"x" * 16)
        hit = UA.find_audio_file(d, "Calamity Fortune")
        self.assertEqual(os.path.basename(hit), "Calamity Fortune.ogg")

    def test_load_chart_audio_meta_only_does_not_read_bytes(self):
        import zipfile
        d = os.path.join(self.root, "1")
        os.makedirs(d)
        z = os.path.join(d, "a.mc")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("a.mc", "{}")
            zf.writestr("song.ogg", b"X" * 1000)
        got = UA.load_chart_audio(z, title="song", meta_only=True)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "song.ogg")
        self.assertIsNone(got[1])

    def test_load_chart_audio_reads_bytes_from_folder(self):
        d = os.path.join(self.root, "2")
        os.makedirs(d)
        open(os.path.join(d, "chart.mc"), "wb").write(b"{}")
        open(os.path.join(d, "chart.ogg"), "wb").write(b"Y" * 512)
        got = UA.load_chart_audio(os.path.join(d, "chart.mc"), title="chart")
        # 目录形态下 label 是完整路径；zip 条目形态下是内部条目名
        self.assertEqual(os.path.basename(got[0]), "chart.ogg")
        self.assertEqual(len(got[1]), 512)

    def test_missing_audio_returns_none(self):
        d = os.path.join(self.root, "3")
        os.makedirs(d)
        open(os.path.join(d, "chart.mc"), "wb").write(b"{}")
        self.assertIsNone(UA.load_chart_audio(os.path.join(d, "chart.mc"), title="chart"))

    # ---- 回归：zip 内层「谱面文件名 == 曲名」不得被当成音源 ----
    # 复现真机故障（cid=2741 Dadadadadadadadadada）：
    # 商店下载的 .mc 是 zip 包装，内层成员名去掉扩展名后**正好等于曲名**，
    # 旧的 title 匹配不校验扩展名 -> 把 JSON 谱面当音频 -> ffmpeg:
    # "Invalid data found when processing input"。
    def test_zip_inner_chart_named_like_title_is_not_picked_as_audio(self):
        import zipfile
        d = os.path.join(self.root, "2741")
        os.makedirs(d)
        z = os.path.join(d, "chart.mc")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("songtitle.mc", '{\n    "meta": {\n' + "x" * 500)
        # 真音源在**同目录**（商店下载目录的常见布局）
        with open(os.path.join(d, "songtitle.mp3"), "wb") as f:
            f.write(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 200)
        got = UA.load_chart_audio(z, title="songtitle", hint="")
        self.assertIsNotNone(got, "回退到同目录音源时应能找到")
        self.assertTrue(got[0].lower().endswith(".mp3"),
                        "必须选中 .mp3，而不是同名 .mc：%s" % (got[0],))
        self.assertTrue(UA.looks_like_audio(got[1]))

    def test_zip_non_audio_with_audio_ext_is_skipped(self):
        """扩展名像音频、内容却不是（被再包一层）时，内容兜底应跳过它。"""
        import zipfile
        d = os.path.join(self.root, "5")
        os.makedirs(d)
        z = os.path.join(d, "a.mc")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("song.ogg", '{"meta": {}}')          # 假音频
            zf.writestr("real.ogg", b"OggS\x00\x02" + b"\x00" * 64)  # 真音频
        got = UA.load_chart_audio(z, title="nomatch")
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "real.ogg")

    def test_looks_like_audio_magic(self):
        self.assertTrue(UA.looks_like_audio(b"OggS\x00\x02" + b"\x00" * 32))
        self.assertTrue(UA.looks_like_audio(b"ID3\x04\x00\x00" + b"\x00" * 32))
        self.assertTrue(UA.looks_like_audio(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 32))
        self.assertTrue(UA.looks_like_audio(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 32))
        self.assertTrue(UA.looks_like_audio(b"\xff\xfb\x90\x00" + b"\x00" * 32))
        self.assertFalse(UA.looks_like_audio(b'{\n    "meta": {\n' + b"x" * 64))
        self.assertFalse(UA.looks_like_audio(b"PK\x03\x04" + b"\x00" * 64))
        self.assertFalse(UA.looks_like_audio(b""))
        self.assertFalse(UA.looks_like_audio(b"short"))

    def test_is_audio_ext(self):
        for x in (".ogg", ".OGG", ".mp3", ".m4a"):
            self.assertTrue(UA.is_audio_ext("a" + x))
        for x in (".mc", ".mcz", ".json", ".jpg", ""):
            self.assertFalse(UA.is_audio_ext("a" + x))


if __name__ == "__main__":
    unittest.main(verbosity=2)
