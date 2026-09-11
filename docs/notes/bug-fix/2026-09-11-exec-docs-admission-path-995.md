# R06-F10 落地：权威执行文档同步 admission 现行路径（#995）

Status: implemented
Class: bug-fix

## Decision

`01-execution-pipeline.md` 与 `07-execution-protocol.md` 仍把 SCHEDULE/CHAIN
描述为 inline/sync gate、子 Run 推进归于 SAQ `precheck_and_dispatch_task`：
这是 ADR-0021 时代的主路径描述。ADR-0026 落地后现行主路径为
**`prepare_plan_run` → PlanRun(QUEUED) → admission pump claim(PRECHECK) →
`plan_admission_task` 单事务物化 Jobs**——旧描述会误导故障定位与补偿操作
（可能被误读为 ADR-0026 尚未实现）。

修复（文档同步到代码事实）：

- `01-execution-pipeline.md`：
  - 顶部流程图改为「派发 prepare(QUEUED) → 准入 claim/慢验证 → 物化 Jobs」；
  - §2 触发表三行（MANUAL/SCHEDULE/CHAIN）统一指向 `prepare_plan_run`
    收口 QUEUED + admission 物化，并标注触发链各自入口
    （`dispatch_plan`→`dispatch_plan_sync`、`plan_chain_trigger`）；
  - 明确历史 ADR-0021 sync gate（`precheck_and_dispatch_task`）仅存于
    precheck_reaper V1 兜底与显式重试路径；§3 标注现行执行者为 admission
    Phase A（门禁 phase 语义不变）。
- `07-execution-protocol.md` §6：子 Run 经 `prepare_plan_run` 落 QUEUED +
  admission 物化。

事实核对（以代码为准）：`dispatch_plan_sync` docstring/实现（1033/1053 行）
「Creates a QUEUED PlanRun; the admission pump materialises jobs」；
`plans.py` MANUAL 与 `plan_chain_trigger` 的 CHAIN 均直调 `prepare_plan_run`。

## Alternatives

- **保留旧表述并加「历史」标记**——放弃：主路径描述必须是现行事实，历史
  路径只在明确处（兜底/重试）提及；
- **改写 ADR-0021/0026 文档**——放弃：两者是已裁决 ADR，不改历史记录；
  设计文档（01/07）承担现行状态描述职责。

## Verification

- 事实核对：`grep` 三条触发链末端均为 `prepare_plan_run`（QUEUED）；
  `dispatch_plan_sync` 内部调用与 docstring 一致；
- 残留漂移词检查：两文件仅剩「历史路径说明」处的有意提及；
- `check:quick`（含 go-surface 文档结构/链接检查）：见 PR；
- 本单 `test_impact=none`（纯文档，无代码行为变更）。

## Revisit

- ADR-0026 队列退役或再演进时，同步更新本两处（尤其流程图与 §2 表）；
- `01` 文档 §3 precheck phase 表与 `DispatchGateCard` 前端文案的对应关系
  未在本单核对（超出 #995 范围；若 phase 语义再变需一并同步）。
