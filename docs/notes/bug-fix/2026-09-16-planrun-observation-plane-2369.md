# #2369 PlanRun / visibility / DEVICE_UPDATE 观测面收敛

Status: implemented
Class: bug-fix

## Decision

#2324 收口 Dashboard 摘要 REST 风暴后，同类审计仍有三条放大路径：

1. **PlanRun 详情**：每条 `JOB_STATUS` / `PRECHECK_UPDATE` 立刻失效 devices+timeline+logs
   （`WATCHER_SIGNAL` 已有 2s 节流）。历史 nginx 429 已有 `/plan-runs/*/timeline|devices`
   实锤；2000 device 时开着详情页会再次打穿 UI 限流桶。
2. **`visibilitychange → visible`**：`useCrossClientSync` 调用无参 `invalidateQueries()`，
   切回前台全仓重拉。
3. **`DEVICE_UPDATE` 全局 `/dashboard` 扇出**：#2324 后 Dashboard 已不再消费该事件，
   设备页也不曾消费——纯 WS 带宽浪费。

长期方案（继续修订 ADR-0026，**不开新 ADR**）：

1. PlanRun 详情对 `JOB_STATUS` / `PRECHECK_UPDATE` 做 2s trailing 合流（`PLAN_RUN_SOCKET_COALESCE_MS`）；
   `PLAN_RUN_STATUS` 保持即时（run 级低频 + 终态时效）。
2. visibility 改为 `invalidateCrossClientSyncQueries`（plan/project 域），禁止无参全仓失效。
3. `broadcast_device_update` 仅 emit 到 `fleet:devices`；dashboard 全局订阅去掉 `device_update`；
   设备页经 `useFleetDeviceUpdates` 订阅并对列表 invalidate 做 2s 节流。

## Alternatives

- **服务端再推 PlanRun 摘要 WS**：否决为本单范围——前端合流已能给 REST QPS 上界；
  摘要推送可作为规模再上台阶时的复利项。
- **完全停发 DEVICE_UPDATE**：否决——设备页仍可用事件加速列表收敛，room 收窄已够。
- **新开 ADR**：否决——属 ADR-0026 观测面条款续作。

## Verification

```bash
venv/bin/python -m pytest \
  backend/tests/realtime/test_dashboard_subscribe.py \
  backend/tests/api/test_websocket.py -q --tb=line
cd frontend && npm test -- --run \
  src/hooks/plan-run/planRunDetailUtils.test.ts \
  src/hooks/useCrossClientSync.test.tsx \
  src/hooks/parseSubscription.test.ts
python scripts/run_gates.py check:quick
```

## Revisit

- PlanRun 详情若仍打穿限流：考虑服务端 `plan_run_summary` 合流推送（对称 #2324）。
- 设备/主机大列表 10s 全量轮询（P2）与 Agent `recovery/sync` 背压另单跟踪。
