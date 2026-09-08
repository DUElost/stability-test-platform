# 进程树收敛判据改为进程组级（父退出 ≠ 整组退出）

Status: implemented
Class: bug-fix

## Decision

#1003（R07-F02）：`_terminate_process_tree` 把「父进程已退出/已回收」当作「进程
树已清理」——

- `proc.poll() is not None` 时立即 return；
- SIGTERM 后 `proc.wait(grace)` 成功也立即 return。

父脚本退出但同组子孙忽略 SIGTERM 时，这两条都会让清理提前结束：Agent 继续上报
终态、释放许可，残留进程仍在操作设备（交接面 R06 的设备所有权）。

修复：收敛判据从「父进程」换成「进程组」，TERM→KILL 两阶段都用同一个判据。

- 新增 `_remember_process_group(proc)`：`Popen` 之后立刻把 `os.getpgid()` 存到
  `proc._stp_pgid`。**必须在任何 wait/poll 之前** —— 父被回收后 `getpgid` 会
  ESRCH，pid 还可能已被复用；而我们要处理的恰好是「父已退出」的情形。
- 新增 `_resolve_pgid`：优先用留存值，否则趁父存活现取；取不到返回 `None` 并
  放弃动手（不用可能已复用的 pid 去 `killpg` 一个陌生进程组）。
- 新增 `_process_group_alive(pgid)`：`killpg(pgid, 0)` 探测，ESRCH=整组已散，
  其余按「仍在」处理（宁可多收敛一次）。
- 新增 `_await_tree_exit(proc, pgid, timeout)`：等到「父已回收 **且** 整组已散」。
  循环里的 `poll()` 顺带回收僵尸父进程 —— 否则父的僵尸项本身会让 `killpg(0)`
  一直成功，永远探不到真实残留。
- 主流程：组已散则直接返回（保留「已退出 no-op」语义）；否则 SIGTERM → 等整组
  收敛 → 未收敛则记 `process_group_alive_after_sigterm` 警告并升级 SIGKILL →
  仍未收敛才 `process_tree_did_not_exit_after_sigkill`。
- 两阶段各占 `grace_seconds`，最坏耗时与修复前（两次 `proc.wait(grace)`）持平。
- Windows 分支不变：`taskkill /T /F` 本就扫整树，保留 `poll() is not None`
  提前返回（无组语义可依）。

## Alternatives

- 只把 entry 的 `poll()` 判断换成「组是否为空」：修不住第二条（`wait()` 成功即
  return），那是超时/取消路径的主路径。
- 用 `pgid == proc.pid` 硬编码（隔离起组时成立）：省一次 `getpgid`，但把
  `_popen_isolation_kwargs` 的实现细节钉进清理逻辑，且父已退出时同样要防止 pid
  复用 —— 不如留存实际 pgid 可靠。
- 扫 `/proc/*/stat` 找同组进程：Linux-only 且比 `killpg(0)` 更贵；`killpg(0)`
  是内核现成的组级存在性判据。

## Verification

- `pytest backend/agent/tests/test_pipeline_engine_process_group.py`：14 passed
  （CI 同款 env：`TESTING=1` + 占位 `DATABASE_URL`）。
- 新增真实进程组回归 `test_terminate_reaps_orphaned_descendants_after_parent_exit`：
  `sh` 立刻退出、后台 python 子孙 `SIG_IGN` SIGTERM；先断言组仍存活（用例前提），
  再断言 `_terminate_process_tree` 返回后整组已散。修复前该路径一条信号都不会发
  （`poll()` 非 None 直接 return），子孙必然漏网。
- 既有 `test_terminate_posix_sigterm_succeeds_no_sigkill` 原断言「wait 成功 →
  不再 SIGKILL」正是本单缺陷的固化，已按新语义改写（父已回收 **且** 组已散才
  不发 SIGKILL）；`test_terminate_no_op_when_proc_exited` 语义保持不变。
- `pytest backend/agent/tests`：1435 passed（全目录）。
- ruff 干净；Registry：fix-1003-process-tree-parent-exit 全程登记（--issue 1003）。

## Revisit

- 探测到「组仍在」到发出 SIGKILL 之间存在 pid/pgid 复用的理论窗口（组真散了才
  可能被复用，且我们随后仍只对该 pgid 发信号）；若将来需要绝对安全，可在
  Popen 时一并留存 `start_time` 做二次校验。
- 正常完成路径不会调用 `_terminate_process_tree`，故本次不触及「脚本后台常驻
  进程本该保留」的策略问题 —— 若将来出现该需求，应在调用点区分 cancel 与完成，
  而不是放宽组级收敛判据。
