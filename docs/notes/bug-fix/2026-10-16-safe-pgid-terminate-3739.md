# Cancel / 进程树终止的可信 PGID 边界（#3739）

Status: implemented
Class: bug-fix

## Decision

在 POSIX 终止路径上，把「可信进程组身份」收成硬闸，再允许 `os.killpg`：

- 新增 `_trusted_posix_id` / `_trusted_pgid`：只接受真实 `int`（`type is int`，拒绝
  `bool` / MagicMock 等），且必须 `> 1`；并拒绝等于当前 `os.getpgrp()` 的目标。
- `_remember_process_group`、`_resolve_pgid`、`_process_group_alive`、
  `_terminate_process_tree` 的缓存路径与实时解析路径都过闸；Windows `taskkill`
  对 PID 使用同一 `> 1` 真实 int 约束。
- 拿不到可信身份就放弃组级信号（既有「宁可不动手」语义），**不**禁用合法隔离
  子进程组的 `killpg` 收敛。

根因（#3739）：`killpg(1, SIGTERM)` 在 libc 变为 `kill(-1, SIGTERM)`，向调用者
有权信号的大量进程广播；无显式 PID 的 MagicMock 经 `__index__` 变成 1，可走到
该路径。生产边界必须拒绝，不能只依赖测试 mock。

## Alternatives

- 只修临时变异插件 / 只给测试补显式 PID：宿主复发面仍在，生产路径仍接受 PGID 1。
- 完全禁用 `killpg`、改回只杀父 PID：破坏 `#1003` 父退出后残留子孙收敛契约。
- 仅拒绝 PGID 1、不拒绝 `<= 0` / 自身 pgrp / 非真实 int：覆盖不全，仍可能会话级误伤。

## Verification

- `python scripts/run_pytest.py backend/agent/tests/test_pipeline_engine_process_group.py -q`：
  27 passed（含 #3739 危险 PID/PGID / MagicMock→1 / 缓存旁路 / 自身 pgrp / Windows PID 闸）。
- 相关套件（process_group + stall + log_sink + lease_lost）：72 passed。
- 完整 Agent 套件 `python scripts/run_pytest.py backend/agent/tests/ -q`：2584 passed。
- `python scripts/run_gates.py check:quick`：16 gates OK（schema-at-head 因未配置
  DATABASE_URL 显式 WARN/跳过）。
- 本机**未**重跑原 `/tmp/review-b6-u1` cancel 变异路径（#3739 止血要求）；危险 PGID
  用桩替换证明到不了真实 `os.killpg`。临时插件 globals 复制问题在
  `/tmp/review-b6-u1/review_mutation.py` 做了本地最小修复（`FunctionType` 绑回原
  `__globals__`），**不进仓**；原宿主变异复核保持 pending。

## Revisit

- 合入后在隔离环境按安全方式重跑原 cancel 变异路径，确认断言失败且桌面不被注销。
- 若 B6-U1（#3733）合入后仍带无显式 PID 的 MagicMock cancel 用例，建议补显式安全
  PID 作为测试卫生；生产闸门已覆盖该事故路径。
- 不授权本单部署或改生产库。
