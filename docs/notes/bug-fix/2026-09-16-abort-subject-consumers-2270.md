# abort 消费侧主体语义对齐：门禁/API/派发/恢复四处（#2270）

Status: implemented
Class: bug-fix

## Decision

ADR-0043 把 abort 宽限的**请求主体 ≡ 计时主体**改了写侧与 reaper，但四个消费点仍按
「`run_context` 里有 `abort_requested` 键」判定。host 级 abort **不写 run 级时钟**
（只写名单 + `abort_requested_hosts[host].at`），于是**一台主机的 abort 让同 run 的
旁主机看起来也在 abort 中**——热更新门禁对它们永久 409（「abort 收口中」），而 reaper
按主体语义永远不会回收那些 job（不是变慢，是永不收敛）。

新增**共享判据**（`backend/services/plan_run_abort.py`，abort 语义的唯一属主）：

- `run_abort_pending(run_context)`：run 主体是否在窗（只看 `abort_requested.at`）；
- `abort_pending_job_ids(run_context, [(job_id, host_id)])`：这些 job 里哪些被**在窗**
  的请求覆盖——run 时钟 → 名单内（名单缺失/空按历史兼容 = 全部）；host 时钟 → 该
  host 的 job；**两个时钟都没有 → 不覆盖任何 job**（旧判据的永久 409 成因）。

四处消费点改用该判据：

| 消费点 | 症状（旧判据） |
|---|---|
| `host_upgrade_gate._abort_pending_ids` | 旁主机的 job 被判「待中止」→ 门禁对它们**永久** `HostAbortPendingError`（本单主症状） |
| `api/routes/jobs.py` / `hosts.py` 的 `_abort_pending` | 主机页把旁主机的 job 标成 `abort_pending=true` → UI 长期显示「abort 收口中」（`HostHotUpdateConfirmDialog` 的 `allAbortPending` 分支） |
| `services/plan_dispatcher_sync` 的派发收尾 | 一台主机的 abort 停掉**整个 run** 的派发收尾（`run_ctx.get("abort_requested")`） |
| `api/routes/agent_api.py` 的恢复/续跑 | 对旁主机的 job 也下发 `ABORT_LOCAL, reason=abort_requested`——**误杀从未被请求中止的主机** |

## Alternatives

- **A. 各消费点各自补一段「有没有时钟」的判断**：否决。同一语义在四处复制必然再次漂移
  （本单就是写侧改了、消费侧没跟）。
- **B. 消费侧改读 reaper 的 `_abort_request_covers_job` / `_host_abort_clock_at`**：方向对，
  但那两个函数回答的是「**这个 job** 该不该回收」且需要 `PlanRun` 对象与时间戳；
  这次要的是纯函数 + 「在不在窗内」的布尔。取相同语义、不同形状：新判据在 docstring 里
  写明与 reaper 同源，供后续合并时对照。
- **C. 让 host 级 abort 也写 run 级时钟**（把旧判据变正确）：否决——正是 ADR-0043 要消除
  的形态（run 级时钟会盖住整个 run，重新引入「旁主机被牵连」）。
- **D. 「键在但无时钟」按「全部在窗」处理**（保守）：否决。那正是当前 bug；且会让门禁在
  abort 收口完成后仍永久 409。

## Verification

- **判据差分**（同一 `run_context`：名单 `[1]` + `host-a` 时钟，无 run 时钟）：
  - 旧判据（键存在即 pending）：`[1, 2]` ← host-b 也被判待中止；
  - 新判据：`[1]`；`run_abort_pending` = `False`（host 级 abort 不算 run 主体在窗）；
  - run 级 abort（有 `at`）：`[1, 2]` 且 `run_abort_pending` = `True`（语义保持）。
- **新增用例 7 条**：`backend/tests/services/test_abort_subject_predicate_2270.py`——
  旁主机 host 级 abort 不覆盖、run 级（无名单）覆盖全部、run 级带名单只覆盖名单、
  **键在无时钟不覆盖任何 job**、缺失/异形 run_context 安全、以及门禁 `_abort_pending_ids`
  的两条 DB 级用例（旁主机 abort → 空集；本机 abort → 命中）。
- **受影响面回归**：`pytest backend/tests -k "abort or upgrade_gate or dispatch or jobs or
  hosts or agent_api or recovery"` → **464 passed**（含既有门禁/abort/dispatch 用例）。
- `ruff check backend/` 全绿（`check:quick` 由 CI 复核）。
- **未跑**：全量 backend pytest（受影响面已按关键词覆盖，其余交 CI required checks）。
- **未做**：前端未改（`abort_pending` 由后端计算并透传，`HostHotUpdateConfirmDialog`
  的展示逻辑无需变动）；`abort_requested_hosts` 的**清理**（host 升级完成后是否删键）
  不在本单，见 Revisit。

## Revisit

- **host 时钟的清理**：`abort_requested_hosts[host].at` 目前只写不删。若同一 host 之后
  再次进入门禁，旧时钟会让它**仍然**在窗（判据是「时钟存在」而非「在宽限内」）——与
  run 级时钟同形态。需要时按「升级完成即删 host 键」补，属独立议题（清理路径要审计留痕）。
- **判据与 reaper 的收敛**：两者现在语义同源但形状不同（布尔 vs 时间戳取最早）。若将来
  出现第三种消费形状，应把「取时钟」抽成一个函数（返回 `[(at, subject)]`），四处共用。
- **前端展示面**：本次只修了 `abort_pending` 的来源；`HostHotUpdateConfirmDialog` 的
  「abort 收口中」文案在**真·在窗**时仍然正确，未动。
