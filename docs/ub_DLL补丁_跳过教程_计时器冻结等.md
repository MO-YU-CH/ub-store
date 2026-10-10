# jubeat「beyond the Ave.」DLL 补丁实施报告

机台：`<TARGET_HOST>`　目标文件：`<ARCADE_ROOT>\jb_org.dll`
参考补丁站：<https://getze56901.github.io/patcher/beyondave.html>
日期：2026-10-05

---

## 1. 目标文件的确定

机台上有两个「jubeat DLL」：

| 文件 | 大小 | PE 时间戳 | 作用 |
|---|---|---|---|
| `contents\jubeat.dll` | 571,187 | 2022-08-23 | **omnimix shim**（由 omnimix.dll 改名，内部加载 `jb_org.dll`） |
| `contents\jb_org.dll` | 3,276,440 | **2024-11-12** | **真游戏本体**（原版 jubeat.dll 改名） |

补丁站给出的偏移最大到 `0x1ADAFA`（≈1.76 MB），远超 shim 的体积 → **补丁必须打在 `jb_org.dll`**。

---

## 2. 版本匹配（关键难点）

补丁站只列了 4 个版本：`2023-09-20 / 2023-11-20 / 2023-12-19 / 2024-06-05`；
机台 `jb_org.dll` 是 **2024-11-12** 构建，**所有候选偏移处的字节都对不上**。

解法：本地增量更新包里找到了对照样本 ——
`<GAME_DIR>\L44JF_2024060300from2024042200\modules\jubeat.dll`（PE **2024-05-30**，3,217,048 B）
在补丁站 **“2024-06-05”** 条目的 4 个偏移处 **全部吻合**，即该条目对应该包。

于是以它为参照，在机台 DLL（同源、晚 5 个月）中定位对应位点。

---

## 3. 定位方法与结果

方法：**掩码模式匹配 + Capstone 反汇编交叉验证**（屏蔽 rel32/绝对地址等重定位字段）。

| 补丁 | 位点偏移 | 原字节 → 新字节 | 反汇编（打补丁后） |
|---|---|---|---|
| Skip Tutorial（跳过教程） | `0xDAD7F` | `0F 85` → `90 E9` | `nop` + `jmp 0xdaf04`（原 `jne 0xdaf04`） |
| Select Music Timer Freeze（选曲计时器冻结） | `0xC42EF` | `75` → `EB` | `jmp 0xc4314`（原 `jne 0xc4314`） |
| Skip Category Select（跳过类别选择） | `0xC467B` | `04` → `07` | `push 0x23840700`（原 `0x23840400`） |
| Result Timer Freeze（结算计时器冻结） | `0xABC2B` | `75` → `EB` | `jmp 0xabc36`（原 `jne 0xabc36`） |

**关键性质：跳转目标不变，只把条件跳转改成无条件跳转**（`0F 85 rel32` 与 `E9 rel32` 的 rel32 长度相同，因此 `90 E9` 可原地替换）。

定位依据（±40 字节逐字节比对，差异全部落在重定位的 4 字节地址组内）：
- `Select Music Timer Freeze`：40 字节中仅 14 字节不同 → 代码除地址外完全一致
- `Skip Category Select`：40 字节中仅 10 字节不同 → 一致；且 `push` 的立即数是常量 `00 04 84 23`（非地址）
- `Skip Tutorial` / `Result Timer Freeze`：因中间插入了代码，位移不再是常量，改用掩码模式唯一命中

模式（`??` = 通配）：
```
Skip Tutorial      : 8b c8 ff 15 ?? ?? ?? ?? 84 c0 0f 85 7f 01 00 00
Result Timer Freeze: 85 f6 0f 95 c0 83 3d ?? ?? ?? ?? 00 75 09 33 c9 e8
```

---

## 4. 落地结果

- 改动总量：**5 个字节**
  ```
  0xABC2B: 75 -> EB      0xC42EF: 75 -> EB
  0xC467B: 04 -> 07      0xDAD7F: 0F -> 90      0xDAD80: 85 -> E9
  ```
- MD5：原始 `ca26c344e64485e140e853c4afbd912f` → 补丁版 **`ab71c96d8b718714db6b8a9e047d8bfc`**
- 机台：`<ARCADE_ROOT>\jb_org.dll` 已替换，大小/MD5 校验通过
- 备份（3 份，均为原始版）：
  - 机台 `<ARCADE_ROOT>\jb_org.dll.bak_orig`
  - 本地 `<GAME_ROOT>\jubeat.dll.bak_orig`（已同步打补丁，见下）
  - 工作区 `out/jb_org_orig.dll`
- 项目源镜像 `<GAME_ROOT>\jubeat.dll` 也已同步为补丁版（原版存为 `.bak_orig`），以后重新部署不会再丢补丁。
- 补丁版工作区副本：`out/jb_org_patched.dll`

启动验证（重启后日志）：

```
texname error = 0
no package    = 0
overlay 图集加载 = 13（含 om_001 / om_002）
failed to get file buffer = 0
```

无崩溃，标题画面正常。其余 `mounttab / bookkeep0,1 / pac_package / audio CoCreateInstance` 警告为打补丁前既有无害项。

---

## 5. 补丁站还有但本次未打的项目（备用）

| 补丁 | 2024-06-05 版偏移 | 内容 |
|---|---|---|
| Skip Online Matching | `0xD41FF` | `0F 84` → `90 E9` |
| Force Unlock All Markers | `0x4` / `0x1B1CA0` / `0x1B1F8A` | 3 处 |
| Force Unlock All Backgrounds | `0x139B1E` / `0x139DFB` / `0x167DBD` | 3 处 |
| Force Enable Expert Option | `0xDCF30` | `55 8B EC` → `B0 01 C3`（直接 `return 1`） |
| Default Marker For Guest Play | `0x5D117` | 联合补丁（默认/Festo/Qubell/Shutter） |
| music\_db.dll「Unlock all songs」 | `0x2135` | `74 0A` → `90 90`（2023-11-20 / 2023-12-19） |

> 这些同样需要按「用 `L44JF_2024060300` 的 `modules\jubeat.dll` 做参照 → 掩码定位到机台 2024-11-12 版」的流程重定位。
> `music_db.dll` 需另找同版本对照（机台/包内的 `modules\music_db.dll`）。

---

## 6. 复现脚本

- `omnisrc/probe_dll_ver.py` —— 列出各候选 DLL 的 PE 时间戳并核对补丁站偏移
- `omnisrc/locate_sites.py` / `locate_sites2.py` —— 字节窗口 / 掩码模式定位位点
- `omnisrc/patch_jb_org.py` —— 打补丁 + Capstone 反汇编验证 + 差异字节清单
