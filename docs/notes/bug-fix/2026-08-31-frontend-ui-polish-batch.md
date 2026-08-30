# 前端 UI 只读核查后的一批界面修复

Status: implemented
Class: bug-fix

## Decision

一次只读核查（逐页审查 20+ 页）后的批量修复，均为纯前端、无 API/状态机变化：

1. UserMenu `/docs` 入口此前走 SPA `Link`，前端路由无此 path → 404 页。`UserMenuItem`
   新增 `external?: boolean`，外链用 `<a target="_blank">`；AppShell 的「文档」目标为
   后端 Swagger（`STP_API_DOCS_ENABLED` 缺省开启，main.py:250）。
2. 脚本库展开区 `JSON.stringify(..., null, 2)` 曾渲染进 `<code>`，换行缩进全塌；
   改为 `<pre>`（`overflow-x-auto`）。
3. AssistantPage 移动端无会话操作入口（SessionList `hidden md:flex`）：新增
   `md:hidden` 的紧凑会话切换条（原生 `<select>` + 新建按钮）。同一会话标题在
   DOM 出现两份，对应测试改用 `findAllByText`。
4. UserModal / AddHostModal / ScriptVersionDialog 由手写 `MODAL.overlay/panel`
   迁移到 Radix `Dialog`（焦点圈定、ESC、滚动锁、`max-h-[85vh]` 溢出滚动均由
   ui/dialog.tsx 统一提供）。design-system `MODAL` token 保留（WifiPage 仍用
   `MODAL.closeButton` 样式）。
5. PlanRun 终态归档询问框文案去掉硬编码「从 15.4 取事件日志」→「从中心存储」。
6. Dashboard 尾行布局：「主机在线率」卡改为整行横条（`md:col-span-2` + 行内左右排布），
   消除尾随半格空位。
7. `StatusBadge` sm 档 10px→11px（`px-2 py-0.5`），图标 10→11；`dedupActionBtnClass`
   10px→11px。其余 `text-[10px]`（18 个文件、55 处 pill chip/行内元数据）全量升到
   11px，与 sm 徽标基线对齐；唯一例外是 NotificationBell 的未读数红点角标
   （18px 圆形、「99+」临界，升号会折行），保留 10px。
8. `index.html`：`<title>` 从英文改为「稳定性测试平台」，favicon 由青色 #0f766e
   调为主题蓝 #3b82f6（与 `--primary` 217 91% 60% 对齐）；侧栏占位品牌
   「北极星目标」→「稳定性测试平台」，与登录页/标题一致。
9. Plan 列表行操作按钮 `md:opacity-0 md:group-hover:opacity-100` 曾使键盘 Tab
   聚焦时仍透明；补 `md:group-focus-within:opacity-100`（依赖 Card 上 `group`）。
10. 问题追踪草稿卡由纯 div onClick 改为 `role="button"+tabIndex+Enter/Space`；
    描述摘要仅超 100 字才追加省略号。
11. 用词统一：Schedules「N devices」→「N 台设备」；审计表格 action 列按
    `ACTION_LABELS` 中文化（与筛选下拉词表一致）。
12. 通知规则表单无渠道时，渠道下拉禁用+占位「暂无可用渠道」+ hint 引导先建渠道；
    设置页副标题「管理平台全局配置」→「平台运行配置一览（当前为只读视图）」。
13. 清死代码：PlanExecutePage 同值三元 `'overflow-hidden' : 'overflow-hidden'`。

未做（保留现状的理由）：
- 定时任务设备 ID 手填、审计 details 展开——属功能迭代，已反馈为待议。

## Alternatives

- A4 曾考虑给手写 MODAL 补 ESC/焦点锁（成本更低），但三处弹窗各自补丁 vs 一次
  迁移到已有 Radix Dialog：后者统一行为、删除 `MODAL` 旧径更彻底；WifiPage 留
  `MODAL.closeButton` 说明 token 仍在用，不做删除。
- B6 曾考虑删除「主机在线率」卡并入 KPI 行；但那是仪表盘既有信息位，改布局为
  整行横条改动最小。
- B7 曾考虑仅收徽标源头；细查后 10px 全是 chip/元数据、密度不降，且与 sm 徽标
  11px 不统一更刺眼，故全量收口（红点角标例外）。

## Verification

- `npx tsc --noEmit`、`eslint src --max-warnings 0` 干净；
- `vitest run` 全量 88 文件 / 643 用例通过（AssistantPage.test 断言随 A3 调整）；
- `vite build` 通过。

## Revisit

若后端关闭 `STP_API_DOCS_ENABLED`，「文档」外链会 404，届时改为隐藏该入口或
指向产品文档站。
