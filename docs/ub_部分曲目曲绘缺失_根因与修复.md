# jubeat「beyond the Ave.」部分曲目缺曲绘/缩略图 —— 根因与修复报告

机台：`<TARGET_HOST>`（Win7 WES7，`<ARCADE_DIR>`，model `L44:J:F:A:2024112700`）
游戏：`contents\spice.exe`（SpiceTools + `omnimix` layeredFS `IFS layeredFS v1.9-SPICE`）
日期：2026-10-05

---

## 1. 症状

- 游玩界面右上角的**曲绘缩略图空白**（纯白方块），例：`Dream drip macchiato`（id `90000014`）。
- 选曲界面**部分曲目的小曲绘 / 索引图缺失**，例：`11000166 / 11000167 / 11000168 / 11000174`。
- 游戏日志（`contents\log.txt`，cp932）出现：

```
W:aep: texname error [BNR_ID90000014]
W:aep:     layer: bnr_id10000000.png
W:aep: texname error [IDX_MINI_ID11000166]
W:aep:     layer: idx_mini_id10000000.png
```

---

## 2. 根因

**单曲大曲绘图集 `tex_l44_bnr_big_id<id>.bin` 必须存在于 mod 目录才会被加载；只放在 base 目录的同名文件无效。**

判定依据（16 个样本 16/16 吻合）：

| 曲 id | `tex_l44_bnr_big_id<id>.bin` 是否在 mod 目录 | 实际表现 |
|---|---|---|
| 90000012 / 90000014 / 90000016 / 90000174 / 90000178 | ❌ 无（只在 base） | 缩略图全白 + texname error |
| 11000166 / 11000167 / 11000168 / 11000174 | ❌ 无（只在 base） | 缩略图全白 + texname error |
| 11000113 / 60000086 / 90000176 / 10000005 / 50000299 / 50000317 / 70000024 | ✅ 有 | 正常 |

辅助证据：

- 游戏用 `M:layeredfs: Using ./data_mods/omnimix/…` 记录**仅命中 overlay 的文件**（base 文件不记录）——这是判断「游戏实际打开了哪些图集」的唯一可靠手段。
- 重启后游戏一次性加载的图集 = **overlay 里存在的那 13 个**（11 个规范名 + `om_001` + `om_002`）。
- `omnilist.bin`（80 B，`TLKPP`）声明只读取 3 张包表：`alldata.bin` / `allomni.bin` / `dplist.bin`。
- `allomni.bin` 中缺 `L44_BNR_BIG_ID<id>` 注册行的曲目集合与报错集合一致。

---

## 3. 已落地的修复（全部大小 + MD5 校验通过）

