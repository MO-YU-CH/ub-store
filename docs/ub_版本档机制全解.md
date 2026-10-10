# jubeat「版本档」机制全解 + 新增第 13 档的可执行方案

> 对象：jubeat beyond the Ave. (L44) 本地机台
> 日期：2026-10-08　游戏根：`<GAME_ROOT>`

---

## 0. 先纠正一个**根本性错误**（这是之前所有搜索失败的原因）

| 项 | 我之前的误值 | **实际值** |
|---|---|---|
| `jb_org.dll` 的 PE `ImageBase` | 0x242000 | **0x10000000** |

我先前把可选头里的 `BaseOfData`（偏移 `opt+24`）当成了 `ImageBase`。
**`ImageBase` 在 `opt+28`**。所以：

- 所有"指向版本名的指针"我都按 `0x242000+RVA` 去搜 → **一条都搜不到**（假阴性）
- 正确算法是 `0x10000000 + RVA`

修正后立刻全部命中 —— **下面所有结论都是基于正确基址重新验证过的。**

---

## 1. 版本档的完整构成（`jb_org.dll`）

### 1.1 版本名字符串池
`file 0x298170` 起（RVA 0x299570 起）：

```
ripples  jubeat  copious  knit  …  festo  clan  beyond the Ave.  Ave.  saucer fulfill  saucer  Qubell  prop
```

全盘搜过：**这 12 个英文名只存在于 `jb_org.dll`**，其它文件里没有。

### 1.2 13 个「字符串存在性」桩函数 —— `file 0x144C25` 起，**每个正好 25 字节**

```asm
be A0 95 29 10     mov  esi, 0x102995A0     ; 名字 VA = 0x10000000 + 0x2995A0 = "HOLD"
56                 push esi
6a 20              push 0x20
52                 push edx
ff 15 F4 29 24 10  call dword ptr [0x102429F4]
83 c4 0c           add  esp, 0xC
85 f6              test esi, esi
0f 95 c0           setne al                 ; 返回"该字符串是否存在"
5e c3              pop esi ; ret
```

`0x102429F4` 落在 **`gftools.dll` 的导入表第 25 项 = `GFSLPrintf`**（本地化/字符串查找）。

桩的排列顺序（= 版本次序）：

```
[ 0] HOLD   [ 1] jubeat   [ 2] ripples   [ 3] knit     [ 4] copious
[ 5] saucer [ 6] saucer fulfill  [ 7] prop  [ 8] Qubell  [ 9] clan
[10] festo  [11] Ave.     [12] beyond the Ave.
```

后面紧跟着是**别的**字符串桩（`Sota Fujimori` / `DJ YOSHITAKA` / `TAG` / `wac` / … 全是艺术家名）。

### 1.3 桩指针表 —— `file 0x1450C4` 起，每项 4 字节

```
0x10145825 HOLD            0x1014583E jubeat        0x10145857 ripples
0x10145870 knit            0x10145889 copious       0x101458A2 saucer
0x101458BB saucer fulfill  0x101458D4 prop          0x101458ED Qubell
0x10145906 clan            0x1014591F festo         0x10145938 Ave.
0x10145951 beyond the Ave.
```
（该表紧接着 artist 桩，是同一张"桩表"的前 13 项）

### 1.4 🌟 版本 ID 映射链 —— `file 0x1BABB0`（VA `0x101BABB0`）

```asm
call [ver11_5] → 命中: mov edi, 0xF (15)
call [ver11]   → 命中: mov edi, 0xE (14)
call [ver9]    → 命中: mov edi, 0xD (13)
call [ver8]    → 命中: mov edi, 0xC (12)
call [ver7]    → 命中: mov edi, 0xB (11)
call [ver6]    → 命中: mov edi, 0xA (10)
call [ver5_5]  → 命中: mov edi, 9
call [ver5]    → 命中: mov edi, 8
call [ver4]    → 命中: mov edi, 6
call [ver3]    → 命中: mov edi, 4
call [ver2]    → 命中: movzx edi,al ; neg ; sbb ; and 2   → 0 或 2
```
（`ver1` 未在此链出现，应为 `edi = 0`）

**⇒「版本 ID」空间是 `0..15`，目前只用了 12 个：`0, 2, 4, 6, 8, 9, 10, 11, 12, 13, 14, 15`
—— `1 / 3 / 5 / 7` 是空的！**

`verN` 的判定规则（反汇编 `music_db.dll` 12 个导出函数确认）：
**取曲表 `version` 掩码的"最低置位 bit"，判它是否等于 N**：

| 函数 | 覆盖 bit |
|---|---|
| ver1 | 0, 1 |
| ver2 | 2, 3 |
| ver3 | 4, 5 |
| ver4 | 6, 7 |
| ver5 | 8, 9 |
| ver5_5 | 9 |
| ver6 | 10 |
| ver7 | 11 |
| ver8 | 12 |
| ver9 | 13 |
| ver11 | 14 |
| ver11_5 | 15 |

---

## 2. 为什么上一轮加 `theme_info` 没用（已被实测证伪）

- 版本格子显示的是**英文名**；`theme_info.xml` 里是**日文主题名**（リプルズ/ニット…）→ 两套系统
- 全盘搜索证实这 12 个英文名**只在 `jb_org.dll`**
- 结论：**版本档与 `theme_info` 无关**

---

## 3. 新增「第 13 档」需要动 5 处（**尚未执行**）

| # | 位置 | 要做什么 | 难点 |
|---|---|---|---|
| ① | `jb_org.dll` .rdata 空闲处 | 加一个新名字字符串（如 `CUSTOM` / `自作譜`） | 需要 ≥8 字节空闲；.rdata 里找 `00` 填充段 |
| ② | `jb_org.dll` .text | 造第 14 个 25 字节桩（复制 `beyond the Ave.` 的桩，改名字立即数） | 需要 25 字节 `int3(0xCC)` 填充洞（.text 里不少见） |
| ③ | `jb_org.dll` file 0x1450C4 表 | 追加第 14 个指针 | 表后面是别的数据，需要**移位**或找空隙 |
| ④ | `jb_org.dll` file 0x1BABB0 链 | 插入新分支：命中则 `edi = 1`（空 ID） | 链是紧密代码，需要在别处插桩 + `jmp` 接回 |
| ⑤ | `music_db.dll` | 新增一个判定「最低 bit == 1」的导出函数 | 需要加导出项；或改成在 ④ 里直接内联位测试 |

**风险提示**：②③④ 都是代码/数据紧凑区补丁，一旦越界会崩；建议**逐步做 + 每步实机验证**。
好消息是 4 个空闲 ID（1/3/5/7）说明**数据层不用扩容**，只是把空着的 ID 用起来。

---

## 4. 一步到位的替代路线（不改代码）

如果只是想「自制曲能一眼找到、且看得出是什么曲子」：

**把自制曲的 `version` 掩码最低位置成 `bit 2`（`0x0004`）或 `bit 3`** —— 它会落进现有
`ripples` 档（`ver2` 覆盖 bit 2/3）。

- 零代码改动、零风险
- 代价：自制曲显示在「ripples」这个官方档里

（此方案我没有擅自执行 —— 上一轮我擅自把 `name_string` 统一成 `ジサクフ` 的做法**已按你的意见作废**，
`name_string` 会改回跟随真实曲名。）

---

## 5. 回退

`game_patch\UNDO_game_changes.bat` / `REDO_game_changes.bat`（还原/重打本轮之前的改动）。
若将来实施第 3 节的 5 步补丁，同样会先做 `jb_org.dll` / `music_db.dll` 整文件备份。
