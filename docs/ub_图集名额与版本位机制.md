# 分组图集「新增名额」与「版本位」机制 — 实测报告

> 对象：jubeat beyond the Ave. (L44) 本地机台
> 结论日期：2026-10-08　游戏根：`<GAME_ROOT>`

---

## 一、分组图集：加载名单的真源找到了

### 1.1 此前假设被推翻

先前以为「开机加载的图集名单由包表决定」。实测推翻：

| 检查 | 结果 |
|---|---|
| `data/d3/package/alldata.bin` 是否有 `L44AV_BNR_J_11_019` | **有** |
| `data_mods/omnimix/d3/package/allomni.bin` 是否有 | **有** |
| 日志里是否加载过 `tex_l44av_bnr_j_11_019.bin` | **从未** |

⇒ 包表不是加载名单的来源。

### 1.2 真源 = `jb_org.dll` 里的**硬编码定长字符串数组**

- 文件：`<游戏根>/jb_org.dll`（真游戏本体，3,276,440 B，PE32，ImageBase=0x242000）
- 位置：**文件偏移 0x250234 起，20 字节定长 × 54 个槽**（VA 0x493634）

```
[ 0] L44FO_BNR_J_01_001   [ 1] L44FO_BNR_J_02_001   [ 2] L44FO_BNR_J_03_001
[ 3] L44FO_BNR_J_04_001   [ 4] L44FO_BNR_J_05_001   [ 5] L44FO_BNR_J_05_002
[ 6] L44FO_BNR_J_06_001   [ 7] L44FO_BNR_J_07_001   [ 8] L44FO_BNR_J_08_001
[ 9] L44FO_BNR_J_09_001   …（09_001..09_012、09_014..09_027，共 26 个；**无 09_013**）
[35] L44AV_BNR_J_11_001   …（11_001..11_018，共 18 个）
[53] L44FO_BNR_J_99_999   ← **占位 / 无效名**
```

- 53 个真名 + omnimix 钩子 `jubeat.dll` 自己补的 `L44FO_BNR_J_OM_001` / `OM_002`
  → **正好 55 个**，与日志 `layeredfs: Using` 完全吻合（已逐条比对）
- 该数组**没有任何代码绝对引用**（全文件扫 VA / RVA 均 0 命中）→ 引擎按索引/哈希消费；
  同理，版本名字符串池也无绝对引用

### 1.3 已落机的改动（三处，均有备份，可完整回退）

| # | 文件 | 改动 | 备份 | 校验 |
|---|---|---|---|---|
| 1 | `jb_org.dll` | 槽 53：`L44FO_BNR_J_99_999` → **`L44AV_BNR_J_12_001`** | `jb_org.dll.bak_20261008_091518` | 大小不变 3,276,440 B；**仅 7 字节**不同且全在槽内 0x25065B..0x250669 |
| 2 | `data_mods/omnimix/d3/model/` | 新增 `tex_l44av_bnr_j_12_001.bin`（520,880 B，PXET n=27 f14 OK）<br>+ `mdl_l44av_bnr_j_12_001.bin`（60 B） | — | PXET 结构校验通过 |
| 3 | `data/d3/package/alldata.bin` | 插入 `('L44AV_BNR_J_12_001','tex_l44av_bnr_j_12_001.bin','mdl_l44av_bnr_j_12_001.bin')` | `alldata.bin.bak_atlas12` | 2327→2328 行；升序正确；round-trip 一致 |

工具：`tools/ub_dll_atlas.py`（`list` / `add` / `remove`，只改占位槽，不碰代码）

> ⚠️ 尚未实机验证。**重启游戏后看日志**：
> 出现 `layeredfs: Using ./data_mods/omnimix/d3/model/tex_l44av_bnr_j_12_001.bin` = 生效。
> 若没有 → 说明引擎不是遍历到第 54 槽 → 需要改的是"名额计数"，届时再定位（下一步）。

---

## 二、版本位：完整映射已解出

### 2.1 曲子的"版本" = `version` 掩码里**最低的那个置位 bit**

反汇编 `music_db.dll` 的 12 个导出函数（该 DLL 未 strip），12 份代码逻辑完全一致：

