# -*- coding: utf-8 -*-
"""校验 PyInstaller onefile exe 的 PYZ 内嵌模块是否包含指定符号。

用法:
    python tools/ub_verify_exe.py dist/ub_store.exe ub_store BrowseSession next_batch

原理:
    onefile exe 尾部挂着 CArchive 目录；PYZ-00.pyz 是其一个成员，
    取出来后用 ZlibArchiveReader 解出各 module 的 marshal 代码对象，
    再在 code 常量树里递归搜符号名 / 字符串常量。
"""
import marshal
import os
import sys
import types

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader


def _iter_consts(code):
    """递归遍历 code 对象的所有常量（含嵌套 code）。"""
    stack = [code]
    while stack:
        cur = stack.pop()
        yield cur
        for c in getattr(cur, "co_consts", ()) or ():
            if isinstance(c, types.CodeType):
                stack.append(c)


def _collect_names(code):
    names = set()
    # 嵌套 code 的名字
    for sub in _iter_consts(code):
        names.update(getattr(sub, "co_names", ()) or ())
        names.update(getattr(sub, "co_varnames", ()) or ())
        for c in getattr(sub, "co_consts", ()) or ():
            if isinstance(c, str):
                names.add(c)
    return names


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    exe = sys.argv[1]
    module = sys.argv[2]
    wanted = sys.argv[3:]
    if not os.path.isfile(exe):
        print("[FAIL] 文件不存在: %s" % exe)
        return 2

    car = CArchiveReader(exe)
    print("[INFO] CArchive 条目数 = %d" % len(car.toc))
    all_names = set()
    origin = []

    def _load_script(blob):
        """CArchive 里的入口脚本：尝试多种 pyc header 偏移做 marshal。"""
        for off in (0, 8, 12, 16):
            try:
                return marshal.loads(blob[off:])
            except Exception:
                continue
        return None

    # 1) 入口脚本（ub_store_gui）在 CArchive 里
    if module in car.toc:
        code = _load_script(car.extract(module))
        if code is not None:
            all_names |= _collect_names(code)
            origin.append("CArchive:%s" % module)

    # 2) 普通模块在 PYZ 里
    pyz_names = [n for n in car.toc if n.lower().endswith(".pyz")]
    if pyz_names:
        pyz_name = pyz_names[0]
        data = car.extract(pyz_name)
        tmp = os.path.join(os.path.dirname(os.path.abspath(exe)),
                           "_verify_pyz_%d.pyz" % os.getpid())
        with open(tmp, "wb") as f:
            f.write(data)
        try:
            pyz = ZlibArchiveReader(tmp)
            keys = list(pyz.toc)
            hit = [k for k in keys if k == module or k.startswith(module + ".")]
            print("[INFO] PYZ 模块数 = %d，匹配 '%s' 的模块 = %s"
                  % (len(keys), module, hit))
            for k in hit:
                all_names |= _collect_names(pyz.extract(k))
                origin.append("PYZ:%s" % k)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    if not origin:
        print("[FAIL] 既不在 CArchive 也不在 PYZ: %s" % module)
        return 1
    print("[INFO] 来源 = %s，符号/常量总数 = %d" % (origin, len(all_names)))
    ok = True
    for w in wanted:
        found = w in all_names
        print("  [%s] %s" % ("OK" if found else "MISS", w))
        ok = ok and found
    print("[RESULT] %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
