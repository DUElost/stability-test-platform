# R05 动态验证：Plan/Suite/参数编排归属套件隔离回归升「已完成」

Status: implemented
Class: process

## Decision

- 按 `PROJECT_REVIEW_PLAN.md` §5.1 转态条件，对 R05（#979）做与 R01–R04 同口径的
  **隔离环境动态验证**（控制面 testcontainers PG + Vitest）。
- **基线**：`96f00ca7`（验证开始时 worktree HEAD；叠在 R04 动态验证文档分支之上）。
- **结果**：归属套件 **104 passed / 0 failed**；容器巡检零残留。
- 同步把 §5 / §5.1 的 R05 行升「已完成」（与 R01–R04 并列；其余 10 区仍待验证）。

### F 项 ↔ 运行断言

| ID | Issue | 套件 | 条数 |
|---|---|---|---:|
| F01 | #882+#778 | `TestPlanSpecialtyOptional` | 5 |
| F02 / F10 / F12 / F13 | #965 / #973 / #975 / #976 | `test_suite_binding_gate.py` | 28 |
| F05 / F06 | #968 / #969 | `TestSuiteWriteBoundary` | 23 |
| F08 | #971 | PUT 停用在途守卫两例 | 2 |
| F14 | #977 | `test_script_params` + `TestSchemaDefaultSemantics` + `TestStepParamSchemaValidation` | 19 |
| F03 / F04 / F07 | #966 / #967 / #970 | Vitest PlanEdit + planEditUtils + TestSuiteDetail | 27 |
| F09 | #972 | 文案/徽章（无自动化断言） | — |
| F11 | #974 | 已接受延期（deferred） | — |

## Alternatives

- **整文件跑 `test_mtbf_suite_routes` / `test_plan_dispatcher`**：否决。非 R05 归属
  断言会稀释本区证据。
- **把 F09 徽章文案当缺陷重开自动化**：否决。issue 关闭口径是 copy 修正；磁盘级
  完整性另由 #973 Global 基线覆盖。
- **F11 共用 export_dir 强改行为**：否决。台账标注已接受延期。

## Verification

| 批 | 结果 |
|---|---|
| TestPlanSpecialtyOptional | 5 passed |
| suite_binding_gate | 28 passed |
| SuiteWriteBoundary | 23 passed |
| PUT deactivate guard | 2 passed |
| script_params + SchemaDefault + StepParamSchema | 19 passed |
| Vitest orchestration + suite detail | 27 passed |
| **合计** | **104 passed** |
| 容器巡检 | 零残留 |

## Revisit

- R05「已完成」不含浏览器目视与真 NFS 导出演练。
- F11 若产品裁决改为禁止共用 `export_dir`，另开实施单，不重开本台账。
- 下一区建议 R06（#996，调度主链）或继续按编号 R06。
