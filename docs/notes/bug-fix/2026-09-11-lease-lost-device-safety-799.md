# lease 丢失：先杀在跑脚本、设备占位留到进程退出（#799 / R07-F01）

Status: implemented
Class: bug-fix

## Decision

根因：`_on_lease_lost`（`main.py`）只做内存清理
（`_cleanup_after_lease_lost` 摘 `active_job_ids`/`active_device_ids`）+
`coordinator.cancel_waiting_job`：

- **不杀在跑脚本**：runner 只在步骤边界检查 `_is_aborted()`，lease 丢失后
  旧进程继续驱动设备直到当前步骤结束；
- **设备占位立即释放**：`_active_device_ids.discard(device_id)` 让设备马上回到
  本机认领池；分区恢复后后端可把同一物理设备派给**另一台 host**（跨 host 无
  单设备护栏）→ 双 host 同时 adb/刷写同一设备。

修复（复用既有机制，不引新 wheel）：

1. 新增模块级 `handle_lease_lost(...)`（从 main 闭包抽出，便于单测），顺序：
   ① `job_runner_state.request_abort(job_id)` → `runner.cancel()`（killpg 入口）
   并置 `abort_requested_job_ids`；② `_cleanup_after_lease_lost(...)`；
   ③ `coordinator.cancel_waiting_job(job_id)`；
2. `_cleanup_after_lease_lost` 增 `keep_device_slot=False` 参数：**abort 已派发
   时保留设备占位与归属**，直到 worker 真正退出——由 `JobRunnerState.release()`
   的归属感知补偿（#1006/#1203）在 worker 收尾时清理；
3. 无活跃 job（`request_abort=False`，无进程可杀）时维持原语义：立即清占位，
   不把设备无谓锁死。

顺序说明：旧注释要求「先清理再唤醒等待者，保证 `_is_aborted()` 真」。实测
`JobRunnerState.is_aborted()` 的判据是
`job_id in abort_requested_job_ids or job_id not in active_job_ids or token 不匹配`
——`request_abort` 先行使第一条成立，等待者照样退出；该顺序与既有控制面
abort 路径（`main.py` 控制命令 `abort`：request_abort → cancel_waiting_job）
一致，不再是两套顺序。

## Alternatives

- **只杀进程、占位照旧立即清**：仍留「进程收尾 vs 设备回池」窗口，跨 host 双驱
  没有被硬保证封死——否决；
- **只延后占位、不杀进程**：设备被占位锁住但旧进程仍在驱动，等于本机自己
  双驱 + 设备长期不可用——否决；
- **worker 不退出的看门狗（定时强清占位）**：worker 退出是自然信号，引入定时器
  只会增加状态面；确需兜底的场景（线程真挂死）见 Revisit；
- **控制面派发时按设备互斥**：更彻底但属 R06 调度域，且本单的 agent 侧硬保证
  是前提（控制面无法知道旧进程是否真的停手）——不在本单范围。

## Verification

实际运行：

- 新增 `backend/agent/tests/test_lease_lost_device_safety.py` → **3 passed**
  （杀 runner + 占位保留到 release 才清；无活跃 job 立即清占位；main 接线
  静态断言）；
- `backend/agent/tests` 全量 → **1550 passed**（含相邻 `test_recovery_executor.py`
  / `test_fencing_token.py` / `test_lease_renewer.py` 回归）；
- `ruff check backend/agent/main.py backend/agent/tests/test_lease_lost_device_safety.py`
  → All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- **真机跨 host 双驱注入验证**：需两台 host + 后端分区（>lease TTL + UNKNOWN
  grace）才能复现原窗口；本机不具备双 host 隔离环境，未执行。可验证点：分区
  期间旧进程被 cancel（日志 `run_cancel_failed`/进程组退出）、设备占位保持到
  worker 退出、后端复派被本机护栏拒绝或派给其他 host 时旧进程已停。

## Revisit

- 若现场出现「worker 被 cancel 后长时间不退出，设备占位锁死」：评估加有界
  watchdog（超时后强清占位 + 记审计），但必须先有实测数据，不提前设计；
- `request_abort` 仅在 `job_id in active_job_ids` 时生效；若未来引入「不在活跃
  集合但仍有 runner」的形态，需同步扩展（当前 runner 生命周期与活跃集合同源，
  无此形态）；
- 跨 host 单设备互斥若仍需上层兜底，转 R06 调度域另单（本单已把 agent 侧
  「本机不再驱动」变成硬保证）。
