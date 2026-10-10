# ub-store — jubeat 本地机台工具链

针对 **jubeat beyond the Ave.（L44）** 本地机台的魔改工具集，核心是一套把
**Malody 自制谱** 转成 jubeat 可玩的谱面并自动装进游戏的完整链路，外加谱面格式解析、
贴图编解码、音频编解码、官方更新包处理等底层工具。

> ⚠️ **免责声明**
> 本项目**仅供个人备份与研究**。jubeat 是 KONAMI 的注册商标，游戏本体、曲目、贴图、
> 音频等一切资源的版权归 KONAMI 所有。**本仓库不包含任何游戏资源**（贴图 / 曲名 /
> 音频 / 谱面数据均无），只有处理这些资源的**工具代码**。请勿用于商业用途或二次分发
> 游戏资源；因使用本工具产生的任何后果由使用者自负。

---

## 功能

| 模块 | 作用 |
|---|---|
| `ub_store.py` | **Malody 商店客户端核心**：登录、浏览（商店 + 公开库双来源增量拉取）、下载 |
| `ub_store_gui.py` | 上述功能的图形界面（tkinter） |
| `ub_newsong.py` | **新曲注册引擎**：选空闲 music_id、补齐曲表/包表条目、**只新增不覆盖** |
| `ub_chart.py` | Malody `.mc` / `.mcz` → jubeat `.eve` 谱面转换；`_msc.ifs` 容器读写 |
| `ub_audio.py` | **Konami BMP / OKI4S ADPCM** 音频编解码 + ffmpeg 解码 + 预览切片 |
| `tdxt_encode.py` | **TDXT / PXET 贴图**编解码（12 种像素格式 + DXT1/3/5 + LZSS） |
| `ub_version.py` | `theme_info` 版本分类注册表增删（自动备份、可精确回退） |
| `ub_updater.py` | 官方增量更新包处理（剔除删除标记、合并曲表 / 包表） |
| `ub_patcher.py` | DLL 补丁反汇编 + 跨版本重定位（wildcard 签名 + capstone 语义校验） |
| `ub_update.py` | **自更新**：从 GitHub Releases 检查并替换自身（无 Release 时回退读仓库内 `VERSION`） |
| `ub_import.py` | **本地导入**：把自备的谱面文件 / 官方更新包接进安装链路 |
| `ub_verify_exe.py` | 校验 PyInstaller onefile exe 里内嵌的代码（PYZ + CArchive 双路径） |

---

## 环境要求

- **Python 3.7 或 3.8**（32 位；见下方「Windows 7 注意事项」）
- `Pillow`（贴图处理）
- `ffmpeg`（音频解码；可选，不处理自带音源时不需要）
- 可选：`pyinstaller`（自行打包）

```bash
pip install Pillow
```

---

## 快速开始

### 图形界面（推荐）

```bash
python ub_store_gui.py
```

界面分四页：**谱库浏览 / 已下载 / 已装自制谱 / 登录**。

典型流程：登录 Malody 账号 → 谱库浏览里翻页 / 过滤（默认只显示纯 Pad 谱）→
下载 → 到「已下载」页点「一键安装」。

### 命令行

```bash
# 查看某下载目录的信息
python ub_newsong.py list-ids --game "<游戏根>/contents"

# 生成安装计划（不落盘，先看要动哪些文件）
python ub_newsong.py plan --game "<游戏根>/contents" --mcz chart.mc --out plan.json

# 实际安装
python ub_newsong.py install --game "<游戏根>/contents" --plan plan.json

# 卸载（按 music_id 精确回退）
python ub_newsong.py uninstall --music-id 90001036 --game "<游戏根>/contents"
```

> 「游戏根」= 包含 `contents` 的那一层；`contents` 里应有 `spice.exe` 与 `data/`。

### 导入本地文件

不一定非要走商店下载——你自己手上的谱面、或官方更新包，都能直接喂给程序。

**方式一：菜单**（`文件` 菜单）

- **导入谱面文件…** — 选 `.mc` / `.mcz`（可多选）。程序会**自动带上同目录的音源与封面**，
  所以把谱面和 `.ogg`/`.jpg` 放一起就行。
- **导入官方更新包…** — 选 `.7z` / `.zip`。程序先**扫描预览**（条目数、谱面/贴图数量），
  若包内含**删除标记**会单独警告并让你决定是否执行删除；应用前**自动备份**。

**方式二：投放目录**

`文件 → 打开导入目录`，把文件全丢进去，然后 `文件 → 扫描导入目录`。适合一次导入一批。

**方式三：拖放**

直接把文件/文件夹**拖到程序窗口上**即可（原生 `WM_DROPFILES`，不依赖任何第三方库）。
也可以把文件拖到 **exe 图标**上启动。

> **一个目录里放了多个谱面怎么办？**
> 安装器是按「目录」工作的，同一目录里只会取体积最大的那个谱面。所以
> `ub_import.py` 检测到这种情况时，会为每个谱面**自动建暂存目录**（把谱面连同
> 同目录的音源/封面复制过去），逐个安装、互不覆盖；只有一个谱面时则**零拷贝**直接安装。
> 暂存目录在安装结束后自动清理。

### 自更新

```bash
python ub_update.py --check                          # 看有没有新版
python ub_update.py --install                        # 下载并替换自身
```

界面上则是 **帮助 → 检查更新…**；启动时也会做一次静默检查（只写状态栏，不弹窗）。
把 `config.json` 里的 `check_update_on_start` 设为 `false` 可完全关闭。

---

## 配置

配置文件在**程序同目录**的 `ub_store_data/config.json`（便携模式，首次运行自动生成）：

