# jubeat 被删曲恢复报告（第一批：16 首）

> 机台：`<TARGET_HOST>` `<ARCADE_DIR>`  jubeat beyond the Ave. (L44) + omnimix
> 执行时间：2026-10-05 02:11
> 结果：**16 首被删曲已恢复并部署**（含 14 首版权曲），日志零报错

---

## 0. 一句话结论

用新补的 **`L44-011-2022052400`（2022-05-24 完整包）** 里的音频+谱面+贴图，配合 **festo omnimix 里现成且字段完全一致的曲目表**，
把 **16 首被删曲**补回机台：`music_omni.xml` 从 1595 → **1611 首**，并新增 10 个独立贴图包。

---

## 1. 判定方法（这次的做法与上次不同，更准）

| 步骤 | 内容 |
|---|---|
| ① | 拉机台 `data/ifs_pack` 全部 `_msc.ifs` 尺寸表（`tests/ub116/ifs_sizes.txt`，1752 行） |
| ② | **144 字节 = 空壳**（官方"删曲"形式）→ 得到 **465 首被删曲** 的完整 id 集 |
| ③ | 机台 omnimix 覆盖层已补了其中 **379 首**（这是为什么大部分被删曲其实还能打） |
| ④ | **剩 86 首完全没有数据** ← 本次的目标 |
| ⑤ | 把 86 个 id 与各旧包的文件清单交叉比对 → **85 首在某包里能拿到 `_msc.ifs`** |

> ⚠️ **教训**：不要用「id 是否在曲目表里」当判据 —— 被删曲的 id 大多**仍在表里**（只是数据被换成 144B 空壳）。
> 正确判据是 **`_msc.ifs` 的字节数**。

## 2. 一个关键排除：`X00100xx` 是"幽灵 id"

86 首里有 69 个形如 `40010001`、`50010001`、`50020001`、`60010001`、`90010022` 的 id：

- 它们在**任何一个版本**（saucer 2014 / fulfill 2014 / prop 2016 / Qubell 2017 / clan 2018 / 2022）的 `music_info.xml` 里**都不存在**；
- 但机台 base 里有它们的 144B 空壳，旧包里也有它们的 `_msc.ifs` + `tex_l44_bnr_big_id*.bin`。

→ 判定为**先行/临时 id 的遗留资源**（正式 id 另有一套），**没有曲目记录可加**，本次**跳过**。

## 3. 本次恢复的 16 首

| music_id | 曲名（据读音/清单） | BSC/ADV/EXT | 默认可见 | 资源来源 |
|---|---|---|---|---|
| 20000022 | **夏祭り** | 2/6/8 | ✔ | 2022 包 |
| 50000209 | **回レ! 雪月花** | 4/7/9 | ✔ | 2022 包 |
| 60000086 | **シュガーソングとビターステップ** | 3/6/9 | ✔ | 2022 包 |
| 90000031 | **POP TEAM EPIC** | 2/6/10 | ✔ | 2022 包 |
| 90000032 | **ドラマツルギー** | 2/6/9 | ✔ | 2022 包 |
| 90000034 | **高嶺の花子さん** | 2/5/8 | ✔ | 2022 包 |
| 90000083 | **アンビバレント** | 2/6/8 | ✔ | 2022 包 |
| 90000139 | **Pretender** | 1/3/8 | ✔ | 2022 包 |
| 90000140 | **マリーゴールド** | 1/5/7 | ✔ | 2022 包 |
| 90000141 | **令和** | 3/7/9 | ✔ | 2022 包 |
| 90000170 | **Do Well** | 2/6/8 | ✔ | 2022 包 |
| 90000171 | **Alba** | 2/6/8 | ✔ | 2022 包 |
| 90000172 | **紅蓮華** | 2/5/8 | ✔ | 2022 包 |
| 90000176 | **思想犯** | 2/8/9 | ✔ | 2022 包 |
| 90000216 | フライアーハーフ（festo 期被删，额外收获） | 3/6/9 | — (解锁曲) | 2022 包 |
| 90001022 | デイリーワンダースペシャル（**HOLD 版**，额外收获） | 5/7/10 | — (HOLD) | 2022 包 |

**曲目记录来源**：`jubeat_festo_omnimix` 的 `music_omni.xml` —— 经字段比对，它与现役表的 **32 个字段完全一致**（2022 官方表反而是另一套 schema，不能直接拼接）。

---

## 4. 部署内容（55 个文件）

