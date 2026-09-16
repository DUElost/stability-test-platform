# #2324 Dashboard 观测面：摘要 WS 推送 + DEVICE_UPDATE 变更门控 + 限流分桶

Status: implemented
Class: bug-fix

## Decision

根因是 Dashboard 把每个 `DEVICE_UPDATE` 无节流地 `invalidateQueries(['dashboard-summary'])`，
而心跳对每台设备全量广播；规模到 100 host / 2000 device 时 REST 风暴打穿 UI 300/min 限流。

长期方案（修订 ADR-0026，**不开新 ADR**）：

1. **摘要热路径走 WS**：`dashboard_summary_publisher` dirty-flag 合流，默认 ≤1Hz 计算并
   `broadcast_dashboard_summary`；前端 `setQueryData(['dashboard-summary'], payload)`。
2. **DEVICE_UPDATE 与摘要解耦**：仅 status / adb / 告警阈值（电量<20、温度>45）变化时扇出；
   前端不再因 `DEVICE_UPDATE` invalidate 摘要。
3. **废弃 Agent WS 逐设备 fan-out**：`AgentNamespace.on_heartbeat` 只续租 SID registry。
4. **限流故障域拆分**：`STP_UI_RATE_LIMIT_REQUESTS`（默认 300）与
   `STP_AGENT_RATE_LIMIT_REQUESTS`（默认 2000）；`/api/v1/heartbeat` 仍豁免。
5. **REST `dashboard-summary`** 仅冷启动 + 慢兜底（前端 `refetchInterval: 60_000`）。

## Alternatives

- **只把前端 invalidate 节流到 2s**：否决——仍与 device 基数耦合，且 Agent WS fan-out
  与共桶限流未解决；属短期止血而非终态。
- **单纯抬高 UI 限流上限**：否决——掩盖风暴，不降低控制面/DB 计算载荷。
- **新开 ADR**：否决——属 ADR-0026「控制面减负 / 观测面」缺口收口，修订原文即可。
- **Redis 共享限流桶**：保留为 #91 远期项；本单不引入。

## Verification

```bash
venv/bin/python -m pytest \
  backend/tests/services/test_dashboard_summary.py \
  backend/tests/services/test_dashboard_summary_publisher.py \
  backend/tests/test_rate_limiter.py \
  backend/tests/api/test_stats.py -q --tb=line
ls backend/services/dashboard_summary*.py
```

期望：materiality / coalesce / UI·Agent 分桶用例通过；既有 dashboard-summary API 用例不回归。

## Revisit

- 多 worker 下 publisher dirty-flag 仍是进程内合流；若启用多实例，需经 Redis adapter
  的 emit 路径验证跨进程订阅者收到同一摘要（ADR-0027）。
- 主机资源均值随心跳变化：当前每次 HTTP heartbeat 都 `schedule_dashboard_summary_push`，
  由 ≤1Hz 合流吸收；若 host 元数据噪声仍偏高，可再对 host 侧做 materiality。
- `STP_*_RATE_LIMIT_REQUESTS` 进程内分桶在多副本下仍按副本线性放大（既有 #91 限制）。
