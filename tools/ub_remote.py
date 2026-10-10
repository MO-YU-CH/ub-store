#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""实机（<TARGET_HOST>, Win7 SP1）远程操作工具。

用法：
    python tools/ub_remote.py run   "<PowerShell 命令>"
    python tools/ub_remote.py put   <本地路径> <远端路径>
    python tools/ub_remote.py get   <远端路径> <本地路径>
    python tools/ub_remote.py probe

要点（踩过的坑）：
  * **必须用原生 Windows 路径**给 key_filename —— git bash 的 $HOME 展开成
    /c/Users/... 这种 MSYS 路径，paramiko 在 Windows 上不认，会静默卡住。
  * 命令里**不要带非 ASCII**（中文），否则经 bash -> argv -> UTF-16LE 链路会卡。
    需要中文输出时改用英文，或把脚本先 put 上去再执行。
  * ssh_104.py 的 exec_command 读流会卡，这里改成显式 timeout + 完整读。
"""
import base64
import os
import sys
import time

try:
    import paramiko
except ImportError:
    sys.exit("[FATAL] 缺少 paramiko：D:\\python\\python.exe -m pip install paramiko")

HOST = os.environ.get("UB_HOST", "<TARGET_HOST>")
PORT = int(os.environ.get("UB_PORT", "22"))
USER = os.environ.get("UB_USER", "Administrator")
KEY = os.environ.get("UB_KEY", r"<USER_HOME>\.ssh\id_ed25519")


def connect(timeout=20):
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, PORT, username=USER, key_filename=KEY,
                timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
                look_for_keys=False, allow_agent=False)
    return cli


def dec(raw):
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp936", "replace")


def run(cli, cmd, timeout=120, quiet=False):
    """在实机执行命令，返回 (rc, out, err)。

    ⚠️ 实机（Win7 + OpenSSH_for_Windows_7.7）上 **通过 SSH 启动 PowerShell 会挂死**：
       `powershell -Command/-EncodedCommand` 实测一律 25s 超时，而 cmd 原生命令 0.1s 返回。
       ⇒ 本工具**只发 cmd 原生语法**（dir / if exist / copy / del / type / set / 直接跑 exe）。
       需要复杂逻辑时，先 put 一个 .bat 上去再执行。
    """
    if not quiet:
        print("$ " + cmd)
        print("-" * 60)
    _in, out, err = cli.exec_command(cmd, timeout=timeout)
    o = dec(out.read())
    e = dec(err.read())
    rc = out.channel.recv_exit_status()
    if o.strip():
        print(o.rstrip())
    if e.strip():
        print("[stderr] " + e.rstrip()[:2000])
    if not quiet:
        print("[exit=%d]" % rc)
    return rc, o, e


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    mode = sys.argv[1]
    t0 = time.time()
    cli = connect()
    if not mode.startswith("put") and not mode.startswith("get"):
        print("[OK] connected %s@%s (%.2fs)" % (USER, HOST, time.time() - t0))
    try:
        if mode == "run":
            run(cli, sys.argv[2])
        elif mode == "probe":
            for label, c in PROBE:
                print("\n" + "=" * 60 + "\n# " + label + "\n" + "=" * 60)
                try:
                    run(cli, c, quiet=True)
                except Exception as ex:                       # noqa: BLE001
                    print("[WARN] %s: %s" % (type(ex).__name__, ex))
        elif mode in ("put", "get"):
            sf = cli.open_sftp()
            try:
                if mode == "put":
                    sf.put(sys.argv[2], sys.argv[3])
                    print("[OK] put %s -> %s" % (sys.argv[2], sys.argv[3]))
                else:
                    sf.get(sys.argv[2], sys.argv[3])
                    print("[OK] get %s -> %s" % (sys.argv[2], sys.argv[3]))
            finally:
                sf.close()
        else:
            sys.exit("[FATAL] 未知模式: %s" % mode)
    finally:
        cli.close()


PROBE = [
    ("OS",
     'ver && echo PROC_ARCH=%PROCESSOR_ARCHITECTURE% && echo COMPUTER=%COMPUTERNAME%'),
    ("UCRT in System32",
     'for %f in (ucrtbase.dll api-ms-win-crt-runtime-l1-1-0.dll '
     'api-ms-win-crt-stdio-l1-1-0.dll vcruntime140.dll msvcp140.dll) do '
     '@if exist "%SystemRoot%\\System32\\%f" (echo [FOUND]   %f) else (echo [MISSING] %f)'),
    ("TEMP + leftover _MEI",
     'echo TEMP=%TEMP% && dir /b /ad "%TEMP%\\_MEI*" 2>nul && echo (end of _MEI list)'),
    ("Game dir",
     'if exist "D:\\JUBEAT\\contents\\spice.exe" (echo GAME OK at D:\\JUBEAT\\contents) '
     'else (echo GAME NOT at D:\\JUBEAT\\contents) && '
     'dir /b "D:\\JUBEAT\\*.exe" 2>nul && echo (end of exe list)'),
    ("SSH server",
     'sc query sshd 2>nul | findstr /i "SERVICE_NAME STATE"'),
]


if __name__ == "__main__":
    main()
