# 心跳重建 `host.extra` 时保留控制面 `health_probe`（修复对账探针每 5 秒被擦除）

Status: implemented
Class: bug-fix

## Decision

1. 在 `backend/api/routes/heartbeat.py` 的心跳 `host_extra` 重建流程中，将控制面自持键
   `HEALTH_PROBE_EXTRA_KEY`（`"health_probe"`，来自 `backend/services/host_health_probe.py`）
   的保留逻辑置于 `payload.extra` 合并**之后**：
   - 若 `prev_extra` 含有 `health_probe`，原样回填到 `host_extra`；
   - 若 `prev_extra` 不含该键，则从 `host_extra` 剔除，防止 Agent 上报的 `payload.extra`
     伪造或覆写控制面独立 SSH 探针结论。
2. 触发事实（2026-11-23 生产 Prometheus 只读核查）：`host_health_probe_sweep` 每 10 分钟
   成功巡检全部 78/78 台 ONLINE 主机（近 7 天 1012 次 `outcome=success`，日志 `selected=78
   ok=78 errors=0 strike_open=0`），但 `stability_host_health_probe_strike` 在实时抓取中
   为 0 条 series、7 天覆盖率不足 2.5%，且 `strike_open` 恒为 0。根因即 `heartbeat.py`
   每 5 秒重建 `host_extra = {}` 时只保留 6 个 `agent_*` 键，写入 5 秒内即擦除
   `health_probe`，使 `recent_verdicts` 永远无法累积到 `strike_need=2`。
3. 涉及文件：
   - `backend/api/routes/heartbeat.py`
   - `backend/tests/api/test_heartbeat_preserve_health_probe.py`
   - `docs/notes/bug-fix/2026-11-23-heartbeat-preserve-health-probe.md`

## Alternatives

- 将 `health_probe` 移出 `host.extra`、新增独立数据库列或专表：放弃。当前 #2983 探针的读写
  （`host_health_probe.py`、`api/routes/metrics.py`）已稳定复用 `host.extra["health_probe"]`，
  单键保留即可零迁移止血；若后续主机诊断面演进需要独立 schema 再评估。
- 把 `"health_probe"` 直接加入 `keep_key` 元组（放在 `payload.extra` 合并之前）：放弃。
  `health_probe` 的存在意义是独立于 Agent 的控制面旁路对账，放在合并之前会让 Agent
  payload 中同名键覆盖控制面结论。

## Verification

- 回归测试：`backend/tests/api/test_heartbeat_preserve_health_probe.py`（验证连续心跳保留
  `recent_verdicts` → 跨两轮探针触发 `strike_open=True` → `_refresh_host_health_probe_strike_gauges`
  产出 `1.0`，以及 Agent payload 无法伪造或覆盖 `health_probe`）。
- 关联既有测试：`backend/tests/api/test_heartbeat.py`、`backend/tests/services/test_host_health_probe_2983.py`。
- 静态门禁：`venv/bin/python scripts/run_gates.py check:quick`。

## Revisit

- 后续若继续向 `host.extra` 新增控制面自持字段，建议统一收拢为单一「控制面保留键清单」或
  独立列，避免再次出现新增字段被心跳重建静默擦除的问题。
