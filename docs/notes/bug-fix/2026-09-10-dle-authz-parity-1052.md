# DLE 归属一致性与状态迁移显式化（#1052）

Status: implemented
Class: bug-fix

## Decision

#1052（R09-R02）：DLE 摄入端点（`POST /agent/device-log-events`）的授权弱于
log_signal 路径——只有共享 Agent 密钥 + 部分 host/job 关系校验；且更新分支
`row.plan_run_id = ev.plan_run_id` **无条件赋值**，不带 `plan_run_id` 的合法
迟到 patch 会把归属清空（extract 作用域丢锚）；状态迁移任意（只有 #1174 的
extractable 降级保护）。

修复（不引入租约级强校验——issue 明确「不能简单禁止合法迟到补报」，租约绑定
依赖 R02/R03 的未定项，见 Note）：

1. **三元组一致性**：插入与更新都校验 `plan_run_id == job.plan_run_id`
   （二者非空时，400）——错误组合事件会让 extract 在错误 run 下取数；
2. **身份字段不可变**：更新时 `serial` / `job_id` / `plan_run_id` 与已入库行
   不一致 → 403（跨设备/跨任务混淆或错误 Agent）；
3. **归属保留**：`plan_run_id` 只在 payload 显式携带时更新——修掉 None 覆盖；
4. **状态迁移显式化**：`_ALLOWED_TRANSITIONS` 矩阵（来源 = Agent 生命周期
   LOCAL→UPLOADING→REMOTE/…、PULL_FAILED 重试直达 REMOTE、控制面
   UPLOAD_PENDING 标记），同态幂等；表外迁移 409 `DLE_INVALID_TRANSITION`；
   extractable 降级仍走 #1174 幂等成功分支（不破坏 Agent outbox ACK）；
5. **文档化可信边界**：设计规格新增「可信边界与状态迁移」节——共享密钥的
   半可信 Agent 模型、不可变身份、矩阵、未覆盖项（密钥轮换/控制面直写/内容
   鉴定）。

与 ADR-0033 的关系：无直接约束（Agent 侧摄取契约属 ADR-0028/0025 域）。

## Alternatives

- 对齐 log_signal 的 `_require_job_bound_upload_lease`（租约绑定）：直接照搬
  会拒掉合法迟到补报（Agent 重启/outbox 重放时租约可能已释放），且 R02/R03
  未定「未绑定事件授权」——本单用一致性校验收窄，租约增强留待 R02/R03；
- 状态迁移只文档不校验：验收明确「状态迁移显式化」，且 409 能让 Agent 侧
  错误路径尽早暴露；
- 完全禁用 extractable 降级（改 409）：会破坏 #1174 已定的幂等 ACK 语义。

## Verification

- `pytest backend/tests/api/test_agent_device_log_events.py`：9 passed，新增
  3 例——job/plan_run 错误组合 400 / 身份字段改写 403 + 不带 plan_run_id 的
  迟到补报归属保留 / 表外迁移（UPLOADING→LOCAL）409 + 合法路径
  （LOCAL→UPLOADING）不受影响；
- `test_read_api_auth.py` 等相邻套件 82 passed（读取端点无回归）；
- ruff 全绿。

## Revisit

- 租约/设备占有证明与「未绑定事件」授权通道：R02/R03 定项后另立单；
- 矩阵随 Agent 生命周期演进而更新——新增状态或路径必须先改
  `_ALLOWED_TRANSITIONS`（测试会暴露）；
- 存量 DLE 行中可能已有被清空 plan_run_id 的（旧 None 覆盖 bug 造成）——
  本单不清洗，如需对账另立数据脚本单。
