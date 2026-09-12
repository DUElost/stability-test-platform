# types.ts ↔ backend schema 漂移收口（#787）

Status: implemented
Class: bug-fix

## Decision

按「前端 API 类型以 `types.ts` 为入口并与后端 schema 同步」硬不变量，逐项
核对 issue 清单**以现值代码为准**（部分断言与 2026-09-03 时点已有差异），
收口如下（`frontend/src/utils/api/types.ts`）：

1. **`PlanRunAbortResult`**：删除 6 个幽灵键（`pending_aborted_job_ids` /
   `running_abort_requested_job_ids` / `quarantined_job_ids` /
   `released_lease_count` / `aborted_pending_count` / `drained_running_count`）
   与无后端来源的 `abort_requested`；改为后端两分支并集的权威 6 键
   （`plan_run_abort.py:206/531`：`plan_run_id/status/phase/aborted_jobs/
   abort_requested_jobs/released_leases`）；
2. **`ScriptEntry.capabilities?: string[]`**：后端
   `routes/scripts.py` 已产出（供停滞钟等能力提示）；
3. **快照/生命周期缺键**（后端 `plan_dispatcher_core.py` 两处产出）：
   - `PlanSnapshot.plan` 补 `barrier_timeout_seconds` /
     `barrier_max_wait_seconds`（`build_plan_snapshot` 512-513）；
   - `PlanSnapshotStep` 补 `params`（#508 固化合并参数，537）与
     `stall_seconds`（541）；
   - `PipelineLifecycle` 补同名 barrier 两键（pipeline_def 组装 254-258）——
     **barrier 键在后端有两个落点**，前端两侧都已对齐；
4. **`JiraRunRecord.jira_project_key?: string | null`**：后端
   `schemas/jira_run.py:19` 起返回（当前无消费方，消除潜伏漂移）；
5. **Ai 可空字段**：`AiAssistantConfig.updated_at`、`AiChatSession.updated_at`
   （`AiAssistantConfigOut/AiSessionOut.updated_at: datetime | None`，32/61）与
   `AiAssistantAction.requested_by`（`AiActionOut`，85）改为 `?: … | null`。

（核对修正：`PipelineStep` 的 `params`/`stall_seconds` 现值**已存在**
（879-891，编辑器透传注释）；真缺在快照专用类型与 lifecycle/plan 的
barrier 键——按现值收口。）

## Alternatives

- **保留幽灵键并标注 legacy**——放弃：无后端来源即死声明，`toBeDefined`
  式安全感的来源；删除后 tsc 零错误证明无消费点；
- **新增跨语言契约测试（TS ↔ Pydantic 自动对拍）**——本单缓：跨语言解析
  成本高；现有兜底（tsc 编译 + knip 未用导出 + PR 评审逐项来源注明）已
  闭合本轮；机制化留 Revisit；
- **改后端对齐前端**——不适用：后端为权威 schema。

## Verification

- 每项均注明后端权威来源（文件:行，见 Decision）；`PipelineStep` 等
  issue 断言与现值差异已如实修正；
- `tsc --noEmit` **零错误**（幽灵键确无消费点，纯死声明收口）；
- 相关前端测试（PlanRun 详情 + assistant 系）**33 passed**；
- `check:quick` 7 门禁全绿（含 tsc/knip/eslint）；
- test_impact=none（纯类型声明，无运行时路径）。

## Revisit

- 契约测试兜底（类型 ↔ Pydantic 自动对拍）机制化：可作为 R15 门禁批的
  候选（当前靠 tsc + 评审）；
- `AiPendingAction.requested_by` 现为 `string | null`（非 optional）——
  后端字段总输出（None 也发键）则合理；若后端改 exclude_none 需同步。
