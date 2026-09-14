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

## Revisit

- **`kick_datetime` 是设备本地时区裸串**（`2026-09-08_06:59:12.031`，无 tz）：`device_timestamp`
  优先取 `event_time`（epoch 毫秒，无歧义），`kick_datetime` 只作**原文**保留 → 若将来要用它做
  时间对齐，需先确认设备时区。
- **附加源尚未纳入**：toolkit `_scan_platform_sources()` 还采 `/data/anr`、`/data/tombstones`
  （该机 100 条）、`/data/ylog`，本单只对齐了 uniview 主路径 → 是否纳入待边界裁决。
- **载荷策略**：现为整目录 pull，toolkit 是按 `{seq}-{ts}.tar.gz` 与事件行**按序号对应**增量拉取
  → 目录很大时（如 `NE.103000003` 有上百个 tar）值得收敛。
- **`normalboot` 之外的 `reboot_reason`** 取值枚举未知（本次只观测到 `normalboot`）→ 需补样本。
- 本单正文的过时前提（"代码库 grep 展锐/UNISOC 0 hits"、`/data/unisoc_log` 假设）已在 issue 评论中更正；
  `AGENTS.md` 并无「AEE crash detection chain」章节，展锐说明暂落本 note。
