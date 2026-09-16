# 展锐(UNISOC)异常落盘真源：`/data/uniview` 层级与 collector 期望不符（#73 真机调研）

Status: implemented
Class: bug-fix

## Decision

- **交付两个展锐脚本**（MTK `aee_prepare` / `aee_signal_trigger` 的对应物）：
  - `unisoc_probe`（v1.0.0 路径清单 + v1.0.1 固定只读深探）；
  - `unisoc_signal_trigger`（v1.0.0 `kill -<sig>`；v1.0.1 以 `am crash` 为主、`kill` 兜底、1s 差分采样）。
  它们是 #73 验收「至少一款展锐真机跑 PlanRun、`job_log_signal` 出现行」的**必要工具**——与 #72 为其验收新增 `aee_signal_trigger` 同构（先例：`cb56eddc`）。
- **不改** `UnisocUniviewReconciler` / `UnisocPlatformCollector` 的 root 与解析逻辑：真机上**从未观测到**带 `unievent_info.json` 的事件目录，凭观察改路径等于无据猜测（#73 正文的路径假设已被证伪，见 Verification）。
- 探测脚本**刻意不做任意命令执行**（命令集在代码内固定且只读）：避免把脚本库变成「平台 → 任意 shell」的越权入口（对照 priv wrapper / 安全边界取向）。

## Alternatives

- **照 issue 正文加 `/data/unisoc_log` 等路径**：实测 `/data/unisoc_log`、`/data/vendor/unisoc`、`/sdcard/unisoc_log` **三者均不存在** → 加了只会制造「看似覆盖」的假象。
- **直接把 root 改成 `/data/uniview/logs`**（因为观察到 `logs/tmp`）：层级真实，但**没有任何事件样本**能证明事件落在该层、也不能证明元数据文件仍是 `unievent_info.json` → 先取样本再改。
- **把 `/data/tombstones` 直接当事件源**：属新增语义 + 风险定级设计（需产品裁决），超出「对齐 MTK 解析能力」的边界 → 另立单。
- **加一个通用 exec 脚本做调研**：省事，但会给脚本库引入任意命令面 → 不做，改用固定只读命令集。

## Verification

**全部为真机实测**：`Z2581` / `ro.board.platform=ums9230` / Android 16 / `root_uid=0`；设备 `id=382`、host `172-21-x-x`；只读探测 + App 崩溃诱发，**未写设备任何持久配置**。

| 项 | 结果 |
|---|---|
| 执行通路 | 平台脚本派发（`POST /api/v1/scripts/scan` 注册 → 建计划 → `POST /api/v1/plans/{id}/run`）。**Agent 有独立的本地脚本注册表**：新脚本需经心跳 `script_registry.initialize()` 同步（`agent/main.py:1098`）；首次派发必 `Script … not found in local registry`，**重试即成功，无需 hot-update/重启** |
| 目录实测 | `/data/uniview` **存在**（内含 `logs/`，`logs/` 内含 `tmp/`）；`/data/vendor/uniview`、`/data/unisoc_log`、`/data/vendor/unisoc`、`/sdcard/unisoc_log`、`/data/aee_exp`、`/data/vendor/aee_exp` **均不存在** |
| uniview 活性 | `[init.svc.uniview]: [running]`；`debug.uniview.last_event_key_info` 仅 `event_time` 周期刷新，**`event_id` 恒为 `104001002`** |
| 诱发结果 | `am crash com.android.settings` 与 `kill -11 <pid>` 均 rc=0 → 产出 **`/data/tombstones/tombstone_01(.pb)`**（654/413 KB，触发窗口内出现）；**`/data/uniview*` 无任何新条目** |
| 现有实现判定 | `unisoc_reconciler.py:292` 只 `ls -1 <root>`（不递归），`:344/:348` 要求 `<root>/<name>/unievent_info.json`；`collectors/unisoc.py:28` 同 → 与真机层级不符 |
| 新增脚本单测 | `pytest backend/agent/tests/test_unisoc_probe_v101.py backend/agent/tests/test_unisoc_signal_trigger_v101.py -q` → **10 passed** |

