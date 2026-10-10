# jubeat beyond the Ave. (L44) 本地机台魔改 — 项目总纲与后续规划

> 生成：2026-10-08 21:2x　游戏根：`<GAME_ROOT>`　项目目录：`<PROJECT_ROOT>`
> 配套：`.workbuddy/memory/MEMORY.md`（长期约定）、`ub_基线清单.json`（改动核对用）

---

## 一、成果总览

### 1.1 底层格式 / 机制破解（已完成，是后续一切工作的地基）

| # | 能力 | 关键结论 | 落地工具 |
|---|---|---|---|
| 1 | **TDXT/PXET 贴图格式** | 12 种像素格式 + DXT1/3/5 + LZSS；PMAN 头 `f14` 必须按条目数重算；纹理表按名升序、PMAN 按 hash 升序、4 字节对齐 | `tools/tdxt_encode.py` |
| 2 | **Konami BMP / OKI4S ADPCM 音频** | 头 32 B（`BMP\0`+采样数+loopS/E+ch2+bits16+rate48000）；立体声 **1 字节/帧**；幅度档位恒为 `(2+4k)*STEP[idx]` → 可解析求最优 | `tools/ub_audio.py` |
| 3 | **`.ifs` 容器（MiscIfs）** | 改写后自动重算偏移/长度/manifest md5/`_info_` md5+size | `tools/ub_chart.py` |
| 4 | **`_msc.ifs` 内部结构** | 每包含 **两个音频成员**：`bgm.bin` 整曲 + `idx.bin` 预览（恒 **10.0 s = 480000 帧**）；预览起点取谱面 `meta.preview`，曲绘取 `meta.background` | 同上 |
| 5 | **曲表 / 包表契约** | `music_info.xml`（曲目）+ `alldata.bin`（**图集索引**）+ `packlist.bin` / `dplist.bin` | `ub_updater.py`、`ub_alldata_get.py` |
| 6 | **`theme_info.xml` 版本分类注册表** | SHIFT_JIS、单引号属性；`theme_id/pos_index/theme_type/theme_version/name_j`；本机 49 块，`theme_version` 0..10（9 跳过） | `tools/ub_version.py` |
| 7 | **版本位掩码机制（全解）** | 曲子的"版本" = `version` 掩码（u32）**最低的置位 bit**；`music_db.dll` 有 12 个判定函数；**16 位全被占用，无空位**；循环上限**硬编码 `cmp ecx,0x10`** | 文档 `ub_版本档机制全解.md` |
| 8 | **版本 ID 映射链** | `jb_org.dll` file **0x1BABB0**：命中最低 bit → `edi` = 版本 ID；已用 `0,2,4,6,8,9,10,11,12,13,14,15`，**空 ID = 1/3/5/7** | 同上 |
| 9 | **分组图集加载名单的真源** | 不是包表！是 `jb_org.dll` file **0x250234** 起、**20 字节 × 54 槽**的硬编码定长字符串数组（第 53 槽原为无效占位 `L44FO_BNR_J_99_999`） | `tools/ub_dll_atlas.py` |
| 10 | **layeredfs 机制 + 隐藏开关** | mod 目录 = `data_mods/` 下的子目录；命中写 `log.txt`；开关 `--layered-verbose` / `--layered-devmode` / `--layered-disable` / `--layered-allowlist` / `--layered-blocklist`；还支持 **PNG 打包式 mod**（`data_mods/_cache/<mod>/tex/*.png` + `texturelist.xml`，**不必手搓 TDXT**） | 文档 + 实测 |
| 11 | **网络设定机制** | TEST 模式 NETWORK CHECK 报的是**真实网卡 IP**（本机 192.168.0.102），与 `network.xml` 的 `ipaddr` **无关**；后者只是机台"声明值" | 见 §五 铁律 |
| 12 | **输入通道** | SpiceTools 按键默认绑到**具体物理设备**（`devid`）；清空 `devid` 后 RDP 键盘 + 脚本注入**同时可用**；抢前台 + DPI 感知坐标是可靠点击的前提 | `tools/gclick.py` |

