# 前端三项待议体验修复落地：Schedule 设备多选 / 审计 details / 通知逐条已读+跳转

Status: implemented
Class: feature

## Decision

`2026-08-31-frontend-ui-polish-batch.md` 收尾时遗留 3 项「功能迭代级」待议，本次全部落地（PR 从 `feat/frontend-ui-polish` 续分出）：

1. **定时任务设备 ID 手填 → 多选选择器**
   `frontend/src/components/schedule/DeviceMultiSelect.tsx`：chips + 下拉 checkbox 列表 +
   serial/型号过滤。`ScheduleForm.device_ids: string` → `deviceIds: number[]`；手填
   通道及 `parseDeviceIds` 删除（无兜底输入口——ID 不在设备列表即不可选，校验逻辑
   简化为「至少一台」）。
2. **审计日志 details 展开视图**
   `AuditLogPage` 行首加 chevron 开关（仅 `details` 非空时渲染），展开行为该列下方
   `<pre>` 全width JSON；行内原有列不变。数据本就由 `AuditLogOut.details`
   返回（此前前端未展示）。
3. **通知逐条已读 + 上下文跳转**
   - `pages/notifications/notificationTarget.ts`（新增纯函数）：`RUN_COMPLETED/
     RUN_FAILED/RISK_HIGH` 且 `context.run_id` 为合法正整数 → `/execution/plan-runs/:id`；
     `DEVICE_OFFLINE` → `/devices`；其余 null（Alertmanager 路由的 labels/annotations
     不映射前端路由，有意不猜）。
   - 通知中心日志卡：未读时加「标为已读」按钮（`PATCH /notifications/logs/{id}/read`），
     可跳转时加外链式行内入口。
   - `NotificationBell` 下拉条目：即点即已读（`markRead` fire-and-invalidate）+ 有可跳转
     目标则 navigate；目标标题进 `title` 提示。
   - 已读状态源统一走 `read` 字段 + 两处 invalidate（`notification-logs*` 与
     `notification-unread-count`），两侧抽屉不会互踩。

## Alternatives

- 通知单条目点击不跳转的可行方案：「单独加跳转箭头按钮」。选点击整条是参照
  常见 IM/邮件中心交互；`title` 提示已标注语义，避免误跳转。
- Schedules 曾考虑「多选 + 手填并存」，但并存会保留 ID 输入错误的回归面
  （审计页面 #60 报错原因）；选型器价值之一正是砍掉这个出错口。

## Verification

- `tsc / eslint / vitest run`：643/643（含 NotificationBell 既有测试）全绿；
- BroadcastChannel/WS 未改，仅复用 `notification-new` invalidate 链路。

## Revisit

- 若后续要 `unread` 标记恢复（已读→未读），后端 `PATCH /logs/{id}/read` 需要加
  `unread` 变体或 `PUT {read: bool}`。
- Alertmanager 通知若能可靠映射到 PlanRun（labels 内注入），`notificationTarget`
  可直接扩一条映射。