### ① 机台端复制单曲图集到 mod 目录
把 795 首曲的 `tex_l44_bnr_big_id<id>.bin` + `mdl_l44_bnr_big_id<id>.bin`（共 **1590 个文件**）
从 `contents\data\d3\model\` 复制到 `contents\data_mods\omnimix\d3\model\`。

→ mod 目录单曲大曲绘：**502 → 1297**

### ② 补注册 `allomni.bin`
补入 **789** 条 `L44_BNR_BIG_ID<id>` 注册行（`tex_…` / `mdl_…` 三元组）。

→ **527 → 1316 条**（5 7392 B → 143392 B）　备份：`allomni.bin.bak_big`

### ③ 单曲图集增强（最稳妥，不依赖任何「分组/全局查找」假设）
把每首曲缺失的 `IDX_ID<id>` / `IDX_MINI_ID<id>` 直接并入**该曲自己的** `tex_l44_bnr_big_id<id>.bin`。
游戏在显示该曲时必定打开此文件，因此贴图必然可见。

→ **795 个文件、并入 1590 张贴图、171.2 MB**，构建 0 失败、上传 0 失败、大小校验 OK

源素材：`<GAME_DIR>\L44-013-2025020500`（完整 2025-02-05 构建，2560 tex / 1330 单曲大曲绘）。

---

## 4. 踩坑记录（务必牢记）

- 🔴 **D3 文件加载器有单文件体积上限。**
  把 `om_001` 合并到 **46.9 MB** 后，游戏抛
  `W:d3-fileloader: failed to get file buffer. :D3FileLoader::Update,310`
  并**静默跳过整个图集**（其余图集照常加载，只有该图集全部贴图找不到）。
  已知可加载的最大值：`om_002` = **21.7 MB**。
  → **任何 overlay 图集请控制在 ≤ 21.7 MB。**（om_001 已回滚至 19,015,180 B）

- 曾误判为「PMAN 未按 hash 升序 → 二分查找失败」。实测 overlay 与暂存目录中的
  `tex_rom_*` / `merged_atlas/*` **全部升序 = True**，该假说不成立。

- 日志文件是**覆盖式写入**：新会话内容写在文件头、旧会话残留在后；且 sntp 会大幅校时
  （`diff=1271310318sec`）导致时间戳跳变。**必须按时间戳筛会话，不能按行序判断。**

- 游戏以**原生 IO 模式**启动 → 鼠标/键盘注入（会话 1 的 `clickat` / `key`）**无效**，
  需用户手动操作游戏；`shot` 截图仍可取证。

- 本机需使用 `/d/python/python.exe`（3.14.2，含 paramiko）；`<PY32_HOME>\python.exe` 无 paramiko。

---

## 5. 验证方法

重启后日志已确认：

```
=== texname error (0) ===
=== no package (0) ===
layeredfs 命中 bnr_j 图集 13 个（含 om_001 / om_002）
"failed to get file buffer" 计数 = 0
```

请用户实机确认以下曲目：

| id | 曲名 | 检查点 |
|---|---|---|
| 90000014 | Dream drip macchiato | 游玩界面右上缩略图 |
| 90000012 | 幸せが鳴る夜に | 缩略图 |
| 90000016 | ペガサス | 缩略图 |
| 90000174 | 世界の果てに約束の凱歌を -ユビイトエディション- | 缩略图 |
| 90000178 | ターンイン トゥ ホワイト | 缩略图 |
| 11000166 | アメージング… | 选曲小曲绘 + 缩略图 |
| 11000167 | ハラヘコヒーロー | 选曲小曲绘 + 缩略图 |
| 11000168 | クレイジー・アーツ | 选曲小曲绘 + 缩略图 |
| 11000174 | ロマンシング・エスケープ | 选曲小曲绘 + 缩略图 |

---

## 6. 工具与产物

新增脚本（`omnisrc/`）：
`ub_log_slice.py`（按时间戳切日志并统计错误）、`atlas_names_light.py`（轻量图集名称索引）、
`plan_bigbnr.py`、`build_bigbnr_fix.py`、`build_alldata_big.py`、`deploy_bigbnr.py`、
`build_per_song_idx.py`、`ub_get.py`、`final_audit.py`、`audit_ovl.py`、`diag_atlas.py`、`atlas_where.py`

产物：
- `out/fix_bigbnr/allomni_new.bin`（1316 条）
- `out/fix_bigbnr/l44_bnr/`（795 个增强单曲图集）
- `out/fix_bigbnr/plan.json` / `per_song_report.json` / `om_report.json`

机台备份：`allomni.bin.bak_big`、`tex_l44fo_bnr_j_om_001.bin.bak_big`

---

## 7. 第二轮 / 第三轮（彻底收敛）

### 7.1 命名规则澄清（重要）

实测单曲图集 `tex_l44_bnr_big_id<id>.bin` 内贴图名是 **`BNR_BIG_ID<id>`**（大曲绘），而不是 `BNR_ID<id>`。
完整命名分工：

| 贴图名 | 所在图集 | 用途 |
|---|---|---|
| `BNR_BIG_ID<id>` | 单曲图集 `tex_l44_bnr_big_id<id>.bin` | 大曲绘 / 游玩界面右上缩略图 |
| `BNR_ID<id>` | 分组图集 `tex_l44??_bnr_j_<grp>_<n>.bin` | 横幅层（layer `bnr_id10000000.png`） |
| `IDX_ID<id>` | 分组图集 | 中号曲绘 |
| `IDX_MINI_ID<id>` | 分组图集 | 选曲界面小曲绘（layer `idx_mini_id10000000.png`） |

### 7.2 第一轮遗漏（判据缺陷）

第一轮 `need_big` 的判据里多加了「`BNR_ID<id>` 已在 overlay 命中」这一条，导致
**317 首只缺 `BNR_BIG_ID`（单曲图集不在 mod 目录）的曲目被漏掉**。

### 7.3 余量审计（按 mod 目录实际清单）

| 项 | 修前 | 修后 |
|---|---|---|
| 缺单曲大曲绘 `BNR_BIG_ID` | 317 首 | **0** |
| 缺小曲绘 `IDX_MINI_ID` | 1 首（`90000048`） | **0** |
| 缺索引图 `IDX_ID` | 1 首（`90000048`） | **0** |
| mod 图集内缺 `BNR_ID`（横幅层） | 796 首 | 已并入单曲图集 |

### 7.4 本轮落地

1. **复制 317 首单曲图集**（+mdl，共 634 文件）从 base → mod 目录。
   → mod 目录单曲大曲绘 **1297 → 1614**（覆盖全部 1612 首）
2. **allomni.bin 全量注册**：补到 **1632 条**，覆盖全部 1612 首的 `L44_BNR_BIG_ID<id>`
   （57392 → 177836 B，md5 `da930cde36f0…`）；备份 `allomni.bin.bak_big2`
3. **第二轮增强**：317 首 + `90000048` 的单曲图集并入 `IDX_ID` / `IDX_MINI_ID`（318 文件 / 71.3 MB）
4. **第三轮增强（终极保险）**：为 796 首把 **`BNR_ID<id>` 也并入单曲图集**，
   使每个图集**自带全部 4 张贴图**（`BNR_BIG_ID` / `BNR_ID` / `IDX_ID` / `IDX_MINI_ID`）。
   796 文件 / 219.0 MB / 构建与上传 0 失败。

**设计意图**：无论是「全局按名字查找」还是「按该曲自己的图集查找」，两种模型下贴图都能命中，
不再依赖对引擎查找策略的假设。

### 7.5 体积安全

mod 目录 1643 个 tex 文件，最大为 `tex_l44fo_bnr_j_om_002.bin` = 21,747,664 B（已知可加载上限），
**无任何文件超过限制**；启动日志 `failed to get file buffer = 0`。

### 7.6 脚本与产物

- `omnisrc/audit_residual.py` —— 按 mod 目录实际清单做余量审计
- `omnisrc/build_per_song_idx2.py` / `build_per_song_idx3.py` —— 第二/三轮增强构建
- `out/fix_bigbnr/l44_bnr/` `l44_bnr2/` `l44_bnr3/` —— 各轮产物
- `out/fix_bigbnr/per_song_report{,2,3}.json`、`residual.json`、`residual_src.json`、`residual_bnrid.json`
