# implementation-plan-adr0018 评估结论

**评估对象**：`docs/implementation-plan-adr0018.md`  
**关联 ADR**：`docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md`  
**评估日期**：2026-04-08  
**评估范围**：仅评估计划可行性，不涉及代码修改

---

## 结论摘要

当前这版 `implementation-plan-adr0018.md` 不是“不可行”，但**不能按原文直接执行**。

更准确的判断是：

- **方向可行**：与最新 `ADR-0018` 的目标架构一致，框架替代边界基本合理
- **实施顺序需修正**：若不先补齐若干替代链路，迁移过程中会出现断链
- **计划状态应视为“可采纳但不可直接执行”**

一句话结论：

> `implementation-plan-adr0018.md` 目前是“可采纳但不可直接执行”的状态；先补齐 `Agent 状态上报替代链路` 和 `dispatch 是否异步化` 两个关键决策后，这个计划才适合落地。

---

## 正向判断

以下部分是成立的：

1. 用成熟框架替换基础设施层，而不是替换 Agent Pipeline 内核，这个边界是正确的。
2. 用 `APScheduler / SAQ / python-socketio` 分别接管调度、异步后台任务、实时通道，这一总体方向与 `ADR-0018` 一致。
3. 迁移顺序按“调度层 -> 后台任务 -> 实时层 -> Redis 清理”推进，宏观顺序合理。
4. `HTTP 是权威路径，实时通道只承担展示` 的原则没有与现有 Accepted ADR 冲突。

---

## 主要问题

### 1. Phase 4 删除 `agent/mq/producer.py` 的前提不成立

计划在 Phase 4 中删除 `backend/agent/mq/producer.py`，但当前 Agent 执行引擎仍依赖该链路上报：

- `step_trace`
- `job_status`

这些不是单纯的日志流，而是当前系统运行状态闭环的重要组成部分。计划里只安排了“日志迁移到 SocketIO”，没有先把上述事件迁到新的权威路径，因此会在 Redis 清理阶段断链。

相关现状：

- `backend/agent/pipeline_engine.py` 仍通过 `_report_step_trace_mq()` / `_report_job_status_mq()` 上报运行态事件
- `backend/agent/main.py` 仍向 `PipelineEngine` 注入 `mq_producer`
- `backend/main.py` 仍启动 `consume_status_stream()` / `consume_log_stream()` / `monitor_backpressure()`

判断：

- 这是当前计划的**最高优先级阻塞项**

---

### 2. Step 状态在目标链路中的归属仍未定义清楚

计划要求：

- `SocketIO handler` 不做数据库写入
- 日志流迁移到 `SocketIO`

这个方向与 `ADR-0018` 一致，但当前文档没有明确：

- `StepTrace` 的**权威持久化**最终由谁承担
- Agent 执行过程中的 `STARTED / COMPLETED / FAILED` 步骤事件，是走 HTTP 主路径、补偿路径，还是仍保留某种异步中间层

如果这个问题不先明确，迁移到 `python-socketio` 后很容易出现：

- 前端能看到实时步骤变化
- 但数据库里没有等价的权威状态

判断：

- 这是第二个关键阻塞项

---

### 3. Phase 2 默认把 dispatch 改成 SAQ 异步，但文档末尾又建议先保持同步

计划正文在 Phase 2 中把 dispatch 改为：

- API 立即返回 `pending`
- 由 SAQ 异步创建 `WorkflowRun`

但文档末尾“决策待确认项”中又建议：

- **先保持 dispatch 同步**
- 仅将 post-completion 异步化

这说明计划主路径与决策状态并不一致。当前这个问题不是实现做不到，而是：

- 前端接口语义
- 用户体验
- `workflow_run_id` 返回时机

都还没有最终定稿。

判断：

- 若直接按当前 Phase 2 实施，高概率导致前后端联调返工

---

### 4. SocketIO 挂载路径方案前后不一致

计划 Phase 3.2 写的是：

- `app.mount("/sio", sio_app)`

但文档末尾推荐的是：

- 采用默认 `/socket.io`

这不是架构级问题，但属于实施前必须收口的接口规范问题。否则：

- 前端 hook
- Agent 客户端
- 服务端挂载配置

会在迁移中出现路径不一致。

判断：

- 不是阻塞架构可行性的硬伤
- 但属于实施前必须明确的协议项

---

### 5. Phase 5 有部分工作项与现状不一致

计划写到：

- 如果当前 `/metrics` 还是 JSON，则改为 Prometheus text format

但现状已经是 Prometheus exposition format。

判断：

- 这不会阻塞实施
- 但说明计划仍有部分内容没有完全对齐当前代码

---

## 与最新 ADR 的一致性判断

结合最新文档状态：

- `ADR-0018` 当前状态为 `Proposed`
- `ADR-0002` 已明确“后台线程/异步任务的启动方式将由 ADR-0018 接管”
- `ADR-0006` 已明确“WebSocket 实现层将由 ADR-0018 替代，但 REST + WS 分工原则保留”

因此，`implementation-plan-adr0018.md` 作为：

- **目标架构实施计划**：成立
- **当前即可原样执行的迁移手册**：不成立

---

## 建议的修正方向

建议把实施顺序调整为以下形式：

1. **Phase 1 保留**
   - 先替换调度层：`cron_scheduler / recycler / session_watchdog`

2. **Phase 2 缩小范围**
   - 先只做 `post-completion -> SAQ`
   - `dispatch` 保持同步

3. **新增 Phase 2.5：补齐 Agent 状态上报替代链路**
   - 明确 `step_trace / job_status` 的新主路径
   - 在这一步完成前，不删除 `mq_producer`

4. **Phase 3 再做 python-socketio**
   - 仅接管日志与展示事件
   - 不承担权威状态持久化

5. **Phase 4 最后清理 Redis Streams**
   - 仅在 `stp:status / stp:logs` 已无主路径依赖后执行

---

## 建议优先确认的决策项

实施前建议优先确认以下两项：

1. **Dispatch 是否异步化**
   - 建议短期保留同步
   - 先避免改动 API 语义和前端交互模型

2. **Agent 的 `step_trace / job_status` 如何替代 MQ 主路径**
   - 这是 Redis 清理能否成立的前提
   - 也是 `SocketIO 只负责展示` 能否成立的前提

---

## 最终评估结论

`implementation-plan-adr0018.md` 当前版本：

- **架构方向正确**
- **阶段拆分基本合理**
- **但缺少关键迁移闭环**

因此最终结论为：

> 该计划可以作为 `ADR-0018` 的实施基础，但在补齐 `Agent 状态上报替代链路` 与 `dispatch 同步/异步决策` 之前，不建议按原文直接执行。

