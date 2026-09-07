# R02-D2/D3 落地：token_version 会话纪元 + 三面校验收敛（#902/#903）

Status: implemented
Class: bug-fix

## Decision

按设计 note [`docs/design/2026-09-08-session-identity-revocation.md`](../../design/2026-09-08-session-identity-revocation.md)
（PR #1001）落地 D2 与 D3，B2 拆单 B（接单 A #900/#901，PR #1016）：

1. **D2 会话纪元**：`users.token_version INTEGER NOT NULL DEFAULT 1`（迁移
   `n4o5p6q7r8s9`，常量 server_default 走 PG 11+ 元数据级 ADD COLUMN）。
   access/refresh 签发携带 `ver` claim；`resolve_payload_user` 比对
   `ver != user.token_version` 即拒。bump 点三处：自改密
   （users.change_password）、admin 更新（password/role/is_active 任一变更）、
   admin toggle-active——改密后本会话在内的全部在发 token 立即失效，
   重新启用后旧 token 亦不复活。
2. **D3 单一校验面**：新建 `backend/services/auth_session.py`
   （`resolve_payload_user` + `authenticate_token`），REST `get_current_user`、
   `metrics.verify_metrics_access`（Bearer 分支，补 get_db 依赖）、
   Socket.IO dashboard 握手（sync SessionLocal）三处全部收敛——此前
   metrics/socket 仅签名级 decode，停用/删除用户 token 到 exp 前全通（#903）。
   静态 ws token 分支（机器对机器）不变。
3. 硬切换：无 `ver` 的存量 token（含单 A 部署后签发的无 ver token）一律拒绝
   （note §3）。

## Alternatives

- **按用户逐 jti 吊销实现改密失效**——放弃：需要 user 索引的黑名单表结构；
  ver 纪元一列即可，且同时覆盖 access token（黑名单只覆盖 refresh）；
- **Socket.IO 侧自建校验**——放弃：第三种强度正是 #903 的根源；
- **bump 放在 login 时比较而非显式递增**——放弃：隐式纪元不可审计，显式
  bump 点与审计动作一一对应。

## Verification

- 目标回归 **51 passed**（新增：改密后旧 token 401 + 新密码可登录/旧密码被拒、
  admin 停用后旧 token 401、无 ver 存量 token 401、metrics 拒停用用户（直改 DB
  隔离 is_active 检查）、metrics 拒旧纪元 token、socket 拒停用/旧纪元 token、
  socket 接受真实活跃用户 token）；
- 全量 `backend/tests/api` **870 passed**（conftest fixtures、action_templates、
  plans_api 的伪造 token 站点补 `ver` 后无回归）；
- **隔离 PG 迁移链验证**：scratch PG16 全链 `upgrade head` 至
  `n4o5p6q7r8s9`，`\d users` 确认 `token_version integer not null default 1`；
- `check:quick` 7 门禁全绿。

## Revisit

- 迁移 revision id 与并行会话两次撞号（l2m3…被 #908 线 harden 中游占用、
  m3n4…被 merge revision 占用）——最终定 `n4o5p6q7r8s9`/down=`k1l2m3n4o5p6`；
  并行开迁移单时应先 `sd.get_heads()` 核对再选号；
- Socket.IO 存量连接不受 bump 影响（断线重连重新握手后收敛）——设计 note
  §6 已知边界维持；
- `users` 硬删除 FK 阻断（单 A 实证）→ #937，B3。