> 该设备 mtime 不可信（文件显示 `2026-07-31`、目录显示 `1970-01-01`，真实为 09-14），因此判新旧只看**差分列表**，不看 mtime；这也印证仓库既有「设备时钟漂移」处理（`#785`）的必要性。

## 修正实施（权威布局已在真机坐实，本 PR 落地）

上文的 Revisit 条件（"拿到真实事件样本后再改 root/解析"）**已满足**：Z2581/Z2582
双机型真机 + toolkit 源码双向确认后，已按下表修正（`Status: implemented`），
并以**反例实证**把修正钉住（把根改回旧值 → 2 条用例转红；恢复后全绿）。

| 项 | 修正前 | 修正后（真机确认） |
|---|---|---|
| 事件根 | `/data/uniview`、`/data/vendor/uniview` | **`/data/ylog/uniview_exception`** |
| 元数据文件 | `unievent_info.json` | **`unievent_info`**（JSONL） |
| 事件行判据 | 任意 JSON | 含元数据键（`event_id`/`event_name`/`event_type`/`event_level`）**或**发生键（`kick_datetime`/`event_time`/`timestamp`/`reboot_reason`）；设备头行（`sn`/`software_version`/`soc_model`）天然排除 |
| normalboot | 不过滤 | **丢弃** `reboot_reason == "normalboot"`（Z2581 的 `Reboot.103000002` 全部是它） |
| `event_subtype` | `event_name`（常缺失） | `event_name`（如 `Java Crash`）**否则目录名前缀**（`ANR`/`JE`/`NE`/`Reboot`） |
| 默认探针面（验收工具） | `/data/uniview` 系 | `/data/ylog/uniview_exception` + `/data/anr` + `/data/tombstones` + `/data/ylog`（新增 `unisoc_signal_trigger v1.0.2`；v1.0.0/v1.0.1 保持不可变） |

真机原文（`JE.103000004/unievent_info`，JSONL 三类行）：

```json
{"sn":"62002360","software_version":"MyOS16.0.3_Z2582_GEN_AF","soc_model":"UMS9230E"}
{"event_id":"103000004","event_type":"FAULT","event_level":"GENERAL","event_name":"Java Crash"}
{"kick_datetime":"2026-09-08_06:59:12.031","pid":"23847","proc":"com.android.camera2","tag":"system_app_crash"}
```

## 追加现场复核（2026-09-16，#2211/#2212 合并后回归）

**背景**：Job 运行中的观察窗口（6h）对设备 `62002360`（Z2582 / `MyOS16.0.3_Z2582_GEN_AF`，
`ro.debuggable=1`，`persist.sys.monkey=true`）做合并后回归。全程只读取证。

### Known-issue：Z2582 build 静默吞掉三类 adb 调试触发（不可再作触发构造机）

| 触发 | 实测（均在 Job 运行中） |
|---|---|
| `am crash com.android.settings`（package / pid / `--user 0` / 前台 / force-stop 后新进程五种姿势） | rc=0 但进程 pid 不变；无新 `/data/system/dropbox/system_app_crash`（最新仍 09-13）；logcat 无任何痕迹 |
| `am hang`（前台与后台） | 打印 `Hanging the system...` 但无实际挂起（12s/112s 探活正常）；无 watchdog 日志、无 `SWT.*` 目录 |
| `kill -STOP` + `input tap`（造输入分发超时 ANR） | 20s 内 `/data/anr` 无新 trace、`ANR.103000005` 无新行、logcat 无 `Input dispatching timed out` |

定性：**命令在 ROM 层被吞**（rc=0、无 stderr、无任何副作用——不是执行失败）。触发台账三条在
Z2581/MyOS16.0.1 上有效，在本组合**全部失效**（`ro.debuggable=1` 也拦不住）→
**Z2582/MyOS16.0.3 不得再作为主动触发构造设备**。归因修正（见下节换机实证）：
`persist.sys.monkey=true` 在 Z2581/MyOS16.0.1 build **同样存在**且触发有效 → monkey 开关假说
否定，抑制器收窄为 Z2582/MyOS16.0.3 build 特有策略；不再深究（属设备侧行为，不在平台边界内，
台账只登记组合事实）。