```asm
mov  ecx, [ebp+8]        ; music_id
call <取曲目记录>
mov  edx, [eax+0x68]     ; version 掩码 (u32)
xor  ecx, ecx
loop: mov eax,1 ; shl eax,cl ; test edx,eax ; jne found
      inc ecx ; cmp ecx,0x10 ; jl loop     ; 循环上限 = 16 位（硬编码）
      xor al,al ; ret                       ; 无任何位置位 → 0
found: <比较> ; sete al ; ret
```

### 2.2 完整映射表

| 版本函数 | "找到"分支的判定 | 覆盖的 bit |
|---|---|---|
| `ver1` | `bit==0` 或 `bit==1` | 0, 1 |
| `ver2` | `bit==2` 或 `bit==3` | 2, 3 |
| `ver3` | `bit==4` 或 `bit==5` | 4, 5 |
| `ver4` | `bit==6` 或 `bit==7` | 6, 7 |
| `ver5` | `bit==8` 或 `bit==9` | 8, 9 |
| `ver5_5` | `bit==9` | 9 |
| `ver6` | `bit==10` | 10 |
| `ver7` | `bit==11` | 11 |
| `ver8` | `bit==12` | 12 |
| `ver9` | `bit==13` | 13 |
| `ver11` | `bit==14` | 14 |
| `ver11_5` | `bit==15` | 15 |

**⇒ 16 个位全部有归属，没有任何空位。**

这完美解释了官方 base 曲表的全部 16 种取值：

| 取值 | 最低 bit | 版本 | 官方首数 |
|---|---|---|---|
| `8000` | 15 | ver11_5 | 96 |
| `c000` | 14 | ver11 | 79 |
| `e000` | 13 | ver9 | 228 |
| `f000` | 12 | ver8 | 113 |
| `f800` | 11 | ver7 | 121 |
| `fc00` | 10 | ver6 | 91 |
| `fe00` | 9 | ver5_5 / ver5 | 66 |
| `ff00` | 8 | ver5 | 201 |
| `ff80` | 7 | ver4 | 19 |
| … | … | … | … |
| `ffff` | 0 | ver1 | 12 |

早期版本占 **2 个 bit**（本体 + APPEND 修订），后期各占 1 个 —— 与 jubeat 真实沿革一致。

### 2.3 版本名与 theme_info 的关系

- `jb_org.dll` 0x298170 起的字符串池里有 **12 个版本名**：
  `jubeat / ripples / knit / copious / saucer / saucer fulfill / prop / Qubell / clan / festo / Ave. / beyond the Ave.`
  —— 与 12 个版本判定函数一一对应（`ver11` = `Ave.`、`ver11_5` = `beyond the Ave.`）
- `theme_info.xml` 的 `theme_version` **只有 11 个取值（0..10，跳过 9）**，数量与版本名不完全对齐
- 好消息：游戏**确实读取** `data/theme_info/theme_info.xml`
  （`jb_org.dll` 里有 `theme_db_xml_load`、`/theme_data/body`、`/theme_id`、`/theme_type`、`/theme_version`）

### 2.4 "新增一个版本位"的两条路（都需要动核心模块，未执行）

| 方案 | 做法 | 代价 / 风险 |
|---|---|---|
| **A. 版本档来自 theme_info** | 给自制曲挑一个 **2 bit 版本里空出来的那一位**（例如 `ver1` 覆盖 {0,1}，可把 bit1 划给新版本），把该曲 `version` 掩码的最低位置成这一位；同时在 theme_info 加对应 `theme_version` 条目 | 只需改数据；**但要先确认版本档由 theme_info 驱动** |
| **B. 版本档硬编码在 DLL** | 需同时：① 改 `music_db.dll` 里某个判定函数的常量（拆 2-bit 版本）；② 在 `jb_org.dll` 里补调用/表项 | 核心模块**代码级**补丁，风险高，必须先摸清 jb_org.dll 的调用点 |

**判定哪条路的唯一办法 = 重启游戏看一眼**：
选曲画面 → 长按切换分类 → 若多出「自作譜」一档 ⇒ 走 A（数据即可）；
若没有 ⇒ 走 B（要动代码），再动手。

---

## 三、回退方法

