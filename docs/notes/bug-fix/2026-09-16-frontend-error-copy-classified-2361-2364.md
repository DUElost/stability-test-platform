# 前端失败文案分类与风险卡保留上次数据（#2361 / #2364）

Status: implemented
Class: bug-fix

## Decision

**1. 新增共享映射 `loadErrorCopy(error, { notFound, network?, fallback? })`**（`frontend/src/utils/api/client.ts`，
紧邻既有的 `classifyApiError`，并随 `index.ts` 导出）：

| 判据（只看 status，不猜文案） | 文案 | 是否给「重试」 |
|---|---|---|
| 404 | 调用方给的资源不存在文案 | **否**——资源没了，重试不可能成功 (#2361) |
| 无 status + `code === 'TIMEOUT'` | 「请求超时（服务端繁忙或网络慢），请稍后重试」 | 是 |
| 无 status（其余网络层） | 调用方 `network` 或「请检查网络连接或稍后重试」 | 是 |
| 其余带 status | 服务端 message，缺了回落调用方 `fallback` | 是 |

**文案留在调用方**——同一个 404 在「执行记录」与「用例结果」措辞不同；共享的只是
「status → 语义」这一步。此前三处调用点各自把任何失败写成同一句话，正是 #2361 的病灶。

**2. 三处接线**（#2361）：

- `PlanRunDetailPage`：404 时标题由「加载 PlanRun 失败」改为「执行记录不存在」，
  描述用本地化文案（此前直出服务端英文 `plan run not found`），**不下发 onRetry**；
- `PlanRunEventStream`：新增 `error?: unknown` prop（只有布尔 `isError` 分不出 404 与
  网络层）；`PlanRunLogsPage` 传 `eventsQ.error`。日志页此前对 404 说「请检查网络连接」；
- Dashboard 风险卡：见下。

**3. 风险卡保留上次成功数据**（#2364）：刷新失败但 react-query 仍有 `data` 时，**保留
图表**并在上方标一行「风险分布最近一次刷新失败：<分类文案>（下方为上次成功的数据）」；
只有连上次数据都没有时才整卡换成错误态（文案同样走分类，超时与服务端繁忙不再同形）。
间歇性失败一次的代价从「信号整块消失」降为「数据陈旧提示」——这正是 issue 里实测到的
13:42 消失 / 13:45 又正常。

**与本批其余两单的关系**：#2359（`classifyApiError`）与 #2363（路由标题）认领后
**实测已由他人修复并关闭**（WifiPage 已用 `classifyApiError`；路由标题随 PR #2390
在 base 提交 `a789c99d` 落地），本 PR 不重复修，只复用了 #2359 留下的分类原语。
本 PR 只关 #2361、#2364。

## Alternatives

- **A. 每个调用点各自写映射**：否决。三处会各自漂移，且 404/超时的判据必须唯一
  （与 `classifyApiError` 同源），否则「同一个失败两个页面两种说法」。
- **B. 继续直出 `toApiError(error).message`**：否决。服务端 detail 是英文
  （`plan run not found`），既不本地化，也把用户引向查网络。
- **C. 404 也给重试按钮**：否决。已回收/不存在的 run 重试必然同结果，只会让用户
  在循环里打转；这也是 `retryable` 字段存在的唯一理由。
- **D. 风险卡维持「失败即整卡消失」**：否决。一次瞬时失败丢掉整块信号，是 issue
  实测到的现象本体。改为保留陈旧数据 + 标注时点后，「一次抖动」与「持续故障」在界面上
  可区分。
- **E. 顺手做 #2364 的另外两个排查方向（超时预算 / 并发扇出限流 / 卸载竞态）**：
  不做。根因未定位（见 Revisit），没证据就改超时或限流会掩盖问题；本单先把失败形态
  **可辨识**（超时 / 服务端 / 网络层各有文案），下一次复现能自证是哪一类。

## Verification

- **红绿差分（先证明守卫会失败）**：
  - 新增 Dashboard 两例在 `git checkout` 回的旧组件上跑 → `2 failed`（找不到陈旧提示与
    超时文案）；换回新组件 → 全绿；
  - 新增 EventStream 404 一例在旧组件上 → `1 failed | 15 passed`（旧组件只会说
    「请检查网络连接或稍后重试」）；换回新组件 → 全绿。
- **测试**（`frontend/` 下运行）：`npx vitest run src/utils/api src/pages/execution
  src/components/plan-run src/pages/Dashboard.test.tsx` → **276 例：274 passed / 2 failed**。
  两例失败为**存量**：`PlanRunDetailPage.test.tsx` 的 socket 失效与抽屉同步，已用
  `git checkout` 还原本单改动的三个页面文件后复跑 → 同样 `2 failed | 22 passed`，与本次
  改动无关（另行报告）。
- **类型与 lint**：`npx tsc --noEmit` 通过；`npx eslint <本单 9 个文件>
  --max-warnings 0` 通过。
- **仓库门禁**：`python scripts/run_gates.py check:quick` → `[OK] check:quick (10 gates)`。
- **未做**：未做浏览器/生产实测；#2364 的间歇性失败**未复现**，根因仍未定位（见 Revisit）。

## Revisit

- **#2364 的根因**：卡片数据源是 `api.results.summary(30)` → `/results/summary?limit=30`
  （`analytics.ts`），**不是** issue 里对照的 `/results/risk-trend`——「risk-trend 稳定
  200」并不能证明该卡片的数据源健康，两者是不同端点。默认超时是 `API_TIMEOUT_MS = 30s`
  （`timeouts.ts`）。下次复现时，卡片文案会自报是哪一类：若显示「请求超时」，就该去查
  `/results/summary` 的聚合耗时与仪表盘挂载时的 6 路并发扇出；若显示服务端 message，
  则是后端 5xx。**在拿到一次带分类的复现之前不改超时/不加限流。**
- **取消（cancel）未单列**：react-query 的取消/卸载竞态走 `toApiError` 后无 status，
  会被归到「网络层」而显示连接提示。当前不区分——若下次复现的形态是「切换页面即
  失败」，这一支需要单独拉出来（先确认是否真有 query cancel 路径，再决定文案）。
- **同类接线**：其它「失败即整块消失」的卡片（活动曲线、成功率趋势等）是否也该保留
  上次数据，等有现场证据再逐个做——本单只按 issue 覆盖风险卡。
