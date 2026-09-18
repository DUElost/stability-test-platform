# #84 关单：agent_api 内联 schema 已迁出

Status: implemented
Class: bug-fix

## Decision

关闭 [#84](https://github.com/DUElost/stability-test-platform/issues/84)。开单目标是把
`agent_api.py` 内约 29 个内联 `BaseModel` 迁出路由文件；现路由内 **0 个 class**。

落点不是原文首选的「全部进 `backend/api/schemas/agent.py`」，而是随 #1520
垂直下沉到各 `backend/services/agent_*.py`（实测约 **33** 个 BaseModel 与领域
同文件）；`schemas/agent.py` 仍只保留日志查询等少量公共模型。与 #60 的
「`routes/agent/` vs `schemas/`」位置之争：**路由文件已无内联 schema**，不再阻塞。

## Alternatives

- **再搬一次进 `schemas/` 再关单**：弃——零契约收益，纯搬家；
- **保持 OPEN 等独立举证 PR**：弃——目标状态已在主干，继续挂着误导排期。

## Verification

- `agent_api.py`：`class` 定义 = 0；约 391 行
- `services/agent_*.py`：BaseModel 子类约 33
- `schemas/agent.py`：仍有 `AgentLogQuery` / `AgentLogOut` 等

## Revisit

无。若要统一「所有 Agent DTO 必须进 schemas 包」属新规范，另开单。
