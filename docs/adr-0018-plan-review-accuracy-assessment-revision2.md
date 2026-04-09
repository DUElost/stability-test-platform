# adr-0018_plan_review_c2e4a8ed.plan.md 准确性评估（Revision 2）

**评估对象**：`C:\Users\Rin\.cursor\plans\adr-0018_plan_review_c2e4a8ed.plan.md`  
**评估版本**：用户修改后的 Revision 2 版本  
**关联文档**：
- `docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md`
- `docs/implementation-plan-adr0018.md`
- `docs/implementation-plan-adr0018-assessment.md`
- `docs/adr-0018-plan-review-accuracy-assessment.md`  
**评估日期**：2026-04-09  
**评估范围**：仅评估文档结论是否准确，不涉及代码修改

---

## 总结结论

修订后的 `adr-0018_plan_review_c2e4a8ed.plan.md` 明显比上一版更准确，已经可以作为 `implementation-plan-adr0018.md` 的高质量修订草案使用。

但它还不能视为“完全准确”的最终稿。

更准确地说：

- 它已经正确识别了 `step_trace` 才是 MQ 清理前的真实主阻塞项
- 它也重新校正了终态 `job_status` 的主路径与补偿路径关系
- 但它对 `PENDING_TOOL` 和 `step_trace HTTP replay` 的现状判断仍有偏差

一句话结论：

> 该文件目前可评为“基本准确”，大约 85% 准确；可作为计划修订依据，但在 `PENDING_TOOL` 与 `step_trace` 回放能力两处仍需再修正一次。

---

## 准确的部分

### 1. `step_trace` 是 MQ 删除前的最高风险项

这一判断正确。

当前代码里：

- Agent 执行过程仍通过 `PipelineEngine._report_step_trace_mq()` 上报步骤事件
- 这些事件写入 Redis `stp:status`
- 服务端由 `consumer.py._persist_step_trace()` 落库

说明 `step_trace` 目前仍主要依赖 MQ 持久化链路。

相关位置：

- `backend/agent/pipeline_engine.py`
- `backend/mq/consumer.py`

---

### 2. 终态 `job_status` 的主路径是 HTTP `complete_job`

这一判断正确。

当前代码里：

- Agent 在任务结束后调用 `complete_run(...)`
- 该调用最终走 `POST /api/v1/agent/jobs/{job_id}/complete`
- `complete_job` 才负责：
  - 写入 `RUN_COMPLETE` 快照
  - 释放设备锁
  - 触发 post-completion

而 MQ consumer 中的终态处理明确是补偿路径。

相关位置：

- `backend/agent/main.py`
- `backend/api/routes/agent_api.py`
- `backend/mq/consumer.py`

---

### 3. 原生 WS fallback 的 `step_trace / job_status` 实际不工作

这一判断正确。

当前 Agent 在 MQ 不可用时会经 WS fallback 发送：

- `type: "step_trace"`
- `type: "job_status"`

但服务端 `/ws/agent/{host_id}` handler 只处理：

- `pong`
- `log`
- `step_update`
- `heartbeat`

并没有对应的 `step_trace` / `job_status` 分支，因此这两类 fallback 消息当前会被静默丢弃。

---

### 4. 修订后的 Phase 调整方向基本成立

以下修正方向判断正确：

- Phase 2 改为 `dispatch` 保持同步，仅将 post-completion 迁到 SAQ
- SocketIO 路径统一为 `/socket.io`
- 将 Agent 状态上报迁移单独抽成新增阶段
- 将 `consumer.py`、`ControlListener`、`stp:logs` 读取点一起放入后续 Redis 清理阶段
- 删除 `backend/scheduler/dispatcher.py` 这一不存在的引用

这些修正与当前仓库实现是一致的。

---

## 仍不准确的部分

### 1. `PENDING_TOOL` 被写成“当前可正常承接”，这一点不准确

这是当前修订版里最明显的剩余问题。

文档将 `PENDING_TOOL` 描述为：

- 当前通过 MQ 可落库
- 将来通过 `/jobs/{job_id}/status` 可直接承接

但当前状态机定义并不支持这一判断。

`VALID_TRANSITIONS` 里只有：

- `PENDING -> RUNNING`
- `RUNNING -> COMPLETED/FAILED/ABORTED/UNKNOWN`
- `PENDING_TOOL -> PENDING`

并没有：

- `RUNNING -> PENDING_TOOL`

而 Agent 在真正执行前，服务端已经将 job 置为 `RUNNING`，Agent 还会额外发 heartbeat 确认。

因此当前 Agent 在执行中上报 `PENDING_TOOL` 时：

- MQ consumer 里大概率会触发 `InvalidTransitionError`
- HTTP `/status` 端点同样也不能直接承接

所以更准确的表述应当是：

> `PENDING_TOOL` 不是“现成可迁移路径”，而是“当前语义和状态机都需要先澄清/修正”的迁移点。

---

### 2. “当前 Agent 仅在 reconnect 场景下批量重放 step_trace 到 HTTP” 缺少代码依据

这条表述偏乐观。

当前代码里确实存在：

- `step_trace_cache`
- `get_unacked_traces()`

但我没有找到现成的逻辑把这些 trace 批量上传到：

- `POST /api/v1/agent/steps`

现有 `mark_acked()` 发生在 Redis `XADD` 成功后，而不是 HTTP 上传成功后。

所以更准确的说法应当是：

> 服务端已经有 `POST /api/v1/agent/steps` 这一能力，但 Agent 侧当前并不存在明确可用的 step_trace HTTP replay uploader。

---

### 3. `3.7.1 方案 A` 不是单纯实现细节，而是架构取舍

文档把“SocketIO handler 收到 step_trace 后直接写 DB”列为推荐方案，这个方向技术上可行。

但如果采用该方案，系统就不再是严格意义上的：

- `HTTP 是唯一权威路径`

而会变成：

- `SocketIO 也承担权威持久化入口`

这不一定错误，但必须显式写成架构取舍，否则会和前面 ADR 讨论中的边界原则混淆。

更稳妥的写法应是：

- 若选方案 A，需要在计划中明确声明“SocketIO 兼具实时通道与权威写入入口”
- 若坚持“HTTP 权威、实时只展示”，则应选方案 B

---

## 综合判断

与上一版相比，这份修订版已经从“部分准确”提升到了“基本准确”。

它已经成功修正了此前最关键的几项偏差：

- 不再把终态 `job_status` 风险评估得过高
- 正确认出 `step_trace` 才是 Redis Streams 清理前的真实主风险
- 补充了 `consumer.py`、`ControlListener`、日志读取点等实际遗漏项

但它仍不适合直接当作“最终无误版本”，因为以下两点还没压实：

1. `PENDING_TOOL` 的当前有效性判断
2. `step_trace` 是否已有现成 HTTP replay 路径

---

## 最终评估结论

最终判断如下：

- 该文件已经可以作为 `implementation-plan-adr0018.md` 的修订依据
- 但仍建议在正式采纳前，再修正 `PENDING_TOOL` 与 `step_trace replay` 两处表述
- 修正后，这份文档才适合充当最终版评审结论

一句话结论：

> `adr-0018_plan_review_c2e4a8ed.plan.md` 的 Revision 2 版本目前“基本准确但未完全收敛”；适合作为高质量修订草案，不宜直接视为最终准确结论。
