# -*- coding: utf-8 -*-
"""jubeat 更新包安全应用器（ub_updater）

设计依据（全部来自本地实测，非推测）：
  * 更新包 = L44JF_<新版本>from<旧版本>.7z（无需密码）或已解开的同名目录
  * 包结构固定为 <包名>/data/... 、<包名>/modules/... 、<包名>/prop/...
  * 包内有两类「删除标记」，会把已恢复的曲目打回空白：
      1) <music_id>_msc.ifs  大小为 144 字节（空谱面桩）   —— 覆盖谱面
      2) tex_l44_bnr_big_id<id>.bin  大小为 1 字节（空贴图桩） —— 覆盖大曲绘
  * 60 字节的 mdl_*.bin 是【正常】模型元数据，绝不可当标记跳过

用法：
  python ub_updater.py scan  --pack <7z|目录>                 # 只读分析
  python ub_updater.py apply --pack <7z|目录> --game <游戏目录> [--dry-run]
  python ub_updater.py apply --pack ... --game ... --allow-delete   # 允许应用删除标记（一般不用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import sys
import time

# ---------------------------------------------------------------- 删除标记规则
MSC_MAX_STUB = 1024      # *_msc.ifs 小于此值视为空桩（实测 144B）
TEX_MAX_STUB = 16        # tex_*.bin 小于此值视为空桩（实测 1B）
MDL_NORMAL = 60          # mdl_*.bin 正常大小（不是标记）


def classify(rel: str, size: int) -> str:
    """返回 'msc' | 'tex' | 'normal' | 'msc_stub' | 'tex_stub'"""
    low = rel.lower().replace('\\', '/')
    base = low.rsplit('/', 1)[-1]
    if base.endswith('_msc.ifs'):
        return 'msc_stub' if size <= MSC_MAX_STUB else 'msc'
    if base.startswith('mdl_') and base.endswith('.bin'):
        return 'normal'                      # 60B 是正常大小
    if base.startswith('tex_') and base.endswith('.bin'):
        return 'tex_stub' if size <= TEX_MAX_STUB else 'tex'
    return 'normal'


def is_deletion_marker(rel: str, size: int) -> bool:
    return classify(rel, size) in ('msc_stub', 'tex_stub')


STUB_RE = re.compile(r'(\d+)_msc\.ifs$|_id(\d+)\.bin$')


def song_ids_of(rel: str) -> list:
    """从文件名里提取可能相关的 music_id（用于报告受影响曲目）"""
    base = rel.replace('\\', '/').rsplit('/', 1)[-1]
    out = []
    m = re.match(r'^(\d+)_msc\.ifs$', base)
    if m:
        out.append(int(m.group(1)))
    m = re.search(r'_id(\d+)\.bin$', base)
    if m:
        out.append(int(m.group(1)))
    return out


# ---------------------------------------------------------------- 读包
def _common_top(names):
    """若所有条目都位于同一个顶层目录下，返回该目录名，否则 None。"""
    firsts = set()
    for n in names:
        p = n.replace('\\', '/').split('/')
        if len(p) > 1:
            firsts.add(p[0])
    return firsts.pop() if len(firsts) == 1 else None


def _pack_entries(infos):
    """[(原始名, 大小)] → [(剥掉统一顶层目录后的相对路径, 大小)]，按路径排序。"""
    top = _common_top([n for n, _ in infos])
    out = []
    for name, size in infos:
        rel = name.replace('\\', '/')
        if top and rel.startswith(top + '/'):
            rel = rel[len(top) + 1:]
        out.append((rel, size))
    return sorted(out)


def read_pack(pack_path: str):
    """返回 [(相对路径(包内去掉顶层目录), 大小)]

    支持三种形式：`.7z`（py7zr）、`.zip`（标准库 zipfile，无需额外依赖）、
    以及**已解开的目录**。
    """
    if os.path.isdir(pack_path):
        entries = []
        for dp, _dn, fns in os.walk(pack_path):
            for f in fns:
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, pack_path)
                # 目录形包可能多一层同名目录，剥掉
                parts = rel.replace('\\', '/').split('/')
                if len(parts) > 1 and parts[0] == os.path.basename(pack_path.rstrip('\\/')):
                    rel = '/'.join(parts[1:])
                entries.append((rel, os.path.getsize(full)))
        return sorted(entries)

    ext = os.path.splitext(pack_path)[1].lower()
    if ext == ".zip":
        import zipfile
        with zipfile.ZipFile(pack_path) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
        return _pack_entries([(i.filename, i.file_size) for i in infos])
    if ext != ".7z":
        raise ValueError("不支持的更新包格式（只认 .7z / .zip / 目录）：%s" % pack_path)

    import py7zr
    with py7zr.SevenZipFile(pack_path, 'r') as z:
        infos = [i for i in z.list() if not i.is_directory]
    return _pack_entries([(i.filename, i.uncompressed or 0) for i in infos])


def _extract_into(pack_path, target):
    """把包解到 target。

    `.zip` 用**标准库 zipfile**（不需要任何第三方依赖，随 exe 一起内置）；
    `.7z` 用 py7zr（需要打在包里）。
    """
    ext = os.path.splitext(pack_path)[1].lower()
    if ext == ".zip":
        import zipfile
        with zipfile.ZipFile(pack_path) as z:
            z.extractall(path=target)
        return
    if ext != ".7z":
        raise ValueError("不支持的更新包格式（只认 .7z / .zip / 目录）：%s" % pack_path)
    import py7zr
    with py7zr.SevenZipFile(pack_path, 'r') as z:
        z.extractall(path=target)


def extract_pack(pack_path: str, workdir: str):
    """解压到 workdir/<包名>/ ，返回解压后的顶层目录。

    支持 `.7z` / `.zip` / 已解开的目录。
       ★ 每个包独立目录（曾因复用同一目录导致后续包挑到上一个包的解压结果）
       ★ 目录形式的包常为「<包名>/<包名>/data/...」双层嵌套，必须下钻一层，
         否则 src+rel 路径错位 → 复制 0 个文件（read_pack 已按剥层后的相对路径返回）"""
    os.makedirs(workdir, exist_ok=True)
    if os.path.isdir(pack_path):
        sub = os.path.join(pack_path, os.path.basename(pack_path.rstrip('\\/')))
        return sub if os.path.isdir(sub) else pack_path
    stem = os.path.splitext(os.path.basename(pack_path))[0]
    target = os.path.join(workdir, stem)
    marker = os.path.join(target, '.extract_ok')
    if not os.path.exists(marker):
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        os.makedirs(target, exist_ok=True)
        _extract_into(pack_path, target)
        with open(marker, 'w') as f:
            f.write('ok')
    subs = [d for d in os.listdir(target) if os.path.isdir(os.path.join(target, d))]
    if len(subs) == 1:
        return os.path.join(target, subs[0])
    return target


# ---------------------------------------------------------------- scan
def do_scan(pack_path, as_json=False):
    entries = read_pack(pack_path)
    buckets = {'msc': [], 'tex': [], 'normal': []}
    stubs = {'msc_stub': [], 'tex_stub': []}
    for rel, size in entries:
        k = classify(rel, size)
        if k in stubs:
            stubs[k].append((rel, size))
        else:
            buckets[k].append((rel, size))

    r = {
        'pack': pack_path,
        'entries': len(entries),
        'normal_files': len(buckets['normal']),
        'charts': len(buckets['msc']),
        'textures': len(buckets['tex']),
        'deletion_markers': {
            'msc_stub(144B谱面桩)': [(a, b) for a, b in stubs['msc_stub']],
            'tex_stub(1B贴图桩)': [(a, b) for a, b in stubs['tex_stub']],
        },
        'affected_song_ids': sorted({i for k in stubs for a, _ in stubs[k] for i in song_ids_of(a)}),
        # ★ 包里的 modules/*.dll = 官方新版真本体。直接覆盖会让 DLL 补丁失效，
        #   必须重新用特征码定位打补丁（GUI 会就此单独提示）。
        'new_dlls': [{"rel": a, "size": b} for a, b in list_pack_dlls(pack_path)],
    }
    if as_json:
        # ★ JSON 强制 UTF-8：stdout 已被改成控制台编码(GBK)，重定向给脚本消费会乱码
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        print('包 = %s' % os.path.basename(pack_path))
        print('  条目总数        = %d' % r['entries'])
        print('  普通文件        = %d' % r['normal_files'])
        print('  谱面(真实)      = %d' % r['charts'])
        print('  贴图(真实)      = %d' % r['textures'])
        print('  ── 删除标记 ──')
        for k, v in r['deletion_markers'].items():
            print('  %-22s = %d' % (k, len(v)))
            for a, b in v:
                print('        %6d B  %s' % (b, a))
        print('  受影响的曲目 id = %s' % r['affected_song_ids'])
    return r


# ---------------------------------------------------------------- apply
def do_apply(pack_path, game_dir, dry_run=False, allow_delete=False,
             workdir=None, backup_root=None):
    game_dir = os.path.abspath(game_dir)
    if not os.path.isdir(game_dir):
        raise SystemExit('游戏目录不存在：%s' % game_dir)
    workdir = workdir or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      '..', 'tmp', 'ub_updater')
    workdir = os.path.abspath(workdir)
    backup_root = backup_root or os.path.join(workdir, 'backup')

    src = extract_pack(pack_path, os.path.join(workdir, 'extract'))
    entries = read_pack(pack_path)

    stamp = time.strftime('%Y%m%d_%H%M%S')
    plan = {'apply': [], 'skip_marker': [], 'missing_src': [], 'backup': []}
    for rel, size in entries:
        s = os.path.join(src, rel.replace('/', os.sep))
        if not os.path.exists(s):
            plan['missing_src'].append(rel)
            continue
        if is_deletion_marker(rel, size) and not allow_delete:
            plan['skip_marker'].append((rel, size))
            continue
        plan['apply'].append((rel, size, s))

    print('=== 更新计划 %s ===' % ('(DRY RUN 不写盘)' if dry_run else ''))
    print('  将写入   : %d 个文件' % len(plan['apply']))
    print('  跳过标记 : %d 个（保护已恢复的曲目）' % len(plan['skip_marker']))
    for rel, size in plan['skip_marker']:
        print('       跳过 %6d B  %s' % (size, rel))
    if plan['missing_src']:
        print('  源缺失   : %d' % len(plan['missing_src']))

    ids = sorted({i for rel, _ in plan['skip_marker'] for i in song_ids_of(rel)})
    print('  受保护曲目 id = %s' % ids)

    if dry_run:
        return plan

    for rel, size, s in plan['apply']:
        d = os.path.join(game_dir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.exists(d):
            b = os.path.join(backup_root, stamp, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copy2(d, b)
            plan['backup'].append(rel)
        shutil.copy2(s, d)
    print('\n  已写入 %d 个文件，被覆盖的原件备份在 %s' % (len(plan['apply']), backup_root))
    with open(os.path.join(workdir, 'last_apply_%s.json' % stamp), 'w', encoding='utf-8') as f:
        json.dump({'pack': pack_path, 'game': game_dir, 'stamp': stamp,
                   'applied': [a for a, _, _ in plan['apply']],
                   'skipped_markers': [a for a, _ in plan['skip_marker']],
                   'backed_up': plan['backup']}, f, ensure_ascii=False, indent=1)
    return plan


# ---------------------------------------------------------------- build（核心：产出可直接覆盖的更新包）
DEFAULT_GAME = r'<ARCADE_ROOT>'

READ_ME = """更新包已处理完成 —— 可直接覆盖到游戏本体
==================================================
处理内容：
  1. 已【剔除】所有删除标记（144B 空谱面桩 / 1B 空贴图桩）→ 不会再把已恢复的曲目打回空白
  2. 已按需并入新曲到 data_mods/omnimix/music_info/music_omni.xml
  3. 需人工处理的文件放在 _manual/ 目录（见下）

