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

**全部为真机实测**：`Z2581` / `ro.board.platform=ums9230` / Android 16 / `root_uid=0`；设备 `id=382`、host `172-21-15-77`；只读探测 + App 崩溃诱发，**未写设备任何持久配置**。

| 项 | 结果 |
|---|---|
| 执行通路 | 平台脚本派发（`POST /api/v1/scripts/scan` 注册 → 建计划 → `POST /api/v1/plans/{id}/run`）。**Agent 有独立的本地脚本注册表**：新脚本需经心跳 `script_registry.initialize()` 同步（`agent/main.py:1098`）；首次派发必 `Script … not found in local registry`，**重试即成功，无需 hot-update/重启** |
| 目录实测 | `/data/uniview` **存在**（内含 `logs/`，`logs/` 内含 `tmp/`）；`/data/vendor/uniview`、`/data/unisoc_log`、`/data/vendor/unisoc`、`/sdcard/unisoc_log`、`/data/aee_exp`、`/data/vendor/aee_exp` **均不存在** |
| uniview 活性 | `[init.svc.uniview]: [running]`；`debug.uniview.last_event_key_info` 仅 `event_time` 周期刷新，**`event_id` 恒为 `104001002`** |
| 诱发结果 | `am crash com.android.settings` 与 `kill -11 <pid>` 均 rc=0 → 产出 **`/data/tombstones/tombstone_01(.pb)`**（654/413 KB，触发窗口内出现）；**`/data/uniview*` 无任何新条目** |
| 现有实现判定 | `unisoc_reconciler.py:292` 只 `ls -1 <root>`（不递归），`:344/:348` 要求 `<root>/<name>/unievent_info.json`；`collectors/unisoc.py:28` 同 → 与真机层级不符 |
| 新增脚本单测 | `pytest backend/agent/tests/test_unisoc_probe_v101.py backend/agent/tests/test_unisoc_signal_trigger_v101.py -q` → **10 passed** |

> 该设备 mtime 不可信（文件显示 `2026-07-31`、目录显示 `1970-01-01`，真实为 09-14），因此判新旧只看**差分列表**，不看 mtime；这也印证仓库既有「设备时钟漂移」处理（`#785`）的必要性。

## Revisit

- **拿到真实 uniview 事件样本后再改 root/解析**：触发条件未知（用户态崩溃不进 uniview）。需在 **Z2582** 复核（当前可用展锐机全为 Z2581），或等 vendor/系统级事件自然出现。
- `/data/uniview/logs/tmp` 的消费时机（事件是否暂存后即被清）需**连续采样**确认。
- 本单正文的过时前提（"代码库 grep 展锐/UNISOC 0 hits"、`/data/unisoc_log` 假设）已在 issue 评论中更正；`AGENTS.md` 并无「AEE crash detection chain」章节，展锐说明暂落本 note。
