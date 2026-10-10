# omnimix 移植到 beyond the Ave —— 结论：不用重编译，改 1 个字节

> 结论先行：**上游没有 beyond the Ave 版本的 omnimix**，但也不需要重新编译。
> v3.0 的全部 5 组补丁里有 **4 组在 beyond the Ave 上依然唯一命中**，
> 只有 `bnr_hook` 的 d3 锚点失效 —— 把它改成新签名即可，**1 个字节**。

---

## 1. mod 是谁

| 项 | 值 |
|---|---|
| 源码仓库 | `mbilker/jubeat_patcher`（C++ / meson / i686-w64-mingw32 交叉编译） |
| 分支 | 只有 `festo`、`master` —— **没有任何 beyond-the-Ave 分支** |
| tag / release | `v3.0.1`、`v3.1.0`、`festo_v1.0`；omnixmix.dll 最新只到 **v3.1.0（2022-08-25）** |
| 机器上这份 | **v3.0, Build Aug 23 2022 11:04:00**，571187 B，md5 `9817733b4d5e8b7e8f2aa78f555615c9` |
| 对应源码提交 | `2c206cbc2`（判据：该提交的 `festo.cc` = 389 B，不含 `mem_set`，与 DLL 完全一致） |

`<GAME_ROOT>\jubeat.dll` 的 md5 = `ca26c344e64485e140e853c4afbd912f`，**与街机真身一致**
⇒ 本地这份 `contents\` 就是 116 的同版本克隆，全部静态分析都可在本地完成。

---

## 2. v3.0 的补丁集（只有 5 组）

| 补丁 | 作用目标 | 在 beyond the Ave 上 |
|---|---|---|
| `packlist` → `omnilist` | jubeat.dll | ✅ 唯一命中 |
| `music_db limit patch 1~4` | music_db.dll | ✅ 4 个全唯一命中 |
| `music_omni`（`music_info.xml` → omni） | music_db.dll | ✅ 唯一命中 |
| `song unlock patch` | music_db.dll | ✅ 唯一命中 |
| **`bnr-hook` d3 锚点** | jubeat.dll | ❌ **0 命中** ← 唯一的问题 |

签名体检总计 32 条，命中 22 条。**ultimate 变体**（另一个 release）则大面积失配：
`mdb 1.0/1.1/1.2/2.0/2.1/2.2`、`mdb 4.0`、`ui_state_iter_cap_1/2` 共 **9 个 MISS**，
另需重推 7 处硬编码 imm32 站点 —— 那是真正需要逆向的工作量。

> ⚠️ **不要升级到 v3.1.0**。v3.1.0 新增了 `hook_iat_ordinal(avs2-core 序号 0xF4 = memset)`，
> 它是**按序号挂钩、不需要签名就能装上**，然后用 festo 的硬编码偏移 `s+0xAEE8` / `s-0x6F3598`
> 去写内存。在 beyond the Ave 上这些偏移指向完全不同的变量 ⇒ 会写坏 d3 的纹理状态。
> v3.0 没有这段代码，所以 v3.0 反而是更安全的基线。

---

## 3. 为什么 bnr_hook 会失效，新锚点是什么

festo 锚点（`Festo v1.0` / Ave. 时代）：

```
C7 45 F4 FF FF FF FF   E8 77 88 00 00   8B
mov [ebp-0xC], -1      call crc32       mov ...
```

`E8 77 88 00 00` 里的 rel32 是**版本相关的函数位移**。beyond the Ave 里同一条指令变成 `E8 A7 88 00 00`。

发现过程（可复现）：用通配 `C7 45 F4 FF FF FF FF E8 ?? ?? ?? ?? 8B` 扫 jubeat.dll，
要求 `+(-0x1D)` 正好落在合法函数序言上 —— **全文件恰好 1 处**：

| 项 | 值 |
|---|---|
| 锚点位置 | file `0x14C43D`（`.text`，RVA = file + 0xC00 ⇒ RVA `0x14D03D`） |
| rel32 | `0x88A7` |
| 函数入口 | RVA `0x14D020` = EA `0x1014D020` |
| 反汇编核对 | `8B F9` / `C7 45 F4 FF FF FF FF` / `E8 A7 88 00 00` —— 与源码注释 “call to crc32 just near fn start” 吻合 |

**新锚点 = `C7 45 F4 FF FF FF FF E8 A7 88 00 00 8B`**（jubeat.dll 中唯一；旧锚点 0 处）。

---

## 4. 要改的那 1 个字节

GCC `-O2` 把 13 字节模式**展开成逐字节 `cmp byte [reg+i], imm8`**，
所以 DLL 里既找不到连续数组、也找不到 4 字节立即数（这是本次定位的最大坑）。
`bnr_hook_init` 位于 file `0x35F0`：

```
0036CB  80 7B 08 77    cmp byte [ebx+8], 0x77   ← 改这里
0036D1  80 7B 09 88    cmp byte [ebx+9], 0x88
```

| 项 | 值 |
|---|---|
| 修改位置 | file offset **`0x36CE`** |
| 改动 | `0x77` → **`0xA7`** |
| 修改前 md5 | `9817733b4d5e8b7e8f2aa78f555615c9` |
| 修改后 md5 | `145b5a60db255eb2779edb339bb1dde1` |
| diff | **恰 1 字节** |

---

## 5. 产物

| 文件 | 说明 |
|---|---|
| `out/omnimix_bta.dll` | 修补后的 omnimix 模块（md5 `145b5a60db255eb2779edb339bb1dde1`） |
| `out/jubeat_bta.dll` | 游戏模块 shim：把 `jubeat.dll` → `jb_org.dll` 的 4 处字面量等长替换（md5 `65bc16d4d31339afc629c0549e327ea3`）。直接顶替 `contents\jubeat.dll` 即生效 |

生成脚本（都在 `omnisrc/`）：
`scan_patterns.py`（32 条签名体检）· `scan_d3.py`（锚点发现）·
`coff_syms2.py`（COFF 符号 → 地址）· `patch_omnimix.py`（打补丁）· `make_shim_bta.py`（做 shim）

---

## 6. 部署 / 验证 / 回退

**部署**（需先在街上停游戏）
1. 备份现场：`contents\jubeat.dll`（真身 md5 `ca26c344…`）
2. `out/jubeat_bta.dll` → `contents\jubeat.dll`
3. 保持 `contents\data_mods\omnimix\` 启用（2.8 GB，1459 文件）
4. 启动游戏，**看日志**应出现 `... L44FO_BNR_J_OM_001` / `... L44FO_BNR_J_OM_002`
   （bnr_hook 每次成功强制加载都会打这行）

**验收标准（必须两样都对）**
- 日志：出现上面两行、且没有 `could not find ... base address` 之类的补丁失败告警
- 画面：**远程截待机（attract）画面**，那一排 5 个曲绘格子应当都有图

**回退**
- `contents\jb_org.dll` → `jubeat.dll`（或者用 md5 `ca26c344…` 那份）
- 原始 mod 备份在 `tests/ub116/omnimix.dll.bak`

---

## 7. 还没被证实的一环（别再只看日志就下结论）

`bnr_hook` 失效 ⇒ `L44FO_BNR_J_OM_001/002` 两个曲绘图集永不加载 ⇒ 靠它们的曲子曲绘变白。
`L44_BNR_BIG_ID99999999` 这个哨兵名字**本来就在原版 `alldata.bin` 里**
（实测 `alldata_live.bin` 首处 @0x24717），mod 借它当触发器 —— 机制解释是自洽的。

但此前 A/B 观测到的是“待机画面 4/5 白”，是否与“仅图集缺失”完全等价，**必须靠
日志 + 截图双重确认**。上一轮就是只凭 `texname error` 计数就宣布修好，结果被用户否掉。

**教训：可见效果（截图）才是验收标准，日志计数只能当辅助。**

---

# 8. ✅ 实测结果（2026-10-04 19:24—19:35，已部署到 116）

**一次通过，不需要走"重编译"（方案 C）。**

## 8.1 部署

| 文件 | 变动 | 结果 md5 |
|---|---|---|
| `<ARCADE_ROOT>\jubeat.dll` | 换成修补版 shim | `65bc16d4d31339afc629c0549e327ea3` |
| `<ARCADE_ROOT>\modules\omnimix.dll` | 换成 1 字节补丁版 | `145b5a60db255eb2779edb339bb1dde1` |
| `<ARCADE_ROOT>\jb_org.dll` | 不动（真游戏本尊） | `ca26c344e64485e140e853c4afbd912f` |
| `_probe\backup\jubeat.dll.vanilla20261004` | 备份 | 3276440 B |
| `_probe\backup\omnimix.dll.orig` | 备份 | 571187 B |

启动：`spice.exe -exec jubeat.dll`（与之前跑通的同款；**SpiceTools 自带 layeredfs，不需要 `-k ifs_hook.dll`**）

## 8.2 决定性日志证据（`tests/ub116/bta/log_bta2.txt`，1711 行）

```
 177: I:omnimix: jubeat omnimix hook by Felix, Cannu & mon v3.0 (Build Aug 23 2022 11:04:00)
 189: M:ea3-config: soft/rev : "X"                      ← omnimix 生效（原版是 "A"）
 171: M:layeredfs: Detected mod folders: ./data_mods/omnimix
 888: M:layeredfs: Using .../d3/package/omnilist.bin
 889: M:layeredfs: Using .../d3/package/allomni.bin