```
data_mods/omnimix/music_info/music_omni.xml          1595 → 1611 首（data_num 已同步）
data/d3/package/alldata.bin                          2298 → 2308 条（基础包表）
data_mods/omnimix/d3/package/allomni.bin              499 →  509 条（覆盖层包表）
data_mods/omnimix/ifs_pack/d<7位>/<id>_msc.ifs        16 个（音频+谱面，5~7MB/个）
data_mods/omnimix/d3/model/tex_l44_bnr_big_id<id>.bin 16 个（大曲绘）
data_mods/omnimix/d3/model/tex_rom_*.bin + mdl_rom_*  10 + 10 个（BNR/IDX/小曲绘图集）
```

### 4.1 为什么要改名成 `tex_rom_*` / `L44ROM_*`

所需贴图（`BNR_ID` / `IDX_ID` / `IDX_MINI_ID`）位于**共享图集**里，例如 `tex_l44fo_bnr_j_09_017.bin`。
该文件在机台 base 里**也存在**（2025 版，含更新的其它曲贴图）。若直接同名投放到覆盖层，
会**遮蔽**掉 base 里更新的版本 → 其它曲的横幅可能变旧或缺失。

因此：**图集改名投放**（`tex_rom_xxx.bin`），包名用 `L44ROM_*` 并**追加在包表末尾** ——
解析时先命中 base 的条目，只有被删曲这些"base 里根本没有"的名字才会落到新包上，**不产生任何遮蔽**.

`mdl_*` 是 60 字节固定空网格桩，缺失的按模板自行生成。

---

## 5. 验证结果

| 检查项 | 结果 |
|---|---|
| 55 个文件上传 | 全部完成 |
| 小文件（37 个 xml/bin）远端 MD5 | **37/37 一致** ✔ |
| 16 个 `_msc.ifs` 远端尺寸 | **16/16 一致** ✔ |
| `music_omni_new.xml` XML 合法性 | ElementTree 解析通过，data 数 1611 = data_num ✔ |
| 重启后 `no package` | **0** ✔ |
| 重启后 `texname error` | **0** ✔ |
| 重启后 `READ ERROR` / `could not find` | **0** ✔ |
| 日志确认新表被加载 | `M:layeredfs: Using ./data_mods/omnimix/music_info/music_omni.xml` ✔ |
| 新曲等级 | 16/16 与官方一致（见 §3 表） |

回滚点：`music_omni.xml.bak_20261005_*`、`allomni.bin.bak_20261005_*`、`alldata.bin.bak_20261005_*`

---

## 6. 顺带发现（对以后有用）

* **`L44-011-2022052400.zip` 的密码是 `bemani.cc`**（ZipCrypto；97% 的条目被加密，缺密码时 `7z` 会**静默等待密码输入**，
  表现为"卡住不动" —— 这次卡了 24 分钟就是这个原因）。
* 加密包用 **Python `zipfile` + pwd 随机访问解压**比 7z 快得多（213 个文件 / 536MB / 7 分钟）。

---

## 7. 仍未恢复的部分

| 项 | 情况 | 需要什么 |
|---|---|---|
| `11000113` やっぱり雨は降るんだね | 表里有、数据是空壳；**所有已有包都没有它的 `_msc.ifs`** | **L44（beyond）2024-02-14 ~ 2024-06-06 之间的完整包** |
| `60000063` / `60001023` | prop 期被删；2022 包有资源，但两处表都是老 schema（无 `name_string`/`detail_level_*`） | 需要做老→新 schema 归一化（kana 可用 pykakasi 生成） |
| 梅雪夜 / Ψです I LIKE YOU / HALLUCINATION / Good bye little girl / PLAY THE GAME / 魁！ミッドナイト | 这几首的 id 尚未定位（判据不足，读音反推失败）；其中 4 首「jubeat 1st/knit」期的数据在 `.pak` 时代包里（不是散装 `_msc.ifs`），需先解 pak | 先定位 id；pak 需另做解包 |
| 69 个 `X00100xx` 幽灵 id | 无曲目记录 | 无解（除非找到它们所属的 append 包） |

**剩余量统计**：机台 465 首被删曲 → omnimix 原有 379 → 本次 +16 → **还剩 70 首**（其中 69 是幽灵 id + 1 首 `11000113`）。

---

## 8. 本次新增工具

