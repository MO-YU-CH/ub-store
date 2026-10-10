# 版本格（バージョンからお探しします）定位 — 终版报告

> 日期：2026-10-09　游戏根：`<GAME_ROOT>`　项目：`<PROJECT_ROOT>`
> 状态：**未完全攻破**，但范围已收窄、机制已摸清、并排除了大量错误方向

---

## 一、目标

在选曲界面「**バージョンからお探しします**」这一屏里，增加第 13 个版本档（「自作譜」），
让自制谱独立成一类。前置条件：**找到这一屏"11 个版本格"的瓦片美术 / 标签来源**。

---

## 二、已确认的事实（全部有硬证据）

| # | 结论 | 证据 |
|---|---|---|
| 1 | **版本格名单 = 14 个版本 − 3 = 11 格**<br>去掉 `plus`(04) / `app`(12) / 当前版本 `ave_byd`(14) | `data/aep/l44avby_result.bin` 里有完整 14 版本表：`res_exc_01_jubeat` … `res_exc_14_ave_byd`；与用户截图 11 格逐一对上 |
| 2 | **`tex_l44av_panel.bin` 被这一屏使用**<br>`PNL_CMN_BD_20~28`(124×124) = 底部 ◀ ▶ BACK / NEXT 圆按钮 | 探针：把它们涂品红 → **按钮真的变品红** |
| 3 | **`tex_l44avby_demo.bin` 被这一屏使用**<br>`DMO_SELECTION_BG`(180×180 纯深灰) = **CLOSE 格子的底板** | 探针：把该图集 13 个方形精灵涂黄 → **CLOSE 格子变橙** |
| 4 | **`tex_l44avby_background.bin`** = 这一屏的**砖墙背景**<br>`BGD_BASE2_01~04`(192×464)×4 + `BGD_SPOT` 光斑 + 全屏特效 | 渲染该图集全部 20 精灵确认 |
| 5 | `tex_l44av_smc_sm.bin` / `tex_l44av_select_genre.bin` / `tex_l44av_navi.bin` 等被这一屏**加载** | `log.txt` 的 `layeredfs: Using` 命中 |
| 6 | **`layeredfs: Using` = 判定"某图集是否被打开"的权威信号** | 探针①：overlay 里放了 `avby_result` 副本却零命中 ⇒ 该图集从未被打开 |
| 7 | **AEP 按精灵名绑定贴图**（`aep_set_texture` / `aep_player_create`） | 事故日志：贴图绑定失败 → `aep_player_create(...) failed` → SYSTEM ERROR |
| 8 | `ifs_pack/`(216) 全是单曲音频包；`datapackage/data` 与 `datapackage/revision` **为空**；`dev/raw/fscache` **为空** | 无隐藏 UI 包、无渲染缓存 |

---

## 三、已排除的候选（都实测过，附方式）

| 候选 | 排除方式与结果 |
|---|---|
| `tex_l44avby_result.bin` 的 `RES_EXC_01..14` | ❌ **该图集从未被打开**（log 零命中）。水印只是视觉相似 |
| `tex_l44av_select_genre.bin` 的 `SGR_PNL_ON/OFF` | ❌ 图集被打开，但画面没变（那是**流派格**） |
| `tex_l44av_panel.bin` 的 `PNL_CMN_BD_00/10/01/11/02/14/30` | ❌ 只影响按钮，不影响格子 |
| **全部约 230 个"单色板"精灵**（`hf3=0x81`，整图单一 RGBA） | ❌ 涂品红后格子仍不变（**这条曾被认为是最有希望的**） |
| `tex_l44avby_demo.bin` **全部 94 精灵**（含 `DMO_SELECTION_*`） | ❌ 只有 CLOSE 变 |
| `tex_l44av_smc_sm.bin` **全部 173 精灵**（含 `SSM_T8001..T8009`） | ❌ 全不变 |
| `navi` / `smc_mm` / `stg_bg_change` / `music_bar` / `music_information` / `light_chat` / `local_matching_select` / `password_matching` … | ❌ 不变 |
| **全量 1044 个非 bnr 图集**做"版本名字形"模板匹配 | ❌ 最像的 d=0.710（≈零相关）⇒ **版本名字不是贴图** |
| `jb_org.dll` 的 12 个版本名字符串（改成 `V01..V12`） | ❌ 实测格子不变；**代码级反证见 §四.4** |
| `theme_info.xml` 加 `theme_version=11 自作譜` | ❌ （该条目仍在文件里）未产生新格子 |

