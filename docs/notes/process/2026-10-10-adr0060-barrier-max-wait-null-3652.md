# ADR-0060 v1.1：barrier_max_wait_seconds NULL 继承主机策略（#3652）

Status: implemented
Class: process

## Decision

按 [#3652 Owner 裁决](https://github.com/DUElost/stability-test-platform/issues/3652#issuecomment-6095035247) 只改文档与注释，不改 Agent / env / DB / 状态 / 回报协议 / B5 Appetite。

- ADR-0060 升 v1.1：D3-1 / D3-2 与 §5 边界反例把 `barrier_max_wait_seconds` 未设从 `unset_definite`（无硬顶）改为 `env_fallback`（Plan → `STP_BARRIER_MAX_WAIT_SECONDS` → 1800s；非法 env 回落 1800s；非正值 env = 无硬顶）。L1 / L2 不得把 1800s 写成已确认运行期实际值。
- 同步 `docs/adr/README.md` 索引行。
- 对齐仍写「NULL = 不设上限」的 Plan 相关注释：`backend/models/plan.py`、`frontend/src/utils/api/types.ts`（Plan / PlanCreate / PlanUpdate）。
- 事实依据（只读证据，不授权改 Agent）：`pipeline_engine._default_barrier_max_wait_seconds`（#872）；B5-U1 登记表已按现行代码投影 `env_fallback`（`backend/schemas/plan_settings.json`）。
- 避开开放 draft #3667（`plans.py` / `plan_runs.py` 等）；`plans.py` 内仍写「None = 不设上限」的 Field 注释本 PR 不碰，留待不冲突时另改。

## Alternatives

- 顺手改 Agent / dispatcher 注释或运行时语义：Owner 明确禁止；会扩大 Appetite。
- 改 `plans.py` Field 注释：与 #3667 同文件冲突，本批跳过。
- 把 `plan_settings.json` 里「与 ADR-0060 原文冲突」过渡句一并删掉：登记表语义已正确，属 B5 文案收口，不在本 docs PR 范围。

## Verification

- 前检：开放 PR 仅 #3667；本 PR 文件集与其无交集。
- `rg 'barrier_max_wait_seconds 未设 = 无硬顶|unset_definite.*barrier_max|NULL = 不设上限' docs/adr backend/models frontend/src/utils/api/types.ts`：无命中（对齐后）。
- `./scripts/project_python.sh scripts/run_gates.py check:quick`：见 PR 正文实测结果。

## Revisit

- `backend/api/routes/plans.py` Field 注释仍可能写「None = 不设上限」；#3667 合入或错开后另开小 PR。
- `plan_dispatcher_core` / `pipeline_engine` 中与「无硬顶」相关的历史注释若继续误导读者，可在不改行为的前提下另批对齐。
- #3652 文档合入后，再按实际证据收口 issue；本 PR 不得关闭 #3652、不得 auto-merge。
