# -*- coding: utf-8 -*-
"""
ub_patcher.py  -  jubeat.dll 通用补丁器（反汇编 + 特征码重定位）

为什么需要它
    街机 jubeat 的 jubeat.dll 每次官方更新都会重新编译，硬编码偏移必然失效。
    本工具用「稳定字节写死 + 易变字节 ?? 通配」的特征码定位 4 个补丁站点，
    再用 capstone 反汇编做语义校验，只有 4 个站点全部「唯一命中且站内字节符合预期」
    才会写出补丁版，避免把补丁打到错误位置。

4 个补丁
    1. Skip Tutorial            跳过教程          0F 85 -> 90 E9   (jne rel32 -> nop;jmp,目标不变)
    2. Select Music Timer Freeze 选曲计时器冻结    75    -> EB      (jne rel8 -> jmp)
    3. Skip Category Select      跳过类别选择      04    -> 07      (push imm32 立即数 0x23840400 -> 0x23840700)
    4. Result Timer Freeze       结算计时器冻结    75    -> EB      (jne rel8 -> jmp)

用法
    ub_patcher.py locate --dll <jubeat.dll>              定位 4 个站点（只读）
    ub_patcher.py patch  --dll <jubeat.dll> [--out <f>]  打补丁（默认 <dll>.patched）
    ub_patcher.py <jubeat.dll>                           拖放 DLL 上去 = locate

退出码
    locate: 0 = 4 个站点全部定位成功（可打补丁或已是补丁版）；1 = 有站点未命中/歧义
    patch : 0 = 已写出补丁文件；1 = 拒绝写出（站点不全/字节不符/已是补丁版）；2 = 文件错误
"""

import ctypes
import hashlib
import os
import sys

try:
    import capstone
except ImportError:
    sys.stderr.write('缺少 capstone 模块，请先安装：<PY_HOME>/python.exe -m pip install capstone\n')
    raise


# ---------------------------------------------------------------- 控制台编码
def setup_console():
    """中文 Windows 控制台用 GBK 输出（用 GetConsoleOutputCP 取实际代码页）。

    测试时可用环境变量 UB_PATCHER_ENC 覆盖编码（如 utf-8）。
    """
    enc = os.environ.get('UB_PATCHER_ENC')
    if not enc:
        try:
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
        except Exception:
            cp = 0
        if cp:
            enc = 'cp%d' % cp
    if not enc:
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding=enc, errors='replace')
        except Exception:
            pass