1022: M:layeredfs: Using .../music_info/music_omni.xml
1528: I:bnr-hook: ... L44FO_BNR_J_OM_001                 ← ★ 钩子装上了并触发（以前从未出现）
1529: I:bnr-hook: ... L44FO_BNR_J_OM_002
1530: M:layeredfs: Using .../d3/model/tex_l44fo_bnr_j_om_001.bin   ← ★ 曲绘图集真的加载了
1531: M:layeredfs: Using .../d3/model/tex_l44fo_bnr_j_om_002.bin
```

错误计数：`could not find` = **0**、`texname error` = **0**、`READ ERROR` = **0**、
`VirtualProtect` 失败 = **0**；`Warning!! no package` = 2（都是**旧有的** `L44FO_LCS_J_09_027`，
老日志里也各有 6 次，与本改动无关）。

attract 轮播确在读 mod 覆盖层的独占曲：`d7000013/70000137_msc.ifs`、`d6000002/60000029_msc.ifs`、
`d5000032/50000326_msc.ifs`、`d5000016/50000169_msc.ifs` … ⇒ omni 曲目表确实在工作。

## 8.3 画面验收（客观度量）

曲绘行区域 `crop(0,220,540,350)` 的**近白像素比例**：

| 状态 | 截图 | 白像素 |
|---|---|---|
| **坏掉**（旧 DLL，omnimix 全开） | `shots/ub116/idle1.png` | **24.8%**（4 张纯白 + 1 有图） |
| **修补后**（12 张连拍） | `shots/ub116/burst_00..11.png` | 最小 1.7% / 最大 8.2% / **平均 3.9%** |

对比拼图：`shots/ub116/bta_compare.png` —— 上 5 行是修补后（**5/5 全部有图**），
末行是坏掉状态（**4 张纯白**）。

## 8.4 结论

「bnr_hook 的 d3 锚点失效」就是**白曲绘的完整根因**。之前 A/B 只能说"内存补丁有问题"，
现在被精确到「唯一失效的那一条 MinHook 锚点」，且 1 字节即可修复。

**现场保持"已修补 + omnimix 生效"状态**，可直接刷卡游玩。

**回退**
```
copy /y <ARCADE_DIR>\_probe\backup\jubeat.dll.vanilla20261004 <ARCADE_ROOT>\jubeat.dll
copy /y <ARCADE_DIR>\_probe\backup\omnimix.dll.orig           <ARCADE_ROOT>\modules\omnimix.dll
```

**仍未验证**：omni 曲**实际刷卡选曲游玩**（无法远程刷卡/投币），需你亲手测一次。
