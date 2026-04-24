# adr-0018_plan_review_c2e4a8ed.plan.md 准确性评估

**评估对象**：`C:\Users\Rin\.cursor\plans\adr-0018_plan_review_c2e4a8ed.plan.md`  
**关联文档**：
- `docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md`
- `docs/implementation-plan-adr0018.md`
- `docs/implementation-plan-adr0018-assessment.md`  
**评估日期**：2026-04-08  
**评估范围**：仅评估文档结论是否准确，不涉及代码修改

---

## 总结结论

这份评审计划**部分准确，但不够严谨，不能直接作为最终修正版依据**。

更准确地说：

- 它正确发现了若干真实问题
- 也提出了不少合理修正方向
- 但它对 `Agent 状态上报迁移难度` 的判断偏乐观

一句话结论：

> 该文件可以作为“二次修订草案”，但不能认定为完全准确；尤其在“已有 HTTP 端点足以低风险替代 MQ 主路径”这一判断上，存在明显高估现有能力的问题。

---

## 准确的部分

以下判断是准确的：

### 1. dispatch 同步/异步存在自相矛盾

该评审指出：

- `implementation-plan-adr0018.md` 正文把 dispatch 改为 SAQ 异步
- 但文末“待确认项”又建议先保持同步

这一点判断正确。当前代码中：

- `backend/api/routes/orchestration.py` 仍是同步 `await dispatch_workflow(...)`
- 返回值仍依赖 `WorkflowRunOut` 和即时 `run.id`

因此该矛盾必须消除。

---

### 2. SocketIO 路径前后不一致

该评审指出：

- Phase 3.2 使用 `/sio`
- 决策区建议 `/socket.io`
- 前端示例又使用 `/sio/socket.io`

这一点判断正确，属于计划内部协议不一致问题。

---

### 3. `/metrics` 已经是 Prometheus 格式

该评审指出：

- Phase 5.1 中“如果当前是 JSON 格式，则改 Prometheus text format”这句已过时

这一点判断正确。当前 `/metrics` 已通过 `prometheus_client.generate_latest()` 输出 Prometheus exposition format。

---

### 4. `consumer.py` 也会触发 post-completion

该评审指出：

- 不仅 `agent_api.py`，`backend/mq/consumer.py` 也会在终态转换后调用 `run_post_completion_async()`

这一点判断正确，因此 SAQ 迁移不能只改一处触发点。

---

### 5. `backend/scheduler/dispatcher.py` 不存在

该评审指出：

- `implementation-plan-adr0018.md` 中提到的 `backend/scheduler/dispatcher.py（legacy）` 不存在

这一点判断正确。当前 `backend/scheduler/` 下并无该文件。

---

### 6. `ControlListener` 依赖 `MQProducer`

该评审指出：

- `backend/agent/mq/control_listener.py` 使用 `MQProducer`
- 如果 Phase 4 直接删除 `mq_producer`，控制链路会断

这一点判断正确，属于当前计划遗漏的重要迁移点。

---

## 不够准确的部分

以下判断存在偏差：

### 1. “已有 HTTP 端点足以承接 MQ 事件，因此原评估高估风险” 这一结论不准确

该评审认为：

- 服务端已经存在足够完整的 HTTP 端点
- 因此原评估将风险估计过高

这个结论偏乐观。

当前代码里，HTTP 端点确实存在，但并**不等价**于当前 MQ 主路径完整语义：

1. `POST /api/v1/agent/jobs/{job_id}/status`
   - 只负责状态转换与聚合
   - **不会释放设备锁**

2. `POST /api/v1/agent/steps`
   - 只做 `StepTrace` 幂等 upsert
   - `reconcile_step_traces()` 的终态重算逻辑也**不会释放设备锁**

3. `POST /api/v1/agent/jobs/{job_id}/complete`
   - 才会写入 `RUN_COMPLETE` 快照
   - 才会释放设备锁
   - 才会触发 post-completion

因此，不能简单说：

- “已有 HTTP 端点已经足够”

更准确的说法应是：

- “已有 HTTP 端点提供了迁移基础，但**还不能直接无缝替代**当前 MQ 主路径语义”

---

### 2. 它把“原评估要求补齐替代链路”误读成“需要从零新建能力”

原评估的重点是：

- 当前迁移计划没有定义完整闭环
- 所以不能直接删 MQ 主路径

而不是说：

- 仓库里完全没有任何 HTTP 能力

该评审在反驳时把这个区别抹平了，导致结论过度偏向“风险较低”。

---

### 3. 它提出的“新增 Phase 3.6，Agent 切到已有 HTTP 端点”方向对，但描述不完整

该评审建议：

- 将 `step_trace / job_status` 改走现有 HTTP 端点

这个方向本身是对的，但它没有充分说明：

- 如何覆盖终态 `complete_job` 与普通 `job_status` 的职责分工
- 如何保留 `RUN_COMPLETE` 快照
- 如何保证锁释放语义不丢
- 如何保证 MQ consumer 当前承担的补偿角色被安全迁出

所以这是一个**方向正确但描述不足**的修正方案。

---

## 综合判断

这份 `adr-0018_plan_review_c2e4a8ed.plan.md` 可以作为：

- **二次修订讨论稿**
- **问题补充清单**

但不适合直接作为：

- 最终修正版计划
- 最终准确性结论

其最主要的问题是：

- 正确识别了很多遗漏
- 但错误地把“已有 HTTP 端点存在”推导成“迁移风险明显降低”

这个推导并不成立。

---

## 最终评估结论

最终判断如下：

- **整体方向**：基本正确
- **问题识别能力**：较强
- **关键风险判断**：存在偏乐观问题
- **可作为最终依据**：否

一句话结论：

> `adr-0018_plan_review_c2e4a8ed.plan.md` 不是错误文档，但它只能作为修订草案，不能直接视为准确的最终评审结论。

