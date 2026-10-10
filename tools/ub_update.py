# -*- coding: utf-8 -*-
"""程序自更新：从 GitHub Releases 检查并替换自身。

设计要点
--------
* 只依赖标准库（``urllib``），不额外引入请求库；可选走代理（与 ub_store 的配置一致）。
* ``check()`` 只做网络查询，返回结构化结果，**不落盘、不替换**，便于测试与预演。
* ``replace_self()`` 用 Windows 的「运行中的 exe 允许改名」特性完成自替换：
    当前 exe 改名为 ``*.old`` → 把新 exe 写到原路径 → 重启。
  ``*.old`` 留到下次启动时由 ``cleanup_old()`` 清掉。
* 全部函数都不弹 GUI，方便被 CLI / 测试直接调用。

用法::

    python ub_update.py --check                    # 看有没有新版
    python ub_update.py --check --proxy http://127.0.0.1:10808
    python ub_update.py --install                  # 下载并替换自身
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# 版本与更新源
# ---------------------------------------------------------------------------
APP_VERSION = "1.14.0"
GITHUB_REPO = "MO-YU-CH/ub-store"

#: 各平台从 Release 资产里挑哪个文件。key 由 pick_asset_key() 决定。
ASSET_NAMES = {
    "win32": "ub_store.exe",          # 32 位（Win7 / WES7 机台）
    "win64": "ub_store_x64.exe",      # 64 位
}

_UA = "ub-store-updater/%s (+https://github.com/%s)" % (APP_VERSION, GITHUB_REPO)
OLD_SUFFIX = ".old"


# ---------------------------------------------------------------------------
# 版本号
# ---------------------------------------------------------------------------
def parse_version(text):
    """'v1.2.3' / '1.2.3' / '1.2.3-beta' -> (1, 2, 3)。

    解析不出来的部分一律当 0，保证任何 tag 都能比较而不会抛异常。
    """
    if not text:
        return (0,)
    s = str(text).strip().lstrip("vV")
    # 去掉预发布/构建元数据
    for sep in ("-", "+"):
        if sep in s:
            s = s.split(sep, 1)[0]
    out = []
    for part in s.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def is_newer(remote, local):
    """remote 是否比 local 新（按逐段数值比较，长度不同时短的一方补 0）。"""
    a, b = parse_version(remote), parse_version(local)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def pick_asset_key(arch=None):
    """按当前解释器位数选资产名。"""
    if arch is None:
        arch = "win64" if sys.maxsize > 2 ** 32 else "win32"
    return arch


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------
def _opener(proxy=None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))     # 显式不走代理
    return urllib.request.build_opener(*handlers)


def _get_json(url, proxy=None, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/vnd.github+json",
    })
    with _opener(proxy).open(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8"))


def _get_text(url, proxy=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with _opener(proxy).open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


class UpdateError(Exception):
    pass


# ---------------------------------------------------------------------------
# 检查
# ---------------------------------------------------------------------------
def _check_release(repo, current, proxy, timeout, arch):
    """走 GitHub Releases API。"""
    api = "https://api.github.com/repos/%s/releases/latest" % repo
    res = {"ok": False, "current": current, "latest": None, "newer": False,
           "notes": "", "html_url": "", "asset_name": None, "asset_url": None,
           "error": None, "source": "release"}
    try:
        data = _get_json(api, proxy=proxy, timeout=timeout)
    except urllib.error.HTTPError as ex:
        # 仓库还没有 Release 时是 404 —— 这是正常状态，不算错误
        res["error"] = ("尚未发布任何 Release" if ex.code == 404
                        else "HTTP %s" % ex.code)
        return res
    except Exception as ex:                                  # noqa: BLE001
        res["error"] = "%s: %s" % (type(ex).__name__, ex)
        return res

    tag = data.get("tag_name") or data.get("name") or ""
    res["latest"] = tag
    res["notes"] = (data.get("body") or "").strip()
    res["html_url"] = data.get("html_url") or ""
    res["newer"] = is_newer(tag, current)
    res["ok"] = True

    want = ASSET_NAMES.get(pick_asset_key(arch), ASSET_NAMES["win32"])
    for a in (data.get("assets") or []):
        if (a.get("name") or "").lower() == want.lower():
            res["asset_name"] = a.get("name")
            res["asset_url"] = a.get("browser_download_url")
            break
    if res["newer"] and not res["asset_url"]:
        res["error"] = "新版 Release 里没有找到资产 %s" % want
    return res


def _check_repo_files(repo, current, proxy, timeout, arch):
    """回退方案：直接读仓库里的 ``VERSION`` 与 ``release/<asset>``。

    这样即使还没来得及发 Release，只要把新版 exe 提交进 ``release/`` 目录
    并更新 ``VERSION``，自更新就能工作。
    """
    raw = "https://raw.githubusercontent.com/%s/HEAD" % repo
    res = {"ok": False, "current": current, "latest": None, "newer": False,
           "notes": "", "html_url": "https://github.com/%s" % repo,
           "asset_name": None, "asset_url": None, "error": None, "source": "repo"}
    try:
        latest = _get_text("%s/VERSION" % raw, proxy=proxy, timeout=timeout).strip()
    except Exception as ex:                                  # noqa: BLE001
        res["error"] = "%s: %s" % (type(ex).__name__, ex)
        return res
    if not latest:
        res["error"] = "仓库里的 VERSION 是空的"
        return res
    want = ASSET_NAMES.get(pick_asset_key(arch), ASSET_NAMES["win32"])
    res["latest"] = latest
    res["newer"] = is_newer(latest, current)
    res["ok"] = True
    res["asset_name"] = want
    res["asset_url"] = "%s/release/%s" % (raw, want)
    res["notes"] = "（来自仓库 VERSION，未发布 Release）"
    if res["newer"]:
        # 顺手确认产物真的存在，免得下载时才失败
        try:
            req = urllib.request.Request(res["asset_url"], method="HEAD",
                                         headers={"User-Agent": _UA})
            _opener(proxy).open(req, timeout=timeout).close()
        except Exception as ex:                              # noqa: BLE001
            res["error"] = "仓库里找不到 %s（%s）" % (want, type(ex).__name__)
    return res


def check(repo=GITHUB_REPO, current=APP_VERSION, proxy=None, timeout=20,
          arch=None, include_prerelease=False):
    """查询最新版本：**优先 GitHub Releases，查不到则回退到仓库内文件**。返回 dict：

        {ok, current, latest, newer, notes, html_url,
         asset_name, asset_url, error, source}
    """
    res = _check_release(repo, current, proxy, timeout, arch)
    if res["ok"]:
        return res
    alt = _check_repo_files(repo, current, proxy, timeout, arch)
    if alt["ok"]:
        return alt
    # 两边都失败：把两个原因都带上，便于排查
    res["error"] = "%s / %s" % (res.get("error"), alt.get("error"))
    return res


# ---------------------------------------------------------------------------
# 下载 / 替换
# ---------------------------------------------------------------------------
def download(url, dest, proxy=None, timeout=60, progress=None):
    """下载 url 到 dest（先写 .part 再改名，避免半截文件）。返回 dest。"""
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with _opener(proxy).open(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    os.replace(tmp, dest)
    return dest


def current_exe():
    """当前可执行文件路径；源码模式下返回 None。"""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return None


def cleanup_old(exe=None):
    """删除上次自更新留下的 *.old（启动时调用）。"""
    exe = exe or current_exe()
    if not exe:
        return False
    old = exe + OLD_SUFFIX
    if os.path.exists(old):
        try:
            os.remove(old)
            return True
        except OSError:
            pass                                        # 还被占着，下次再说
    return False


def replace_self(new_exe, restart=True, exe=None):
    """把 new_exe 覆盖到当前 exe 并（可选）重启。

    Windows 下运行中的 exe 不能直接覆盖，但**可以改名**，所以：
        原 exe -> exe.old  →  新 exe 写到原路径  →  启动新 exe
        旧的 .old 下次启动时由 cleanup_old() 清掉。

    返回新 exe 的路径。
    """
    exe = exe or current_exe()
    if not exe:
        raise UpdateError("当前不是打包后的 exe，无法自更新（源码模式请手动更新）")
    if not os.path.exists(new_exe):
        raise UpdateError("新文件不存在：%s" % new_exe)
    old = exe + OLD_SUFFIX
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        pass
    os.rename(exe, old)                    # ① 运行中的 exe 允许改名
    try:
        shutil.copy2(new_exe, exe)         # ② 写到原路径
    except Exception:
        os.rename(old, exe)                # 失败则回滚，别把程序弄丢
        raise
    if restart:
        subprocess.Popen([exe], close_fds=True)
    return exe


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="ub_store 自更新")
    ap.add_argument("--check", action="store_true", help="只检查是否有新版")
    ap.add_argument("--install", action="store_true", help="下载并替换自身")
    ap.add_argument("--repo", default=GITHUB_REPO)
    ap.add_argument("--version", default=APP_VERSION)
    ap.add_argument("--proxy", default=None)
    a = ap.parse_args(argv)

    if not (a.check or a.install):
        ap.print_help()
        return 0

    info = check(repo=a.repo, current=a.version, proxy=a.proxy)
    print("当前版本 : %s" % info["current"])
    if not info["ok"]:
        print("检查失败 : %s" % info["error"])
        return 1
    print("最新版本 : %s" % info["latest"])
    if not info["newer"]:
        print("已是最新版本。")
        return 0
    print("发现新版本！")
    if info["notes"]:
        print("--- 更新说明 ---")
        print(info["notes"][:800])
    if info["error"]:
        print("注意: %s" % info["error"])
        return 1
    if a.check:
        print("下载地址 : %s" % info["asset_url"])
        return 0

    exe = current_exe()
    if not exe:
        print("源码模式无法自更新。")
        return 1
    dst = exe + ".new"

    def prog(done, total):
        if total:
            sys.stdout.write("\r下载中 %d%%" % (100 * done // total))
            sys.stdout.flush()

    print("开始下载 %s" % info["asset_name"])
    download(info["asset_url"], dst, proxy=a.proxy, progress=prog)
    print("\n下载完成，正在替换…")
    replace_self(dst)
    print("已更新，新版本已启动。")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
