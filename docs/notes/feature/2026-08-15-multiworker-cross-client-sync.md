# 多Worker：多人多端协作（审计补齐 / 跨端同步 / 并发写防护 / 账号补强）
Status: implemented
Class: feature

## Decision

支持 2-5 人按人分配账号、多台电脑浏览器同时访问控制面 Web UI 且状态一致。
基于只读审计（账号体系/会话安全/部署可达性已确认完备）落地四组改动：

- **B1 审计补齐**：auth.py（register/login/login_failed/token/logout/refresh_rejected）、
  users.py（CRUD/toggle/change_password）、plans.py（create/update/delete）补
  `record_audit`；用户更新审计只记字段名不记值（防密码明文落审计）。
- **B2 跨端同步**：① `notification:new` 此前服务端裸发字段、前端白名单缺它
  → 服务端改信封格式 `{type,payload,timestamp}` + 前端白名单补齐，通知铃铛
  恢复实时；② 新增 `plan_changed` 广播（create/update/delete 后 emit），
  AppShell 全局失效 plans/plan 缓存——此前另一浏览器陈旧最长 60s+；
  ③ 后台 tab 恢复可见时全量失效缓存（visibilitychange 追平）；④ 删除死事件
  `job_update`（服务端无任何 emit）。
- **B3 Plan 并发写防护**：`PlanUpdate.expected_updated_at` 乐观锁（不一致 → 409）
  + PG 行级 `FOR UPDATE` 串行化 step 全量替换；前端保存携带加载时的
  `plan.updated_at`。此前 last-write-wins。
- **B4 账号补强**：`backend/core/login_lockout.py` 每账户失败锁定
  （默认 5 次 / 300s 窗 / 900s 锁，`STP_LOGIN_*` 可配），挂进
  `_authenticate_user`（login 与 /token 共用）；密码最小长度前后端对齐为 8。

## Alternatives

- 只做账号不做同步：拒绝——审计确认同步缺口是真实的用户可见 bug
  （通知断链、Plan 跨端陈旧）。
- 乐观锁用版本号而非 updated_at：拒绝——updated_at 已存在且由 ORMBaseModel
  序列化为 ISO-UTC，零 schema 成本。
- 每账户锁定落 DB：拒绝——2-5 人内部环境，进程内即可（多 worker 需挪 Redis，
  与 #91 同类局限，模块注释已声明）。

## Verification

- 本地：check:pr 8 门禁全绿（1018 agent tests，含 5 条 lockout 新测试）；
  vitest 74 files / 468 tests 全绿；
- 后端 409 行为：backend/tests/api/test_plans_api.py 新增
  `test_update_plan_optimistic_lock_409`（PG 套件，夜间全量 CI 覆盖）。

## Revisit

多 worker 进程部署时：login_lockout 与 limiter 同步迁移 Redis（#91 同批）。
