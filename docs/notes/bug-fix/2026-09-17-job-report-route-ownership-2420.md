# Job 报告路由改权威形状 + 报告端点的可选归属校验（#2420 第 2 项）

Status: implemented
Class: bug-fix

## Decision

只做 #2420 的第 2 项，且刻意选**不制造第二权威**的那条路：

### 1. 权威形状改成它一直是的东西：`/jobs/:jobId/report`

`/runs/{run_id}/report*` 里的 `run_id` 从第一天起就是 **`JobInstance.id`**
（`compose_run_report` 里 `db.get(JobInstance, run_id)`、`RecentRun(run_id=job.id)`、
`_resolve_draft_project_key` 的注释也自认）。前端路由沿用了这个"run"字样，于是
`/runs/1/report` 读起来像"run #1 的报告"，实际给的是 run #2 的 job #1。

- 前端路由改为 `jobs/:jobId/report` 渲染页面；旧路径 `runs/:runId/report` **只重定向**
  （历史深链与书签不 404），且**不**再渲染页面 —— 本单第 4 项批评的正是"两条都能用、
  哪条权威要考古"，不能一边修它一边制造它。
- 重定向带查询串（`?planRun=` 不能因为换路径而丢掉）。
- `RecentRun.run_id` 的 JSON 字段名**不改**（那是对外契约，需单独裁决），改为在
  `types.ts` 里把它钉死成"名字是 run、装的是 job"，并注明 UI 一律按 job 语义使用。

### 2. 归属校验：给出就必须配对，不给出保持原行为

后端只加一条可选参数 `plan_run_id`（报告三端点同源）：给出时与 `job.plan_run_id` 不配对
就 404（结构化 `code=job_not_in_plan_run`，与同类
`/plan-runs/{run}/jobs/{job}/artifacts` 的配对语义对齐）。前端在**两个 id 都在手上**的
入口（PlanRun 详情的设备抽屉"查看报告"）带上它；`/results` 的列表入口只有 job id，不带。

不传保持旧行为：老脚本、导出链接、历史深链都不被这次收紧打断 —— 校验强度由"调用方是否
声明了归属"决定，而不是偷偷改变已有请求的结果。

### 3. 差点留下一个假校验（被自家测试抓到）

第一版直接写 `?planRun=${id}`，而 `id` 拿不到时（路由无参/harness 桩不全）会生成
`planRun=undefined` —— 那不是"不校验"，而是**让后端把一个合法报告判成错配 404**，
比原来的问题更坏。改为 `/^\d+$/` 守卫：拿不到数字 run id 就不带参数。这条被
`PlanRunDetailPage.test.tsx` 的抽屉用例抓到（它桩的是 `{ runId: '12' }`，断言从
`/jobs/3001/report` 变成带 `?planRun=12` 时才通过），记在这里是因为**参数拼装类的洞
最容易在"看起来有参数"的假绿里溜过去**。

## Alternatives

- **给后端也造 `/jobs/{job_id}/report*` 别名路由**：否决。URL 不动是故意的 —— 别名会把
  第 4 项（双下载路由）那个病复制到报告面，而且外部脚本、审计记录、导出链接都认这个
  路径，双权威一旦形成就得再考古。前端路由是内部形状，改它只需一条重定向。
- **把 `RecentRun.run_id` 一起改名 `job_id`**：否决于本单。对外 JSON 契约变更（含
  OpenAPI/脚本/前端全链），影响面比"消除歧义"的收益大；已用 `types.ts` 注释 + 权威路由
  把语义钉住，真改名留作有裁决时的独立 PR（本单是"择一"，两支互斥）。
- **校验设为必填**：否决，直接打断既有深链与脚本，且 dev/生产两侧部署节奏不同。
- **顺手做第 3 项（live/cached 信封不一致）**：否决，那是响应形状变更，与归属校验不同面。

## Verification

- **后端** `backend/tests/api/test_runs.py` **7 passed**，新增 4 条：错配 → 404 且
  `detail.code=job_not_in_plan_run`、配对 → 200、**不传参数保持原行为**（回归护栏）、
  `report/cached` 同样校验（漏一个端点就等于"校验看运气"）。
  **红绿自证**：回退 `runs.py` 保留用例 → **2 failed**（错配与 cached 两条），恢复 → 7 passed。
- **前端**：新增 `router/jobReportRoute.test.tsx`（权威形状只登记一次 + 旧路径只重定向
  且不渲染页面 + 重定向带查询串），`RunReportPage.test.tsx` 新增两条（`?planRun=` →
  client 收到 `{ planRunId: 7 }`；不带时不硬造参数），并把两处 mount 从旧形状改到新形状；
  `PlanRunDetailPage.test.tsx` 的抽屉用例改为断言带 `?planRun=12`。
- 全量前端 **969 passed (121 files)**、`tsc --noEmit`、`eslint src --max-warnings 0` 干净；
  后端 `test_runs.py` 与 `check:quick`（10 gates）见 PR。
- 已知未覆盖：报告页的 `queryKey` 里加了 `planRunId`（同一 job 在两个 run 语境下不能共用
  缓存），但"跨 run 复用缓存"这条**没有对应用例** —— 要真验证得同时挂两个路由参数跑同一
  queryKey，收益低于成本，记在此处而不是假装已验。

## Revisit

- #2420 第 3 项（`/runs/{id}/report` 裸对象 vs `/report/cached` 信封）与第 4 项（产物下载
  双路由）仍是对外契约问题，本单不动。
  （**已落地**：见
  [`2026-09-17-live-envelope-and-download-unify-2420.md`](./2026-09-17-live-envelope-and-download-unify-2420.md)
  ——live 补 `ApiResponse` 信封；两条下载路由共用 `services/job_artifact_download`，
  plan-runs 配对路由标 UI 权威、job 域路由标脚本对外入口。）
- 若将来决定连 `RecentRun.run_id` 一起改名，落点是 `backend/api/schemas` + `types.ts` +
  `ResultsPage`；届时前端路由已是 job 形状，改名只剩 JSON 层。
- 可选校验目前只有一个调用方（PlanRun 详情抽屉）。真正的收口是"所有拿 job 的地方都知道
  自己属于哪个 run"，那需要 `/results` 列表也带上 `plan_run_id` —— 属于响应字段增补，
  与第 3/4 项一起裁决更合适。
