# ADR-0033 Phase A3：PlanRunArtifact 下载 + JiraRun plan_run_id 过滤

Status: implemented
Class: architecture

## Decision

落地 ChatGPT / Contract Phase A3 的**最小可导航切片**（#3013）：

1. `GET /api/v1/plan-runs/{id}/artifacts/{artifact_id}/download` —— 下载
   `PlanRunArtifact`（scan/merge xls），路径守卫复用 JobArtifact 下载实现。
2. `DedupReportCard` 产物行加「下载」链接（不再只展示 NFS `storage_uri`）。
3. `GET /api/v1/jira/runs?plan_run_id=` —— PlanRun 维过滤 Jira 历史。

不新建 ToolRun 表；不碰 Package Store；不实现 DLE zip / jira 目录树浏览
（列为 #3013 follow-up）。

## Alternatives

| 选项 | 为何不选 |
|---|---|
| 只做 API、不改 UI | DedupReportCard 已是 PlanRun 产物入口，少一链就仍要抄路径 |
| 顺带 DLE download + jira zip | 权限/目录安全面更大，超出本切片 |
| 等 #3005 合入再叠 | A3 与 D0/Jira ACL 正交，独立 PR 更清 |

## Verification

- `python3 -m pytest backend/tests/services/test_plan_run_artifact_download.py -q --noconftest`
- `npm --prefix frontend test -- DedupReportCard.test.tsx`（或 vitest 等价）
- `python3 scripts/run_gates.py check:quick`

## Revisit

- DLE `remote_path` open/zip
- extract 后登记 `extract_bundle` + 目录下载
- PlanRun 详情内嵌 JiraRun 列表（现仅 API 过滤就绪）
