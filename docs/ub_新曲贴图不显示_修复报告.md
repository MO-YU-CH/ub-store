# jubeat 新曲「曲名横幅 / 小曲绘 / 缩略图不显示」修复报告

> 机台：`<TARGET_HOST>`  `<ARCADE_DIR>`  jubeat beyond the Ave. (L44) + omnimix
> 修复时间：2026-10-05 01:11
> 状态：**已修复并验证**（文件级 100%、日志级 0 报错）

---

## 1. 现象

截图两台：
| 截图 | EXTREME LEVEL | 曲目 | music_id |
|---|---|---|---|
| 上 | 9.4 | （彩色大字 LOGO 那首） | **11000166** |
| 下 | 9.6 | **CRAZY ARTS** | **11000168** |

（`detail_level_ext` 对得上：11000166 = 9.4、11000168 = 9.6、11000167 = 9.2）

缺失内容：
* 曲名横幅（`BNR_ID*`）
* 选曲界面小曲绘（`IDX_MINI_ID*`）
* 选曲界面缩略图（`IDX_ID*`）

**不是只有这两首** —— 受影响的是一整批曲：`11000166 / 11000167 / 11000168 / 11000170 / 11000171 / 11000172 / 11000173 / 11000174`。

---

## 2. 根因

上一次做 0205（L44-013-2025020500）更新时，只把新曲用到的**贴图包注册**写进了 omnimix 覆盖层的
`data_mods\omnimix\d3\package\allomni.bin`，**没有写进基础包表**：

```
<ARCADE_ROOT>\data\d3\package\alldata.bin      ← 游戏用它把「贴图名 → 包」解析出来
```

结果：游戏找不到 `BNR_ID11000167` / `IDX_MINI_ID11000167` / `IDX_ID11000167`，
在 `log.txt` 里刷：

```
W:aep: texname error [BNR_ID11000167]
W:aep:     layer: bnr_id11000167.png
W:aep: texname error [IDX_MINI_ID11000167]
W:aep:     layer: idx_mini_id11000167.png
W:aep: texname error [IDX_ID11000167]
```

另一侧的数据差集把问题钉死了：

| | 条目数 | 独有条目 |
|---|---|---|
| 机台 `alldata.bin` | 2287 | `L44FO_LCS_J_09_002/008/017/018`、`L44_BNR_BIG_ID60000100/90000049/50/51`（8 个） |
| 更新包 `alldata.bin` | 2290 | `L44AV_BNR_J_11_019`、`L44AV_BNR_J_11_020`、`L44AV_LCS_J_11_019`、`L44_BNR_BIG_ID11000166/167/168/170/171/172/173/174`（11 个） |

* 双方**共同条目 2279 条，内容差异 0 条** → 两份表只差条目，不差内容。
* 更新包多出来的 11 个包，正好就是新曲要用的贴图包。
* 而这 11 个包对应的**图集文件早就在机台上了**（上次更新的 38 个新文件里），只是没被注册。

---

## 3. 修复动作

### 3.1 合并包表（并集，而不是直接覆盖）

```
合并 = 更新包 2290 条 ∪ 机台独有 8 条 = 2298 条
```

* 保留机台独有的 8 条，避免覆盖后反而出现新的 "no package"。
* 按游戏自身的排序规则重排：**`_` 被当作低于字母的字符**（实测 `name.replace('_',' ')` 升序，两份原表都满足）。
* 输出：`out/alldata_merged_0205.bin`，236192 字节，md5 `e4a8fc8135728d4e7767127a683c9258`。

### 3.2 部署

```
备份：alldata.bin  →  alldata.bin.bak_20261005_001111   (235032 B)
上传：out/alldata_merged_0205.bin → contents\data\d3\package\alldata.bin
校验：远端 md5 = e4a8fc8135728d4e7767127a683c9258  ✔ 一致
重启：kill spice.exe → schtasks 投递到 Session 1 重新启动（as.exe/asphyxia 不动）
```

