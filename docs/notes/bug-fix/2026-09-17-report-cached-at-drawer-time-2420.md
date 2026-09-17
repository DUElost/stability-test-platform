# 报告页补上 #1082 的 UI 半边 + 设备抽屉时间统一口径（#2420 第 1、5 项）

Status: implemented
Class: bug-fix

> **范围就是标题里的两项**：#2420 共 5 项，第 2/3/4 项（路由 id 语义、响应信封、
> 双下载路由）是对外契约变更，本单**未做**，判据与去向见文末 Revisit。
> （S10 的 Status 词表只有 `proposed/implemented/rejected`，没有"部分完成"这一档，
> 所以把范围写在正文而不是状态里。）

## Decision

**只取这批里判据明确、且不触碰对外契约的两项。** #2420 的第 2/3/4 项都动到路由与响应
信封（`runs/:runId` 实为 jobId、live/cached 两种信封、产物下载双路由），属契约变更，
本单刻意不碰 —— 见文末 Revisit 与 issue 上的留言。

### ① `cached_at`：把「快照」和「重算」在界面上分开

`/runs/{id}/report/cached` 的 docstring 写明 #1082 的裁决语义：快照生成时刻经响应体
`cached_at`（= `post_processed_at`）暴露，**UI 据此标注「截至 xx 时刻」** —— 但
`rg cached_at frontend/src` 是 0 命中，`RunReport` 类型也没这个字段。于是 #1082 当初
关掉某项工作时依赖的前提（「UI 已标注」）其实从未成立，用户在 dev 与生产看到的都只是
「生成时间」。

修法：`types.ts` 声明 `cached_at?: string | null`，报告页在字段存在时多渲染一行
「快照截至」。两条取向值得说明：

- **缺失时整行不渲染**，而不是显示「截至 —」。空占位会被读成「截至时间未知」，
  而真正的事实是「这份不是缓存快照」——前者是误导，后者是不渲染。
- 用**新增一行**而不是把「生成时间」改名：两个时刻各有含义（缓存条目生成 vs 快照内容
  截至），合并成一个标签会丢掉其中一个事实。

### ② 设备抽屉时间：一屏之内只留一套口径

抽屉里 `开始时间/结束时间/最近心跳/下次重试` 直接把后端裸 ISO 铺出来
（`2026-09-16T11:49:18.577637+00:00`），而**同一页面页头**显示 `09/16 19:48`。改走页头
同一个 `formatDateTimeShort`，不新写格式化函数、也不改 `utils/format.ts`（该文件正被
在窗 Execution #2358/#2359 声明，只读消费不动它）。同族缺陷见 #2265 / #2358。

## Alternatives

- **只补 `types.ts` 声明、不加渲染**：否决。那正是本项的病根形态——「字段有、没人读」，
  下一次仍会被当成已实现。
- **把「生成时间」直接改成「快照截至」**：否决，丢掉一个真实事实（见上）。
- **在抽屉里就地 `new Date(x).toLocaleString()`**：否决，那是第三套口径；页头已有
  `formatDateTimeShort`，统一比就近更重要。
- **顺手把第 2 项（`runs/:runId` 实为 jobId）一起改**：否决。它要么改路由形状 +
  重定向、要么改 `RecentRun.run_id` 字段名，两者都是对外契约变更，且 dev 实测暴露的
  「无实体归属校验」需要后端一起判，不该混进一批 UI 收口里。

## Verification

- 前端 `vitest`：`RunReportPage.test.tsx` + `DeviceDetailDrawer.test.tsx` **23 passed**
  （新增 4 条：快照行有/无两种、抽屉非裸 ISO、空值仍是「—」）。
- **红绿自证**：只回退两个组件（保留用例与类型）→ **2 failed**（抽屉仍铺裸 ISO；
  快照行根本不出现），恢复后 23 passed。另 2 条防过头用例两版皆绿，按实际角色标注，
  不计入自证条数。
- `tsc --noEmit`、`eslint src --max-warnings 0`、全量 vitest、`check:quick`：见 PR。
- 未跑后端：本单两项纯前端，`cached_at` 由 `backend/api/routes/runs.py:153` 注入这一
  事实按代码读取确认（未起服务复测），故「后端确实在给这个字段」是静态结论而非实测。

## Revisit（#2420 剩下的三项，留给契约裁决）

- **第 2 项**：`/runs/:runId/report` 的 `runId` 实为 `JobInstance.id`（后端
  `db.get(JobInstance, run_id)`、`RecentRun.run_id = job.id`），且**不做实体归属校验**
  —— dev 里 `/runs/1/report` 会返回 run #2 的 job #1 的报告。修法要么改路由 +
  重定向、要么连 `types.ts` 一起改名 `job_id`，并加 plan_run 归属可选校验：这是
  契约 + 后端行为变更，需单独裁决。
- **第 3 项**：`/runs/{id}/report`（裸对象）与 `/report/cached`（`ApiResponse` 信封）
  两种信封并存，而前端只用后者 —— 对外脚本按 UI 代码抄会解不开。统一信封是破坏性变更。
- **第 4 项**：产物下载两条可用路由（`/runs/{job}/artifacts/{id}/download` 零消费者，
  另一条带 run 配对校验且是前端唯一在用的）。删还是标为脚本入口，取决于是否有外部
  消费者，需 grep 部署脚本/runbook 后定。
- `summary_metrics` 面板在生产的死活问题属 **#2419 第 4 项**（本单第 1 项的相关面：
  报告页里"永远为空"的东西），两边不要各修一半。

---

**后续走向（2026-09-17 追记，不改写上面的当时口径）**：第 2 项已由
[`2026-09-17-job-report-route-ownership-2420.md`](./2026-09-17-job-report-route-ownership-2420.md)
落地；第 3/4 项已由
[`2026-09-17-live-envelope-and-download-unify-2420.md`](./2026-09-17-live-envelope-and-download-unify-2420.md)
收口（live 补信封、双下载路由共用一份实现并标权威）。#2420 五件至此全部落地。
