# #736 切片：recovery / lease-lost 抽出 `recovery_executor`

Status: implemented
Class: bug-fix

## Decision

把 `backend/agent/main.py` 里可独立测试的 **recovery / lease-lost 辅助簇**整段迁到
`backend/agent/recovery_executor.py`，`main` 只保留接线与 `main()` 过程入口：

- `_make_local_worker_token` / `_cleanup_after_*` / `_rollback_failed_claim`
- `handle_lease_lost` / `execute_recovery_actions_impl`
- `run_recovery_sync_if_needed` / `trigger_recovery_sync_on_device_reconnect`
- `_coerce_recovery_interval`

`patrol_recovery` 改为从 `recovery_executor` 懒加载（安装布局仍是顶层 `agent`
包，不依赖 `backend`）。测试与 `patch` 目标同步迁到新模块；**不**在 `main` 上
保留 re-export（与 #1520 路由薄壳同一纪律）。

同 PR 下调 god-files 棘轮：`main.py` 1622 → **1177**，封顶 **1236**（×1.05）。

## Alternatives

- **一次到位 `AgentApplication` 容器**：弃——#736 验收含生命周期阶段，但 889 行
  `main()` 与全局占位集合耦合深，整包装箱风险高；先按垂直簇搬家，与 #1520
  切片策略一致。
- **保留 `main` re-export 兼容旧 patch 路径**：弃——会掩盖真实归属，测试继续
  绑胖入口。

## Verification

- `.venv/bin/python -m pytest`（recovery / lease-lost / patrol / lifecycle /
  terminal / lease_renewer / test_main）：**88 passed**
- `python tools/dev/check_god_files_ceiling.py`：pending（合入前跑）
- `python scripts/run_gates.py check:quick`：pending

## Revisit

- 下一刀：把 `main()` 启动子系统 configure/start 段抽成
  `initialize_background_subsystems(...)`，再谈 `AgentApplication` 阶段边界；
- #736 的 plan_runs 半边已由 #1520 关闭举证覆盖——本单剩余焦点是 Agent 入口。
