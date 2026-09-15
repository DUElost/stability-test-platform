# ADR-0032 v0.9 落地：平台路由修订（R1–R4）写入 ADR 正文

Status: implemented
Class: bug-fix
Issue: #2192
关联：[`2026-09-15-adr0032-v08-platform-routing-revision.md`](../architecture/2026-09-15-adr0032-v08-platform-routing-revision.md)（提案原文）、[S14 门禁记录](./2026-09-15-adr-code-ref-version-gate.md)

## Decision

- 把 2026-09-15 的平台路由修订提案写入 ADR-0032 正文，**版本号取 v0.9 而非提案原文的 v0.8**。理由：v0.8 已由 `79d9e870`（#2183）以「B3 spike 已执行」占用并发布（ADR 头部 + `docs/adr/README.md` 主表 + M7 行三处已同步）；已发布版本不可原地改，也不应为让位而改 B3 的号——撞号会让同一版本号承载两条不同修订。
- 落地内容：**R1** 完备性单位由 host 改为 (host, platform) 期望集，B1 实现约束的旧函数名 `count_hosts_with_scan_artifacts` 同步为 `scan_completeness`；**R2** 逐平台 merge 结果（`ok`/`skipped_failed`/`no_input`）落 `run_context.merge_platforms`；**R4** 新增 §D9（a1 删 `PlatformCollector.detect()`、b1 控制面派生 `has_collection_impl`、b3 Agent 侧 `platform_reconciler_unsupported` 留痕）。
- **R3 不重复裁决**：提案曾把「同一 merge 工具」由断言降级为待验证假设并绑定条件裁决（B3 通过则维持，失败则引入 `STP_BACKEND_UNISOC_MERGE_*`）。B3 已于 v0.8 执行并通过，故在 D3 记「条件裁决已达成，D3 维持『同一 merge 工具』」，而不是把已验证的结论重新挂起——提案文本的前提已被 v0.8 推翻，照抄即为引入新的文档漂移。
- 同步面：ADR 头部状态、修订记录表、`docs/adr/README.md` 主表与 M7 行（S12 三处一致）；4 处代码注释由无版本号的止血态回填为 `ADR-0032 v0.9`（S14 门禁）。

## Alternatives

| 备选 | 放弃理由 |
|---|---|
| 沿用提案原文的 v0.8 | 与已发布的 B3 v0.8 撞号：同一版本号两条不同修订，正是本治理要防的漂移 |
| 把 B3 那条改号让位（v0.8.1 等） | 已发布版本不可原地改；改历史版本号会让 README、issue、既有引用失真 |
| 新立 ADR 承载平台路由修订 | 同一语义两份权威；提案已论证应走修订（先例 `docs/notes/process/2026-09-11-three-question-confirmation-e16d6d.md`） |
| 只回填注释、不动 ADR 正文 | 注释有版本号而 ADR 无对应裁决，「凭空引用」的根因仍在；S14 通过但语义照旧漂移 |

## Verification

- `python scripts/run_gates.py check:quick` → **[OK] 10 gates**；其中 `gov-surface` 报「阻塞项全绿：S1–S14、S5x」，含 S12 索引同步门禁与 S14 注释版本门禁。
- `venv/bin/python -m pytest backend/tests/api/test_plan_run_aggregation_endpoints.py backend/agent/tests/test_job_session.py -q` → **86 passed**。
- 前端 `npx tsc --noEmit` → 通过（exit 0）；`CI=true npx vitest run src/components/plan-run/AnomalyDashboard.test.tsx` → **11 passed**。
- 4 处注释（`backend/tests/api/test_plan_run_aggregation_endpoints.py`、`backend/agent/tests/test_job_session.py`、`frontend/src/utils/api/types.ts`、`frontend/src/components/plan-run/AnomalyDashboard.tsx`）引用版本 == ADR 头部版本 v0.9（S14 零红灯）。
- 门禁自身也捕获了本次的两处格式错误（新 note 缺 `Status` 头、`Class` 错位），修正后复跑通过——S10 在新 note 上确实生效。

## Revisit

- **S14 的语义粗糙**：S14 只比对「注释引用版本 == ADR 头部版本」，不认「该裁决属于哪一版」。头部 bump 到 v0.9 后，任何写 `ADR-0032 v0.8`（指 B3 spike）的新注释会被误判红灯。若这成为常态摩擦，S14 需从「比头部」升级为「比修订记录中是否存在该版本」。
- **R3 的残留风险**：B3 验的是**工具能力**（`-merge_files_list` 消费 15 列 UNISOC 输入并输出归一表头），**未**验 `merge/unisoc/` 的平台侧发布路径。若该路径端到端不通，D3 需按条件裁决的失败分支引入 `STP_BACKEND_UNISOC_MERGE_PYTHON` / `_SCRIPT`。
- **QCOM 若进入正式支持范围**（#73 落地）：§D9 的「未支持态」应改为正式 collector + reconciler 条目，并新增 QCOM 的 B5 分桶与验收。