---

## 四、代码逆向成果（`jb_org.dll`）

### 1. PE 节表换算（**此前算错过，已校准**）
```
ImageBase = 0x10000000
.text    VA=0x1000    file=0x400      .rdata  VA=0x242000  file=0x240C00
.data    VA=0x2DC000  file=0x2DAA00
校验：file 0x298170 → VA 0x10299570 ✓
```

### 2. 「版本名桩表」真身 = **C++ switch 跳转表**（不是数据表）
`file 0x144990` 附近：
```asm
0x10145580  add  ecx, -6
0x10145584  cmp  ecx, 0x76
0x10145587  ja   0x10145C3F                    ; 默认分支
0x1014558D  movzx eax, byte ptr [ecx+0x10145D70]
0x10145594  jmp  dword ptr [eax*4+0x10145C58]  ; 70 项跳转表
```
每个 case 体：
```asm
mov esi, <字符串VA>; push esi; push 0x20; push edx
call dword ptr [0x102429F4]     ; gftools!GFSLPrintf(dst, size, key)
add esp,0xC; …
```
⇒ **`0x10145580` = 「按标签索引取文本」**（文本经 GFSLPrintf 写入调用方缓冲区）

### 3. ⭐ 完整「标签索引表」（跳转表各分支的字符串）
```
[ 0.. 6] 流派：ポップス / アニメ / ソーシャルミュージック / 東方アレンジ / ゲーム / クラシック / オリジナル
[ 7..16] LEVEL 1..10
[17..26] ア行/カ行/サ行/タ行/ナ行/ハ行/マ行/ヤ行/ラ行/ワ行        ← 艺术家五十音索引
[27]     HOLD
[28..39] **jubeat ripples knit copious saucer saucer fulfill prop Qubell clan festo Ave. beyond the Ave.**
[40..67] 艺术家名（Sota Fujimori / DJ YOSHITAKA / 猫叉Master / TAG / wac / …）
[68]     今週のオススメ
```
**⇒ 版本标签索引 = 28..39（12 个）**，与"12 个版本判定"完全一致

### 4. 🔑 为什么"改字符串"无效（**代码级铁证**）
对 12 个版本名字符串做"谁引用它"的全文件扫描：
```
jubeat          VA=0x10299578  引用 1 次: file 0x144C3F  ← 就是它自己的桩
ripples         VA=0x10299570  引用 1 次: file 0x144C58
knit            VA=0x10299588  引用 1 次: file 0x144C71
copious         VA=0x10299580  引用 1 次: file 0x144C8A
saucer          VA=0x102995E8  引用 1 次: file 0x144CA3
saucer fulfill  VA=0x102995D8  引用 1 次: file 0x144CBC
prop            VA=0x102995F8  引用 1 次: file 0x144CD5
Qubell          VA=0x102995F0  引用 1 次: file 0x144CEE
clan            VA=0x102995B8  引用 1 次: file 0x144D07
festo           VA=0x102995B0  引用 1 次: file 0x144D20
Ave.            VA=0x102995D0  引用 1 次: file 0x144D39
beyond the Ave. VA=0x102995C0  引用 1 次: file 0x144D52
```
**⇒ 每个字符串只被"自己的桩"引用一次，别无他用。**
（补丁位置没错 —— 是这个字符串**只服务于 `0x10145580` 这一个函数**）

### 5. 「按分类取标签索引」= `0x10146130`
```asm
dec ecx ; cmp ecx,0x38 ; ja 默认
jmp dword ptr [ecx*4 + 0x1014629C]
; 各分支： mov eax,6 / 7 / … / 0x17 / 0x4b / … ; ret
```
⇒ 把"分类种类枚举(1..0x39)"映射到"标签索引"。**版本分类的枚举 ≈ 23..34 → 标签索引 28..39**