# ---------------------------------------------------------------- 特征码定义
# pattern：以空格分隔的十六进制字节，'??' 为单字节通配。
# 规律：稳定的机器码/结构写死；绝对虚拟地址(abs32)、相对位移(rel32/rel8)、
#       会被补丁改写的站点字节，全部用 ?? 通配。
#
# orig：原始（未打补丁）特征码；patched：已打补丁形态特征码。
# 两者都用来判定「可打补丁 / 已打补丁」。
SITES = [
    {
        'key': 'skiptutorial',
        'name': 'Skip Tutorial',
        'cn': '跳过教程',
        'site_off': 12,          # 模式内站点字节偏移
        'site_len': 2,
        'old': [0x0F, 0x85],     # 原始站内字节
        'new': [0x90, 0xE9],     # 补丁后站内字节
        # 6a 01 | 8b c8 | call [abs] | test al,al | jne rel32 | call [abs] | test al,al | jne rel32
        # 前后两条 jne 目标相同 —— 本函数独有形状（<前缀>84 c0 0f 85 ... 84 c0 0f 85 ...）
        'orig': '6a 01 8b c8 ff 15 ?? ?? ?? ?? 84 c0 0f 85 ?? ?? ?? ?? '
                'ff 15 ?? ?? ?? ?? 84 c0 0f 85 ?? ?? ?? ??',
        'patched': '6a 01 8b c8 ff 15 ?? ?? ?? ?? 84 c0 90 e9 ?? ?? ?? ?? '
                   'ff 15 ?? ?? ?? ?? 84 c0 0f 85 ?? ?? ?? ??',
        'validator': 'skip_tutorial',
    },
    {
        'key': 'music_timer',
        'name': 'Select Music Timer Freeze',
        'cn': '选曲计时器冻结',
        'site_off': 7,
        'site_len': 1,
        'old': [0x75],
        'new': [0xEB],
        # call rel32 | test al,al | jne rel8 | cmp byte [abs],al | jne rel8 | mov eax,[abs] | xor ecx,ecx
        'orig': 'e8 ?? ?? ?? ?? 84 c0 75 ?? 38 05 ?? ?? ?? ?? 75 ?? a1 ?? ?? ?? ?? 33 c9',
        'patched': 'e8 ?? ?? ?? ?? 84 c0 eb ?? 38 05 ?? ?? ?? ?? 75 ?? a1 ?? ?? ?? ?? 33 c9',
        'validator': 'jne_rel8',
    },
    {
        'key': 'category',
        'name': 'Skip Category Select',
        'cn': '跳过类别选择',
        'site_off': 6,
        'site_len': 1,
        'old': [0x04],
        'new': [0x07],
        # test al,al | jne rel8 | push imm32(0x23840400) | push edi | call [abs] | add esp,8
        #   | push 0 | push edi | call [abs]     <- 尾部 6a 00 57 ff 15 用于与另一处相同前缀区分
        'orig': '84 c0 75 ?? 68 ?? 04 84 23 57 ff 15 ?? ?? ?? ?? 83 c4 08 '
                '6a 00 57 ff 15 ?? ?? ?? ??',
        'patched': '84 c0 75 ?? 68 ?? 07 84 23 57 ff 15 ?? ?? ?? ?? 83 c4 08 '
                   '6a 00 57 ff 15 ?? ?? ?? ??',
        'validator': 'push_imm',
    },
    {
        'key': 'result_timer',
        'name': 'Result Timer Freeze',
        'cn': '结算计时器冻结',
        'site_off': 12,
        'site_len': 1,
        'old': [0x75],
        'new': [0xEB],
        # test esi,esi | setnz al | cmp dword [abs],0 | jne rel8 | xor ecx,ecx | call rel32
        'orig': '85 f6 0f 95 c0 83 3d ?? ?? ?? ?? 00 75 09 33 c9 e8',
        'patched': '85 f6 0f 95 c0 83 3d ?? ?? ?? ?? 00 eb 09 33 c9 e8',
        'validator': 'jne_rel8',
    },
]

MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
MD.detail = True


# ---------------------------------------------------------------- 基础工具
def parse_pattern(pattern):
    """'84 c0 ??' -> [0x84, 0xc0, None]"""
    out = []
    for tok in pattern.split():
        out.append(None if tok == '??' else int(tok, 16))
    return out


def search(data, pattern):
    """返回模式在 data 中所有命中（起始偏移）。首字节做快速过滤。"""
    pb = parse_pattern(pattern)
    n = len(pb)
    if n == 0 or pb[0] is None:
        raise ValueError('模式首字节不能为通配')
    hits = []
    i = data.find(bytes([pb[0]]))
    while i >= 0 and i + n <= len(data):
        ok = True
        for k in range(n):
            if pb[k] is not None and data[i + k] != pb[k]:
                ok = False
                break
        if ok:
            hits.append(i)
        i = data.find(bytes([pb[0]]), i + 1)
    return hits


def disasm_one(data, addr):
    return next(iter(MD.disasm(bytes(data[addr:addr + 16]), addr)), None)


def disasm_from(data, start, end):
    """从 start（已对齐的指令边界）线性反汇编到 end 之前。"""
    return list(MD.disasm(bytes(data[start:end]), start))


