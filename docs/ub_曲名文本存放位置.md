# jubeat 曲名文本到底存在哪？（深挖结论）

> **结论：曲名不以明文形式存在于磁盘上的任何资源里。**
> 唯一能在本机明文读到曲名的地方是 **`data/emblem-info/emblem-info.xml`（覆盖 509 个 id）**。
> 其余曲名是**运行时由 `jubeat.dll` 的「曲名画布」(`rpp_musictitle_cnv`) 绘制**，其文本随**每首歌自己的 `_msc.ifs`** 走（专有容器，压缩/混淆），所以磁盘上搜不到。

---

## 1. 直接答案：曲名跟「歌的包」走，不在曲目表里

三条互相印证的铁证：

1. **omnimix 的 `music_omni.xml` 没有标题字段，却工作正常** —— 它与官方 `music_info.xml` 字段完全一致（只有 `name_string` 假名检索串）。如果标题来自曲目表，omnimix 就必须也提供标题 → 说明**标题不来自曲目表**。
2. **日志显示游戏按需打开每首歌的包**：
   `M:layeredfs: Using ./data_mods/omnimix/ifs_pack/d1000004/10000047_msc.ifs`
   紧接着是 d3 的包查找。
3. **官方删曲会把包写成 144 B 空壳**（`90000048`=39、`11000113`、`60000100`、`90000049~51`）—— 一旦成空壳，该曲的**音频、曲绘、标题一起消失**（这也正是 39 白曲绘的同一根源）。
4. `jubeat.dll` 里存在 **`rpp_musictitle_cnv`（= music title canvas，曲名画布）**、`rpp_board/rpp_musictitle_cnv/rpp_musictitle_cnv.png` —— 标题是**运行时绘制**的，画的内容由包提供。

---

## 2. 逐处排除（全部实测）

| 位置 | 结论 | 证据 |
|---|---|---|
| `data/music_info/music_info.xml` | ❌ **无标题字段** | 全表 36 个字段已枚举：`music_id/parent_id/version/bpm_max/bpm_min/color1/music_type/name_sort_id_j/name_string/level_*/detail_level_*/pos_index/is_default/is_card_default/is_offline_default/is_hold/index_start/step/genre{8 项}/grouping_category`。`name_string` 是 cp932 **假名检索串**，`name_sort_id_j` 是 hex 形式的**排序索引**（同读音两首歌相邻：`90000048`=0x?、`11001007`=次一号） |
| **`data/emblem-info/emblem-info.xml`** | ✅ **唯一明文曲名源** | 2,242 条 emblem，每条 `<music_id>` + `<name>`（形如 `【SP背景】 bass 2 bass`）+ `<name_read>` + `<texname>`，覆盖 **509 个 id**（去掉 `【…】`/`モノトーンの`/`オリジナルの` 前缀即正式曲名）。已导出 `out/jubeat_曲目名_权威表.csv` |
| `modules/jubeat.dll` (3.27 MB) | ❌ 无曲名 | 共 **20,662** 条字符串、其中 1,374 条含日文，全是**界面字符串池**：五十音行分类 `ア行/カ行/サ行/…`、`LEVEL 1~10`、全部版本名（`jubeat/ripples/knit/copious/saucer/saucer fulfill/prop/Qubell/clan/festo/Ave./beyond the Ave.`）、**`HOLD`**、genre 与作者名（`kors k`/`dj TAKA`/`TOMOSUKE`/`猫叉Master`/`ひなビタ♪`…）、报错文本（`△SSD READ ERROR` 等）、资源名、以及 PDB 路径 `d:\konami\l44\client\client_product_jb11\src\...` |
| `modules/music_db.dll` (143 KB) | ❌ 无曲名 | 831 条字符串，只有两个关键文件名：`data/music_info/music_info.xml`、`datarevision.xml`，其余是代码噪声 |
| `achievement_info.xml` (2.0 MB) | ❌ | 字段 `kind/id/index/top_category/sub_category_1~3/template/condition/parts_id` —— 指向**称号零件**，无曲名 |
| `title_parts_info.xml` | ❌ | 只有 `parts_id/data_index/parts`（称号零件文本） |
| `theme_info.xml` | ❌ | UI 主题名（`theme_id/name_j`） |
| `data/aep/*.bin`（322 个 UI 布局） | ❌ | 抽取文本：**0 段日文**（纯布局/坐标数据） |
| `prop/*`（含 1.4 MB `filepath.xml`） | ❌ | 压缩二进制/配置，无曲名 |
| **各曲 `data/ifs_pack/d*/<id>_msc.ifs`** | ❌ 无明文曲名 | 两轮对照测试：①**120** 个「纯 ASCII 曲名」的歌 → 自己的包里搜不到曲名；②**112** 个「ASCII 作者名」的歌 → 搜不到作者名；③把 `name_string` 原始字节拿去搜自己的包 → 也搜不到。容器为专有格式：头 `6c ad 8f 89 00 03 ff fc`，内部有 `BMP\0` 成员标记（每包都有，固定偏移 ~0xD3），其余区段为压缩/混淆数据（无 `OggS/RIFF/ADX` 等音频签名） |
| 大曲绘 `tex_l44_bnr_big_id*.bin` | ❌ | DXT 图像，无文本字节（`FLOWER` 之类的命中来自吸引画面贴图 `tex_l44fo_title.bin`/`background.bin`，是烧进图里的） |
| 服务器侧 `prop/as/**`（asphyxia） | ❌ | 仅插件源码（`index.ts`/`routes/*.ts`/`static/data.ts`/`raw_course.json`）+ `savedata/as.db`（其实是 **NDJSON**：`{"collection":"score","musicId":60000071,...}`，按 musicId 存分数）。**没有曲名表** |
| VFS 落盘区 `dev/raw/fscache/*` | ❌ | 空（服务器下发的 `get_jbox_list` 等未落盘缓存）；`dev/nvram/*` 只有机器设置 |

