# R03-F04 落地：硬删除依赖预检（#937）

Status: implemented
Class: bug-fix

## Decision

三处硬删除入口无依赖检查，数据库 `IntegrityError` 最终以 500 返回（外键
保护数据、无静默误删，但错误不可归因）：

| 入口 | 阻塞依赖 |
|---|---|
| `users.py` delete | `audit.user_id` → users（无 SET NULL） |
| `hosts.py` delete | 历史 Job/Device/PlanRunHost；`step_trace.job_id` 无 ondelete 可阻断 |
| `resource_pools.py` delete | `resource_allocation.resource_pool_id` 非空引用 |

**hosts 路径同时是 #796 的静默清空面：FK CASCADE 会在「无 step_trace 阻断」
时把 job/device/plan_run_host 历史一起删掉。

修复（策略 = **有引用 409 保数据**，不做级联清空；与 #796 同向）：

1. `users` delete：有 `AuditLog` 引用 → 409，指引停用（toggle-active）；
2. `hosts` delete：活跃 Job（原有）+ **历史 Job / 设备 / PlanRunHost 投影**
   → 409，指引先归档/清理——不再静默 CASCADE 清历史；
3. `resource_pools` delete：有 `ResourceAllocation` → 409；
4. 三处 commit 均捕获 `IntegrityError` → rollback + 409（竞态兜底，保证
   「不裸 500」的验收在任何窗口成立）。

## Alternatives

- **audit.user_id 改 SET NULL（保留审计并允许删用户）**——放弃：需迁移且
  语义变成「匿名审计」；用户停用（is_active）已覆盖「禁止登录」需求，硬删
  收益低；
- **hosts 保留原 CASCADE、仅捕获 IntegrityError**——放弃：只治 500 不治
  静默清历史（#796 的在案问题），「删除成功统一级联」正是 issue 明令避免；
- **软删除三表**——放弃：方向级数据模型变更（须 ADR），超出缺陷修复范围。

## Verification

- **反例实证**：回退三路由保留测试 → 3 个「有依赖」用例失败（旧行为
  500/静默删）；修复版全绿；
- 新增用例（+5）：三域「有依赖 409 + 无依赖成功」；hosts 设备分支断言
  detail 含指引文案；
- 三文件全套 **49 passed**（含既有权限/可用列表回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- host 删除预检使「有历史的退役主机」不可硬删——运维需先清理关联
  （或未来引入 host 归档/软删，属 #796 统一裁决面）；若裁决改为「显式
  级联」需同步放宽本预检；
- users 停用路径已有（toggle-active）；审计匿名化需求若出现（GDPR 类），
  按独立单处理 audit FK 迁移。
