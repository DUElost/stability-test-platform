# 幽灵配置清理与死打点收敛（#737 切片）

Status: implemented
Class: bug-fix

## Decision

#737 的可判定切片：清掉示例里的**幽灵配置**、收敛 metrics 的**零调用打点**，并把
「示例键必须有读取点」固化成机械门禁。

### 1) 幽灵配置：9 个（不是 issue 写的 13 个）

先复核再动（issue 立于 2026-09-02，其间已有变化）：

| issue 口径 | 实测（2026-09-13 origin/main） | 处置 |
|---|---|---|
| `UNKNOWN_GRACE_SECONDS` | **正在被读**（`backend/core/job_timeout_config.py:92`） | 保留 |
| `CORS_ORIGINS` / `CORS_ALLOW_METHODS` / `CORS_ALLOW_HEADERS` | **正在被读**（`backend/core/cors.py:15-17`，含越界校验） | 保留 |
| 其余 9 个 | 全仓零读取点 | 删除（见下） |

删除与去向：

- `backend/.env.example`、`deploy/control-plane/env/.env.backend.example`、
  `.env.backend.internal.example`：删 `HEARTBEAT_TIMEOUT_SECONDS` /
  `HEARTBEAT_CHECK_INTERVAL_SECONDS`（旧名）、`BACKPRESSURE_LAG_THRESHOLD` /
  `BACKPRESSURE_RELEASE_THRESHOLD` / `BACKPRESSURE_LOG_RATE_LIMIT`（Phase 4 已移除
  Redis 背压，`agent_api._get_backpressure()` 恒返回 None）、`USE_SESSION_WATCHDOG`
  （watchdog 为常驻调度，无开关）。原位置留一行说明指向真实开关。
- `backend/.env.example` 补齐两个真身的注释条目：`RUNNING_HEARTBEAT_TIMEOUT_SECONDS`
  （默认 900，`RUN_HEARTBEAT_TIMEOUT_SECONDS` 为兼容别名）与
  `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS`（默认 300）——与
  `backend/core/job_timeout_config.py` 的默认值表同源。
- `backend/agent/.env.example`：删 `TASK_TIMEOUT` / `BATCH_SIZE` /
  `DEVICE_DISCOVERY_TIMEOUT`（任务超时归控制面 `job_timeout_config`；发现/心跳节奏
  的真实旋钮是 `POLL_INTERVAL` 与 `STP_HEARTBEAT_INTERVAL_MIN/MAX`），留注记指路。

### 2) 死打点：删 4 个零调用包装 + 1 个纯死对象

| 对象 | 生产调用 | 处置 |
|---|---|---|
| `count_exceptions`（装饰器，无绑定指标） | 0 | 删 |
| `record_task_run_status` | 0 | 删（`task_run_total` 对象由 `scheduler/recycler.py:460` 直接写，保留） |
| `record_device_lease_acquired` + `device_lease_acquired` | 0（对象也零引用） | 删 |
| `record_device_lease_released` | 0 | 删（`device_lease_released` 对象由 recycler 直接写，保留） |
| `record_api_request` + `api_requests` / `api_request_duration` | 0 | **保留**：#1258 明确 deferred（中间件未落地、面板已撤），`tests/test_grafana_dashboard_contract.py` 的 `UNPRODUCED_METRICS` 清单在案；本次仅补 docstring 说明 deferral 出处 |

### 3) 门禁：`tests/test_env_example_parity.py`

8 个 `.env*.example` 的**未注释键**必须出现在仓库语料中（字符串字面量 /
`import.meta.env.X` / compose 插值 / unit 文件等任意形态；语料排除示例文件自身
与 `.git`/`.wt`/`node_modules`/`__pycache__`）。失败信息要求二选一：补读取点或删键。

## Alternatives

- **接线而非删除**（把 recycler 改用 `record_*` 包装）：行为等价，但多两处跨文件
  改动，收益只是「包装被使用」；删除同时消除「对象直写 + 包装」双入口，更干净；
- **连 `api_request*` 一起删**：否决——#1258 已有 deferred 裁决与契约清单，删掉会让
  将来接中间件时重建定义；
- **只清一次、不加门禁**：否决——issue 本身即「幽灵配置靠人眼回归」的实证；
- **本单同时补 173 个未记录变量的文档**：超切片（属文档面），留 Revisit。

## Verification

- 全量对齐扫描（8 个示例 × 全部未注释键 × 仓库语料）：**改前 9 个无引用键；改后全部 0**；
- `pytest tests/`（含新门禁 + grafana/prometheus 契约 + prepare_env）：**312 passed**；
- `pytest backend/tests/services/test_adr0026_p0_metrics.py backend/tests/api/test_metrics_counter_drift.py`：6 passed；
- **反向验证**：临时向 `backend/.env.example` 追加 `GHOST_FOR_MUTATION_TEST=1` → 门禁立即红
  （`assert not ['backend/.env.example::GHOST_FOR_MUTATION_TEST']`），还原即绿；
- `python scripts/run_gates.py check:quick`：7 gates 全绿。

## Revisit

- **反向方向的门禁**（代码读取 → 示例登记）：与 173 个未记录变量的文档化同批做，
  需先定「必录 vs 可选」判据，避免把内部开关强行公开；
- API 请求中间件落地后：恢复仪表板面板、更新 `UNPRODUCED_METRICS`、复用
  `record_api_request`；
- **注释态键**（如 `# DEVICE_LOCK_LEASE_SECONDS=600`）当前不入门禁；若将来注释幽灵
  变多，再评估把注释键纳入「需有读取点」的第二阶段。