### 3.3 回滚（如需）

```powershell
Copy-Item '<ARCADE_ROOT>\data\d3\package\alldata.bin.bak_20261005_001111' `
          '<ARCADE_ROOT>\data\d3\package\alldata.bin' -Force
```
（另有一份更早的 `alldata.bin.bak_20261004_135252`）

---

## 4. 验证结果

| 检查项 | 结果 |
|---|---|
| 重启后 `texname error` | **0** ✔ |
| 重启后 `no package` | 1（仅 `L44FO_LCS_J_09_027`，见 §5） |
| 13 个关键图集 MD5（机台 vs 更新包） | **13/13 完全一致** ✔ |
| 6 首新曲 × 4 类贴图（BNR / BNR_BIG / IDX / IDX_MINI）共 24 项 | **24/24 命中已注册包** ✔ |
| `layeredfs` 覆盖层加载 | `omnilist.bin` + `allomni.bin` 均正常加载 ✔ |

关键图集（已确认机台上字节一致）：`tex_l44av_bnr_j_11_019.bin`、`tex_l44av_bnr_j_11_020.bin`、
`tex_l44av_lcs_j_11_019.bin`、`tex_l44_bnr_big_id11000152/166/167/168/169/170/171/172/173/174.bin`

---

## 5. 残留问题（缺内容，无法用现有数据修）

以下包名**在任何包表里都不存在**，是游戏按规则算出来的名字，对应 0205 更新包里根本没有的文件：

| 包名 | 日志出现 | 影响 |
|---|---|---|
| `L44FO_LCS_J_09_027` | 37 次（浏览 festo/東方 曲时） | 部分 festo 曲的 **GAME OVER 画面 "lcs" 文本条** 不显示 |
| `L44AV_LCS_J_11_018` | 1 次 | 同上（ave 曲） |
| `L44_BNR_BIG_ID00000100` / `IDX_ID00000100` | 8+3 次 | 模板曲 id=00000100（系统占位），基本无害 |

`tex_l44fo_lcs_j_09_026.bin` 已是更新包最新版（154132 B / md5 `44db1263…`），**027 组在 L44-013-2025020500 里没有**。
要修需要更高版本的 L44 数据包（例如 L44-014 及以上）。

---

## 6. 与「曲名文本在哪」的关系（附带结论）

* 本次确认 `BNR_ID<music_id>` 就是**带曲名的横幅贴图**（jubeat 的曲名是画在横幅/封面图里的，不是运行时文字）。
* 因此"曲名不显示"= 横幅贴图加载失败，与"曲名文本存放位置"是同一个问题的两面。
* `music_db.dll` 是原生 PE 代码库（143000 B，内部无任何曲名/日语文本），**不是**曲名表；asphyxia 的 `as.db`
  也只存 `score/course/profile`，**不提供曲名** → 客户端曲名确实只存在于图片资源里。

---

## 7. 请机台侧确认

游戏已重启到标题画面。请到机台：
1. 选曲界面看 11000166 / 11000167 / 11000168（以及 170~174）的**曲名横幅**与**小曲绘**是否恢复；
2. 顺便看其它曲是否有异常（正常曲不应该受影响）。
3. 若仍有异常，把新的 `log.txt` 给我，我按 `texname error` / `no package` 继续追。

---

## 8. 本次新增工具

| 文件 | 用途 |
|---|---|
| `omnisrc/ubq.py` | 机台远程执行 PowerShell（读被占用的 log.txt 用 FileShare.ReadWrite + base64 回传） |
| `omnisrc/ub_put_alldata.py` | 备份 + SFTP 上传 alldata.bin + 远端 MD5 校验 |
| `out/alldata_merged_0205.bin` | 修复合集包表（2298 条） |
| `tests/ub116/alldata_now.bin` | 修复前的机台包表存档（235032 B） |
| `tests/ub116/as_now.db` | asphyxia 数据库存档 |
