# WiFi 资源池入口按角色隐藏（#2360，裁决 A）

Status: implemented
Class: bug-fix

## Decision

**用户裁决：A「入口按角色隐藏」**（对齐 `/storage` 的既有惯例）。现状 C「可见但必 403」
是最差组合：`/wifi` 的列表/详情/loads 全部 `require_admin`，而侧边栏对非 admin 照常
展示入口、路由也没走 `AdminRoute`——点进去得到满页 403。

落地**两处成对**（只改一侧会留下另一半）：

1. `frontend/src/layouts/navItems.ts`：`/wifi` 加 `adminOnly: true`（`Sidebar` 已按
   `isAdmin` 过滤该标记）；
2. `frontend/src/router/index.tsx`：`<Route path="wifi">` 从普通受保护区挪进
   `<Route element={<AdminRoute />}>` 块——直连 URL 也按 admin 门控（重定向首页），
   与 `/storage` 同形态。

**未做**（B 方向的代价，留痕）：没有放开任何后端只读接口。若将来业务上确需 user 查看
资源池，那要先逐接口判定可暴露字段（池里含连接信息），属独立决策。

## Alternatives

- **B「放开只读列表给 user」**：用户否决。需要先做字段级暴露判定（资源池条目含网络
  参数），且与「谁能建测试设备」的权限面耦合——不是一条可见性单能定的。
- **只隐藏入口（不挪路由）**：否决。直连 URL 仍是满页 403，问题只解决了一半；
  `/storage` 的先例两处都有，本单与之对齐。
- **只挪路由（不隐藏入口）**：否决。非 admin 点入口被重定向回首页，观感是「点了没反应」，
  比 403 更费解。
- **在页面内按角色降级渲染（隐藏操作按钮）**：不在本单。页面数据本身拿不到（403），
  降级渲染等于空壳页；要做先得回到 B。

## Verification

- **红绿差分**：四条新守卫在基线实现上**全红**——
  `/wifi 与 /storage 都在 AdminRoute 块内`（`expected '<Route element={<AdminRoute />}>…'
  to contain 'path="wifi"'`）、`/wifi 与 /storage 的导航项都标了 adminOnly`
  （`expected undefined to be true`）、`AdminRoute 块外的路由不得出现 wifi`、
  以及渲染层用例 `user 角色看不到 WiFi 资源池与文件服务器`
  （`expected document not to contain element, found <span …>` —— 正是 issue 描述的
  「user 能看到入口」）。恢复新实现后 12 passed。
- **测试**：新增 `frontend/src/router/adminRoutes.test.ts`（3 条结构性判据：两块都在
  AdminRoute 内 / 导航项都标 adminOnly / 块外不得再出现 wifi）；
  `Sidebar.test.tsx` 的 `useAuthSession` mock 改为可变角色，补「user 看不到、admin 看得到」
  一例。改动面 4 个文件 12 passed；**前端全量 120 files / 960 tests 全部通过**。
- `npx tsc --noEmit`、`npx eslint --max-warnings 0`、`check:quick`（10 gates）通过。
- **未做**：未在浏览器实测非 admin 的直连 `/wifi`（由 AdminRoute 的既有语义 + 结构性
  守卫覆盖；AdminRoute 自身有既有用例）。

## Revisit

- **页面级 admin-only 的其它面**：本单只按裁决处理 WiFi 资源池。同类「页面接口全
  admin-only 但入口可见」是否还有别的路由，未全量清点；`adminRoutes.test.ts` 的形态
  「AdminRoute 块内清单 + 导航 adminOnly 标记」可直接扩展成一张对拍表。
- **若改判 B**：需要一张接口×角色×字段的暴露表（尤其池条目的网络参数），并同步本单的
  两条守卫（把 `/wifi` 移出 AdminRoute 块 + 去掉 `adminOnly`），别只改一半。
- **非 admin 直连 `/wifi` 的观感**：AdminRoute 是静默重定向首页。若产品希望给一句
  「无权访问」提示，属 AdminRoute 的通用行为，单独议。
