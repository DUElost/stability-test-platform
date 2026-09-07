# R02-D1/D4 落地：JWT sub=不可变 PK + refresh 消费即吊销（#900/#901）

Status: implemented
Class: bug-fix

## Decision

按设计 note [`docs/design/2026-09-08-session-identity-revocation.md`](../../design/2026-09-08-session-identity-revocation.md)
（PR #1001）落地 D1 与 D4，B2 拆单 A：

1. **D1 身份基底**：`_issue_token_pair` 的 `sub` 改为 `str(user.id)`，`username`/`role`
   降为信息性 claim；新增 `_user_from_payload(db, payload)`——`int(sub)` 解析失败
   （含存量 username-sub token）即拒，按 PK 查库 + `is_active` 校验。
   `get_current_user` 与 `/auth/refresh` 统一走该 helper。logout 审计与
   missing-jti 日志的 `username` 取 claim（sub 已是 id）。
2. **D4 消费即吊销**：`/auth/refresh` 成功路径在签发新对之前
   `revoke(旧 jti, reason="rotation")` + `refresh` 审计（与 revoke 同事务提交，#281
   纪律）；重放命中既有 `is_revoked` 检查被拒。
3. **硬切换（note §3）**：不设宽限——存量 username-sub token 在部署时全部 401，
   等价一次重登录；不保留任何「按 username 兜底查找」路径。

## Alternatives

- **存量 token 宽限兜底（username 查找保留至过期）**——放弃：存量 token 正是
  #900 冒充窗口的载体，宽限=保留攻击面（note §3 已裁）；
- **rotation 改为「新 jti 记 old_jti 关联」实现检测重放**——放弃：需要表结构与
  查询面扩展；消费即吊销复用现有黑名单，重放语义等价且更强；
- **本单顺带 D2/D3（token_version + 三面收敛）**——放弃：排期定案 A/B 拆单，
  D2 涉及迁移与 metrics/socket 接入，独立单评审（单 B 紧随其后）。

## Verification

- 目标回归 6 文件 **98 passed**（新增 4 用例：sub=PK 断言 / 存量 username-sub
  token 401 / 删建同名后旧 token 401 + 改名不换身份正确性断言 / refresh 重放 401）；
- 全量 `backend/tests/api` **865 passed**（覆盖 conftest `auth_headers`/
  `admin_headers` fixture 全部消费方）；
- 配套更新伪造 token 的测试站点：conftest 两 fixture、test_auth_cookie_session、
  test_action_templates（fixture 内先取 id）、test_plans_api（otheruser 先建后签）；
  test_security 为纯 codec 往返、test_refresh_token_blacklist 为 type-confusion
  拒绝路径，均不受影响；
- `check:quick` 7 门禁全绿。

## Revisit

- 单 B（#902/#903）落地 D2 `token_version` 迁移与 D3 `authenticate_access` 三面
  收敛后，`test_dashboard_auth.py:45` 的伪造 token（socket 面现仅 decode 校验）
  需同步改 id-sub 并建 alice 用户；
- 前端 refresh 已有 single-flight 防抖（审计 Frontend #5，client_strict.test.ts），
  rotation 并发竞态已被拦截，无前端改动需求；
- users 硬删除受 audit_logs FK 阻断（回归测试中实证，测试以先清审计行绕过）——
  #937/R03-F04 的在案缺陷，B3 处理。
