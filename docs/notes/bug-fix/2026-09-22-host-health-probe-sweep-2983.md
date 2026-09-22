# 控制面 host 健康探针——调度 sweep（#2983 切片③）

Status: implemented
Class: bug-fix

## Decision

在解析/SSH 执行器之上接 APScheduler 周期作业：

- `HOST_HEALTH_PROBE_INTERVAL_SECONDS` 默认 600（0=停用）
- 并发帽 / 单机超时 / 连续 N 轮 `AGENT_MUTE` 才 `strike_open`
- 状态落 `host.extra.health_probe`（不含凭据）
- strike 开时写审计 `host_health_probe_agent_mute`
- `/metrics` 折成 `stability_host_health_probe_strike{host_id}`
- 单例注册（`SINGLETON_SCHEDULE_IDS`）

**本切片不含** Prometheus 告警规则（避免与 #3068/#3079 告警文件并改；指标已可挂规则）。

## Alternatives

- **SAQ 任务**：多一层队列，对 10min 低频无收益；否决。
- **同 PR 加告警**：可，但告警文件在窗冲突风险更高；指标先行。

## Verification

- `pytest backend/tests/services/test_host_health_probe_2983.py -q`
- `pytest backend/tests/realtime/test_p3_3_multi_instance.py -k singleton -q`
- `python scripts/run_gates.py check:quick`（env-inventory）

## Revisit

- 对账告警：`stability_host_health_probe_strike == 1` for ≥15m
- #2972 可消费 `topology` / strike 作门控输入（仍缺 wrapper 写面）
