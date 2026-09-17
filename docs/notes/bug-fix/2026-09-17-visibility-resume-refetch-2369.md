# 可见性恢复的按页回追：users / audit-logs 显式 opt-in refetchOnWindowFocus（#2369 残余）

Status: implemented
Class: bug-fix

## Decision

#2369 原修法把「切回前台」从**全仓失效**收窄为**按域失效**（`useCrossClientSync` →
`invalidatePlanSyncQueries` + `invalidateProjectSyncQueries`），以避免人为放大限流桶；
但全局 `refetchOnWindowFocus: false`（`QueryProvider.tsx:5`）之下，**不在该域、又没有
轮询**的活跃查询因此失去了唯一的回前台刷新路径——本单残余即
`UsersPage` 的 `['users']` 与 `AuditLogPage` 的 `['audit-logs', ...]`。

采用 issue 给出的**取向 ②**：在这两处查询上显式 `refetchOnWindowFocus: true`，
并就地写明理由。选它而不是「把这两个键加进跨端同步域列表」的原因：

- `refetchOnWindowFocus` 的语义与「可见性恢复」一致（react-query 的 focusManager
  监听 `window.visibilitychange`），且**只重取活跃查询**——正是「页面开着才需要回追」
  的语义；
- 改动落在**消费点**，新增同类页面时作者在本地就能看到这条既有先例，不需要记得去
  共享 hook 里登记（跨端同步域列表的每一项都是全局失效代价，扩容要谨慎）；
- 不动 `useCrossClientSync` 的域列表，#2369 原修法的「不放大限流桶」前提原样保留。

## Alternatives

- **把 `['users']` / `['audit-logs']` 加进 `invalidateCrossClientSyncQueries`**：
  否决。该列表的语义是「跨端同步域」（服务端广播 plan_changed / project_changed 时
  同样用它），把「无实时覆盖的页面」混进去会让两条不同的语义共用一张表——将来
  新增一个实时事件时无法判断该不该并。
- **恢复全局 `refetchOnWindowFocus: true`**：否决。那正是 #2369 要消除的形态——
  切回前台时所有活跃查询一起重取，放大限流桶。
- **给这两页加轮询**：否决。轮询是持续成本；后台 tab 不跑定时器、回前台补一次，
  才是这个场景的准确支出。
- **在 `QueryProvider` 里按 key 白名单统一开**：否决。全局白名单同样需要维护，
  且把「页面级需求」藏进了基础设施层；页内 opt-in 更贴近「谁需要谁声明」。

## Verification

- 新增用例两条（行为级，不是配置断言）：
  - `frontend/src/pages/users/UsersPage.test.tsx`（新文件）：渲染 → 首次拉取 1 次 →
    置 `document.visibilityState='visible'` 并在 `window` 上派发 `visibilitychange`
    → 断言第二次拉取；
  - `AuditLogPage.test.tsx` 增同款用例。
  两条用例的 QueryClient **默认值都设为 `refetchOnWindowFocus: false`**（与线上
  `QueryProvider` 一致），故绿的唯一来源是页面级 opt-in。
- **反例构造（先证伪再采信）**：分别临时删掉两处的 `refetchOnWindowFocus: true`
  → 两条用例各自 **FAILED**（`expected "vi.fn()" to be called 2 times, but got 1 times`）；
  恢复后转绿。
- 实测命令与结果：
  - `npx vitest run`（全量前端）→ **120 files / 958 tests passed**；同轮报 1 个
    unhandled error（`HostsPage.test.tsx` 的 xterm `matchMedia`），**与本单无关**——
    已在干净 `origin/main` 的一次性 worktree 上复现同一错误；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**
    （含 eslint / tsc / knip）。

## Revisit

- **同类页面盘点**：无轮询、又不在跨端同步域内的活跃查询还有若干（例如审计页筛选
  用的 `['users', 'audit-filter-options']`，有 60s staleTime，本轮不动——它是**仅当
  筛选下拉打开时**才需要的辅助数据）。若后续再出现「切回前台数据陈旧」的现场，
  出口是抽一条 `useFocusRefresh()` 约定，而不是逐页复制选项。
- **新增实时事件时的对账**：每新增一个域级广播，应回看是否让某些页面**不再需要**
  本 opt-in（能由域失效覆盖就撤掉，避免两条路径都刷）；判据可考虑并入
  `tests/test_realtime_wiring_contract.py` 的接线守卫家族（该文件正在 #2458 的另一
  PR 上扩判据 4，避免同文件并行改动故本轮未动）。
- `['users']` 同时被用户管理与审计筛选复用，键前缀相同：本轮只给了管理页的
  `['users']` 开 opt-in，筛选下拉沿用 staleTime——若将来出现下拉陈旧反馈，改的是
  审计页那一条查询，不要用前缀失效把两者一起刷。