### 平台侧回归结论（负向成立；正向缺口收窄为「构造手段」）

- 合并后 UNIVIEW 新增 0 条、Boot Category 仍 2 条（均为合并前存量）→ **无假阳性成立**；
  与 #2083（normalboot-only 不发射）、#2080（键补事件身份不改写存量）的行为自洽。
- 「合并后再产生一条新鲜信号」的正向路径本轮未构造（被上节触发抑制所阻）。注意正向**端到端**
  证据并非空白：#1956 note 已记录本机（`serial=62002360`）一次普通 PlanRun 即产出
  `device_log_event(UNIVIEW, Java Crash/ANR/Boot Category)` 与对应
  `job_log_signal(source=reconciler)`——那 2 条 Boot Category 即其存量。故本轮缺口性质是
  **构造手段失效**，不是链路有效性存疑。
- 正向替代路径（不依赖触发器）：原拟「观察窗口内被动等自然新事件目录」**当日证伪**——驻留
  观察者独占设备且 probe 只读，15h/1170 tick 0 事件（等待 = 占机 = 无压测，自相矛盾，见下节
  结尾）；改挂真实压测 run 即自然成立（reconciler 任一 UNISOC job 在跑都会接住落盘事件）。
  主动触发移回 Z2581 执行——**已完成，正向闭环成立，见下节。**

### 换机正向再实证（同日 11:48–12:05，Z2581 `00004a4f` @ host `172-21-x-x`，
`MyOS16.0.1_Z2581_GEN_AF`）

**编排**：PlanRun 410 / Job 17940（Plan 51：`unisoc_probe` v1.0.1 四根、patrol 45s 驻留——
与 gO7uVr 窗口同形态；触发用手工 adb，同台账姿势）。基线：该序列 UNIVIEW 信号 **0 条**，
uniview 根仅 `Reboot.103000002`（normalboot 被 #2083 拒收 → 基线干净，任何新增都可证新鲜）。

| 触发 | 设备端效果 | 平台端效果 | 判定 |
|---|---|---|---|
| `am crash com.android.settings`（11:51:27） | **新目录 `JE.103000004`** + dropbox 新 `system_app_crash`（设备时钟恒慢 ~6.9 天，文件名 epoch 对应真实时刻，#785 形态） | 11:52:02 seq1 emit（**tick 35s 接住**，`subtype=Java Crash`，`package=com.android.settings`）；11:55:03 seq2 因目录追加载荷签名变化重发（#2010 原语义；消费侧 #2080 同键合并）；DLE→`REMOTE` | **有效**——Z2582 上被阻的正向路径在此闭环 |
| `am hang`（约 11:56） | 该 ROM 的 hang 作用在 **system_server**：dropbox `system_server_pre/post_watchdog` ×3 + **新目录 `SWT.103000006`**（`event_name=watchdog`，头行 `sn=00004a4f`） | 11:58:03 seq3 emit（`subtype=watchdog`，`package=system_server`）；DLE→`REMOTE` | **有效**（Z2582 连 watchdog 痕迹都不产生 → 机型 build 差异坐实） |
| `kill -STOP <pid>` + `input tap`（两次尝试） | `/data/anr` 40s+ 无 trace、dropbox 无 ANR 行、`ANR.103000005` 无新行；`input tap` **阻塞在挂起的分发队列上**（排队而非被吞，与 Z2582 的「秒回 rc=0 零痕迹」形态学相反） | 无对应信号 | **未复现**——台账第三条即使在 Z2581 也未按该姿势复现（疑 ANR 落盘延迟/路径不同），ANR 类正向以自然事件补 |

**副产物验证**：三条信号 + 三条 DLE 全部 `REMOTE`（上送 `/mnt/stp-aee/devices/410/`）→
采集→emit→入库→上送全链在合并后代码上一次性走通；`#2010 重发 + #2080 同键合并`在真机上首见
原始双行样本。