```powershell
# 1) 图集名额退回占位
<PY_HOME>\python.exe tools\ub_dll_atlas.py remove --game "<GAME_ROOT>" --name L44AV_BNR_J_12_001 --apply
# 2) 包表还原
copy "<GAME_ROOT>\data\d3\package\alldata.bin.bak_atlas12" "<GAME_ROOT>\data\d3\package\alldata.bin"
# 3) DLL 整体还原（最保险）
copy "<GAME_ROOT>\jb_org.dll.bak_20261008_091518" "<GAME_ROOT>\jb_org.dll"
# 4) 删掉新建的图集文件
del "<GAME_ROOT>\data_mods\omnimix\d3\model\tex_l44av_bnr_j_12_001.bin"
del "<GAME_ROOT>\data_mods\omnimix\d3\model\mdl_l44av_bnr_j_12_001.bin"
```

（`tools/ub_version.py remove --game <根> --name 自作譜 --apply` 可回退上一轮的 theme_info 改动）

---

## 四、附录：启动报错「IPアドレスが変更されました 5-1506-0000」的排查（2026-10-08 12:23）

### 4.1 好消息：图集名额**已生效**

```
[2026/10/08 12:22:49] M:layeredfs: Using ./data_mods/omnimix/d3/model/tex_l44av_bnr_j_12_001.bin
本次启动加载图集数 = 56（原 55 + 新的 1 个）
```
→ `jb_org.dll` 占位槽 → `L44AV_BNR_J_12_001` 的做法**被游戏接受**，确实多加载了一个。

### 4.2 这个报错与本次改动无关（硬证据）

把「上次正常运行」(10/07 23:59 起) 与「本次」(10/08 12:22 起) 的日志逐行 diff：
**前 300 行只有 ASLR 地址和一个随机 xrpc tag 不同**，其余完全一致；告警集合也完全一致
（两边都有 `W:network: ADDR-OLD:I` / `ADDR-NOW:I`、各 4 次 `receiver returns failure`、
同样的 `Network preferences: 10.9.0.0` + `Using fallback adapter: …192.168.0.102`）。

→ 属于**环境 / 机台设定**类问题，不是贴图 / 图集 / 曲表改动引起的。

### 4.3 关键环境事实

| 项 | 值 |
|---|---|
| `spice.exe` | = **SpiceTools**（`layeredfs:` / `Network preferences` / `find-circle` 都是它打的） |
| `prop/as/as.exe` | = **Asphyxia CORE v1.40d** 本地 e-amusement 服务，监听 127.0.0.1:8083（实测健康） |
| 本机网卡 | WLAN = **192.168.0.102 / 255.255.255.0 / gw 192.168.0.1**（DHCP）；另有 Hyper-V、VirtualBox ×2 |
| `dev/nvram/network.xml` | `ipaddr 192.168.44.1`，**与 `prop/defaults/network.xml` 完全相同** → 从未写入本机 IP |
| `prop/as/config.ini` | ⚠️ 开头 **316 字节 0x00**，第一行键名被抹掉（只剩 `=true`）；Asphyxia 仍能跑 |

整合包作者自己的 `contents/使用说明.txt` 写明：
- `dev\nvram\` = 机台设定数据，**取自原机台**；「换机器部署时，联网相关项可在 `spicecfg.exe` 或游戏 TEST 模式里调整」
- 同类错误 `BACKUP DATA ERROR 5-2500-0000` 的解法：**在错误画面按 TEST 键（SpiceTools 默认 F1）让游戏自动修复**
- 键位：**TEST=F1 / SERVICE=F2 / COIN=F3**；`start-vanilla.bat` 是专用于排查的原版模式

游戏侧 5-1506-0000 的文案表在 `jb_org.dll` 0x29C3xx（Shift-JIS 字符串表）。

### 4.4 处置建议（按优先级）

1. **在错误画面按 F1（TEST）** 让游戏自动修复机台设定 —— 文档记载的解法
2. 根治：**给这台机器固定 IP**（静态 IP / 路由器 DHCP 保留），`dev\nvram\` 来自原机台，IP 一变就可能触发自检
3. 或在 `spicecfg.exe` / 游戏 **TEST 模式 → NETWORK SETTINGS** 重设联网项并保存
4. 想确定性归因：先跑 `game_patch\UNDO_game_changes.bat` 还原我改的 4 处 → 重启看报错是否还在
   （还在 = 环境问题；不在了 = 再逐步 `REDO` 定位）

### 4.5 一键还原 / 重打

```
<PROJECT_ROOT>/game_patch/
    UNDO_game_changes.bat   还原：DLL 占位槽 + theme_info + alldata + 删掉新图集文件
    REDO_game_changes.bat   重打：以上四步反向执行
```