| 文件 | 用途 |
|---|---|
| `omnisrc/plan_restore.py` | 空壳×omnimix×各旧包 三方交叉，产出恢复计划 `out/restore_plan_stub.json` |
| `omnisrc/pk_scan.py` | 解析各版本 `music_info.xml`（兼容单/双引号、新旧 schema），与现役表求差集 |
| `omnisrc/mk_xlist.py` | 生成 2022 包定向提取清单 |
| `omnisrc/restore_build.py` | 记录合并（festo 表优先）+ 图集扫描 → `music_omni_new.xml` + manifest |
| `omnisrc/restore_stage.py` | 生成部署暂存目录 + 合并 `alldata.bin`/`allomni.bin`（`L44ROM_*` 命名） |
| `omnisrc/restore_deploy.py` | 备份 + SFTP 上传 + 远端 MD5/尺寸校验 |
| `omnisrc/ubq.py` | 机台远程跑 PowerShell（读被占用 log.txt 用 FileShare.ReadWrite + base64 回传） |
| `omnisrc/tlkp.py` | TLKP 包表解析/序列化（`alldata.bin`/`allomni.bin`/`omnilist.bin`） |

---

## 9. 请机台侧确认

1. 选曲界面看 **夏祭り / Pretender / マリーゴールド / 令和 / 紅蓮華 / 思想犯 / POP TEAM EPIC / 高嶺の花子さん /
   ドラマツルギー / アンビバレント / Alba / Do Well / 回レ!雪月花 / シュガーソング** 这 14 首是否出现且能开打；
2. 顺便确认原有曲目无变化（横幅/小曲绘未变旧）；
3. 若出现异常，把新 `log.txt` 给我，按 `texname error` / `no package` 继续追。

---

## 10. 补记（2026-10-05 12:12）：`11000113` やっぱり雨は降るんだね —— 已恢复 ✔

**资源来源**：`<GAME_DIR>/xiyangyang.7z`（**0214 完整包**，9871 条目、完整 `contents/` 树）
—— 里面 `11000113_msc.ifs` 是**真文件 6,038,144 B**（不是 144B 空壳），另有真的大曲绘。

**本次部署 8 个文件**：

| 远端路径 | 内容 |
|---|---|
| `data_mods/omnimix/ifs_pack/d1100011/11000113_msc.ifs` | 音频+谱面（6.04 MB，来自 0214 包） |
| `data_mods/omnimix/d3/model/tex_l44_bnr_big_id11000113.bin` (+`mdl_`) | 大曲绘（96620 B） |
| `data_mods/omnimix/d3/model/tex_rom_l44av_bnr_j_11_009.bin` (+`mdl_rom_`) | 横幅/缩略图（`BNR_ID/IDX_ID/IDX_MINI_ID11000113` 都在这个图集里）→ **改名投放，零遮蔽** |
| `data_mods/omnimix/music_info/music_omni.xml` | 1611 → **1612** 首（记录直接取机台 base 表里的官方条目：ツユ「やっぱり雨は降るんだね」2/5/9、`is_default=1`） |
| `data/d3/package/alldata.bin` | 2308 → 2309（新增 `L44ROM_L44AV_BNR_J_11_009`） |
| `data_mods/omnimix/d3/package/allomni.bin` | 509 → 510 |

**验证**：8/8 上传、尺寸 8/8 一致、7/7 MD5 一致；重启后 **`no package`=0 / `texname error`=0 / `READ ERROR`=0**，
日志确认 `layeredfs: Using ./data_mods/omnimix/music_info/music_omni.xml`。
回滚点：`music_omni.xml.bak_20261005_*`、`allomni.bin.bak_20261005_*`、`alldata.bin.bak_20261005_*`。

**顺带核对（0214 完整包还能补什么）**：
* 0214 包曲目表 1083 首 **⊆** 现役 1612 首 → **不带来任何新曲目记录**（0 首新增）；
* 剩余 69 个 144B 空壳（全是 `X00100xx` 幽灵 id）在 0214 包里**也全是空壳** → **本包已榨干，边际价值 = 仅 11000113**。

> **结论**：被删曲恢复累计 **17 首**（16 首 festo 批 + 11000113）。剩余可补项 = 需**更新版官方完整包**才能拿到的 39 首新版追加曲 + 22 个 `110100xx` 记录。

---

## 11. 重要修正（2026-10-05 12:19）：包名与文件名必须严格对应，否则整包不加载

### 现象
`11000113` 补进去后**曲目出现，但曲名、曲绘都不显示**。日志给出确切证据：

