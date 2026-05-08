# ADR-0022 — Patrol 心跳化 + 失败退避 + 手动干预

| Status     | Draft                                  |
| ---------- | -------------------------------------- |
| Date       | 2026-05-08                             |
| Authors    | dai.lv                                 |
| Reviewers  | (待评)                                 |
| Successors | —                                      |
| Related    | ADR-0020 (Plan), ADR-0021 (派发门禁)   |

## 背景

ADR-0020 落地后,Plan 编排进入 Plan + PlanStep 模型。一个典型生产场景:

> 150 台设备并发跑 Monkey 稳定性测试,patrol 周期 60s,持续 8 天 = 11520 周期。

按现有 `pipeline_engine._run_lifecycle_steps` 的实现,patrol 阶段每个周期内**每个 step 都会写两条 step_trace**(STARTED + COMPLETED/FAILED),数据量级:

```
patrol step_trace 总行 ≈ 11520 周期 × 5 步骤 × 150 设备 × 2 = 1730 万行 / PlanRun
单行 ~500B → 8.5 GB / PlanRun
```

**问题**:1730 万行 / PlanRun 远超 PostgreSQL 单 PlanRun 的合理量级,会导致:

1. 详情页查询超时(per-job step_trace 全量拉取触发 OOM)
2. 异常事件被海量"步骤循环成功"事件淹没,排查 ANR/AEE 反而困难
3. WAL 日志膨胀,vacuum/autoanalyze 压力陡增
4. 历史数据归档成本上升

根因在于:**patrol 是循环重复的**,绝大多数事件是冗余的"还在转",只有"出问题"和"周期统计"才是真信号。

## 决策

引入"心跳化"模型: patrol 阶段不再为每周期每 step 写 step_trace,改为 `job_instance` 维度的"计数+心跳"列。失败 step 仍写 step_trace,但叠加退避策略防止单设备故障被无限放大。

### D1 · patrol 成功 step 不写 step_trace

`pipeline_engine._execute_step` 在 patrol 阶段且 step 成功时,**跳过 `_report_step_trace_mq` 调用**。

行为对照:

| 阶段     | step 成功 | step 失败 | step skipped |
| -------- | --------- | --------- | ------------ |
| init     | 写 trace  | 写 trace  | 写 trace     |
| patrol   | **不写**  | 写 trace  | **不写**     |
| teardown | 写 trace  | 写 trace  | 写 trace     |

### D2 · `job_instance` 加 patrol 心跳列(本 ADR 配套 alembic `d7e8f9a0b1c2`)

| 列                            | 类型      | 维护方           | 说明                                                                    |
| ----------------------------- | --------- | ---------------- | ----------------------------------------------------------------------- |
| `patrol_cycle_count`          | INT       | Agent 周期 incr  | 已执行周期总数                                                          |
| `patrol_success_cycle_count`  | INT       | Agent 周期 incr  | 全部 step 成功的周期数                                                  |
| `patrol_failed_cycle_count`   | INT       | Agent 周期 incr  | 至少 1 step 失败的周期数                                                |
| `current_patrol_step`         | TEXT      | Agent 心跳更新   | 当前正在跑的 step name(供设备矩阵实时显示)                            |
| `last_patrol_heartbeat_at`    | TIMESTAMP | Agent 心跳更新   | 最近活性证明,用于 stall 检测(下一 ADR 决议是否营业化)                |
| `current_failure_streak`      | INT       | Agent 周期 incr  | 当前连续失败次数(任一 step 成功则清零;退避计算用)                  |
| `next_retry_at`               | TIMESTAMP | Agent 退避更新   | 下次 patrol 重试时间;非空表示退避中,Agent 在到期前 sleep              |
| `manual_action`               | VARCHAR   | 用户 API 写入    | NULL / `RETRY_NOW` / `EXIT_REQUESTED`;Agent 周期检查后清零或响应       |

新增索引:

```sql
CREATE INDEX idx_job_instance_patrol_heartbeat
    ON job_instance (plan_run_id, last_patrol_heartbeat_at);
```

### D3 · 上报通道分两条

