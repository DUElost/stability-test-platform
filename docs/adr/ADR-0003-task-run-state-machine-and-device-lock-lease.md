# ADR-0003: 任务状态机与设备锁租约机制
- 状态：Accepted
- 日期：2026-02-18
- 决策者：平台研发组
- 标签：状态机, 并发控制, 设备锁, 回收

## 背景

任务执行存在并发争抢设备、Agent 异常退出、长任务中断等风险。系统需要保证“一个设备同一时间只被一个 run 占用”，并能自动回收异常状态。

## 决策

采用“任务状态机 + 设备锁租约 + 回收器”的组合策略：

- 状态机：
  - `Task`: `PENDING -> QUEUED -> RUNNING -> COMPLETED/FAILED/CANCELED`
  - `TaskRun`: `QUEUED -> DISPATCHED -> RUNNING -> FINISHED/FAILED/CANCELED`
- 锁模型：
  - 设备表维护 `lock_run_id` 与 `lock_expires_at`。
  - 派发时原子加锁；Agent 执行期定时续租；完成或失败时释放。
- 异常补偿：
  - Recycler 检测 `DISPATCHED`/`RUNNING` 超时与锁过期，标记失败并释放设备。

## 备选方案与权衡

- 方案 A：仅靠设备状态字段（无租约、无过期）。
  - 优点：实现简单。
  - 缺点：Agent 异常后容易出现死锁设备。
- 方案 B：当前方案（租约 + 回收）。
  - 优点：容错能力更强，支持长任务。
  - 缺点：状态转换复杂，需要严格一致性校验。

## 影响

- 正向影响：并发调度安全性显著提升，降低设备“永久 BUSY”概率。
- 代价：代码路径复杂，需要完善测试覆盖状态转换与超时场景。

## 落地与后续动作

- 已落地：设备锁加锁/续租/释放 API 与回收器超时处理。
- 后续：补充“分布式任务（group）一致性收敛”的更强语义与回归测试。

## 关联实现/文档

- `backend/scheduler/dispatcher.py`
- `backend/api/routes/tasks.py`
- `backend/scheduler/recycler.py`
- `backend/agent/main.py`
- `backend/models/schemas.py`
