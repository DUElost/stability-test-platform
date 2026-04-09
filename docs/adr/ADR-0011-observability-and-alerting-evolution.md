# ADR-0011: 可观测性与告警体系演进
- 状态：Accepted（第一层指标已落地）
- 优先级：P1
- 目标里程碑：M2
- 日期：2026-02-18（2026-04-09 第一层落地）
- 决策者：平台研发组
- 标签：可观测性, 指标, 告警, 运维

## 背景

平台已具备 Prometheus 指标定义、运行日志和通知通道，但尚未形成统一的 SLO/告警基线与运维闭环。

## 决策

建立"指标 + 日志 + 告警"三位一体可观测性体系：

- 指标基线：
  - 主机心跳超时数
  - 任务派发延迟与失败率
  - 设备锁冲突与超时回收数
  - WebSocket 连接与错误
- 日志基线：
  - 统一结构化字段（run_id、task_id、host_id、device_id、event）。
  - 跨线程任务补充关联 ID，便于问题追踪。
- 告警基线：
  - 心跳异常、任务失败率突增、队列积压、部署失败。
  - 通过通知规则路由到 Webhook/邮件/钉钉。

## 备选方案与权衡

- 方案 A：仅保留日志人工排查。
  - 优点：无需额外建设。
  - 缺点：故障发现慢、定位成本高。
- 方案 B：当前提案（建立统一基线）。
  - 优点：可快速发现并定位回归问题。
  - 缺点：需要定义并持续维护阈值与仪表板。

## 影响

- 正向影响：提升线上可运营性，支撑规模化 Agent 接入。
- 代价：初期需要投入监控面板、告警策略与值班流程。

## 落地与后续动作

- ✅ 第一层（指标暴露）：Prometheus 指标定义 + `/metrics` 端点 + Grafana dashboard 模板。由 ADR-0018 Phase 5 落地。
  - 业务指标：task dispatch、device lock、task run、host/device、recycler、API 请求（共 18 项）
  - 框架指标：saq_tasks_total、saq_task_duration、saq_queue_depth、socketio_connections_active、apscheduler_job_runs_total、apscheduler_job_duration（共 6 项）
  - Grafana dashboard：`docs/grafana/stability-platform-dashboard.json`（7 分组、20 面板）
- ⬜ 第二层（告警规则）：选定 MVP 告警阈值并落地默认规则。
- ⬜ 第三层（运维闭环）：建立"部署后 30 分钟观测窗口"标准动作；结合回归数据持续调整阈值。

## 关联实现/文档

- `backend/core/metrics.py` — 全部 24 项 Prometheus 指标定义
- `backend/api/routes/metrics.py` — `/metrics` + `/metrics/health` 端点
- `backend/scheduler/app_scheduler.py` — APScheduler job 指标埋点 + SAQ queue depth 轮询
- `backend/tasks/saq_worker.py` — SAQ 任务指标埋点（before/after_process hooks）
- `backend/realtime/socketio_server.py` — SocketIO 连接指标埋点
- `docs/grafana/stability-platform-dashboard.json` — Grafana dashboard 导入模板
- `backend/services/notification_service.py`
- `backend/scheduler/recycler.py`
- `docs/preprod-drill-runbook.md`
- `docs/production-minimum-deployment-checklist.md`