| 端点                                                        | 用途                                                       | 写 step_trace? |
| ----------------------------------------------------------- | ---------------------------------------------------------- | -------------- |
| `POST /api/v1/agent/jobs/{id}/patrol-heartbeat` (新)        | patrol 周期完成统计 + current_patrol_step + 心跳时间       | **不写**       |
| `POST /api/v1/agent/jobs/{id}/steps/{step_id}/status` (现有) | INIT/TEARDOWN per-step 上报 + PATROL 失败 step 上报        | 写             |
| `POST /api/v1/agent/log-signals` (现有)                     | Watcher 采集的 ANR/AEE/Tomb 异常                           | 不写,写 log_signal |

`POST /agent/jobs/{id}/patrol-heartbeat` request 形态:

```json
{
  "fencing_token": "<lease token>",
  "cycle_index": 14,
  "success_delta": 1,
  "failed_delta": 0,
  "current_step": "patrol.monkey_check",
  "current_failure_streak": 0,
  "next_retry_at": null
}
```

服务端原子 UPDATE(`UPDATE ... SET patrol_cycle_count = patrol_cycle_count + 1, ...`),不写新行。

### D4 · 失败 step 退避策略 (D1)

公式:

```python
backoff_seconds = min(60 * (2 ** max(0, streak - 2)), 3600)
```

| streak | next_interval | 含义                    |
| ------ | ------------- | ----------------------- |
| 1      | 60s           | 正常                    |
| 2      | 60s           | 正常                    |
| 3      | 120s (2min)   | 指数退避起点 (60 × 2¹)  |
| 4      | 240s (4min)   | 60 × 2²                 |
| 5      | 480s (8min)   | 60 × 2³                 |
| 6      | 960s (16min)  | 60 × 2⁴                 |
| 7      | 1920s (32min) | 60 × 2⁵                 |
| 8+     | 3600s (1h)    | 上限 (60 × 2⁶ = 3840 > 3600 → cap) |

退避在 Agent 端实现:

- 每周期结束后,如果该 device 任一 step 失败,`current_failure_streak` 自增
- Agent 计算 `next_retry_at = now() + backoff_seconds`,通过 patrol-heartbeat 上报
- Agent 主循环 sleep 到 `next_retry_at`(可被 manual_action 中断)
- 下一周期任一 step 成功 → streak 清零,退出退避

### D5 · 退避策略可在 Plan.lifecycle 覆盖 (BO2)

```yaml
lifecycle:
  patrol:
    interval_seconds: 60
    backoff_policy:
      max_streak: 10           # 默认 7+
      max_interval_seconds: 7200  # 默认 3600
      base_seconds: 60         # 默认 60
      growth_factor: 2.0       # 默认 2.0
    steps: [...]
```

字段缺失时使用全局默认。Agent 在 patrol 启动时从 `pipeline_def.lifecycle.patrol.backoff_policy` 读取并缓存。

### D6 · 设备离线不写 step_trace (BO3)

设备离线(adb 不通)由 Agent 本地探测识别,**不写 step_trace**:

- 仅更新 `device.last_seen` (现有机制)
- 写 `job_log_signal(category=DEVICE_OFFLINE)` 用于事件流可见性
- 不计入 `current_failure_streak` (这是基础设施问题,不是脚本/被测对象问题)
- Agent 短退避自愈(30s/60s/120s 上限 5 分钟),恢复后继续 patrol

### D7 · 手动干预 API (BO4 + ADR-0021 abort 风格)

