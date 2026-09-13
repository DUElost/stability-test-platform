# Agent Note: 控制面 DB 维护善后 runbook（#744）

Status: implemented
Class: process
Issue: #744

## Decision

新增 `docs/operations/control-plane-db-maintenance.md`，并在 `docs/operations/README.md` §8
「数据库迁移」下挂索引。内容按 `#744` 的诉求三件事：

1. **什么时候用**：清/截断 `job_instance`、整库回滚、手工 DELETE 前先读；
2. **会发生什么**：`#764` 之后，中心答 `job not found` → drainer **自动 ack 清理**；
   非结构化 404（部署错位）才保留 + 告警。→ 多数清库**无需人工动作**；
3. **需要人工看的两处**：① 非结构化 404（查路由，不是清库问题）；② 死信行
   （`acked = 0 AND dead_letter = 1`，drainer 不再重试）。

**比 issue 原文多给的一层**：issue 只写了「检查舰队 unacked / 可选运维脚本列出高 attempts 行」。
本轮把**中心侧可见信号**放在前面——`stability_agent_outbox_pending{host_id, type="terminal"}`
（backlog 深度）与心跳字段 `terminal_outbox_dead_letter_total`（死信行数，落 `Host.extra`）——
**先看指标、必要时才逐台 ssh**，比让运维直接上主机翻 SQLite 更省事也更安全。

## Alternatives

- **并进 `production-diagnostics.md`**：不选。那是「只读诊断 + 凭据边界」，而本页是
  **改库前后的善后流程**，体例不同（对照 `device-log-event-recovery.md` 的
  「什么时候用 / 流程 / 边界」三段式）。
- **只写「有 404 日志属正常」**：不选。运维无法区分「自动清理中」与「部署错位导致的无效重试」，
  而这两者的处置完全相反（一个是等，一个是查路由）。
- **提供 `DELETE FROM job_terminal_outbox` 一键清**：不选。会把未作废的终态事实一并删掉——
  恰是 `#764` 要防的静默丢事实；本页因此把「按 job_id 白名单删」写成红线。

## Verification

- **全部事实取自代码，不取自 issue 转述**：
  - 404 语义与 `ack_terminal` 分支 → `backend/agent/outbox_drainer.py:215-230`；
  - 本地库路径 `<安装目录>/agent_state.db` → `backend/agent/main.py:783`；
  - 表结构与字段 → `backend/agent/registry/local_db.py:82-91`；
  - backlog 口径 `acked = 0 AND dead_letter = 0` → `count_pending_terminals`（同文件）；
  - 死信口径 `dead_letter = 1` → `count_terminal_dead_letters`；
  - 指标 `stability_agent_outbox_pending{host_id,type}` → `backend/core/metrics.py:430-433` + `:692-698`
    （由心跳 extra 更新）；心跳字段 `terminal_outbox_dead_letter_total` → `backend/api/routes/heartbeat.py:294-297`
    落 `Host.extra`；
  - `snapshot_metrics` 各键的**事件计数 vs 积压 gauge** 区分 → 该函数 docstring（#762 口径提示）。
- `check:quick`（含 gov-surface 对本目录索引的校验）→ 见 PR。
- **未验证（诚实标注）**：本页流程未在真实清库演练中走过（无演练环境）；文字依据是代码分支与既有
  incident/note，不含实机演练证据。若日后做一次清库演练，建议把实测的指标回落曲线补进本页。

## Revisit

- `#743`（告警 terminal outbox pending / complete 404 速率）落地后，本页「中心侧可见信号」一节
  应补上**告警名与阈值**的对照，让值班能直接从告警跳到本页。
- 若将来 `local_db` 增加「清死信」的专用接口（目前只有读写原语），本页的 sqlite 手删段应改为
  推荐该接口。