```
W:aep: texname error [BNR_ID11000113]
W:aep: texname error [IDX_ID11000113]
W:aep: texname error [IDX_MINI_ID11000113]
W:aep: texname error [BNR_BIG_ID11000113]
W:d3: Warning!! no package (L44_BNR_BIG_ID11000113)
```

### 根因（两个）
1. **大曲绘包从未注册**：`tex_l44_bnr_big_id<id>.bin` 文件部署了，但没在包表里登记 `L44_BNR_BIG_ID<id>` 条目
   → 游戏查不到包 → `no package`。
2. **包名 ↔ 文件名是硬约定**：实测 base 表全部为

   ```
   包名 X      ->  文件 tex_<x>.bin  /  mdl_<x>.bin      （x = 包名小写）
   例: L44_BACKGROUND -> tex_l44_background.bin
       L44FO_BNR_J_OM_001 -> tex_l44fo_bnr_j_om_001.bin
   ```
   我之前为"避免遮蔽"把图集文件改名成 `tex_rom_*.bin`，**但包名写成了 `L44ROM_*`**
   → 游戏按包名去找 `tex_l44rom_*.bin`，**找不到文件 → 整个包不加载 → 里面的 BNR/IDX/IDX_MINI 全部解析失败**。

> ⚠️ 这个坑同时影响**上一批 16 首**（它们的横幅/小曲绘其实也没显示），本次一并修好。

### 修复
* 把 11 个图集的包名从 `L44ROM_<X>` 改成 **`ROM_<X>`**（与已部署的 `tex_rom_<x>.bin` 完全对应，**无需重传图集**）；
* 补注册 **17 个** `L44_BNR_BIG_ID<id>`（16 首 festo 批 + 11000113），并补齐 16 个 `mdl_l44_bnr_big_id<id>.bin` 桩；
* `alldata.bin` 2309 → **2326**；`allomni.bin` 510 → **527**；共部署 19 个文件（17 mdl 桩 + 2 包表）。

### 验证
* **新增静态校验**：对两张包表逐条检查 `tex_<小写包名>.bin` 是否真实存在 →
  **2852 / 2853 命中**，唯一 1 个不符的是 base 自带的 `L44AV_LIGHT_CHAT_ICON`（与本项目无关、日志从未报过它）。
  这条校验以后每次加包都要跑，能提前抓出此类命名错误。
* 重启后：`texname error`=0、`no package`=0、`READ ERROR`=0；日志确认加载新 `allomni.bin` 与 `music_omni.xml`。

---

## 12. 二次修正（2026-10-05 12:30）：贴图必须落在**规范组名**的图集里

### 现象
上一轮把包名改对后，`BNR_BIG_ID11000113`（大曲绘）**好了**，但
`BNR_ID` / `IDX_ID` / `IDX_MINI_ID11000113` **仍然失败**：

```
W:aep: texname error [BNR_ID11000113]
W:aep: texname error [IDX_MINI_ID11000113]
W:aep: texname error [IDX_ID11000113]
```

### 根因
游戏**只按"规范组名"去找图集文件**，不会去扫描我另起的 `tex_rom_*`：

| | 文件 | 说明 |
|---|---|---|
| 机台 base | `tex_l44av_bnr_j_11_009.bin` = 708652 B | 2025 版，**没有** 11000113 的贴图 |
| 我上一轮放的 | `tex_rom_l44av_bnr_j_11_009.bin` = 749964 B | 2022 版，有贴图，但**游戏根本不看** |
| 日志证据 | `layeredfs: Using ./data_mods/omnimix/d3/model/tex_l44_bnr_big_id11000113.bin` ✓ | overlay 的 `d3/model` **确实会被读取**，且**优先于 base** |

⇒ 结论：**要补贴图，必须覆盖"规范名图集"本身**，而不是新起名字。