---

## 3. 为什么搜不到？容器结构

`<id>_msc.ifs` 不是标准 IFS，而是专有容器：

```
6c ad 8f 89 | 00 03 ff fc | <4B 变化> | 00 00 04 98 | 00 00 00 d0 | <4B> | …
  (魔数)        (固定)                     ↑ 完整包 0x498 / 空壳 0x398   ↑ 0xd0 / 0x80
… 0x28 起固定 12 字节 … 0x74 起若干长度字段/16 字节摘要(MD5?) …
0xD3: "BMP\0"   ← 每包都有的成员标记（曲绘缩略图）
之后为压缩/混淆区（占文件的绝大部分）
```

* 同曲不同 ID 的两个包（如 `70001002` vs `80001002`）**只有前 0xD0 内 40 字节不同**，其余逐字节相同 → 容器是确定性的、无加盐。
* 不同曲子的包块级重叠 ≈ 0% → 内容是按曲变换过的（音频在里头，但被压缩/混淆）。
* 结论：即便曲名在包内，也是**压缩态**，`grep` 永远搜不到 —— 要拿出来只能**破掉这层压缩/混淆**。

---

## 4. 实用结论（现在能做什么）

| 目标 | 可行路径 |
|---|---|
| 批量拿**真正的曲名** | ①本机可读：**509 条**（`out/jubeat_曲目名_权威表.csv`，已回填进 `out/jubeat_曲目等级总表.csv` 的 `曲名(emblem权威)` 列）；②wiki 归档：**1,624 行**（`out/wiki_jubeat_曲目表.csv`，含曲名/BPM/三档等级） |
| 想拿全 **1,595 条**的真名 | 只能 ①**进游戏逐条记录**（题名是运行时画的），或 ②**破解 `_msc.ifs` 容器**把成员解出来（唯一根治路径，需要攻破那层压缩/混淆） |
| 想给**新曲配标题** | 光改 `music_omni.xml` 不够，**必须带正确的 `_msc.ifs` 包**（omnimix 就是这么整包发的） |
| 删曲/空壳的后果 | 一旦包变成 144 B 空壳，**标题、曲绘、音频一起丢** —— 这与 39 白曲绘同源 |

---

## 5. 副产品

* `out/jubeat_曲目名_权威表.csv` —— **509 条 id → 正式曲名**（emblem-info 权威），含读音/BPM/等级/是否 HOLD
* `out/emblem_id2title.json` —— 同上 JSON
* `out/jubeat_曲目等级总表.csv` —— 1,595 条，新增列 `曲名(emblem权威)`（508 条有真名）
* 解析要点：`emblem-info.xml` 是 **Shift_JIS**、必须用 `xml.etree`（正则切 `<emblem>` 块会失败），取 `emblem_list/emblem` 下的 `music_id` + `name`