怎么用：
  把本目录（{out}）里的所有内容，整个复制/覆盖到游戏本体的 contents\\ 目录即可。

_manual/ 目录（重要，不要直接覆盖）：
  _manual/jubeat.dll.new   ← 官方新版真游戏本体（modules/jubeat.dll）
      更新后必须：
        a) 把它另存为 contents\\jb_org.dll（覆盖旧的）；
        b) 确认 contents\\jubeat.dll 仍是 omnimix 挂载模块（571,187 字节）；
        c) 【重新打补丁】：新版 DLL 的补丁偏移会变，必须用 locate_sites.py/patch_jb_org.py
           重新定位并反汇编验证后再打，切勿套用旧偏移。
  _manual/music_db.dll.new  ← 官方选曲清单，覆盖后需确认 omnimix 曲目清单行为未被改坏

生成时间：{ts}
来源包：{pack}
"""

def _merge_music_info(game_dir, pack_src, out_dir):
    """把包内 music_info.xml 的新曲并入游戏现有 music_omni.xml，产出到 out_dir。
       返回 (新增数, 说明)"""
    import xml.etree.ElementTree as _ET
    pack_mi = os.path.join(pack_src, 'data', 'music_info', 'music_info.xml')
    if not os.path.exists(pack_mi):
        return 0, '包内无 data/music_info/music_info.xml，跳过曲表合并'
    cur_mi = os.path.join(game_dir or '', 'data_mods', 'omnimix', 'music_info', 'music_omni.xml')
    if not (game_dir and os.path.exists(cur_mi)):
        return 0, ('未找到游戏现有曲表 %s —— 无法自动合并新曲。\n'
                   '     请把游戏的 data_mods\\omnimix\\music_info\\music_omni.xml 放到本 exe 同级目录后重跑，\n'
                   '     或手动把包内 music_info.xml 的新曲并进去。' % cur_mi)

    def blocks(path):
        txt = open(path, encoding='utf-8', errors='replace').read()
        return re.findall(r'<data __type="void" id="\d+">.*?</data>', txt, re.S)

    def mid(b):
        m = re.search(r'<music_id __type="s32">(\d+)</music_id>', b)
        return int(m.group(1)) if m else None

    cur_txt = open(cur_mi, encoding='utf-8', errors='replace').read()
    cur_blocks = blocks(cur_mi)
    have = {mid(b) for b in cur_blocks}
    new_blocks = []
    for b in blocks(pack_mi):
        k = mid(b)
        if k is None or k in have:
            continue
        have.add(k)                      # ★ 顺带去重：排除包内自身重复的 id
        new_blocks.append(b)
    if not new_blocks:
        return 0, '包内曲表与现有曲表无新增曲目'
    # ★ 结构安全：只在最后一个 </data> 之后【插入】新记录，绝不重建 head/body。
    #   曾经用 cur_txt.find('<data') 取 head —— 会误命中 header 里的 <data_num，
    #   把头截断导致 <header> 未闭合、产出非法 XML（游戏曲表会坏）。
    i = cur_txt.rfind('</data>')
    if i >= 0:
        tail_after = cur_txt[i + len('</data>'):]
    else:
        tail_after = ''
    # ★ 输入完整性校验：光判「有没有 </data>」不够 —— 表被截断（缺最后一个 </data>）时
    #   会照写非法 XML（静默损坏）。必须同时确认尾部闭合标签完整，否则宁可不合并。
    if i < 0 or '</body>' not in tail_after or '</music_data>' not in tail_after:
        return 0, '现有曲表结构异常（缺 </data> 或尾部闭合标签不完整），已跳过合并'
    cut = i + len('</data>')
    # ★ 新块 id 起点 = 现有最大 id + 1（而非块数）：现有表若 id 不连续(如 0,1,2,3,5)，
    #   用块数当起点会撞号；data_num 则必须等于【最终块数】。
    cur_ids = [int(x) for x in re.findall(r'<data __type="void" id="(\d+)">', cur_txt)]
    base_id = (max(cur_ids) + 1) if cur_ids else 0
    final_n = len(cur_ids) + len(new_blocks)
    # ★ 两条格式契约（本仓既有工具 ub_prune_omni.py/merge_musiclist.py 均强制遵守，
    #   真实游戏表与全部 6 个官方包也都满足）：
    #   1) 块的 id 属性必须连续 —— 官方包内的块自带 id(如 1109)，直接插会断号/撞号
    #   2) header 的 <data_num> 必须等于最终块数 —— 声明值小于实际值通常意味着末条被忽略
    fixed = [re.sub(r'^<data __type="void" id="\d+">',
                    '<data __type="void" id="%d">' % (base_id + k), b, count=1)
             for k, b in enumerate(new_blocks)]
    out_txt = cur_txt[:cut] + '\n' + '\n'.join(fixed) + cur_txt[cut:]
    out_txt = re.sub(r'(<data_num __type="s32">)\d+(</data_num>)',
                     lambda m: m.group(1) + str(final_n) + m.group(2),
                     out_txt, count=1)
    out = os.path.join(out_dir, 'data_mods', 'omnimix', 'music_info', 'music_omni.xml')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='') as f:
        f.write(out_txt)
    return len(new_blocks), '新增 %d 首（data_num=%d，id 追加自 %d）→ %s' % (
        len(new_blocks), final_n, base_id, out)


def do_build(pack_path, game_dir=None, out_root=None, workdir=None):
    """拖放式主流程：产出「可直接覆盖到游戏本体」的更新包目录"""
    pack_path = os.path.abspath(pack_path)
    if not os.path.exists(pack_path):
        raise SystemExit('找不到：%s' % pack_path)
    stem = os.path.splitext(os.path.basename(pack_path.rstrip('\\/')))[0]
    workdir = os.path.abspath(workdir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'tmp', 'ub_updater'))
    out_dir = os.path.abspath(out_root or os.path.join(
        os.path.dirname(pack_path), stem + '_ready'))
    # ★ 先建输出目录：否则「空包 / 无 data 目录 / 路径错位」时写 应用说明.txt 会抛 FileNotFoundError
    os.makedirs(out_dir, exist_ok=True)

    game_dir = game_dir or DEFAULT_GAME
    if not os.path.isdir(game_dir):
        game_dir = None                       # 找不到游戏就只做「去标记」这一步

    print('=' * 68)
    print(' 输入包 : %s' % pack_path)
    print(' 输出到 : %s' % out_dir)
    print(' 游戏目录: %s' % (game_dir or '（未找到，跳过曲表合并）'))
    print('=' * 68)

    src = extract_pack(pack_path, os.path.join(workdir, 'extract'))
    entries = read_pack(pack_path)
    removed, copied, manual = [], 0, []
    for rel, size in entries:
        s = os.path.join(src, rel.replace('/', os.sep))
        if not os.path.exists(s):
            continue
        if is_deletion_marker(rel, size):
            removed.append((rel, size))
            continue
        low = rel.replace('\\', '/').lower()
        if low.startswith('modules/') and low.endswith('.dll'):
            d = os.path.join(out_dir, '_manual', os.path.basename(rel) + '.new')
        elif low == 'data/music_info/music_info.xml':
            continue                          # 由曲表合并统一产出
        elif low in ('data/d3/package/alldata.bin', 'data/d3/package/allomni.bin'):
            continue                          # 由包表合并统一产出
        else:
            d = os.path.join(out_dir, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        copied += 1

    added, note = _merge_music_info(game_dir, src, out_dir)
    tbl_notes = merge_pkg_tables(game_dir, src, out_dir)
    dup_warns = detect_duplicates(game_dir, src)
    with open(os.path.join(out_dir, '应用说明.txt'), 'w', encoding='utf-8-sig',
              newline='\r\n') as f:
        f.write(READ_ME.format(out=out_dir, ts=time.strftime('%Y-%m-%d %H:%M'), pack=pack_path))
        f.write('\n本次统计：\n  写入文件 %d 个\n  剔除删除标记 %d 个\n  曲表：%s\n'
                % (copied, len(removed), note))
        if tbl_notes:
            f.write('\n包表合并：\n')
            for n in tbl_notes:
                f.write('  %s\n' % n)
        if dup_warns:
            f.write('\n⚠ 重复曲目检查（新曲与已有曲同读音）：\n')
            for w in dup_warns:
                f.write('  %s\n' % w)
        else:
            f.write('\n重复曲目检查：未发现新增重复\n')
        if removed:
            f.write('\n被剔除的删除标记（保护了这些曲目）：\n')
            for rel, size in removed:
                f.write('  %6d B  %s\n' % (size, rel))

    print('  写入文件 %d 个' % copied)
    print('  剔除删除标记 %d 个（保护曲目 %s）'
          % (len(removed), sorted({i for r, _ in removed for i in song_ids_of(r)})))
    print('  曲表：%s' % note)
    for n in tbl_notes:
        print('  包表：%s' % n)
    if dup_warns:
        for w in dup_warns:
            print('  [重复提示] %s' % w)
    print('  产出目录：%s' % out_dir)
    print('  → 把该目录内容整体覆盖到游戏 contents\\ 即可；_manual\\ 里的 DLL 需人工处理')
    return out_dir


# ---------------------------------------------------------------- 包表合并 / 去重 / DLL 校验 / 回滚
PKG_TABLES = ['alldata.bin', 'allomni.bin']


def _tlkp_rows(buf):
    """解析 TLKP 包表 → (rows, (f8,fc,f14,f18,f1c))。
       ★ 内联实现（不再 import omnisrc/tlkp.py）：exe 是单文件打包，外部模块不会被收进去，
         之前 `from tlkp import serialize` 直接导致 build 崩溃。"""
    if buf[:4] != b'TLKP':
        raise ValueError('不是 TLKP 表')
    f8, fc, n = struct.unpack_from('<3I', buf, 8)
    f14, f18, f1c = struct.unpack_from('<3I', buf, 0x14)
    rows = []
    for i in range(n):
        offs = struct.unpack_from('<3I', buf, 0x20 + i * 24)
        r = []
        for off in offs:
            if not off:
                r.append('')
            else:
                e = buf.find(b'\x00', off)
                r.append(buf[off:e].decode('latin-1'))
        rows.append(tuple(r))
    return rows, (f8, fc, f14, f18, f1c)


def _tlkp_build(rows, hdr):
    """按 TLKP 格式序列化（与 omnisrc/tlkp.serialize 等价，已实测 round-trip 一致）"""
    f8, fc, f14, f18, f1c = hdr
    n = len(rows)
    cur = 0x20 + n * 24
    offs = []
    for r in rows:
        o = []
        for s in r:
            o.append(cur)
            cur += len(s.encode('latin-1')) + 1
        offs.append(o)
    total = cur + ((4 - cur % 4) % 4)
    out = bytearray(b'TLKP' + struct.pack('<4I', total, f8, fc, n)
                    + struct.pack('<3I', f14, f18, f1c))
    for o in offs:
        out += struct.pack('<3I', *o) + b'\x00' * 12
    for r in rows:
        for s in r:
            out += s.encode('latin-1') + b'\x00'
    out += b'\x00' * (total - cur)
    return bytes(out)


def _find_table(game_dir, tbl):
    """overlay 优先，然后官方目录（实测：alldata.bin 可能只在官方 data/d3/package 下）"""
    for rel in ('data_mods/omnimix/d3/package/', 'data/d3/package/'):
        p = os.path.join(game_dir or '', rel.replace('/', os.sep) + tbl)
        if os.path.exists(p):
            return p
    return None


def merge_pkg_tables(game_dir, pack_src, out_dir):
    """把包内的包表（alldata.bin / allomni.bin）合并进 overlay 的包表并产出。
       ★ 关键：游戏实际读的是 data_mods/omnimix/... 的 overlay 版本（会遮蔽官方同名文件），
         官方更新包带来的新曲包在新版包表里；若不合并进 overlay，新曲的贴图/谱面包就注册不上。"""
    notes = []
    for tbl in PKG_TABLES:
        p_pack = os.path.join(pack_src, 'data', 'd3', 'package', tbl)
        if not os.path.exists(p_pack):
            continue
        p_exist = _find_table(game_dir, tbl) if game_dir else None
        if not p_exist:
            notes.append('%s：未找到游戏现有包表，已按原样放到 _manual 供人工合并' % tbl)
            d = os.path.join(out_dir, '_manual', tbl + '.new')
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(p_pack, d)
            continue
        try:
            old_rows, hdr = _tlkp_rows(open(p_exist, 'rb').read())
            new_rows, _ = _tlkp_rows(open(p_pack, 'rb').read())
        except Exception as e:
            notes.append('%s：解析失败 %s（已放到 _manual）' % (tbl, e))
            d = os.path.join(out_dir, '_manual', tbl + '.new')
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(p_pack, d)
            continue
        rows = list(old_rows)
        seen = set(rows)
        added = 0
        for t in new_rows:
            if t not in seen:
                seen.add(t)
                rows.append(t)
                added += 1
        out = os.path.join(out_dir, 'data_mods', 'omnimix', 'd3', 'package', tbl)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, 'wb') as f:
            f.write(_tlkp_build(rows, hdr))
        notes.append('%s：现有 %d 行 + 新增 %d 行 = %d 行（源：%s）'
                     % (tbl, len(old_rows), added, len(rows), p_exist))
    return notes


def detect_duplicates(game_dir, pack_src):
    """新曲与现有曲表的「同读音不同 id」重复检查（今天已实测存在 12 组此类重复）"""
    pack_mi = os.path.join(pack_src, 'data', 'music_info', 'music_info.xml')
    cur_mi = os.path.join(game_dir or '', 'data_mods', 'omnimix', 'music_info', 'music_omni.xml')
    if not (os.path.exists(pack_mi) and os.path.exists(cur_mi)):
        return []
    def read(p):
        txt = open(p, encoding='utf-8', errors='replace').read()
        out = {}
        for b in re.findall(r'<data __type="void" id="\d+">.*?</data>', txt, re.S):
            m = re.search(r'<music_id __type="s32">(\d+)</music_id>', b)
            ns = re.search(r'<name_string __type="str">([0-9a-fA-F]*)</name_string>', b)
            ho = re.search(r'<is_hold __type="s32">(\d+)</is_hold>', b)
            if not m:
                continue
            try:
                rd = bytes.fromhex(ns.group(1)).decode('cp932') if ns else ''
            except Exception:
                rd = ''
            out[int(m.group(1))] = (rd, ho.group(1) if ho else '0')
        return out
    cur, pk = read(cur_mi), read(pack_mi)
    byrd = {}
    for i, (rd, ho) in cur.items():
        if ho == '0' and rd:
            byrd.setdefault(rd, []).append(i)
    warns = []
    for i, (rd, ho) in pk.items():
        if i in cur or ho != '0' or not rd:
            continue
        if rd in byrd:
            warns.append('新曲 %d 读音「%s」与已有曲 %s 重复（同曲多 id）' % (i, rd, byrd[rd]))
    return warns


def list_pack_dlls(pack_path):
    """列出更新包里的 modules/*.dll（这些是**官方新版真本体**，不是可直接用的模块）。

    返回 [(rel, size), ...]
    """
    out = []
    for rel, size in read_pack(pack_path):
        low = rel.replace('\\', '/').lower()
        if low.startswith('modules/') and low.endswith('.dll'):
            out.append((rel, size))
    return out


def apply_new_dll(dll_new, game_dir, target_rel='jb_org.dll',
                  backup_root=None, patch=True):
    """把官方新版 DLL 装到游戏里并**重新打补丁**。

    背景：`modules/jubeat.dll` 是官方真游戏本体，而游戏真正加载的是
    `contents/jb_org.dll`（`contents/jubeat.dll` 是 omnimix 挂载模块，**不要动**）。
    官方每次更新都会重编译 DLL，**旧的补丁偏移必然失效**，必须用特征码重定位。

    步骤：
      1. 备份现有目标（若存在）
      2. 把 dll_new 复制成 <game>/<target_rel>
      3. 用 ub_patcher 定位 4 个站点并打补丁（只有全部唯一命中才写出）

    ⚠️ 只做"复制 + 打补丁"，**绝不碰 `contents/jubeat.dll`**（挂载模块）。
    返回 {target, backup, patched, sites, error}
    """
    res = {"target": None, "backup": None, "patched": False,
           "sites": None, "error": None}
    if not os.path.exists(dll_new):
        res["error"] = "新版 DLL 不存在：%s" % dll_new
        return res
    tgt = os.path.join(game_dir, target_rel)
    res["target"] = tgt
    # 1) 备份
    if os.path.exists(tgt):
        stamp = time.strftime('%Y%m%d_%H%M%S')
        root = backup_root or os.path.join(os.path.dirname(tgt), 'jb_backup')
        os.makedirs(root, exist_ok=True)
        bak = os.path.join(root, '%s.bak_%s' % (os.path.basename(tgt), stamp))
        try:
            shutil.copy2(tgt, bak)
            res["backup"] = bak
        except OSError as ex:
            res["error"] = "备份失败：%s" % ex
            return res
    # 2) 复制为新本体
    try:
        shutil.copy2(dll_new, tgt)
    except OSError as ex:
        res["error"] = "写入失败：%s" % ex
        return res
    # 3) 打补丁
    if patch:
        try:
            import ub_patcher as UP
            data = open(tgt, 'rb').read()
            res_list = UP.locate_sites(data)        # [{'site','state','offset',...}, ...]
            states = [x.get('state') for x in res_list]
            res["sites"] = states
            good = ('PATCHABLE', 'PATCHED')
            if not states or any(s not in good for s in states):
                bad = [x['site']['name'] if isinstance(x.get('site'), dict)
                       else '?' for x in res_list if x.get('state') not in good]
                res["error"] = ("补丁站点未能全部唯一命中（异常站点：%s，全部状态：%s）——"
                                "新版 DLL 结构可能变了，需人工分析，**暂未打补丁**。"
                                % (bad or '无', states))
                return res
            if all(s == 'PATCHED' for s in states):
                res["patched"] = True               # 已经是补丁版，无需再打
                res["already"] = True
                return res
            out = tgt + '.patched'
            rc = UP.cmd_patch(tgt, out)
            if rc not in (0, None) or not os.path.exists(out):
                res["error"] = "打补丁被拒绝（ub_patcher 退出码 %s）" % rc
                return res
            os.replace(out, tgt)
            res["patched"] = True
        except Exception as ex:                              # noqa: BLE001
            res["error"] = "打补丁失败：%s" % ex
    return res


def verify_dll(dll_path, patch_too=False):
    """校验 4 个补丁站点。★ 更新包里的 jubeat.dll 是新版本，偏移可能失效 —— 本函数只做校验，
       不匹配就明确报告，绝不盲目打补丁。"""
    PATCHES = [
        ('Skip Tutorial', 0xDAD7F, [0x0F, 0x85], [0x90, 0xE9]),
        ('Select Music Timer Freeze', 0xC42EF, [0x75], [0xEB]),
        ('Skip Category Select', 0xC467B, [0x04], [0x07]),
        ('Result Timer Freeze', 0xABC2B, [0x75], [0xEB]),
    ]
    d = bytearray(open(dll_path, 'rb').read())
    print('DLL: %s  %d B' % (dll_path, len(d)))
    state = {'原始': 0, '已打补丁': 0, '不匹配': 0}
    for name, off, old, new in PATCHES:
        got = list(d[off:off + len(old)])
        if got == old:
            state['原始'] += 1
            print('  [可打]   %-26s 0x%-7X 原字节=%s' % (name, off, got))
        elif got == new:
            state['已打补丁'] += 1
            print('  [已打]   %-26s 0x%-7X' % (name, off))
        else:
            state['不匹配'] += 1
            print('  [!!不符] %-26s 0x%-7X 实测=%s 期望原=%s 期望补丁=%s'
                  % (name, off, got, old, new))
        if patch_too and got == old:
            d[off:off + len(new)] = bytes(new)
    print('  状态：%s' % state)
    if state['不匹配']:
        print('  ⚠ 有不匹配站点 → 这是新版本 DLL，偏移已变，禁止套用旧偏移！'
              '请用 omnisrc/locate_sites.py 重新定位 + 反汇编验证。')
    if patch_too and state['不匹配'] == 0:
        out = dll_path + '.patched'
        open(out, 'wb').write(bytes(d))
        print('  已写出补丁版：%s' % out)
    return state


def do_rollback(workdir, stamp=None, list_only=False):
    backup_root = os.path.join(workdir, 'backup')
    if not os.path.isdir(backup_root):
        print('没有备份目录：%s' % backup_root)
        return
    stamps = sorted(os.listdir(backup_root))
    if list_only or not stamp:
        print('可用备份批次：')
        for s in stamps:
            n = sum(len(f) for _, _, f in os.walk(os.path.join(backup_root, s)))
            print('  %s  （%d 个文件）' % (s, n))
        if not stamp:
            print('\n用 --stamp <批次> --game <游戏目录> 回滚')
        return
    src = os.path.join(backup_root, stamp)
    if not os.path.isdir(src):
        raise SystemExit('批次不存在：%s' % stamp)
    game = os.environ.get('UB_GAME') or DEFAULT_GAME
    if not os.path.isdir(game):
        raise SystemExit('请设置环境变量 UB_GAME 或用 --game 指定游戏目录')
    n = 0
    for dp, _dn, fns in os.walk(src):
        for f in fns:
            s = os.path.join(dp, f)
            rel = os.path.relpath(s, src)
            d = os.path.join(game, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            n += 1
    print('已从批次 %s 回滚 %d 个文件到 %s' % (stamp, n, game))


# ---------------------------------------------------------------- CLI
def _cli():
    ap = argparse.ArgumentParser(description='jubeat 更新包处理器（拖放可用）')
    sub = ap.add_subparsers(dest='cmd')

    p0 = sub.add_parser('build', help='产出「可直接覆盖到游戏本体」的更新包（默认动作）')
    p0.add_argument('--pack', required=True)
    p0.add_argument('--game', default=None)
    p0.add_argument('--out', default=None)

    p1 = sub.add_parser('scan', help='只读分析更新包（识别删除标记）')
    p1.add_argument('--pack', required=True)
    p1.add_argument('--json', action='store_true')

    p2 = sub.add_parser('apply', help='直接对游戏目录应用更新包（默认跳过删除标记）')
    p2.add_argument('--pack', required=True)
    p2.add_argument('--game', required=True)
    p2.add_argument('--dry-run', action='store_true')
    p2.add_argument('--allow-delete', action='store_true',
                    help='允许应用删除标记（会把已恢复的曲目打回空白，慎用）')
    p2.add_argument('--workdir', default=None)
    p2.add_argument('--backup-root', default=None)

    p3 = sub.add_parser('verify-dll', help='校验 4 个补丁站点是否仍匹配（新版 DLL 会偏移失效）')
    p3.add_argument('--dll', required=True)
    p3.add_argument('--patch', action='store_true', help='站点全部匹配时顺便写出 .patched')

    p4 = sub.add_parser('rollback', help='从时间戳备份回滚')
    p4.add_argument('--list', action='store_true')
    p4.add_argument('--stamp', default=None)
    p4.add_argument('--game', default=None)
    p4.add_argument('--workdir', default=None)

    a = ap.parse_args()
    if a.cmd == 'scan':
        do_scan(a.pack, a.json)
    elif a.cmd == 'apply':
        do_apply(a.pack, a.game, a.dry_run, a.allow_delete, a.workdir, a.backup_root)
    elif a.cmd == 'build':
        do_build(a.pack, a.game, a.out)
    elif a.cmd == 'verify-dll':
        verify_dll(a.dll, a.patch)
    elif a.cmd == 'rollback':
        if a.game:
            os.environ['UB_GAME'] = a.game
        do_rollback(a.workdir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), '..', 'tmp', 'ub_updater'),
            a.stamp, a.list)
    else:
        ap.print_help()


def main():
    """入口。
       ★ 拖放支持：把更新包（.7z / 文件夹）拖到 exe 上 → 直接走 build 流程。
         拖放时 Windows 会把路径作为唯一参数传入，因此这里先做「非子命令即路径」判断。
       ★ 控制台编码：中文 Windows 控制台是 GBK(cp936)，打包成 exe 后默认按 UTF-8 输出会乱码，
         这里显式改成系统偏好编码。"""
    try:
        # 中文 Windows 控制台原生编码是 GBK(cp936)：打包成 exe 后默认 UTF-8 输出会乱码。
        # 优先按控制台实际代码页输出；失败再退回 utf-8。
        import ctypes
        cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        enc = {936: 'gbk', 65001: 'utf-8', 950: 'big5'}.get(cp, 'gbk')
        for s in (sys.stdout, sys.stderr):
            if s is not None and hasattr(s, 'reconfigure'):
                s.reconfigure(encoding=enc, errors='replace')
    except Exception:
        try:
            for s in (sys.stdout, sys.stderr):
                if s is not None and hasattr(s, 'reconfigure'):
                    s.reconfigure(encoding='gbk', errors='replace')
        except Exception:
            pass
    args = sys.argv[1:]
    # ★ 子命令白名单必须与 _cli() 里注册的一致，否则会被误判成「拖放进来的包路径」
    known = {'build', 'scan', 'apply', 'verify-dll', 'rollback'}
    if args and not args[0].startswith('-') and args[0] not in known:
        if os.path.exists(args[0]):
            do_build(args[0])
        else:
            print('找不到：%s' % args[0])
            print('（把更新包 .7z 或文件夹拖到本 exe 上即可；或用 build/scan/apply/verify-dll/rollback 子命令）')
            sys.exit(1)          # 非 0 退出码，便于批处理/脚本判断失败
        return
    _cli()


if __name__ == '__main__':
    main()
