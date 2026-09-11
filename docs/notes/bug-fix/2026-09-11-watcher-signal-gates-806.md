# watcher 信号闸门三处（#806 / R09-F07）

Status: implemented
Class: bug-fix

## Decision

watcher/contract 信号链路三层同源失效（四域审查复核基线 `dfe4895a`；
本单实测收口情况：子项三已先行修复，子项一/二仍在）：

1. **[未修 → 本单修] Reconciler 自关闭不复位 watcher 抑制位**：`aee/reconciler.py`
   连续 tick 错误超阈值后只 `_emit_rollback_signal()` + `_stop_evt.set()`，
   从不通知 watcher 复位；`device_watcher` 处于 active=True 时持续抑制
   AEE/VENDOR_AEE 信号与 DLE 注册 → 该 Job 余下生命周期信号静默全黑
   （与注释「inotifyd 兜底独立工作」相矛盾——兜底只在 active=False 时接管）。
   **修复**：`AeeDbHistoryReconciler` / `UnisocUniviewReconciler` 增加
   `on_self_shutdown` 回调（自停阈值分支内触发，回调异常只记日志、不阻断
   自关闭），`job_session` 注入回调走与「启动失败回滚」同路：
   `watcher.impl.set_aee_reconciler_active(False)`。
2. **[未修 → 本单修] `reconciler_rollback` 不在 source 白名单**：
   `watcher/contracts.validate_log_signal` 的 source 白名单只有
   `{inotifyd, polling, reconciler}` → 自关闭可见性信号 emit 必抛
   `ContractViolation` 且被 `_emit_rollback_signal` 的 except 吞掉
   （「让 dashboard 展示 Reconciler 已自关闭」的设计意图 100% 落空）。
   **修复**：白名单加入 `reconciler_rollback`。
3. **[部分已修]** UNISOC `UNIVIEW` category 与 LocalDB `.get()/.set()`：
   现状 `contracts.py` category 白名单已含 `UNIVIEW`；
   `unisoc_reconciler._load/_save_processed_state` 已改用
   `get_state/set_state`（#1043 先行修复）。本单补测试钉住形态，不改代码。

## Alternatives

- **自关闭路径直接持有 watcher 引用**（reconciler 里 import device_watcher
  并调用）：让 reconciler 依赖 watcher 实现细节，测试与解耦都变差；回调注入
  与既有「启动失败回滚」同构，且同时适配 MTK/UNISOC 两条构造路径；
- **回调失败时重试/阻断自关闭**：否决——自关闭是 #72 现场（11M 行日志死循环）
  的防线，通知属于尽力而为；失败只记日志；
- **把 source 白名单放宽为任意字符串**：否决——契约校验是 fail-fast 设计，
  精确加一个合法 source 即最小修正；
- **本单重写 UNISOC 状态 API**：已是 `get_state/set_state`，#806 复核为
  过期项，仅补测试锁定。

## Verification

实际运行：

- `pytest backend/agent/tests/test_aee_reconciler.py
  backend/agent/tests/test_watcher_contracts.py
  backend/agent/tests/test_job_session.py -q` → **62 passed**（新增 5 例：
  自关闭触发回调、回调异常不阻断自关闭、`reconciler_rollback` source 过契约、
  `UNIVIEW` category 过契约、非法 source 拒绝；JobSession 注入回调且调用后
  watcher `set_aee_reconciler_active(False)`）；
- `backend/agent/tests` 全量 → **1585 passed**；
- `ruff check`（4 个改动文件 + 3 个测试文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机注入验证：制造 reconciler 连续 tick 错误（如 `STP_AEE_LOCAL_ROOT`
  不可写）观察自关闭后 inotifyd 路径恢复 emit；本机无对应 Job 环境，未执行。

## Revisit

- 若未来把「自关闭」升级为可自愈（重启 reconciler），回调需扩展为状态事件
  （当前只表达「已停」）；届时与本回调合并设计；
- 子项三（UNIVIEW/LocalDB）如后续再次漂移，`test_watcher_contracts.py` 与
  `test_unisoc_reconciler.py` 会先红——以测试为准，不改本 Note 历史结论。
