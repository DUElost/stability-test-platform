# 通知记录页对普通用户开放（#1196，R12-F10）

Status: implemented
Class: bug-fix

## Decision

通知铃铛向所有登录用户展示「查看全部通知」并链接 `/notifications?tab=logs`，
但该路由挂在 `AdminRoute` 下——普通用户点击被重定向首页，看不到通知历史。
后端口径早已区分（`backend/api/routes/notifications.py`：`logs` 端点
`get_current_active_user`、内容含主机名/序列号故仅要求登录；`channels` /
`rules` 配置仍 `require_admin`），是前端把两者捆在了同一个 admin 门后。

修正（最小前端改动，后端零改动）：

- 路由：`notifications` 从 `AdminRoute` 组移出，移入普通受保护路由区
  （`router/index.tsx`）；
- 页面：`NotificationsPage` 按角色收窄——非 admin 只渲染「通知记录」页签
  （`effectiveTab` 派生，URL 显式带 `?tab=channels` 也落回 logs）、页头改只读
  语义（「通知记录 / 平台通知历史（只读）」）、`channelsQ`/`rulesQ`
  `enabled: isAdmin` 不请求配置端点（免 403 噪音）；
- 管理员行为不变：三页签与配置能力原样（既有用例持续覆盖）；
- 权限底线仍由后端 `require_admin` 保证（纵深防御，前端收窄只是 UX）。

## Alternatives

- 按权限隐藏铃铛入口（验收的另一分支）：改动最小，但普通用户失去完整通知
  历史（铃铛下拉仅 8 条），且与后端「logs 是日常读写」的口径相悖。
- 新增独立只读历史路由/页面：重复页面与查询逻辑，收益仅命名清晰。
- 放开 `/notifications` 全部页签、靠后端 403 兜底：普通用户会看到报错的配置
  页——明确不做（issue 要求不放开配置管理权限）。

## Verification

- `npx vitest run src/pages/notifications/NotificationsPage.test.tsx` → 34/34
  （新增 3 例：普通用户只读且不请求配置端点 / `?tab=channels` 落回 logs /
  管理员三页签与配置语义不变）
- 红绿：未修复页面上 2 条新用例失败（管理员 guard 恒过）
- 全量前端套件 / type-check / eslint / build / `check:quick` 通过

## Revisit

- 当前通知记录为全局共享（read 标记也全局）；若未来需要按用户/角色过滤，
  属后端数据模型变更，另开需求/ADR。
