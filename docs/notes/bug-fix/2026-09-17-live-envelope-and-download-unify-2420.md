# 报告 live 口径补信封 + 产物下载双路由收敛为一份实现（#2420 第 3、4 项）

Status: implemented
Class: bug-fix

> #2420 五件的收官单：第 1、5 项见
> [`2026-09-17-report-cached-at-drawer-time-2420.md`](./2026-09-17-report-cached-at-drawer-time-2420.md)，
> 第 2 项见 [`2026-09-17-job-report-route-ownership-2420.md`](./2026-09-17-job-report-route-ownership-2420.md)。

## Decision

**第 3 项：给 live 补信封，而不是把 cached 拆信封。**
`GET /runs/{run_id}/report` 的 `response_model` 改为 `ApiResponse[RunReportOut]`、返回
`ok(report)`。理由：`{data, error}` 是本仓对外 API 的多数派约定
（`backend/api/response.py` 头注：All new Phase 3+ routes return ApiResponse[T]；
同文件里 `report/cached`、`jira-draft/cached`、`jira-drafts` 全部走信封）——拆 cached
会同时砸掉前端 `unwrapApiResponse` 一条链和脚本已抄的 UI 代码，补 live 只影响一个
**实测零消费者**的端点（`frontend/src/utils/api/runs.ts` 两条都是 `*/cached`）。
"UI 用 cached、脚本用 live" 的分工写进路由 docstring（进 OpenAPI）与 `runs.ts` 注释。

**第 4 项：两条下载路由都保留，但实现收敛为一份，并显式标权威。**
新增 `backend/services/job_artifact_download.py`：job/配对校验 → `run_log_bundle`
409 守卫 → redirect/FileResponse，一条不漏。两条路由分别钉死身份：
`/plan-runs/{run}/jobs/{job}/artifacts/{id}/download` = **UI 权威**（唯一带 run 配对），
`/runs/{job}/artifacts/{id}/download` = **脚本对外入口**（job 域——`/results` 与报告
DTO 里的 id 本来就是 job，只持有 job id 的调用方不该被迫先考古 run）。

**顺带修掉的真实漂移**：409 守卫此前只存在于 plan-runs 一条；job 域路由对早已离开
中心盘的历史 `run_log_bundle` 会落到路径解析、以 400/404 失败收场。收敛后两侧同形
（与 PR #2514 在 retention 侧的"形态判据两侧同形"同一口径）。404 文案统一为
"artifact not found for this job"（原 job 域文案 "artifact not found" 不区分 job/产物，
一并归一；实测无任何消费者断言旧文案）。

## Alternatives

- **删掉 job 域下载路由**（issue 给的另一选项）：grep 过 `deploy/`、`tools/`、`scripts/`、
  `docs/operations/` 与 runbook——无仓内消费者，但生产上"脚本按报告 DTO 的 job id 直接
  下载"正是该端点 docstring 承诺的对外路径；删路由对外部脚本是静默 404，标权威 + 同形
  实现已解决"哪条权威要考古"的问题，删除收益只剩路由表少一行。**否决**。
- **cached 去信封对齐 live**：砸前端一行不划算，且违背 response.py 的既有约定。**否决**。
- **`/runs/{id}/report` 保留裸对象 + 加 `Accept` 协商**：为单一端点引入内容协商机制，
  复杂度不成比例。**否决**。

## Verification

- `python -m pytest backend/tests/api/test_runs.py -q` → 11 passed（新增 4 例：
  live/cached 信封与内层键集合一致 ×1；双路由 run_log_bundle 409、跨属主 404、
  http redirect 307 同形 ×3）。
- 相邻面回归：`test_read_api_auth.py + test_plan_run_export.py +
  test_plan_run_aggregation_endpoints.py` → 141 passed（含既有 plan-runs 409 用例——
  换成共用实现后仍绿，即守卫行为未漂移的正向证据）。
- `scripts/run_gates.py check:quick` 结果见 PR。
- 前端仅注释变化，`tsc` 面零改动。

## Revisit

- 若将来 `/results` 响应带上 `plan_run_id`（ownership note 提过），job 域下载入口的
  存在感会进一步下降，届时可携消费者数据重议"删除"——现在没有数据，只有本单的证据。
- `response_model=ApiResponse[dict]` 正规化台账（`tests/test_api_response_shape_contract.py`
  的 opt-in 文件集）**未**把 `runs.py` 纳入——`RunReportOut` 对 `types.ts` 的
  `RunReport` 双向对拍值得做，但那是独立批（#2187 同族），不混进本单。