### 1.2 已落地且实机验证的功能

| # | 功能 | 状态 |
|---|---|---|
| 1 | **分组图集名额扩展 55 → 56** | ✅ **实机验证生效**：日志出现 `layeredfs: Using ./data_mods/omnimix/d3/model/tex_l44av_bnr_j_12_001.bin`。三处改动：① `jb_org.dll` 槽 53 换名 ② 新增 `tex_l44av_bnr_j_12_001.bin`(520,880 B) + `mdl_...`(60 B) ③ `alldata.bin` 注册（2327→2328 行） |
| 2 | **自制谱商店 GUI** `dist/ub_store.exe`（40 MB） | ✅ 封面卡片网格浏览、已下载卡片列表、一键安装/卸载、便携数据目录（`ub_store_data`）、下载缓存、登录 |
| 3 | **新曲注册器** `tools/ub_newsong.py` | ✅ `list-ids / plan / install / uninstall / verify / group-tex`（只新增不覆盖，含小曲绘注册） |
| 4 | **谱面转换** | ✅ Malody `.mc` / `.mcz` → `.eve` + `_msc.ifs` 回写 |
| 5 | **更新器** `dist/ub_updater.exe` | ✅ 官方增量包处理（剔除删除标记、合并曲表/包表） |
| 6 | **补丁器** `dist/ub_patcher.exe` | ✅ DLL 补丁反汇编 + 跨版本重定位（wildcard 签名 + capstone 语义校验） |
| 7 | **omnimix 移植** | ✅ 曲表 **1613 首**；全曲贴图/曲绘/音频补齐；`data_mods` 共 3996 个文件 |
| 8 | **一键还原脚本** | ✅ `game_patch/UNDO_game_changes.bat`、`REDO_game_changes.bat`、`APPLY/REVERT_version_label_test.bat` |
| 9 | **取证 / 定位工具** | ✅ `probe_ver_atlas.py`（同名同内容覆盖探针）、`gclick.py`（DPI 感知点击+抢前台）、`screen_trace.py` |

### 1.3 工具清单（`tools/` 共 60 个，主力如下）

| 工具 | 用途 |
|---|---|
| `tdxt_encode.py` | PNG ↔ TDXT（12 种格式 + DXT + LZSS）+ 文本条生成 |
| `ub_audio.py` | Konami BMP / OKI4S 编解码 + ffmpeg 解码 + 预览切片 |
| `ub_chart.py` | Malody → `.eve` + `MiscIfs` 容器读写 |
| `ub_newsong.py` | 新曲注册（list-ids/plan/install/uninstall/verify/group-tex） |
| `ub_dll_atlas.py` | 分组图集名额槽增删（只改占位槽，不碰代码） |
| `ub_version.py` | `theme_info.xml` 版本分类注册表增删 |
| `ub_updater.py` / `ub_patcher.py` | 更新包处理 / DLL 补丁 |
| `ub_store.py` / `ub_store_gui.py` | 商店逻辑 / GUI |
| **`baseline_manifest.py`（本轮新增）** | 游戏目录基线生成与比对（`make` / `check`） |
| **`probe_ver_atlas.py`（本轮新增）** | 图集探针：布点 / 撤点 / 报告 |
| **`gclick.py`（本轮新增）** | DPI 感知点击 + 抢前台（截图坐标 == 点击坐标） |

---

## 二、当前游戏目录基线（改动核对用）

```
<PY_HOME>\python.exe tools\baseline_manifest.py check      # 任何时候一条命令比对差异
```

| 目录 | 文件数 |
|---|---|
| `data/` | 10225 |
| `data/d3/model/` | 5703 |
| `data_mods/` | 3996 |
| `data_mods/omnimix/d3/model/` | **3491**（手感敏感区基准线） |

关键文件：`jb_org.dll` = `c80647edcd4013676f77b3b2cb96d0ab`（**原版 + 图集槽扩展**，实验性版本名改动已清零）；
`dev/nvram/network.xml` = `336c728d68d0a6d60e7f2bbdb5ac77ae`（出厂原样）。