def md5(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def _u16(data, off):
    return int.from_bytes(data[off:off + 2], 'little')


def _u32(data, off):
    return int.from_bytes(data[off:off + 4], 'little')


# ---------------------------------------------------------------- PE 完整性门
def verify_pe_integrity(data, site_offsets):
    """写出补丁前的最后一道关：确认输入文件本身是一份完整的 PE。

    返回问题字符串列表，空列表 = 通过。判据：
      (1) 文件 >= 0x40 且前两字节为 'MZ'；e_lfanew(+0x3C) 在文件内且该处为 'PE\\0\\0'
      (2) 节表可解析：NumberOfSections(PE+0x06) / SizeOfOptionalHeader(PE+0x14)，
          节表起点 = e_lfanew + 0x18 + SizeOfOptionalHeader；
          逐节确认 PointerToRawData + SizeOfRawData <= 文件大小（节 raw 不越界）
      (3) 传入的站点偏移必须落在某个节的 raw 区间内（不在节外/空洞）
      (4) SizeOfImage / FileAlignment / SectionAlignment 基本自洽

    目的：拦住「节表已越界、但因站点恰好落在文件前段而被误判通过」的截断/损坏 PE。
    注意：这里只校验「文件是否完整」，不涉及特征码/语义逻辑。
    """
    n = len(data)
    # (1) DOS 头 + PE 签名
    if n < 0x40:
        return ['文件过小（%d B < 0x40），不可能是 PE 文件' % n]
    if data[0:2] != b'MZ':
        return ['缺少 MZ 头（前 2 字节为 %s），不是 PE 文件' % data[0:2].hex(' ')]
    e_lfanew = _u32(data, 0x3C)
    if e_lfanew < 0x40 or e_lfanew + 0x18 > n:
        return ['e_lfanew=0x%X 越界（文件 %d B），DOS 头不可信' % (e_lfanew, n)]
    if data[e_lfanew:e_lfanew + 4] != b'PE\x00\x00':
        return ['e_lfanew=0x%X 处无 PE\\0\\0 签名，不是有效 PE' % e_lfanew]

    problems = []
    # (2) COFF 头 + 节表
    num_sections = _u16(data, e_lfanew + 0x06)
    size_opt = _u16(data, e_lfanew + 0x14)
    if num_sections == 0:
        return ['NumberOfSections=0，无节区，不是有效 PE']
    sec_tab = e_lfanew + 0x18 + size_opt
    sec_tab_end = sec_tab + num_sections * 40
    if sec_tab_end > n:
        return ['节表越界（需要到 0x%X，文件仅 %d B）——文件被截断/损坏'
                % (sec_tab_end, n)]

    sections = []
    for i in range(num_sections):
        base = sec_tab + i * 40
        name = data[base:base + 8].rstrip(b'\x00').decode('latin-1', 'replace') or ('#%d' % i)
        vsize = _u32(data, base + 0x08)
        vaddr = _u32(data, base + 0x0C)
        raw_size = _u32(data, base + 0x10)
        raw_ptr = _u32(data, base + 0x14)
        sections.append((name, vaddr, vsize, raw_ptr, raw_size))
        if raw_size == 0:
            continue  # .bss 之类无 raw 数据
        if raw_ptr + raw_size > n:
            problems.append('节 %s 的 raw 区间 [0x%X, 0x%X) 越界（文件仅 %d B）'
                            % (name, raw_ptr, raw_ptr + raw_size, n))

    # (3) 站点必须落在某个节的 raw 区间内
    for off in site_offsets:
        inside = any(raw_size > 0 and raw_ptr <= off < raw_ptr + raw_size
                     for (_nm, _va, _vs, raw_ptr, raw_size) in sections)
        if not inside:
            problems.append('站点偏移 0x%X 不在任何节的 raw 范围内（落在节外/空洞）' % off)

    # (4) SizeOfImage / 对齐自洽
    magic = _u16(data, e_lfanew + 0x18)
    if magic in (0x10B, 0x20B):
        sec_align = _u32(data, e_lfanew + 0x18 + 0x20)
        file_align = _u32(data, e_lfanew + 0x18 + 0x24)
        size_image = _u32(data, e_lfanew + 0x18 + 0x38)
        if file_align == 0 or (file_align & (file_align - 1)) != 0:
            problems.append('FileAlignment=0x%X 非 2 的幂' % file_align)
        if sec_align == 0 or (sec_align & (sec_align - 1)) != 0:
            problems.append('SectionAlignment=0x%X 非 2 的幂' % sec_align)
        # 规范：SectionAlignment 小于页大小(0x1000)时，FileAlignment 必须与之相等
        if 0 < sec_align < 0x1000 and file_align != sec_align:
            problems.append('SectionAlignment=0x%X < 0x1000 时 FileAlignment(0x%X) 必须相等'
                            % (sec_align, file_align))
        if sec_align > 0 and size_image > 0:
            end_va = 0
            for (_nm, vaddr, vsize, _rp, _rs) in sections:
                if vaddr == 0 and vsize == 0:
                    continue
                e = (vaddr + vsize + sec_align - 1) // sec_align * sec_align
                end_va = max(end_va, e)
            if end_va > size_image:
                problems.append('节虚拟末尾 0x%X > SizeOfImage 0x%X，映像头自相矛盾'
                                % (end_va, size_image))
    return problems


# ---------------------------------------------------------------- 语义校验
def _v_skip_tutorial(data, site, state):
    """语义校验（区分原始/补丁后形态），并确认前后两条跳转目标一致。

    原始：test al,al ; jne rel32        （站点 = jne）
    补丁后：test al,al ; nop ; jmp rel32（站点 = nop，跳转目标必须与原来一致）
    """
    insns = list(MD.disasm(bytes(data[site:site + 24]), site))
    if not insns:
        return False
    if state == 'PATCHABLE':
        if not (insns[0].mnemonic == 'jne' and insns[0].size == 6):
            return False
        jump = insns[0]
    else:
        if not (insns[0].mnemonic == 'nop' and insns[0].size == 1
                and len(insns) > 1 and insns[1].mnemonic == 'jmp' and insns[1].size == 5):
            return False
        jump = insns[1]
    tgt = jump.operands[0].imm
    # 站点之后应还有一条 jne，目标相同（说明是同一处两条并列的条件跳转）
    for ins in insns:
        if ins.address > site and ins.mnemonic == 'jne' and ins.operands[0].imm == tgt:
            return True
    return False


def _v_jne_rel8(data, site, state):
    """原始：jne rel8（2 字节）；补丁后：jmp rel8（2 字节）。"""
    ins = disasm_one(data, site)
    if ins is None or ins.size != 2:
        return False
    return ins.mnemonic == ('jne' if state == 'PATCHABLE' else 'jmp')


def _v_push_imm(data, site, state):
    """站点是 push imm32 立即数的第 2 字节（imm = 0x2384_04xx / 07xx），指令从站点前 2 字节开始。"""
    if site < 2 or data[site - 2] != 0x68:
        return False
    ins = disasm_one(data, site - 2)
    if ins is None or ins.mnemonic != 'push' or ins.size != 5:
        return False
    imm = ins.operands[0].imm
    want_lo = 0x04 if state == 'PATCHABLE' else 0x07
    return (imm >> 16) & 0xFFFF == 0x2384 and (imm >> 8) & 0xFF == want_lo and (imm & 0xFF) == 0x00


VALIDATORS = {
    'skip_tutorial': _v_skip_tutorial,
    'jne_rel8': _v_jne_rel8,
    'push_imm': _v_push_imm,
}


# ---------------------------------------------------------------- 定位逻辑
def locate_sites(data):
    """对每个站点返回 dict：state / offset / match_start / orig_hits / new_hits / valid。"""
    results = []
    for s in SITES:
        ho = search(data, s['orig'])
        hn = search(data, s['patched'])
        r = {'site': s, 'orig_hits': ho, 'new_hits': hn, 'offset': None, 'match_start': None}
        if len(ho) == 1 and len(hn) == 0:
            r['state'] = 'PATCHABLE'
            r['match_start'] = ho[0]
            r['offset'] = ho[0] + s['site_off']
        elif len(ho) == 0 and len(hn) == 1:
            r['state'] = 'PATCHED'
            r['match_start'] = hn[0]
            r['offset'] = hn[0] + s['site_off']
        elif len(ho) == 0 and len(hn) == 0:
            r['state'] = 'NOT_FOUND'
        else:
            r['state'] = 'AMBIGUOUS'
        # 语义校验（state 已知：PATCHABLE=原始形态，PATCHED=补丁后形态）
        if r['offset'] is not None:
            r['valid'] = VALIDATORS[s['validator']](data, r['offset'], r['state'])
            if not r['valid']:
                r['state'] = 'SEMANTIC_FAIL'
        else:
            r['valid'] = False
        results.append(r)
    return results


def _site_bytes(data, r):
    s = r['site']
    off = r['offset']
    return list(data[off:off + s['site_len']])


def _fmt_hits(data, r, hits):
    s = r['site']
    return ['0x%X(站点 0x%X 字节 %s)' % (h, h + s['site_off'],
                                        data[h + s['site_off']:h + s['site_off'] + s['site_len']].hex(' '))
            for h in hits]


def print_disasm(data, start, site_lo, site_hi, extra=40, indent='    '):
    """从 start（已对齐的指令边界）反汇编到 site_hi+extra；标出站点区间。

    start 取特征码命中起点，天然是指令边界，因此打印出的每条指令都与真实执行流对齐。
    """
    for ins in disasm_from(data, start, site_hi + extra):
        mk = '   <== 站点' if site_lo <= ins.address < site_hi else ''
        print('%s%08X  %-20s %-8s %s%s' % (
            indent, ins.address, ins.bytes.hex(' '), ins.mnemonic, ins.op_str, mk))


# ---------------------------------------------------------------- locate 子命令
def cmd_locate(path):
    if not os.path.isfile(path):
        print('错误：找不到文件 %s' % path)
        return 2
    with open(path, 'rb') as f:
        data = f.read()
    print('=' * 68)
    print(' ub_patcher 定位  |  %s' % path)
    print(' 大小 %d B   MD5 %s' % (len(data), md5(path)))
    print('=' * 68)
    results = locate_sites(data)
    ok = 0
    for idx, r in enumerate(results, 1):
        s = r['site']
        print()
        print('[%d/4] %-26s (%s)' % (idx, s['name'], s['cn']))
        if r['state'] == 'PATCHABLE':
            ok += 1
            print('  状态  : 可打补丁  (原字节唯一命中)')
            print('  偏移  : 0x%X' % r['offset'])
            print('  站内  : %s   (期望原字节 %s)' % (
                bytes(_site_bytes(data, r)).hex(' '), bytes(s['old']).hex(' ')))
            print('  命中  : 原始模式 %d 处 / 已补丁模式 %d 处'
                  % (len(r['orig_hits']), len(r['new_hits'])))
            print('  校验  : 反汇编语义 OK')
            print('  反汇编:')
            print_disasm(data, r['match_start'], r['offset'], r['offset'] + s['site_len'])
        elif r['state'] == 'PATCHED':
            ok += 1
            print('  状态  : 已是补丁版  (补丁后字节唯一命中)')
            print('  偏移  : 0x%X' % r['offset'])
            print('  站内  : %s   (即补丁后字节 %s)' % (
                bytes(_site_bytes(data, r)).hex(' '), bytes(s['new']).hex(' ')))
            print('  命中  : 原始模式 %d 处 / 已补丁模式 %d 处'
                  % (len(r['orig_hits']), len(r['new_hits'])))
            print('  校验  : 反汇编语义 OK')
            print('  反汇编:')
            print_disasm(data, r['match_start'], r['offset'], r['offset'] + s['site_len'])
        elif r['state'] == 'SEMANTIC_FAIL':
            print('  状态  : 语义校验失败（模式命中但反汇编形状不符，拒绝采信）')
            print('  偏移  : 0x%X' % r['offset'])
            print('  站内  : %s' % bytes(_site_bytes(data, r)).hex(' '))
            print('  候选  : 原始 %s' % (_fmt_hits(data, r, r['orig_hits']) or '无'))
            print('         补丁后 %s' % (_fmt_hits(data, r, r['new_hits']) or '无'))
        elif r['state'] == 'NOT_FOUND':
            print('  状态  : 未命中（原始模式与已补丁模式均 0 命中）')
            print('  说明  : 该站点在这版 DLL 里形状已变，特征码无法定位——不猜。')
        else:  # AMBIGUOUS
            print('  状态  : 歧义（命中数不为 1）')
            print('  候选  : 原始 %d 处 %s' % (len(r['orig_hits']), _fmt_hits(data, r, r['orig_hits'])))
            print('         补丁后 %d 处 %s' % (len(r['new_hits']), _fmt_hits(data, r, r['new_hits'])))
    print()
    print('-' * 68)
    print('汇总：%d/4 站点定位成功' % ok)
    if ok != 4:
        print('存在未定位站点，补丁功能将被拒绝。')
    return 0 if ok == 4 else 1


# ---------------------------------------------------------------- patch 子命令
def cmd_patch(path, out):
    if not os.path.isfile(path):
        print('错误：找不到文件 %s' % path)
        return 2
    with open(path, 'rb') as f:
        orig = f.read()
    if out is None:
        out = path + '.patched'
    print('=' * 68)
    print(' ub_patcher 打补丁  |  %s' % path)
    print(' 大小 %d B   MD5 %s' % (len(orig), md5(path)))
    print(' 输出 %s' % out)
    print('=' * 68)

    results = locate_sites(orig)

    # PE 完整性门：写出前先确认输入文件本身完整、且站点落在真实节区 raw 范围内。
    # 拦住「节表已越界的截断/损坏 PE」——这类文件站点可能仍能命中，但补丁写出去就是坏文件。
    pe_offsets = [r['offset'] for r in results if r['offset'] is not None]
    pe_problems = verify_pe_integrity(orig, pe_offsets)
    if pe_problems:
        print()
        print('!! 拒绝写出补丁文件：输入文件未通过 PE 完整性校验（截断/损坏/非 PE）')
        for p in pe_problems:
            print('   - %s' % p)
        print()
        print('未修改任何文件，未产生任何输出。')
        return 1

    problems = []
    for r in results:
        s = r['site']
        if r['state'] == 'PATCHABLE':
            got = _site_bytes(orig, r)
            if got != s['old']:
                problems.append('%s: 站内字节 %s != 期望 %s'
                                % (s['name'], bytes(got).hex(' '), bytes(s['old']).hex(' ')))
            continue
        if r['state'] == 'PATCHED':
            problems.append('%s: 已是补丁版（偏移 0x%X），无需重复处理' % (s['name'], r['offset']))
        elif r['state'] == 'NOT_FOUND':
            problems.append('%s: 未命中（无法定位）' % s['name'])
        elif r['state'] == 'AMBIGUOUS':
            problems.append('%s: 歧义（原始 %d / 补丁后 %d 命中）'
                            % (s['name'], len(r['orig_hits']), len(r['new_hits'])))
        else:  # SEMANTIC_FAIL
            problems.append('%s: 语义校验失败' % s['name'])

    if problems:
        print()
        print('!! 拒绝写出补丁文件：并非 4 个站点都「唯一命中且字节符合预期」')
        for p in problems:
            print('   - %s' % p)
        print()
        print('未修改任何文件。请人工确认该 DLL 版本后再处理。')
        return 1

    # 应用
    patched = bytearray(orig)
    print()
    print('--- 逐站点应用 ---')
    for r in results:
        s = r['site']
        off = r['offset']
        print()
        print('%s (%s)  @ 0x%X' % (s['name'], s['cn'], off))
        print('  字节  %s -> %s' % (bytes(orig[off:off + s['site_len']]).hex(' '),
                                    bytes(s['new']).hex(' ')))
        patched[off:off + s['site_len']] = bytes(s['new'])

    # 零旁改核对
    diff = [i for i in range(len(orig)) if orig[i] != patched[i]]
    expect = set()
    for r in results:
        s = r['site']
        for k in range(s['site_len']):
            expect.add(r['offset'] + k)
    collateral = [i for i in diff if i not in expect]
    print()
    print('--- 差异核对 ---')
    print('  变动字节共 %d 个，期望 %d 个' % (len(diff), len(expect)))
    for i in sorted(diff):
        print('    0x%X: %02X -> %02X' % (i, orig[i], patched[i]))

    if collateral:
        print('  !! 出现意外旁改 %d 处，已中止，不写文件' % len(collateral))
        return 1

    # 输出目录不存在时自动创建（避免未捕获的 FileNotFoundError）；仅在所有校验通过后才落地
    out_dir = os.path.dirname(out)
    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            print()
            print('错误：无法创建输出目录 %s（%s）' % (out_dir, e))
            return 2
    with open(out, 'wb') as f:
        f.write(bytes(patched))
    print()
    print('已写出 %s  (%d B, MD5 %s)' % (out, len(patched), md5(out)))

    # 打补丁前后反汇编对比
    print()
    print('--- 打补丁前后反汇编对比 ---')
    for r in results:
        s = r['site']
        off = r['offset']
        print()
        print('[前] %s @0x%X' % (s['name'], off))
        print_disasm(orig, r['match_start'], off, off + s['site_len'])
        print('[后] %s @0x%X' % (s['name'], off))
        print_disasm(bytes(patched), r['match_start'], off, off + s['site_len'])

    # 自检：对输出再跑一次定位，应全部为「已打补丁」
    recheck = locate_sites(bytes(patched))
    states = [r['state'] for r in recheck]
    print()
    print('自检：对输出文件再定位 -> %s' % states)
    if all(st == 'PATCHED' for st in states):
        print('自检通过：4 个站点均为「已打补丁」形态。')
        return 0
    print('自检警告：输出文件定位结果异常，请人工复核。')
    return 1


# ---------------------------------------------------------------- 入口
USAGE = """ub_patcher.py - jubeat.dll 通用补丁器（反汇编 + 特征码重定位）

用法:
  ub_patcher.py locate --dll <jubeat.dll>               定位 4 个补丁站点（只读）
  ub_patcher.py patch  --dll <jubeat.dll> [--out <文件>]  打补丁（默认 <dll>.patched）
  ub_patcher.py <jubeat.dll>                            拖放 DLL 上去 = locate
"""


def parse_args(argv):
    if not argv:
        return None, None, None
    if argv[0] in ('-h', '--help', 'help'):
        return 'help', None, None
    # 拖放：第一个参数是文件路径
    if argv[0] not in ('locate', 'patch'):
        if os.path.isfile(argv[0]) or argv[0].lower().endswith('.dll'):
            return 'locate', argv[0], None
        return 'unknown', argv[0], None
    cmd = argv[0]
    dll = None
    out = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--dll' and i + 1 < len(argv):
            dll = argv[i + 1]; i += 2; continue
        if a == '--out' and i + 1 < len(argv):
            out = argv[i + 1]; i += 2; continue
        if a.startswith('--dll='):
            dll = a.split('=', 1)[1]; i += 1; continue
        if a.startswith('--out='):
            out = a.split('=', 1)[1]; i += 1; continue
        if dll is None:
            dll = a; i += 1; continue
        i += 1
    return cmd, dll, out


def main():
    setup_console()
    cmd, dll, out = parse_args(sys.argv[1:])
    if cmd is None or cmd == 'help':
        print(USAGE)
        return 0 if cmd == 'help' else 2
    if cmd == 'unknown':
        print('无法识别的参数：%s\n' % dll)
        print(USAGE)
        return 2
    if not dll:
        print('错误：缺少 --dll <路径>\n')
        print(USAGE)
        return 2
    if cmd == 'locate':
        return cmd_locate(dll)
    if cmd == 'patch':
        return cmd_patch(dll, out)
    print(USAGE)
    return 2


if __name__ == '__main__':
    sys.exit(main())
