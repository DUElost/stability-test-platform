# 通知日志「已读 → 未读」反向切换（#626）

Status: implemented
Class: feature

## Decision

后端此前只有单向 `PATCH /api/v1/notifications/logs/{id}/read`（恒置 `read=true`），
前端 #623 的逐条「标为已读」误点后无法回退。按 issue 建议取「同 URI 加 body」方案：

1. **后端**（`backend/api/routes/notifications.py`）：新增局部 `MarkReadIn`（Pydantic v2，
   `read: bool = True`），端点签名加 `payload: MarkReadIn | None = Body(default=None)`：
   - body `{"read": true}` → 标已读；`{"read": false}` → 恢复未读；
   - **无 body → `read=True`**，保持既有调用方（NotificationBell、旧前端包）语义不变；
   - 响应仍为 `{"ok": True}`，不改既有契约；404 / 鉴权路径不变。
2. **前端**：`api.notifications.markRead(id, read = true)`（默认参数保证既有调用点零改动）；
   列表页已读卡片新增「标为未读」按钮，已读/未读两个按钮互斥渲染，共用 `markingId`
   禁用态与 invalidate 刷新；错误 toast 按动作区分「标记已读/未读失败」。
3. **不做批量反向**（issue 边界）：没有「全部标为未读」入口，避免误操作扩大化。

## Alternatives

- **新增 `PATCH .../unread` 端点**（issue 给出的第二选项）：弃——同一资源状态用两个
  URI 表达会让「读状态」这一语义分裂，且前端需要两套调用；issue 亦推荐前者；
- **body 必填（`MarkReadIn` 不带默认）**：弃——会让现有不带 body 的调用方 422，
  属破坏性契约变更；`Body(default=None)` + 缺省 True 保持向后兼容；
- **前端只传 `{read:true}` 的新契约、不做兼容**：弃——`NotificationBell` 与新列表页
  都在同一发布包内，理论可一起改，但无 body 兼容的成本只有一行默认值，保留它能防
  「旧标签页前端 → 新后端」的混合窗口；
- **顺带在通知铃铛下拉也加「标为未读」**：弃——不在 issue 范围，且铃铛交互以快速
  清未读为主，反向入口放列表页足够。

## Verification

- **红绿对照（后端，真实 PG testcontainer）**：
  `test_mark_read_accepts_explicit_unread_and_defaults_read` 覆盖
  「显式已读 → 显式未读 → 无 body」三段并各验 DB 落值：
  - 还原旧端点（`git show HEAD:...`）→ **1 failed**（`read` 仍为 True）；
  - 本 PR → **24 passed**（该文件全量）；
- **前端**：`NotificationsPage.test.tsx` 新增「已读卡片可标回未读」（断言
  `markRead(7, false)` 且 invalidate 触发重取）与「未读卡片仍传 `read=true`」，
  `npx vitest run src/pages/notifications` → **36 passed**；全量
  `npx vitest run` → **758 passed**（102 files）；
- `npm run type-check` → 通过；`npx eslint src --max-warnings 0` → 通过；
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`。

未做：浏览器内真机点按（接口与按钮行为已由页面级测试覆盖）。

## Revisit

- **`/read` 端点语义已从「标记为已读」扩为「设置已读状态」**：将来若加
  `read_only` 查询参数或批量状态设置，应评估是否改成 `PUT .../read-state`
  或并入通用通知状态端点；当前形态在 OpenAPI 文档里仍描述为 mark_read，
  首次对外（非本仓前端）暴露前建议重命名为 `set_read_state`；
- **审计缺失**：标记已读/未读不写 audit（现状即如此，属读状态非业务变更），
  若将来有合规要求需要补 `record_audit`；
- **批量反向**按 issue 边界未做；若真实出现「误点全部标为已读」的恢复诉求，
  应新开 issue 并单独裁决（涉及误操作面扩大）。
