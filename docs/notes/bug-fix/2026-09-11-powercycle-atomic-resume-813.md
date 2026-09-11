# powercycle_check 收取窗口 pause→resume 原子化（#813 / R08-F04）

Status: implemented
Class: bug-fix

## Decision

根因（`powercycle_check/v1.0.6` 起，`v1.0.7` 沿用）：收取窗口
`pause_task() → collect → resume_task()` 不是成对操作——`collect`/`resume`
任一步抛异常（NFS 写满/adb 瞬断/pull 超时/进程被杀）都会跳过 resume；窗口外
周期只清 `collecting_done_for_window`，从不检查「被暂停但未恢复」。设备停留
在 prefs `running=false` + force-stop（无自启）→ 结果文件 mtime 停滞 →
v1.0.6 判死逻辑在 grace 后报服务死亡 → **整 Plan 误 FAILED（设备与数据实际健康）**。

修复按版本纪律落 **新版本 `v1.0.8`**（`v1.0.6`/`v1.0.7` 已发布不可变）：

1. **先落意图再暂停**：`pause_task()` 前写 `state["paused_by_collect"]=True`
   并落盘——即使进程在 pause 中被杀，标记也在（覆盖 try/finally 覆盖不了的
   场景）；
2. **异常路径补偿**：窗口内 `except` 分支调用
   `_compensate_pending_resume()`（服务在跑则只清标记；否则 `resume_task()`）；
3. **窗口外入口补偿**：窗口外分支先检查标记，补做恢复；成功则本轮输出
   `phase=resume_recovered`、**不判死**并清零 `dead_streak`/`last_mtime`
   （服务启动需要时间，避免把暂停期算作死亡）；
4. **失败不掩盖真故障**：补偿失败保留标记与 `resume_error`，每周期重试，且
   照常进入判死评估（设备真卡死仍会按 grace FAILED）。

## Alternatives

- **原地改 v1.0.6/v1.0.7**：否决——已发布版本不可变（门禁 + ADR-0020）；
- **仅 try/finally**：能覆盖函数内异常，但覆盖不了「进程被杀/被超时终止」；
  意图标记 + 窗口外补偿才闭环（issue 修复方向亦如此）；
- **仅在 except 补偿**：窗口内异常可恢复；但若异常发生在窗口最后一个周期、
  或进程在 pause 后被杀，仍需窗口外补偿——两条路径都做；
- **补偿后继续判死（不清零停滞账）**：会把暂停期累积的 streak 带进来，grace
  小（如 2）时首个恢复周期即误判——故恢复成功当轮直接返回且清零。

## Verification

实际运行：

- `pytest backend/agent/tests/test_powercycle_scripts.py -q` → **59 passed**
  （新增 4 例：collect 异常触发补偿 resume；pause 自身异常仍落意图并补偿；
  窗口外对遗留标记补偿 + `phase=resume_recovered` + streak 清零；补偿失败
  保留标记/`resume_error`、下轮成功再恢复）；
- `ruff check`（v1.0.8 目录 + 测试文件）→ All checks passed；
- `pytest tests/ -q` → 全绿（含脚本目录/治理门禁对新版本目录的检查）；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- **控制面注册 v1.0.8**：需部署顺序 = 本 PR 合入 → 控制面更新 → agent
  热更新分发 `scripts/powercycle_check/v1.0.8/` → `POST /scripts/scan`
  注册新版本（admin 操作；本机无 admin 凭据，未执行）→ Plan 切用 v1.0.8。
- 真机收取窗口异常注入验证（隔离主机）：暂停后杀进程，确认下个窗口外周期
  自动恢复续跑。

## Revisit

- `v1.0.7` 的 job 身份重置逻辑在本版本延续：`paused_by_collect` 属 job 状态，
  新 Job 首周期即重置——与 #1028 语义一致（不会跨 Job 遗留补偿标记）；
- 若未来出现「补偿失败持续到 grace → FAILED」的现场，`progress` 中的
  `resume_error` 与 `phase=resume_pending`（失败路径保留标记但走正常 payload）
  是排查入口；必要时在新版本补结构化告警；
- 旧版本（≤v1.0.7）保持原行为，不回填修复；Plan 切到 v1.0.8 后旧版本可停用
  （scan/停用策略届时按需）。
