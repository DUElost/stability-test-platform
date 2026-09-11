# R07-F14 落地：Coordinator PlanRunHost 投影回收（#1014）

Status: implemented
Class: bug-fix

## Decision

`HostRunCoordinator` 随 claim 持续 `register_plan_run_host`（`_plan_run_hosts`
按 host_row_id 累计）；`deregister_plan_run_host` 存在但**正常 Job 退出路径
从不调用**（仅 stale reconcile 可能 pop）；`_tick` 每次发送全部投影，控制面
逐项读写。长期运行下内存、心跳载荷、控制面写负担线性增长（未测量生产规模，
审查列为设计风险）。

修复（回收 + 防御上限双策，满足验收「测量或上限策略」）：

1. **事件驱动回收**：`deregister_job`（job_runner `run_task_wrapper` finally
   已调用）在该 job 所属 PlanRunHost 不再有活跃 job 时回收投影：从
   `_plan_run_hosts` 移除 + epoch 持久键删除（下一进程按默认 epoch 恢复，
   fencing 语义不变——旧 epoch 的 stale 判定不依赖该键存在）；
2. **防御上限**：`COORDINATOR_MAX_PLAN_RUN_HOSTS`（默认 200）——`register`
   超限时兜底清理**无活跃 job 关联**的投影（覆盖「claim 登记后 job 始终
   未启动」的窗口泄漏），有活跃 job 的投影不动；
3. `local_db.delete_state` 新方法（agent_state 键删除）。

Barrier 语义不受影响：回收仅在 prh 无活跃 job 时发生，等待方（peers_of /
wait_barrier）只关心活跃 job 视图。

## Alternatives

- **TTL/时间裁剪（周期扫描 last_active 超时）**——放弃：事件驱动更精确且
  零延迟；投影的终点即最后一个 job 结束，无需时间维度；
- **发送侧过滤（_tick 只发有活跃 job 的 prh）**——放弃：仅治心跳载荷，
  内存与控制面注册语义仍累计；根因是生命周期终点缺失；
- **仅加上限不回收**——放弃：上限会误伤活跃运行（大量并发 PlanRun 时），
  回收才让上限退化为纯兜底。

## Verification

- **反例实证**：回退 coordinator/local_db 保留测试 → 4 用例失败（投影
  不回收、历史累计、epoch 不清理、无上限兜底）；修复版全绿；
- 新增用例（`test_coordinator_peers.py::TestProjectionReclaim` +4）：
  最后 job 注销才回收 / **50 轮历史循环投影不累计**（验收 2 对拍）/
  回收时 epoch 键删除 / 上限兜底保留活跃投影；
- `backend/agent/tests/` 全套 **1551 passed**（2m19s，含 coordinator
  peers/barrier 与 run_task_wrapper 集成回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 生产规模未测量（#1014 原文）；本修复将投影数量从「历史 PlanRun 数」降为
  「当前活跃 PlanRun 数」，若生产仍观测到异常心跳载荷再评估批量上报；
- 防御上限的回收不保证持久键清理（锁内收集、锁外删——已实现 keys 清理，
  极端异常路径仅 debug 日志）。
