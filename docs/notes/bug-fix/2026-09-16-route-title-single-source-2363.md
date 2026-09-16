# 路由级标题单一来源：缺标题不再靠「每页记得调」（#2363）

Status: implemented
Class: bug-fix

## Decision

失效形态不是「某一页写错」，而是**结构上容许漏**：标题靠每页自己调
`useDocumentTitle('xxx')`，漏调的页（`/hosts`、`/issue-tracker`、`/execution/plan-execute`
实测如此）标签停在默认「稳定性测试平台」，且以后每加一页都会再漏一次。
所以不采用「给缺的页面各补一行」的打法（那只是把同一个漏洞留到下一个新页面）。

### 1. 三态标题模型：路由默认（0）+ 页面覆盖（1），单一写入点

- `frontend/src/router/routeTitles.ts`：`pathname → 标题` 表。侧栏里有的路由**直接取
  `navGroups` 同一份数据**（标签上的词与用户看到的导航词必须同源，否则改一处就漂移）；
  其余路由（详情、账户、管理员菜单项、登录/注册）在表里登记一份；`:param` 段按段匹配，
  **段数相等才命中**（不做前缀吞并），并按段数降序排，让 `/settings/ai-assistant`
  赢过 `/settings`。未命中返回 `null`。
- `useDocumentTitle` 重写为「claim 登记表 + 求值」：`<RouteTitle/>`（挂在
  `<BrowserRouter>` 内、`<Routes>` 之前，全树一处）登记路由默认；页面**可选**
  `useDocumentTitle('动态标题')` 覆盖。**只有 `applyTitle()` 写 `document.title`**，
  不再有「谁后跑谁赢」的隐式竞态。
- 没有 claim 时**不动**标题：旧写法的「记住 original 再 restore」把快照取在模块加载期
  （jsdom 与浏览器下并不等于 `index.html` 的标题），是一条凭空出来的脆弱依赖。

### 2. 顺手把 `navGroups` 挪成数据模块

`Sidebar.tsx` 直接导出常量会撞 `react-refresh/only-export-components`（该规则明确建议
「用新文件共享常量」）。于是 `navItems.ts` 成为导航数据的家，Sidebar 只渲染它，
标题表也只读它 —— 顺带去掉「路由表依赖布局组件模块」这条不该有的边。

### 3. 删掉 5 处静态页面标题

`ProjectsPage` / `PlanListPage` / `ResultsPage` / `PlanRunListPage` / `TestSuitesPage`
的 `useDocumentTitle('...')` 由表接管。**两处标题文案因此变化**（不静默改）：
`Plan 编排 → Plan 管理`、`Plan 执行记录 → 执行记录`——以侧栏用词为准，避免同一页
在导航和标签上叫两个名字。动态标题的 5 处（详情页等）**保留**：它们 claim 的是
数据带来的名字，表给不了。

## Alternatives

- **只给漏调的页面补一行**（issue 的「现成机制补齐缺口路由即可」）：否决。它修的是
  今天的三条路由，留下面的仍是「靠人记得调」——正是本单的失效成因。
- **`title` 用字符串优先级 hack（如空串覆盖）**：否决，语义不可读。
- **用 react-router 的 `handle` + `useMatches()`**：本仓是 `BrowserRouter` + `<Routes>`
  （非 data router），要用就得先改造成 `createBrowserRouter`，为一个 P3 不值。
- **把标题表做成 `path → title` 的字面量清单（不读 `navGroups`）**：否决，两套名字必漂移；
  改用「导航同源 + 非导航单列一份」，并有一条遍历 `navGroups` 全量断言的用例兜住。

## Verification

- 新测试 **11 passed**（`routeTitles.test.ts` 4 条，含「遍历每条侧栏导航路径都要解析出同名标题」；
  `routeTitleWiring.test.tsx` 3 条接线契约；`useDocumentTitle.test.tsx` 4 条）。同目录一起跑时是 14 passed
  —— 多出的 3 条是 `src/layouts` 既有 Sidebar 用例，不计入本单。
- **红绿自证**：把 `<RouteTitle />` 从路由器摘掉 → 接线契约 **2 条红**（「登记且只登记一次」
  + 「挂载点在 BrowserRouter 内、Routes 之前」），装回即绿。
  第一轮我先跑了一次**无效的自证**——改的是 `router/index.tsx` 而用例直接渲染
  `<RouteTitle/>`，于是「全绿」是假绿；补 `routeTitleWiring.test.tsx` 才真正覆盖接线，
  过程记在这里，避免下一个人以为接线有人守。
- 全量前端：`vitest run` **885 passed / 2 failed**，两条失败为 **main 既有红**
  （`git stash` 掉本单全部改动后同样 2 failed；属 #2369 在窗那片 JOB_STATUS invalidate 面，
  本单不碰、不冒充是回归）。
- `tsc --noEmit`、`eslint src --max-warnings 0` 均干净；`check:quick` 见 PR。

## Revisit

- 若日后迁到 data router（`createBrowserRouter`），标题应搬进路由 `handle`，本表与
  `<RouteTitle/>` 由 `useMatches()` 取代；保留 `resolveRouteTitle(pathname)` 的纯函数
  形态可让这步只换挂载方式。
- 管理员菜单里的 `/users`、`/settings`、`/audit`、`/notifications` 名字目前写在
  `AppShell.tsx` 的 JSX 里（非导出数据），本表另登了一份。真同源要把那几项提成数据，
  与 #2262（导航 IA）一并做更合适——本轮不动，登记为第二处来源。
