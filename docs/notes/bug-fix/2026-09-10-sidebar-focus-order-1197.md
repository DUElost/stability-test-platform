# 侧栏隐藏元素退出键盘焦点顺序（#1197，R12-F11）

Status: implemented
Class: bug-fix

## Decision

两类「视觉隐藏但仍在 DOM」的导航元素会留在 Tab 焦点序列：

- 移动抽屉（`AppShell.tsx`）：关闭时仅 `-translate-x-full` 位移，内部链接仍挂载可聚焦；
- 折叠分组（`Sidebar.tsx`）：用 `max-h-0 opacity-0` 动画折叠，链接同理。

修正：

- 抽屉关闭态整棵 `<aside>` 加 `inert`（打开移除）；折叠组内容容器
  `inert={isGroupCollapsed}`——`inert` 同时退出焦点顺序与辅助技术树，且保留
  折叠/位移动画（无需卸载重挂，避免动画中断与状态丢失）。
- 焦点返回：抽屉从开到关时（关闭按钮/遮罩/Escape 三条路径共用）把焦点还给
  头部触发按钮（`sidebarToggleRef`；`wasSidebarOpenRef` 守卫只在真实「开→关」
  迁移时聚焦，不在初始挂载抢焦点）。
- Escape 关闭与遮罩点击为既有行为，未改动。

## Alternatives

- 关闭时卸载抽屉子树：失去滑出动画，且每次开关重建 Sidebar（导航/折叠状态丢失）。
- 手动给每个 NavLink `tabIndex={-1}` + `aria-hidden`：侵入每层链接、遗漏风险高；
  `inert` 是平台能力，一处生效。
- 折叠时 `display:none`：杀死 max-height 过渡动画。

## Verification

- `npx vitest run src/layouts/` → 3/3（新增：Sidebar 折叠组 inert 用例；AppShell
  关闭态 inert / 关闭后焦点返回两用例。jsdom 不实现 inert 行为，断言属性存在
  性与「链接仍挂载」）
- 红绿：未修复源码上 3 条新用例全部失败
- 全量前端套件 / type-check / eslint / build / `check:quick` 通过

## Revisit

- jsdom 无法验证真实浏览器的 Tab 行为；如需更强保证，可在 e2e（Playwright）补
  「Tab 不进入关闭抽屉/折叠组」场景。