| 键 | 默认 | 说明 |
|---|---|---|
| `proxy` | `""` | **默认直连**。需要代理时在【高级 → 网络代理设置…】里填 |
| `custom_title_marker` | `"[自制]"` | 自制曲曲名前缀（渲染到分组图集） |
| `custom_reading_prefix` | `"ジセイ"` | 曲表 `name_string` 的假名前缀（按假名搜「ジセイ」可筛出） |
| `store_pad_only` | `true` | 谱库只显示纯 Pad（jubeat）谱 |
| `check_update_on_start` | `true` | 启动时静默检查更新 |
| `browse_anon_pages` | `10` | 公开库每个 status 最多翻几页（该接口限速） |
| `custom_id_base` | `99900000` | **自制谱 music_id 起始段**，见下方「⚠️ 编号必须远离官方段」 |

### ⚠️ 编号必须远离官方段（否则会被官方更新包撞掉）

官方 `music_id` 的编号规律是 **`世代 × 1000000 + 序号`**：

```
10000036 … 10000070     世代 10
11000001 … 11001010     世代 11
20000002 … 20000125     世代 20
        ⋮
90000001 … 90001035     世代 90  ← beyond the Ave.（当前）
```

每个世代的段内序号**只用到 1~1010 左右**，而且官方**下个更新包会从 90001036 继续往后排**。

所以自制谱**绝不能**紧贴官方尾部编号——老版本默认的 `90001036` 正是踩在这个雷上，
装一次官方更新就会撞 id。

本项目的做法：

- **默认起点 `99900000`**（99 段里 90 万偏移处）。与官方当前最大值相隔约 **990 万**，
  且即便官方将来用到 99 段，也只占到 `99 001010` 左右，离 `99 900000` 差着 89 万位。
- **自动纠正**：万一有人把 `custom_id_base` 改回官方规律区，
  `recommend_ids()` 会调用 `safe_base()` 把它挪到安全位置，并在返回信息里说明。
- 用 `is_official_style_id()` 可以判断某个 id 是否落在官方规律内（段内偏移 < `OFFICIAL_SEQ_LIMIT` 即视为会被官方占用）。

> 已有自制谱**不受影响**（`scan_occupied()` 会照常把它们算作已占用）。
> 但如果你之前用旧默认值装过谱面，建议注意它们占的是 `90001036~` 这段——
> 将来官方更新到这些 id 时需要处理。

---

## 自行打包

用 PyInstaller：

```bash
python -m PyInstaller --onefile --noconsole --name ub_store \
  --paths . --hidden-import ub_newsong --hidden-import ub_chart \
  --hidden-import ub_updater --hidden-import tdxt_encode --hidden-import ub_audio \
  ub_store_gui.py
```

### 🔴 Windows 7 注意事项（非常重要）

**目标机是 Windows 7 时，必须用 Python ≤ 3.7 打包。**

原因：Python 3.8 起，加载扩展模块（`.pyd`）改用了
`LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR`
（见 CPython `Python/dynload_win.c`，改动来自 bpo-36085）。这套 flag 由
**KB2533623** 引入，**Win7 SP1 未装该补丁时 `LoadLibraryExW` 会直接返回
`ERROR_INVALID_PARAMETER (87)`**，表现为：

> 内置模块（`math`/`zlib`/…）全部正常，但**所有独立 `.pyd`
> （`_socket`/`_ctypes`/`_tkinter`/`_ssl`…）全部加载失败**。

| Python | 加载 `.pyd` 的 flag | 缺 KB2533623 时 |
|---|---|---|
| ≤ 3.7 | `LOAD_WITH_ALTERED_SEARCH_PATH` | **可用** ✅ |
| ≥ 3.8 | `LOAD_LIBRARY_SEARCH_*` | **报错 87** ❌ |

两条路任选：

1. **给目标机装 KB2533623**（`Windows6.1-KB2533623-x86/x64.msu`），之后 3.8 也能用；
2. **用 Python 3.7 打包**（本仓库采用这条，不需要动机台）。

另外打包时建议在 `.spec` 里**过滤掉随包分发的 CRT**，让程序用系统的：

```python
_EXCLUDE = ('vcruntime140.dll', 'vcruntime140_1.dll',
            'ucrtbase.dll', 'msvcp140.dll', 'concrt140.dll')
a.binaries = [b for b in a.binaries
              if not any(b[0].lower().startswith(x) for x in _EXCLUDE)]
```

否则 PyInstaller 会从**构建机**的 `System32` 抓一份 `VCRUNTIME140.dll` 打进包，
在目标机上可能与系统版本冲突。

---

## 目录结构

```
tools/            工具与测试（本仓库主体）
docs/             设计文档与排查报告（机制说明、格式分析、踩坑记录）
```

文档按主题命名，`ub_` 开头的是 jubeat 相关。比较值得先读的：

- `ub_项目总纲与后续规划.md` — 全貌
- `ub_版本档机制全解.md` — 曲表 `<version>` 位掩码与版本分类
- `ub_图集名额与版本位机制.md` — 分组图集与贴图注册
- `ub_mscifs容器破解报告.md` — `_msc.ifs` 容器格式
- `ub_更新器设计与现状.md` — 官方增量包处理

---

## 测试

```bash
cd tools
python test_ub_update.py     # 自更新（离线，全用桩）
python test_ub_newsong.py    # 难度分档规则
python test_ub_audio.py      # 音频编解码
python test_ub_version.py    # 版本分类
python test_ub_grouptex.py   # 分组图集
python test_ub_store.py      # 商店客户端（含网络层桩）
python test_ub_store_gui.py  # GUI（需要可用显示）
```

---

## License

MIT
