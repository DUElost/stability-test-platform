# 控制面 DB 维护善后 runbook（job_instance 清/截断 / 库回滚）

## 什么时候用

你要对控制面库做下面任一操作时，先读本页：

- 清空 / 截断 `job_instance`（含按 PlanRun 删除历史）；
- 整库恢复到更早水位（`scripts/pg_backup.sh` 的备份回滚）；
- 手工 `DELETE FROM job_instance WHERE ...` 的临时运维。

这些操作会让 **Agent 本地**的 `job_terminal_outbox` 里出现「指向已不存在 job」的行。
本页说明它会引发什么、**什么时候不需要管**、什么时候要人工介入。

## 会发生什么（多数情况不必处理）

Agent 侧终态补送（outbox drainer）在中心返回 **`job not found`** 时会把该行 **ack 并清理**
——`backend/agent/outbox_drainer.py` 自 `#764` 起按 **404 响应体**区分语义：

- 中心明确答 `job not found` → `ack_terminal(job_id)`（行为：该行不再补送）；
- 响应体是 FastAPI 默认 `{"detail":"Not Found"}`（**部署错位 / 代理打错路径**）→
  **保留**该行并打 `outbox_drain_unstructured_404_retained` 告警（`#764` 的教训是
  「别静默丢终态事实」）。

因此：**正常清库无需人工动作**，旧 job 的行会在下一次补送时自动清掉；期间有若干 404 补送日志属预期。

## 中心侧可见信号（优先看这个，不必先 ssh）

| 信号 | 含义 | 取法 |
|---|---|---|
| `stability_agent_outbox_pending{host_id, type="terminal"}` | 该 host 的 **backlog 深度**（口径 `acked = 0 AND dead_letter = 0`，**不含**死信） | Prometheus（`backend/core/metrics.py` 的 `agent_outbox_pending`，由心跳 extra 更新） |
| 心跳字段 `terminal_outbox_dead_letter_total` | 该 host 库内 **死信行数**（`dead_letter = 1`） | 中心落到 `Host.extra`（`backend/api/routes/heartbeat.py`） |

判读要点：

- **backlog 上升到 0 附近再回落**＝正常消化；**长期不降**＝要么中心侧仍 404 非结构化（查路由），
  要么该行已进死信（死信不计入 backlog，需看 `terminal_outbox_dead_letter_total`）；
- `conflicts_retained_total` / `unknown_retained_total` 是**事件计数**（每 drain 循环每行 +1，非积压
  gauge，见 `outbox_drainer.snapshot_metrics` docstring），不要当积压看。

## 排查与清理（需要时）

Agent 本地库是 SQLite：`<agent 安装目录>/agent_state.db`
（`backend/agent/main.py`：`BASE_DIR / "agent_state.db"`）。逐台执行：

```bash
sqlite3 /opt/stability-test-agent/agent_state.db \
  "SELECT job_id, attempts, dead_letter, substr(last_error,1,80) AS err \
   FROM job_terminal_outbox WHERE acked = 0 ORDER BY attempts DESC LIMIT 50;"
```

字段含义见 `backend/agent/registry/local_db.py` 建表语句
（`job_terminal_outbox(job_id UNIQUE, payload, created_at, attempts, last_error, acked, dead_letter)`）。

清掉**确认已作废**的行：

```bash
sqlite3 /opt/stability-test-agent/agent_state.db \
  "DELETE FROM job_terminal_outbox WHERE acked = 0 AND job_id IN (<清库删掉的 job_id 列表>);"
```

两条红线：

1. **只按 job_id 白名单删**，不要 `DELETE FROM job_terminal_outbox` 全清——未作废的终态事实
   一旦删掉就再也送不上去了（这正是 `#764` 要防的「静默丢终态」）；
2. 建议在 Agent 空闲窗口执行：drainer 持有 SQLite 连接，写入会加锁（本地库无"清死信"的
   专用接口，`local_db` 只提供 `ack_terminal` / `mark_terminal_dead_letter` / `get_terminal_dead_letters`
   等读写接口）。

## 边界

- 不覆盖控制面侧的备份/恢复流程本身（见本目录索引 §5「备份与脚本」、§8「数据库迁移」）；
- 不覆盖设备日志事件（DLE）侧的数据恢复——那是独立 runbook：`device-log-event-recovery.md`；
- 本页只解决「旧 job 的终态补送」，不涉及脚本 sha / 派发链路（见 `incident-2026-07-31-script-sha-drift-dispatch-outage.md`）。

## 关联

`#729`（本页导火索：清 `job_instance` 后 Agent 仍持旧 job_id → 404 重试）、
`#764`（404 按响应体区分语义）、`#762`（死信阈值与队头饿死）、`#744`（本页）、
`#743`（同一族的告警项：terminal outbox pending / 404 速率）。
