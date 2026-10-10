# jubeat beyond the Ave. —「新增第 13 个版本档」本轮进展报告

> 日期：2026-10-08 晚场（17:20–18:10）
> 游戏根：`<GAME_ROOT>`
> 前情：`ub_版本档机制全解.md`、`.workbuddy/memory/2026-10-08.md`

---

## 一、本轮拿下的 4 个硬骨头

### 1. 🎯 定位图集的决定性方法：**同名同内容覆盖探测**（零风险）

layeredfs 命中 mod 目录时会往 `log.txt` 写一行：

```
M:layeredfs: Using ./data_mods/omnimix/d3/model/<文件名>
```

**做法**：把候选图集**原样复制**（byte-identical）到 `data_mods/omnimix/d3/model/`，
游戏一旦打开它就会被记录，而**画面完全不变**（副本和原件一模一样）→ 纯观测，零副作用。

**新工具** `tools/probe_ver_atlas.py`

| 命令 | 作用 |
|---|---|
| `setup` | 把 `data/d3/model` 里的 **1044 个 `tex_*` 非 bnr + 1044 个 `mdl_*` 非 bnr** 复制进 mod 目录（已存在的不动），清单存 `tmp/probe_manifest.json` |
| `remove` | 按清单**精确删除**探针，不留垃圾 |
| `report` | 解析 `log.txt`，按时间列出非 bnr 的命中 |

**实测有效**：启动 7 秒内就抓到 `layeredfs: Using .../tex_l44_system.bin`。

> ⚠️ 探针现在还在 mod 目录里（5579 个文件，原 3491）。**用完后 `remove`**。

### 2. 🛠 挖出 SpiceTools layeredfs 的隐藏开关

从 `modules/ifs_hook.dll` 里提取到：

```
--layered-verbose=0/1        --layered-devmode=0/1
--layered-disable            --layered-allowlist=...
--layered-blocklist=...
```

- mod 目录 = **扫 `./data_mods/` 下的子目录**（`Detected mod folders:` 会列出来）
- **额外发现**：支持 **PNG 打包式 mod** —— `./data_mods/_cache/<mod>/tex/*.png` + `texturelist.xml`，
  用 PNG 直接改贴图、不用手搓 TDXT。**后续做「自作譜」logo 时值得先试这条路**

### 3. ✅ 修掉了反复弹的 IP 报错弹窗（5-1506-0000）

| 项 | 值 |
|---|---|
| 病因 | `dev/nvram/network.xml` 写死 `ipaddr=192.168.44.1`、`gateway=192.168.44.254` |
| 真实网络 | 无线局域网 IPv4 = **192.168.0.102**，网关 192.168.0.1 |
| 修复 | 两个 `network.xml`（`dev/nvram/` + `prop/defaults/`）改成真实值，备份 `*.bak_ipfix_20261008_172210` |
| 结果 | **弹窗消失**（实测截图确认）——之前每 ~30 秒弹一次并抢焦点，是自动化最大障碍 |

### 4. ⌨️ 输入通道重做：`tools/gclick.py`

- `SetProcessDpiAwareness(2)` → **截图坐标 == 点击坐标**（窗口 768×1360 @ (0,0)，屏幕 2560×1440）
- `focus()`：AttachThreadInput + **最小化/还原 + TOPMOST 往返**打破前台锁定，带 6 次重试
- `click / press / seq` 在点击前**必须**拿到前台，拿不到就**跳过**（避免误点用户的 QQ / 开始菜单）

> ⚠️ 教训记录在案：聚焦失败却强行点击会点出别人的窗口，非常打扰。

**另外**：我可以自己启动游戏了 ——
`cd <GAME_ROOT> && cmd //c start "" "spice.exe" jubeat.dll`，
但**必须用后台任务挂住 shell**（`sleep 3600`），否则沙箱退出时会把游戏一起杀掉。
未提权的 spice.exe **可以**接收 SendInput（提权版有 UIPI 拦截，这就是之前"按键不生效"的原因）。

---

## 二、本轮排除的图集（缩小包围圈）

| 图集 | 实际是什么 |
|---|---|
| `tex_l44av_select_genre.bin` | **ジャンル检索画面**：`SGR_T0000..T0008` = 9 个流派文字图 + `SGR_PNL_ON/OFF` = 180×180 黄色发光圆角面板（和版本格同款 UI，但**不是版本格**） |
| `tex_l44av_smc_sm.bin` | 单人检索画面（`SSM_*`：`CTGL`=分类列表、`TUNE`=标签、`JKFR`=曲框） |
| `tex_l44av_smc_mm.bin` | 多人检索画面（`SMM_*`） |
| `tex_l44avby_result.bin` | 结果画面。`RES_EXC_01_JUBEAT … RES_EXC_14_AVE_BYD`（160×160）= **14 个版本徽章底图**（彩色图案，不是 logo） |
| `tex_l44avby_street*.bin` | beyond the Ave. 的「たんさく」街区选曲 UI（`SRT_*`） |
| 各 `tex_l44XX_title.bin` | 只有**本版本自己的**标题 logo（如 `TIT_QUBELL_LOGO` / `TIT_SAUCER_LOGO` / `TIT_PROP_*`），且实测替换无效 |

**从 `jb_org.dll` 挖到的 UI 节点路径**（说明 UI 是数据驱动的）：

```
ssm_all/ssm_ctgl              ← 分类面板
ssm_all/ssm_ctgl_dtl          ← 分类明细
ssm_all/ssm_jklist            ← 曲目列表
0x2AABD2  「…るカテゴリパネルをタッチ！」
VERSION2 / VERSION3 / ARTIST2 / ARTIST3   ← 检索模式名（多语言）
SMM_CTGL_DTL_ICON_0..5        ← 分类明细画面的 6 个检索模式图标
SSM_T8001..T8009（9 个 48×48）← 数量与"9 个版本格"巧合，待验证
```

---

## 三、下一步（只差"进对画面"这一步）

```
1. 进入「バージョンからお探しします」面板画面
   （9 个绿色发光方块 + CLOSE 的那个，见 ver_tiles_zoom.png）
2. 保持游戏开着，运行：  python tools\probe_ver_atlas.py report
   → 时间戳最新的一批图集就是「版本格 logo」所在处 ★一次定位★
3. 用 tdxt_encode 造「自作譜」logo 塞进该图集（遵守 4 条贴图铁律）
4. 版本 ID：music_db.dll 掩码循环上限 cmp ecx,0x10 → 0x20（改用 bit16+）
   再补 jb_org.dll 的 ID 链（file 0x1BABB0）
```

**本轮最后的状态**：游戏由我启动并停在吸引模式（循环浏览选曲/设置/结果画面），
两个后台录屏工具在跑（`tools/watch_attract.py` / `tools/screen_trace.py`），
但吸引模式**没有**走到版本面板画面 —— 所以这一屏还需要手动进一次。

---

## 四、可回退清单

| 改动 | 回退方式 |
|---|---|
| mod 目录 2088 个探针副本 | `python tools\probe_ver_atlas.py remove` |
| `dev/nvram/network.xml` + `prop/defaults/network.xml` 的 IP | 还原 `*.bak_ipfix_20261008_172210` |
| `jb_org.dll` 的 `V01..V12` 补丁（无效） | `python tools\patch_version_strings.py --game <GAME_ROOT> revert` |
| `<GAME_ROOT>\ubstart_probe.bat` | 普通启动脚本，无副作用，可保留 |