---

## 三、未决问题

| # | 问题 | 影响 | 现状 |
|---|---|---|---|
| 1 | **版本格 logotype 贴图在哪个图集** — 未定位 | 🔴 直接阻断"第 13 个版本档" | 已排除 `*_title.bin`、`*_select_genre.bin`、`*_smc_sm/mm*`、`*_panel.bin`、`*_emblem.bin`、`*_local_matching_select.bin` |
| 2 | **自定义版本 ID** — 16 位掩码已满 | 🔴 需要代码级补丁 | 方案已设计（掩码上限 `0x10`→`0x20` + 补 ID 链），未执行 |
| 3 | 启动时 `5-1506-0000`「IP 已变更」警告 | 🟡 可点掉，不影响运行 | 设计如此，**建议不修**（改声明值会撞致命 `5-1505-0022`） |
| 4 | `prop/as/config.ini` 开头 316 字节被 0x00 抹掉 | 🟡 Asphyxia 仍能跑 | 未处理 |
| 5 | 商店「装一首自制曲」端到端未验证 | 🟡 无法确认交付闭环 | 无 |

---

## 四、后续改造规划（分 4 阶段，每步都有验收 + 回退）

### 阶段 0 — 环境复原与基线固化（风险：**零**）

| 编号 | 任务 | 状态 |
|---|---|---|
| T0.1 | 生成游戏目录基线清单（74 个关键文件 md5 + 4 项目录计数） | ✅ **已完成** → `ub_基线清单.json` |
| T0.2 | 把 4 个 `jb_org.dll.bak_*` 归拢到 `_backup\` 子目录（不改内容，只搬家） | ⬜ 待你点头 |
| T0.3 | 启动游戏一次，确认图集仍加载 56 个（验证 DLL 还原没破坏图集扩展） | ⬜ 待你启动 |

**验收**：`log.txt` 出现 `tex_l44av_bnr_j_12_001.bin`；`baseline_manifest.py check` 除预期项外零差异。
**回退**：不需要（纯读 + 新增清单文件）。

---

### 阶段 1 — 定位「版本格贴图」所在图集（风险：**低**）

> 目标：拿到**一个图集文件名 + 承载版本 logo 的精灵名**。这是阶段 2 的前提。

| 编号 | 任务 | 做法 | 风险 |
|---|---|---|---|
| T1.1 | **问你**：那一屏（9 个绿色方块 + CLOSE）你上次是怎么进去的？ | 已知路径 = 零成本直接定位 | 零 |
| T1.2 | 若你不便操作 → **小规模探针** | 只挑 **20~50 个**高概率候选图集（`tex_l44*_{title,select,smc,music_bar,panel,navi,result}*.bin`），同名同内容复制进 `data_mods\omnimix\d3\model\`，你启动一次后我读日志，**当场 remove** | 低（已复盘：全量 2088 个会拖到手感异常，这次严格限量） |
| T1.3 | 备选：`--layered-verbose=1` | 让 layeredfs 打出**所有文件请求**（含未命中），一次就能看到整条加载链 | 低（需临时改启动参数） |
| T1.4 | 兜底：把已渲染的 `TIT_*` / `RES_EXC_*` 精灵与截图逐像素拟合 | 纯离线，不动游戏 | 零 |

**验收**：明确 `<图集文件名>` + `<精灵名>`，并渲染出该精灵与截图对比一致。
**回退**：`probe_ver_atlas.py remove`（探针可一键清空，已验证剩余 0）。

---

### 阶段 2 — 新增「自作譜」版本档（风险：**中**）

> 拆成 A / B 两条支线，**先做 A**，能过就不做 B。

#### 支线 A（纯数据层，先做）

| 编号 | 任务 | 做法 | 风险 |
|---|---|---|---|
| T2A.1 | 给自制曲挑一个"空出来的 bit" | 12 个判定函数里 `ver1` 覆盖 `{bit0, bit1}`，而**官方无任何曲子只用 bit1** → 把自制曲 `version` 掩码设成 `0x0002`（最低位 = bit1） | 零（只改曲表数据） |
| T2A.2 | 观察结果 | 启动后看选曲界面：自制曲是落进 `jubeat` 档，还是压根不出现 | 零 |
| T2A.3 | 若落进 `jubeat` 档且可分 → 加一条 `theme_info` 条目看是否分化 | 用 `ub_version.py add` | 低 |

**验收**：自制曲在选曲界面**只**出现在目标档；官方曲不受影响。
**回退**：`ub_version.py remove` + 曲表恢复备份。

#### 支线 B（代码层，仅当 A 不够时）

| 编号 | 任务 | 做法 | 风险 |
|---|---|---|---|
| T2B.1 | 放开掩码位宽 | `music_db.dll` 的 12 个判定函数里 `cmp ecx,0x10` → `cmp ecx,0x20`（启用 bit16+，掩码是 u32 有空间） | 🟠 中高（12 处定点补丁，先全文件备份） |
| T2B.2 | 补 ID 链 | `jb_org.dll` file `0x1BABB0` 的 ID 链插新分支：最低 bit ≥ 16 → 返回新 ID（空 ID `1/3/5/7` 里挑一个） | 🟠 高（代码紧凑区，需跳板） |
| T2B.3 | 版本格列表多一项 | 找到版本格列表的项数/数据源，加第 13 项 | 🟠 高 |
| T2B.4 | 画「自作譜」logo | 用 `tdxt_encode` + 字体渲染（`data/font/df-futomarugothic-w9.ttc`）造精灵，塞进 T1 定位到的图集 | 🟡 中（须守 4 条贴图铁律） |

**验收（T2B）**：选曲界面多出「自作譜」档，且**只**含自制曲；官方曲分类与数量不变；跑一首官方曲+一首自制曲均正常计分。
**回退**：`music_db.dll` / `jb_org.dll` 整文件备份还原 + 图集从备份还原。

**风险控制**：T2B 每做一小步就**重启实机验证一次**，每步只改一处，绝不合并。

---

### 阶段 3 — 闭环：商店 → 游戏端到端（风险：**低**）

| 编号 | 任务 | 验收 |
|---|---|---|
| T3.1 | 用 `ub_store.exe` 下载 1 首自制谱 → 一键安装 | 曲表/包表/`_msc.ifs`/曲绘/audio 全部就位 |
| T3.2 | 启动游戏，在**目标版本档**里找到它，能选、能玩、能出分 | 实机截图 + 得分 |
| T3.3 | 一键卸载 | 曲表回到安装前（用 `baseline_manifest.py check` 验证零残留） |
| T3.4 | 重新打包 `dist/ub_store.exe` | 新 exe 自检通过 |
| T3.5 | 顺手把「PNG 打包式 mod」跑通（§1.1 第 10 条） | 以后改贴图不用手搓 TDXT，大幅降本 |

---

## 五、🔴 安全约定（本项目红线）

1. **不写游戏目录**：必须写时**先列清单 → 建备份 → 用户点头 → 才动手**；写完立刻报 md5/mtime。
2. **不批量污染 `data_mods\omnimix\`**：那是手感敏感区（掉帧会丢短按）。探针/覆盖**一次会话内用完立刻撤**，基线 **3491** 个文件。
3. **不跑后台周期性全屏截图**（2560×1440 抓屏会掉帧）。取证必须先征得同意并降频。
4. **`dev/nvram/network.xml` 的 `ipaddr`/`gateway` 绝不动**（改它会从可点掉的 `5-1506-0000` 升级成致命 `5-1505-0022`）。
5. **还原 DLL 前必须逐槽 dump 校验**，不能只信文件名（实测两个备份只差 6 字节：`Ave.` vs `CUSTOM`）。
6. **不做任何破坏性操作**（删除 / 覆盖）前，先跑 `baseline_manifest.py check` 留底。
