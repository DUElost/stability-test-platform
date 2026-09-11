# Grafana 仪表板指标：补真实生产者 + 撤无源面板 + 幽灵门禁（#1258 / R14-F12）

Status: implemented
Class: bug-fix

## Decision

本质问题：仪表板 11 个面板引用「有定义、无采集」或「不存在」的指标——面板恒空
或报 no data，观测面说谎。逐面板二选一（补真实生产者 / 撤下），并加机械门禁：

**补（有真实数据源、接入点低风险）**

1. `stability_host_online` / `stability_device_online`：`/metrics` 拉取时现算
   （`backend/api/routes/metrics.py::_refresh_fleet_gauges`——按 hosts/devices
   当前状态 group by 计数，label 用小写枚举值与仪表板 PromQL 对齐）。拉取模型
   无周期任务 staleness；DB 抖动时捕获 `SQLAlchemyError` 仅跳过舰队计数，
   观测面不整体 500；
2. `stability_host_heartbeat_missed_total`：`session_watchdog` 判定心跳超时
   并置 OFFLINE 处 `.labels(host_id=...).inc()`（生产者 = 真实超时事件）。

**撤（无数据源 / 接入点缺失）**

3. 仪表板移除 9 个元素：7 个面板（Job Runs by Status、Task Dispatch Latency、
   WebSocket Messages、Device Temperature、Device Battery、API Request
   Duration/Rate）+ 2 个空行分隔（Job Status Distribution、API Performance）；
4. `backend/core/metrics.py` 删除 3 个全仓零引用的死定义：`task_dispatch_latency`
   （0 引用）、`device_temperature` / `device_battery_level`（devices 表列存在
   但全仓无写入点，遥测链路未建）。

**门禁（新契约测试）**

5. `tests/test_grafana_dashboard_contract.py`：① 幽灵序列——仪表板引用的
   `stability_*` 必须存在于指标注册表；② 无生产者清单——已知无生产者的
   `api_requests*` 不得出现在仪表板；
6. 注册表索引助手抽到 `tests/metrics_registry.py`（告警契约 #1257 与仪表板
   契约共用，避免两处维护同一段注册表私有结构适配）。

**未完成（deferred，待后续）**

- API 请求指标中间件（`record_api_request` 仍无调用方）：面板已撤；接入时需
  路径模板化控制 label 基数，落地后恢复两个面板并更新门禁清单；
- 设备温度/电量遥测链路：Agent 上报 → devices 列 → Gauge 未建成；建成后
  恢复 gauge 定义与面板。

## Alternatives

- **全部撤下（只做减法）**——放弃：host/device 在线与心跳超时三个面板有真实
  数据源（DB 状态 / 超时事件），低成本补齐即可恢复观测能力；
- **全部补齐（含 API 中间件 / WebSocket 计数 / task_run_total 全路径）**——
  本单不做：触点跨 main.py 中间件链 / realtime / 调度核心，风险与工期上升；
  `stability_websocket_messages_sent_total` 在 metrics.py 根本不存在，要建需
  先定指标与 emit 封装点；
- **周期任务推模式（watchdog 刷 Gauge）**——放弃：拉取现算无 staleness、少一处
  跨模块耦合；watchdog 只承接事件型计数（心跳超时）；
- **在 metrics.py 内直接查 DB**——放弃：core 层不引 DB，查询留在 API 路由层；
- **给 task_run_total 面板补更多写入点**——放弃：写入路径分散在任务终态多处
  （recycler 仅覆盖 failed/plan），本单不做；面板已撤、metric 保留。

## Verification

实际运行（worktree `/tmp/stp-1258`，2026-09-11）：

- `pytest tests/test_grafana_dashboard_contract.py tests/test_prometheus_alerts_contract.py -q`
  → **5 passed**；
- `pytest backend/tests/api/test_metrics_fleet_gauges.py backend/tests/tasks/test_session_watchdog.py -q`
  → **3 passed**（/metrics 明文断言 7 个 label 值；心跳超时后 counter=1.0）；
- `pytest backend/tests/api/test_metrics_auth.py -q` → **8 passed**（/metrics 鉴权面回归）；
- `pytest tests/ -q` → **148 passed**；
- `ruff check .` → All checks passed；
- `check:quick` → **7 gates 全绿**；
- 仪表板 diff 为**纯删除**（-198 行、无格式噪声）；死定义在 backend/tests/docs
  零残留（grep 验证）。

未完成（pending）：生产 Grafana 挂载后的面板渲染验证——本机未挂载 Grafana；
数据链路已由 `/metrics` 明文断言覆盖。

## Revisit

- API 中间件落地时：更新 `tests/test_grafana_dashboard_contract.py` 的
  `UNPRODUCED_METRICS` 清单并从本 Note 移除对应待办；
- 设备遥测链路立项时：恢复 `stability_device_temperature_celsius` /
  `stability_device_battery_level_percent` 定义与面板；
- 若 `/metrics` 拉取现算在更大舰队上出现可观测延迟，改为周期推送或短 TTL 缓存。
