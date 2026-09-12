# HddSpill 未达 target 时追打（#1522）

Status: implemented
Class: bug-fix

## Decision

`HddSpillMonitor` 的净腾退能力原来被固定为 **20 目录 / 300 秒 = 4 目录/
分钟**：`_MAX_SPILL_PER_CYCLE=20`（单批）叠加 `_interval=300`（轮询），
`splilled` 未达 target 也一样等满下一轮。事件产生速率超过 4 目录/分钟
（大规模崩溃现场、多设备同时）时水位单调爬升——与 H-04（NFS 侧不清理）
合流为盘满 → 新事件无法落盘 → 上送链中断。

修复（单批节流保留 + 追打提速）：

1. `check_once` 用尽单批上限（`for ... else`）且水位仍高于 target →
   `_catchup_needed=True` 并 WARNING 留痕；
2. `_run` 按 `_next_wait_seconds()` 等待：追打期用
   `_SPILL_CATCHUP_INTERVAL`（env `STP_HDD_SPILL_CATCHUP_INTERVAL`，默认
   30s；≤0 回退常规间隔=禁用追打的逃生阀），回落后恢复 `_interval`；
3. `_MAX_SPILL_PER_CYCLE` 注释写明「兼作 HDD 写放大节流」的意图与追打
   关系（#1522 备注要求，避免被误读为可随意调大的参数）。

**未采用** issue 另一建议「`_spill_enqueued_ids` 跨轮保留」：该集合语义是
「已成功 enqueue」，跨轮保留会让**上传失败仍为 LOCAL** 的事件被永久跳过
（饿死）；每轮 clear 下的重复遍历由 EventUploader `_active_ids` 去重吸收，
不占单批名额——实测候选循环对 active 事件继续找下一个可 enqueue 项。

## Alternatives

- **直接调大 `_MAX_SPILL_PER_CYCLE`**——放弃：若 20 是写放大保护（issue
  备注的存疑点），调大即放弃保护；追打不改单批写放大，只提高频次；
- **动态计算单批上限（按积压/水位差额）**——放弃：磁盘水位是全局量，估算
  单目录字节不可靠；「未达 target 就继续打」是水位驱动的正确闭环。

## Verification

- **反例实证**：回退 local_disk_monitor 实现保留测试 → 2 用例失败（无追打
  语义）；修复版全绿；
- 新增用例（`test_local_disk_monitor.py` +2）：单批上限触发（spill 调用
  恰 20 次、catchup=True、`_next_wait_seconds()==30`）/ 回落到 target 内
  恢复常规间隔（catchup=False）；
- `backend/agent/tests/` 全套 **1667 passed**（2m35s）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- H-04（NFS 侧不清理，控制面）未在本单——issue 明示「只修一端不能解决
  盘满，应同批处理」；本单收 Agent 侧（H-05），控制面侧在 #1515 台账。
- 追打期 CPU/IO 观测：默认 30s 间隔 + 单批 20 的节奏对 HDD 影响未实测，
  若出现抖动可经 env 调大 catch-up 间隔或禁用。