| 端点                                                         | 行为                                                                                                                              |
| ------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v1/plan-runs/{run_id}/jobs/{job_id}/manual-retry` | 设 `next_retry_at = now()`,**不重置** `current_failure_streak`(保留诊断信息);写 audit_log;Agent 下次 patrol 心跳时观察到 RETRY_NOW |
| `POST /api/v1/plan-runs/{run_id}/jobs/{job_id}/manual-exit`  | 设 `manual_action = EXIT_REQUESTED`;Agent 下一周期检查到后**跳过 teardown 直接 ABORTED**,Recycler 走 device lease release;写 audit_log |

**手动退出 → 跳过 teardown 直接 ABORTED 的权衡**(BO4=B):

| 优点                                              | 风险                                  | 缓解                                              |
| ------------------------------------------------- | ------------------------------------- | ------------------------------------------------- |
| 快速响应,device 立即释放给下个 Plan 用            | adb 状态/文件/Wi-Fi 不会被脚本清理    | Recycler 仍走标准 device lease release;下个 Plan 的 init 阶段会重新 check_device + connect_wifi 等;靠 init 兜底 |

测试覆盖: `tests/agent/test_manual_exit_release.py` 验证 manual-exit 后下一个 Plan 能正常 acquire 同一 device。

### D8 · 退避中设备计入 failure_threshold (BO5)

`PlanAggregator.on_job_terminal` 计算 PlanRun.failure_threshold 时,退避中的 job 视同已失败:

- 退避中 job 状态仍是 `RUNNING`,但 `current_failure_streak >= 3` 视为"已观察到稳定失败"
- Plan 链触发判断时(`plan_chain_trigger.evaluate`)读取这一信号

这与现有 failure_threshold 语义一致(threshold 是"失败设备占比"而非"已结束 job 占比")。

### D9 · ADR-0022 适用范围

本 ADR **只重塑 patrol 阶段**:

- INIT / TEARDOWN 沿用现有 per-step 上报,不变
- patrol 阶段 step_trace 仅在失败时写,成功时**不写**
- `cycle_count` 等聚合数据通过 `job_instance` 列暴露,前端按需聚合

### D10 · stall 检测延后(BO3=A)

虽然 `last_patrol_heartbeat_at` 已经具备 stall 检测的数据基础(`now - last_heartbeat > 3 × interval` → STALL),但**本 ADR 不做 Recycler 改造**。stall 检测/熔断是独立关注点,留给后续 ADR 决议。

## 数据量重新估算

| 数据                                               | 旧设计 (1730 万行)         | 新设计                                             |
| -------------------------------------------------- | -------------------------- | -------------------------------------------------- |
| INIT step_trace                                    | 900 行                     | 900 行                                             |
| TEARDOWN step_trace                                | 600 行                     | 600 行                                             |
| **PATROL 成功 step_trace**                         | 1700 万行                  | **0 行**                                           |
| PATROL 失败 step_trace (健康集群 0.01% 失败率)     | (合并到上一行)             | **864 行**                                         |
| PATROL 失败 step_trace (中等异常 0.1% + 退避收敛) | 同上                       | < 1 万行                                           |
| job_log_signal (Watcher)                           | ~1000 行                   | ~1000 行                                           |
| audit_log (PlanRun milestone + manual 干预)        | ~30 行                     | ~50 行                                             |
| **合计 / 8 天 PlanRun (中等异常场景)**             | **~1730 万行 / 8.5 GB**    | **~1.3 万行 / 6.5 MB**                             |

**约 1300x 压缩**。worst case (1% 失败率 + 无退避) 也被退避机制控制在 ~10 万行级别。

## 影响

### Agent 改动

- 新模块 `backend/agent/patrol_heartbeat_uploader.py` (类似 `step_trace_uploader.py` 的批量+重试模式)
- `backend/agent/pipeline_engine.py` 的 patrol 主循环改造:
  - 每周期开始: 检查 `manual_action`;如果 EXIT_REQUESTED → 跳出 patrol 不走 teardown
  - 每周期结束: 计算 success/failed 次数 → patrol_heartbeat_uploader.send()
  - 失败时计算 streak + next_retry_at + sleep

### 后端改动

- `backend/models/job.py`: JobInstance 加 8 列 + `idx_step_trace_job_stage` / `idx_step_trace_job_status_ts` 复合索引(C5a₂)
- `backend/api/routes/agent_api.py`: 新增 `POST /jobs/{id}/patrol-heartbeat`(本 ADR)+ `record_log_signal_ingested` 埋点(C5a₂)
- `backend/api/routes/plan_runs.py`: 新增 `POST /plan-runs/{run_id}/jobs/{job_id}/manual-retry|manual-exit`(本 ADR)+ 5 个聚合端点(C5a₂,见下表)
- `backend/services/plan_run_aggregation.py`: 终态聚合时 `record_plan_run_terminal()`(C5a₂)
- `backend/services/plan_precheck.py`: 全程 `record_dispatch_gate(outcome, duration)`(C5a₂)
- `backend/core/metrics.py`: 新增 6 类 PlanRun/patrol/log_signal/dispatch_gate Prometheus 指标族(C5a₂)
- alembic migration `d7e8f9a0b1c2_add_patrol_heartbeat_columns.py`(本 ADR)+ `e8f9a0b1c2d3_add_step_trace_aggregation_indexes.py`(C5a₂)

#### C5a₂ 聚合端点(供 PlanRunDetailPage 调用)

| 端点 | 用途 | 数据来源 | 性能依赖 |
|---|---|---|---|
| `GET /plan-runs/{id}/chain` | 沿 `parent_plan_run_id` 回溯 + 候选 next Plan(含 block_reason) | `plan_run` + `plan` | `idx_plan_run_root` |
| `GET /plan-runs/{id}/timeline` | 三阶段(init/patrol/teardown)聚合,含 patrol_cycle_index / active_devices | `step_trace` GROUP BY (job_id, stage) + `JobInstance.patrol_*_cycle_count` | `idx_step_trace_job_stage` + `idx_job_instance_patrol_heartbeat` |
| `GET /plan-runs/{id}/events?stage=&severity=&limit=&offset=` | 多源事件流(trigger / step 失败 / log_signal / audit) | 4 表 UNION,内存合并排序 | `idx_step_trace_job_status_ts` + `idx_job_log_signal_detected` + `ix_audit_resource` |
| `GET /plan-runs/{id}/devices?status=&host_id=` | per-device matrix + by_status/by_host facet,含 backoff/risk 派生 | `JobInstance` + `Device` | `idx_job_instance_status` |
| `GET /plan-runs/{id}/watcher-summary?window_minutes=` | log_signal 按 category 聚合 + trend(对比上一窗口)+ exceeded 标志 | `JobLogSignal` GROUP BY category | `idx_job_log_signal_category` + `idx_job_log_signal_detected` |

#### C5a₂ Prometheus 指标族

| 指标 | 类型 | 标签 | 触发点 |
|---|---|---|---|
| `stability_plan_run_terminal_total` | Counter | `status` | `apply_plan_run_aggregation` 终态时 |
| `stability_plan_run_pass_rate` | Histogram | `status` | 同上(buckets: 0/0.5/0.8/0.9/0.95/0.98/0.99/1.0) |
| `stability_dispatch_gate_runs_total` | Counter | `outcome` | `_drive_dispatch_gate` finally(passed/synced_passed/failed/skipped) |
| `stability_dispatch_gate_duration_seconds` | Histogram | `outcome` | 同上 |
| `stability_patrol_heartbeat_total` | Counter | `has_failures` | `POST /agent/jobs/{id}/patrol-heartbeat` |
| `stability_patrol_failure_streak_observed` | Histogram | — | 同上(观察到的 streak 分布) |
| `stability_patrol_manual_action_total` | Counter | `action` | manual-retry / manual-exit 端点 |
| `stability_log_signal_total` | Counter | `category` | `POST /agent/log-signals` 每条入库的 signal |

### 前端改动 (本 ADR 不做,后续 C5b/C5c 跟进)

- 设备总览新增 `BACKOFF` 状态色
- 设备抽屉新增"退避面板"(streak / next_retry_at / 立即重试 / 退出按钮)
- 事件流新增 `device_backoff_entered` / `device_manual_retry` / `device_manual_exit` 事件类型

## 测试覆盖要求

1. `backend/tests/api/test_patrol_heartbeat_api.py` — heartbeat 端点幂等/字段更新/fencing_token 校验
2. `backend/tests/api/test_manual_retry_exit_api.py` — manual-retry 不重置 streak / manual-exit 写 ABORTED + audit
3. `backend/tests/agent/test_patrol_heartbeat_uploader.py` — 批量+重试模式
4. `backend/tests/agent/test_pipeline_engine_patrol.py` — patrol 成功不写 trace / 失败写 trace / 退避计算 / manual-exit 跳出
5. `backend/tests/agent/test_manual_exit_release.py` — manual-exit 后下个 Plan 能 acquire 同 device
6. `backend/tests/services/test_failure_threshold_includes_backoff.py` — 退避中 job 计入 failure_threshold
7. `backend/tests/api/test_plan_run_aggregation_endpoints.py`(C5a₂)— 5 端点 15 cases:chain 链/timeline stage 聚合/events 多源融合/devices facet+派生/watcher-summary trend

## 不在本 ADR 范围

- **stall 检测**(D10): 留给后续 ADR
- **patrol 成功率聚合到 PlanRun.result_summary**: 已落地于 C5a₂ `/plan-runs/{id}/timeline` 聚合返回(无需写回 result_summary)
- **设备级"健康度评分"**: 长期治理,不在本期
- **退避策略的全局/Plan/Agent 三级覆盖优先级**: 本期只支持全局默认 + Plan 覆盖
