# UNISOC scan 环境变量误配致 scan 队列永久停摆（#754）

Status: implemented
Class: bug-fix

## Decision

`STP_UNISOC_LOG_SCAN_POLL_SECONDS` 被误配为非数字时，`backend/agent/unisoc_scan_runner.py`
的 `int(os.getenv(...))`（原 :121）无兜底，异常沿
`_run_log_scan_gt` → `scan_runner._execute_job`（仅 `finally` 释放信号量，无 except）
→ `scan_runner._worker_loop`（无 except）一路上抛，**杀死 scan-queue-worker 线程**。
更关键的是 `_worker_started` 的复位原先只存在于「队列排空的正常 return」路径
（原 scan_runner.py:189）：线程异常死亡后标志仍为 `True`，`_ensure_worker` 永远认为
worker 存活、拒绝重启 → **scan 队列永久停摆，直到 Agent 进程重启**。

两处同源修复，缺一不可：

1. **输入归一（消除触发源）**：`unisoc_scan_runner.py` 新增
   `UnisocScanRunner._poll_seconds()`，统一解析该 env。空值/非数字/非正数
   （`0`、`-5`、`1.5`、`60s`）一律回落默认 60 并记 `warning`；`_build_argv` 与
   `_run_log_scan_gt` 共用它。顺带修掉原实现的不一致：`_build_argv` 只 `strip`
   不做 int 校验，两处对同一 env 的处理口径不同（`_build_argv` 会把
   `"not-a-number"` 原样传给扫描工具 `-i`，`_run_log_scan_gt` 则直接抛）。
2. **结构性兜底（消除后果）**：`scan_runner._worker_loop` 改为
   `try: while True: ... finally: 复位 _worker_started`，并把 `_execute_job`
   包进逐单 `try/except Exception` + `logger.exception`。这样「标志复位」不再依赖
   正常返回路径，任何退出方式都复位；单个 job 失败也不再带走整个队列。

为什么两处都要修：只修 (1) 是**堵住这一个触发源**，下一个未预料异常（网络、
磁盘、第三方库）仍会以同样方式永久停摆队列——`_worker_started` 的复位不属于
「正常路径」，它属于**线程生命周期**，必须由 `finally` 结构性保证。只修 (2)
则 env 误配会静默把 `-i not-a-number` 传给工具且超时按默认算，留下隐性不一致。

## Alternatives

- **只把 `int()` 包 try/except**（issue 的原始建议主项）：能修掉本单触发源，
  但 `_worker_started` 只在正常 return 复位的结构性缺陷仍在——任何其他异常
  仍永久停摆队列。否决为**不完整**，故保留 (2)。
- **`_worker_started` 改为 `threading.Thread.is_alive()` 派生**：更彻底（无标志
  不同步问题），但改动面扩大到 `_ensure_worker`/`_reset_for_tests` 与并发语义，
  且持有 Thread 引用会牵动测试重置路径；`finally` 复位以最小改动达成同等保证。
- **捕获 `BaseException`**：否决——`KeyboardInterrupt`/`SystemExit` 应正常传播，
  只兜 `Exception`；`finally` 已保证这两者退出时标志照样复位。
- **在 `_execute_job` 内 try/except**：否决——那是「单 job」视角，放在
  `_worker_loop` 才能同时表达「单 job 失败不致命」与「循环退出必复位」。

## Verification

实际运行（worktree `/tmp/stp-754`，基线 `origin/main` = `fa82ab53`）：

- 新增 `backend/agent/tests/test_scan_runner_worker_guard.py`（4 例）+ 扩展
  `backend/agent/tests/test_unisoc_scan_runner.py`（`TestPollSecondsBadConfig` 13 例）；
- **fail-to-pass 已实测**：`git stash` 掉两个源文件改动后跑新用例
  → **14 failed, 3 passed**；恢复修复后 → **17 passed**。失败清单与缺陷一一对应
  （`test_job_exception_does_not_stop_worker_and_next_job_runs`、
  `test_worker_started_reset_even_when_execute_raises`、
  `test_ensure_worker_restarts_after_guard_reset` 及 env 归一 11 例）；
- `pytest backend/agent/tests/test_unisoc_scan_runner.py
  backend/agent/tests/test_scan_runner_worker_guard.py
  backend/agent/tests/test_unisoc_reconciler.py -q` → **21 passed**；
- `JWT_SECRET_KEY=<test-only> pytest backend/agent/tests -q` → **1660 passed**
  （无回归；该 env 为 #739 记录的既有收集期依赖，非本单引入）；
- 门禁（直接执行 gate 命令，绕过 runner 的 profile 编排）：
  `ruff check backend/ tools/ scripts/` → All checks passed；
  `compileall -q backend/ tools/ scripts/` → exit 0；
  `collapse-blank-pollution.py --check -q` → exit 0；
  `check_governance_surface.py` → 阻塞项全绿（S1–S13、S5x）；
  `check_invariant_diff.py --base origin/main` → 新增行无不变量违规。

未完成（pending）：

- `scripts/run_gates.py check:quick` 本机无法整体跑完：它在 `eslint` 处失败
  （`sh: eslint: not found`）并中止后续 gate。**该失败与本单无关**——主 checkout
  的 `node_modules/.bin/eslint` 同样缺失，且本单不触及任何 `frontend/` 文件；
  已改为逐条直接运行上述 Python 侧 gate 命令替代（结果见上）。提 PR 后以 CI
  的 `lint` / `pr-typecheck` job 为权威；
- 真机验证：在 Agent 上把该 env 设为非数字并触发 scan_now，观察 worker 不再
  死亡、后续 job 继续处理——需真机环境，未执行。

## Revisit

- 本单与 #805（PR #1476，同改 `unisoc_scan_runner.py`）**代码位置相邻**：按
  execution-contract §5.4/§8 已串行排程——本单先实现不提 PR，待 #1476 合入后
  rebase 到新 main 再提，避免双改同一 hunk。
- `_worker_started` 现由 `finally` 保证复位。若未来 scan 队列改为多 worker 或
  引入线程池，「单一布尔标志」的表达力不足，应改为线程句柄/worker 集合派生；
  届时本单的两条不变量（异常不致命、退出必复位）仍应作为回归基线保留。
- 本单不含「env 校验失败是否应拒绝启动」的方向性判断——当前选择是**降级+告警**
  （与 issue 建议一致）。若后续认为误配应 fail-fast，属运维策略变更，另立单。