### 6. 字体资源（`jb_org.dll` 明确加载）
```
data/font/atilb.ttf          (Atilla Bold)
data/font/jottwn.ttf         (Jott 43 Wide — 手写体)
data/font/mlsaj.ttf  mlsct.ttf  mlscu.ttf  mlscv.ttf  mlscw.ttf
data/font/gillib_.ttf        (Gill…)
data/font/df*.ttc  dfhsmarugothw4.ttc
※ 注意：data/font/qubell_font.ttf 不在 DLL 的加载列表里
```
⇒ **版本名是用这些字体渲染的**（`jottwn` 手写体对应 Prop / Ave. / festo / saucer fulfill 那一类外观）

### 7. UI 节点路径块（`file 0x29FB00` 起）
```
ssm_all / l44_smc_sm / ssm_ctgl / ssm_jklist
ssm_ctgl/ssm_ctgl_con_pos      ssm_ctgl/ssm_ctgl_info
ssm_ctgl/ssm_ctgl_ex_bd_pos/ssm_ctgl_ex_bd
ssm_jklist/ssm_jklist_ex_0_pos/ssm_jklist_ex_0/ssm_t0100.png
ssm_t9100.png  ssm_t9101.png  ssm_t9000_ja.png …
```
⇒ 分类面板节点 = **`ssm_ctgl`**；DLL 里的 `ssm_*` 精灵名共 **119 个**，其中
   **`ssm_name_p1.png` / `ssm_name_r1~r4.png` / `ssm_ctgl_dtl_name_00/01.png`** 被引用
   —— **而这三个正好是我解码成"纯绿单色板"的那批**（可疑：可能是我的解码器对 `hf3=0x81` 处理有误）
   ※ DLL 的 `ssm_*` 列表里**没有 `ssm_t8xxx.png`**

---

## 五、事故与新增铁律

### 2026-10-09 20:41 `SYSTEM ERROR 5-1505-0000`（进选曲界面时）
```
W:aep: aep_player_create(l44_play_background) failed
W:aep: aep_player_create(l44_smc_sm) failed
W:aep: aep_player_create(l44_navi) failed
W:aep_set_texture: apw=NULL, name:smm_t8200_ja.png
```
**根因**：一次性重写 44 张图集 / 4400 个精灵，其中**不能无损往返的精灵**（往返审计证明：
**220 个全是 `fmt=26`(DXT5)，且全部集中在 `tex_l44av_stg_bg_change.bin`**）写回去后
贴图创建失败 → AEP 绑定失败 → 系统错误。**已全部删除探针，基线已复原。**

### 🔴 铁律
1. **探针重编码必须逐个做「往返校验」**（decode→re-encode→decode→逐像素比对）；不一致的**跳过不改**
2. **DXT5(`fmt=26`) 一律不许重写**（有损，且易破坏贴图）
3. **单次探针不要动几千个精灵**；小批量多轮
4. **AEP 按精灵名绑定贴图**：新增精灵必须能被 AEP 正确绑定，否则整个画面崩
5. **`aep_player_create(...) failed` = "该图集被这一屏使用"的强信号**（比 layeredfs 更精确）

---

## 六、结论与剩余可能

**所有被这一屏加载的图集（44 张，含全部精灵）都已被探针覆盖，没有任何一张能影响那 11 个版本格的瓦片。**

剩余两种可能：

1. **瓦片由引擎程序化绘制**：几何圆角矩形 + 渐变着色器（这能解释"任何图集都改不动它"）
   —— 标签文字则来自 §四.3 的字符串池 + §四.6 的字体
2. **瓦片美术在尚未被探到的资源里**：如 **55 个分组图集**（开机加载、至今未探）

---

## 七、三条路线

| 路线 | 内容 | 代价 | 成功率 |
|---|---|---|---|
| **A. 继续逆向** | 顺着 `0x10145580` / `0x10146130` 的调用链，找"绘制版本面板"的函数本体，看它到底取什么资源 | 纯离线，我干 | 中 |
| **B. 重探** | 把 **55 个分组图集**也探一遍（逐精灵往返校验 + 小批量） | 重（55 个大文件，需 1~2 次实机运行） | 中 |
| **C. 务实收尾** | 放弃"第 13 个格子"，把自制谱**归入某个已有版本档**（立刻可用、零风险），或改用「自作譜」主题 / 收藏等方式区分 | 立即可用 | 高 |

**建议顺序：A → B → C。**
