# God-module 垂直切片：agent artifacts 下沉 service（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2504（coordinator_heartbeat）之上切 `POST /jobs/{id}/artifacts`：抽到
`backend/services/agent_artifacts.py`：

| 符号 | 职责 |
|---|---|
| `ingest_agent_artifact` | 路径校验 → 白名单 → upload lease → PG 幂等入库 |
| `ArtifactIn` / `ArtifactOut` | 入参出参 schema |
| `_ARTIFACT_TYPE_WHITELIST` | aee_crash / vendor_aee_crash / bugreport |

`require_job_bound_upload_lease` 复用 `agent_log_signals`。路由退化为
`ok(await ingest_agent_artifact(...))`。

`agent_api.py` **1063 → 957**（本刀约 -106；相对 coordinator tip）。

## Alternatives

- **顺手迁 agent_heartbeat / upgrade-gate**：弃——另一条垂直线；
- **等栈底合入再从 main 切**：弃——可叠 PR。

## Verification

- 服务直测 **4 passed**（白名单 / 空 uri / 类型 / 404）；
- API：`test_agent_api_artifacts` → **12 passed**；
- 合计 **16 passed**；`ruff` → All checks passed；`check:quick` → **10 gates OK**。

## Revisit

- **下一刀**：`agent_heartbeat` 或 upgrade-gate；
- Issue #1520 保持 OPEN；`Refs #1520`。