**现场恢复**：hang 后 system_server watchdog 反复触发（load ≈12，force-stop 卡死、
`kill -9` settings 不解环）→ abort PlanRun 410（Job `ABORTED`、3 信号保留、租约释放）→
`adb reboot` 恢复（`sys.boot_completed=1`，租约空）。gO7uVr 窗口（PlanRun 409 / 62002360）
全程未受影响，并于当日 12:26 复核收口：**15h / 1170 patrol 轮 / 0 新信号（全类别）**——
观察窗对「等自然事件」无效（占机即断供），且每拍仍为 `NE.103000003` 白烧 180s（#2252 形态的
又一实证）。已 abort（Job `ABORTED`、租约释放），62002360 回压测池——ANR 类正向补样改由
后续真实压测 run 承接。

### 实证：NE 大目录整目录 pull 恒超时 → 已立单 #2252

设备侧 `NE.103000003` 共 **1.9GB / 999 个文件**，agent 本地副本恒空、staging `.pulling_*`
每拍建删（父目录 mtime 常新）——与代码逐点吻合：`_pull_event_dir_unlocked`
（`unisoc_reconciler.py:488-521`）整目录 pull `timeout=180`，失败走 #2079
「不落签名、下一拍重试」→ 对永久性超限目录退化为**每拍 180s 白烧且永不收敛**；且每次 pull
持 `host_extraction_slot`（#740 与 MTK 路共享提取预算），挤占同 host 其它 pull。这正是下节
Revisit「载荷策略」项的现实证据 → **已立 [#2252](https://github.com/DUElost/stability-test-platform/issues/2252)**。

### 触发自检 SOP（可批产；两台独立复核通过，2026-09-16）

**用途**：派发中的平台任务上确认「设备自造异常 → 平台监测到信号」持续成立。
**适用面**：仅 Z2581 系（`MyOS16.0.1_Z2581_GEN_AF`）——Z2582/MyOS16.0.3 触发被 ROM 吞（上上节）；
自检判据只用 crash 类（hang 会挂 system_server 致 watchdog 风暴需重启收尾；ANR 姿势两台均未
复现，见上节）。

**配方（按序）**：
1. 选机：host `172-21-x-x`（当前 reconciler 在跑态）上 ONLINE 空闲 Z2581；先看设备端
   `ls /data/ylog/uniview_exception/`——避免带超大 `NE.*` 的机（#2252 每拍白烧 180s，
   拖长时延上限）；
2. 基线：`job_log_signal(category='UNIVIEW', device_serial=…)` 行数（理想为 0，新增即可归因）；
3. 驻留观察窗：`POST /api/v1/plans/51/run {"device_ids":[<id>]}`（Plan 51 = probe v1.0.1 四根，
   patrol 45s——reconciler 随 job 启动，驻留保证接住落盘晚于触发的事件）；
4. 进 patrol 后单发触发：`adb -s <serial> shell am crash com.android.settings`
   （JE 类）或 `kill -11 <pid>`（NE 类，同日补测坐实，见下矩阵）；
5. 设备端差分（≤15s）：应现 `JE.103000004` 新目录 + dropbox `system_app_crash`；
6. 平台端（≤2min）：`job_log_signal` 新 UNIVIEW 行（`subtype=Java Crash`、`package=com.android.settings`、
   `source=reconciler`），DLE 终态 `REMOTE`；
7. 释放：`POST /api/v1/plan-runs/<run>/abort`，回查租约空、设备回 ONLINE。

**复核记录（同日双机，均一次通过）**：

| 设备 | 触发（真实钟） | 设备端落盘 | 平台 emit | 备注 |
|---|---|---|---|---|
| `00004a4f` | 11:51:27 | ≤25s | 11:52:02（seq1，+35s） | 该机时钟恒慢 ~6.9 天（#785 形态）；seq2 为 #2010 签名变化重发样本 |
| `0001cb4d` | 14:22:15 | ≤12s | 14:23:52（+97s） | 时钟准：`aee_ts=2026-09-16_14:22:13` 与真实钟秒级吻合——坐实「慢钟」系单机漂移而非链路问题；Run 411/Job 17941，abort 后设备回池 |
| `0001cb4d`（NE 补测） | 14:36:29（`kill -11` settings） | ≤15s（**新容器 `NE.103000003`** + tombstone） | 14:38:33（+124s，`Native Crash`） | Run 412/Job 17942；`aee_ts` 再对秒；abort 后设备回池 |

**类型覆盖矩阵（监测面 = uniview 守护落盘，解析面对类型无感）**：

| 类别 | 平台解析 | 监测实证 | 状态 |
|---|---|---|---|
| Java Crash（JE） | `event_name` | 今日双机触发复现 ×3 | ✅ 触发级 |
| watchdog（SWT） | `event_name` | 今日 `am hang` 触发复现 | ✅ 触发级 |
| Native Crash（NE） | `event_name`/前缀 | 今日 `kill -11` 触发复现 | ✅ 触发级 |
| ANR | 前缀 `ANR`（行常缺 `event_name`） | 62002360 历史自然事件入库 ×2（09-14/15） | ✅ 历史级（构造姿势未复现，勿作自检判据） |
| 异常 Reboot（Boot Category） | `event_name` | 62002360 历史入库 ×2；normalboot-only **有意拒收**（#2083） | ✅ 历史级 |
| uniview 之外（`/data/anr` 独立 trace、tombstones-only、ylog 系） | — 不在采集面 | — | ❌ 待边界裁决（#73 Revisit「附加源」） |

**粒度限定**：容器目录内同类别多条发生经 `fold_unievent_info` 折叠，一次签名变化发**一条**
（#2010/#2080 语义即为此设计）；逐条精确对应 = #2252 按 `{seq}-{ts}.tar.gz` 增量拉取的终态。

两机基线均 0 → 触发后恰 1 条新鲜信号、1:1 对应；采集→emit→入库→上送（`REMOTE`）每次全链走通。

## Revisit

- **`kick_datetime` 是设备本地时区裸串**（`2026-09-08_06:59:12.031`，无 tz）：`device_timestamp`
  优先取 `event_time`（epoch 毫秒，无歧义），`kick_datetime` 只作**原文**保留 → 若将来要用它做
  时间对齐，需先确认设备时区。
- **附加源尚未纳入**：toolkit `_scan_platform_sources()` 还采 `/data/anr`、`/data/tombstones`
  （该机 100 条）、`/data/ylog`，本单只对齐了 uniview 主路径 → 是否纳入待边界裁决。
  换机实证补充：Z2581 上 `kill -STOP`+tap 40s 窗口内 `/data/anr` 不产 trace——ANR 类事件的
  设备侧产生时延/路径与 uniview `ANR.103000005` 的关系需自然事件样本再判，勿以该姿势否定 ANR 链路。
  自然样本**勿用观察者驻留等待**（驻留 = 占机 = 无压测，15h/1170 tick/0 事件实证），搭真实
  压测 run 顺路采集即可。
- **载荷策略**：现为整目录 pull，toolkit 是按 `{seq}-{ts}.tar.gz` 与事件行**按序号对应**增量拉取
  → 目录很大时（如 `NE.103000003` 有上百个 tar）值得收敛。**已立 #2252**（2026-09-16 实证：
  该目录 1.9GB/999 files，整目录 pull 恒 180s 超时 → 永久无信号 + 每拍占用提取预算，
  见上节「追加现场复核」）。
- **`normalboot` 之外的 `reboot_reason`** 取值枚举未知（本次只观测到 `normalboot`）→ 需补样本。
- 本单正文的过时前提（"代码库 grep 展锐/UNISOC 0 hits"、`/data/unisoc_log` 假设）已在 issue 评论中更正；
  `AGENTS.md` 并无「AEE crash detection chain」章节，展锐说明暂落本 note。
