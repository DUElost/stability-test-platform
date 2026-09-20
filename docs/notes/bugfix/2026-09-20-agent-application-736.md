# #736 切片：薄壳 `AgentApplication`

Status: implemented
Class: bugfix

## Decision

把启动编排与进程内 active-job 占位态迁到 `agent_application.py`：

| 符号 | 职责 |
|---|---|
| `AgentApplication` | occupancy 状态 + `run()` 编排（identity→planes→loop） |
| `run_agent_application` | `main` 入口 |

`main.py` 只剩 dotenv / path / logging / 委托。同 PR 棘轮：`main.py` 269 →
**33**，封顶 **34**（×1.05）。叠在 #2867（`agent_loop`）之上。

生命周期阶段方法（initialize / start_background / …）本刀不做——单 `run()` 薄壳。

## Alternatives

- **继续垂直抽小函数、不上类**：弃——编排已是最后一大块，容器边界更清晰；
- **一次拆 initialize/start_background/shutdown**：弃——过度设计，现有 extract
  模块已覆盖阶段职责。

## Verification

- agent_application + 相关 SourceGuard 重锚：**71+ passed**
- `env_inventory --write`：`STP_WATCHER_*` 首读点改到 `agent_application.py`
- `check:quick`：见 PR（本地 schema-at-head 因 alembic behind 跳过）

## Revisit

- 若 `agent_application.run` 再胖：按阶段拆方法，或再抽 ActiveJobState 值对象。
- #736 god-module 主线：`main` 已降到入口薄壳，可评估关单或只留文档收尾。