### 修复
1. 用 SFTP 拉回机台这 11 个规范图集（base 版）；
2. 写了一个 **PXET 合并器**（`omnisrc/merge_pxet.py`），把 2022 版里 base 没有的贴图**并进去**：

   | 图集 | base → 合并后 | 新增 |
   |---|---|---|
   | `tex_l44av_bnr_j_11_009.bin` | 54 → **57** | **BNR_ID/IDX_ID/IDX_MINI_ID11000113** |
   | `tex_l44fo_bnr_j_02_001.bin` | 75 → 78 | 20000022（夏祭り） |
   | `tex_l44fo_bnr_j_05_002.bin` | 376 → 379 | 50000209（回レ!雪月花） |
   | `tex_l44fo_bnr_j_09_001.bin` | 57 → 66 | 90000031/32/34 |
   | `tex_l44fo_bnr_j_09_007.bin` | 27 → 30 | 90000083 |
   | `tex_l44fo_bnr_j_09_017.bin` | 33 → 45 | 90000139/140/141/170 |
   | `tex_l44fo_bnr_j_09_018.bin` | 18 → 24 | 90000171/172 |
   | `tex_l44fo_bnr_j_09_019.bin` | 15 → 18 | 90000176 |
   | `tex_l44fo_bnr_j_09_026.bin` | 66 → 69 | 90000216 |
   | `tex_l44fo_bnr_j_09_021.bin` / `tex_l44pr_bnr_j_06_009.bin` | 不变 | base 里本来就有 |

3. 把合并结果**以规范名放进 overlay**（`data_mods/omnimix/d3/model/tex_l44av_bnr_j_11_009.bin` 等 11 个），
   遮蔽 base 版。**关键点：合并时保留 base 的原有顺序、只在末尾追加**，
   所以 base 里贴图的下标（texIndex）**完全不变**，不存在"下标错位"风险；
   同名贴图一律保留 base 版（优先更新的美术）。

### 为什么这样一定生效
* 合并后 **base 原有贴图一张不少** → 无回归；
* 新增的贴图落在**规范组名**的图集里 → 无论游戏是"按组名规则找"还是"扫描已注册包"，都能命中；
* layeredfs 对 overlay 的 `d3/model` **优先于 base**（已由 `tex_l44av_bnr_j_11_016.bin` 的日志证实）。

部署 11/11，尺寸+MD5 全部一致，重启完成。**待机台侧确认。**

---

## 13. 三次修正（2026-10-05 12:33）：PMAN 条目必须**按 hash 升序**，否则二分查找查不到

### 现象
第 12 节把贴图合并进"规范名图集"后，`11000113` 的曲名/小曲绘**仍然不显示**。

### 根因（决定性）
PXET 的 PMAN 条目是**按 hash 升序排列**的（实测机台 base 版与 2022 原版都是升序），
说明游戏对贴图名做 **hash 后二分查找**。我 12 节的做法是"**保留 base 顺序 + 把新条目追加在末尾**"，
结果是**破序**：

```
【机台 base】tex_l44av_bnr_j_11_009  n= 54  hash 升序 = True
【2022 原版】                        n= 57  hash 升序 = True
【我 12 节合并的】                    n= 57  hash 升序 = False   ← 破序处 index 53
```

⇒ 新增的 3 张贴图**在表里存在、但二分查找永远找不到** → `texname error`。

> 这也正好解释了为什么**大曲绘一直能成功**：`BNR_BIG_ID11000113` 所在的
> `tex_l44_bnr_big_id11000113.bin` **只有 1 张贴图**，天然有序，怎么合并都不会破序。

### 修复
合并器改为：
1. **条目按 hash 升序排序**（每个条目保留自己原来的 texIndex，指向纹理表槽位 → base 的下标不变）；
2. 名字表按"条目反序"存放（复刻原版的布局：第一个条目的名字落在名字区最高偏移处）；
3. 重新生成 11 个图集 → 复核 **11/11 全部 hash 升序 = True**、回读贴图数一致。

### 验证
重新部署 11/11（尺寸 + MD5 全一致），重启后日志：

```
texname error = 0    no package = 0    READ ERROR = 0
[12:32:57] M:layeredfs: Using ./data_mods/omnimix/d3/model/tex_l44av_bnr_j_11_009.bin   ← 含 11000113 贴图
[12:32:57] M:layeredfs: Using ./data_mods/omnimix/d3/model/tex_l44fo_bnr_j_09_017.bin
[12:32:57] M:layeredfs: Using ./data_mods/omnimix/d3/model/tex_l44fo_bnr_j_09_018.bin
... （11 个全部从 overlay 读取，遮蔽 base 成功）
```

⇒ 贴图所在的图集已由 overlay 提供、且 PMAN 有序，**查找链路完整**。待机台侧确认。

### 三条经验（贴图类改动的完整 checklist）
1. **包名必须等于文件名**：`包名 X` → `tex_<x>.bin` / `mdl_<x>.bin`；
2. **贴图必须落在规范组名的图集里**（不能另起文件名），要让 overlay 遮蔽 base；
3. **PXET 的 PMAN 必须按 hash 升序**——合并贴图时**务必排序**，只追加会破序导致查不到。
