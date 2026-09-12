# God-module 首个垂直切片：manual retry/exit（#1520）

Status: implemented
Class: bug-fix

## Decision

issue #1520 明确「渐进拆分、按垂直切片、每次一条业务线、不建议一次性大
重构」。本单交付**首个切片**：把 `plan_runs.py` 的「手动重试 / 手动退出」
（ADR-0022 D7）业务线抽到 `services/plan_run_manual.py`，路由退化为
「解析 → 调服务 → 序列化」：

- `manual_retry_job_sync` / `manual_exit_job_sync`：承载状态校验（409）、
  设备可达性门禁、`manual_action` 迁移、审计、`record_patrol_manual_action`
  指标、失效事件 emit 与事务提交；
- 路由两端点变薄壳（docstring 指向服务；序列化保留在 API 层——schema
  不跨层进 services）；
- 异常沿用 `HTTPException`（与 #1519 下沉符号同一直例面）；
- 幂等短路语义原样保留（同向 manual_action 不二次写审计/emit）。

**续期**：issue 保持 open 作为持续工程台账；下一批切片候选（agent_api /
projects.py 各业务线）按同一模式推进；#1519 的分层门禁已保证拆分不会
回流。本 PR 以 `Refs #1520` 关联。

## Alternatives

- **一次性重写 3,328 行 plan_runs.py**——放弃：issue 明示风险远大于收益；
- **按层拆（先抽所有 DB 查询）**——放弃：issue 指定垂直切片（按业务线），
  按层拆会产生半成品抽象与跨线回归面；
- **服务返回 pydantic Out**——放弃：schema 属 API 层，服务返回 ORM/数据、
  路由序列化，保持依赖方向（services 不 import api.schemas）。

## Verification

- 端点行为回归：`test_manual_retry_exit_api.py` **17 passed**（含幂等短路、
  断连 409、审计/emit 既有断言）；
- **切片价值直证**：新增 `tests/services/test_plan_run_manual.py` **3
  passed**——不经 HTTP 直测业务分支（非 RUNNING 409 / 迁移与审计落库 /
  幂等短路只写一条审计）；
- plan_run/job 相关 API 子集 **222 passed**（73s）；
- `check:quick` 7 门禁全绿（含 tsc/knip/compileall；ruff 自动清理路由侧
  随切片失效的 import）；
- 测试踩坑记录：`audit_logs.resource_id` 为 varchar（#832 在案），直测
  查询按 `str(job.id)` 比较。

## Revisit

- 下一批切片候选（按收益排序）：`agent_api.py` 的 complete/recovery 段、
  `projects.py`（0 service——业务规则全在路由）的归属/映射线；
- 路由文件行数目标：本切片后 `plan_runs.py` 约 -110 行（3,266 起点）；
  后续切片按 issue 分期继续；
- `_iso` 等序列化辅助仍在路由；若未来多切片共用，可再评估其归属（API 层
  保留为默认）。
