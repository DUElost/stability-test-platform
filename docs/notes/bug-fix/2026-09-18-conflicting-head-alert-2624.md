# #2624 绿但 CONFLICTING 的队首纳入停摆告警（checks-only 判定面的盲区）

Status: implemented
Class: bug-fix

## Decision

`scripts/ci/pr-automerge-queue.sh` 三处改动：

1. `head_json` 的 `gh pr view --json` 增读 **`mergeable,mergeStateStatus`**；
2. 在「required checks 全绿」之后、分支更新之前，新增**冲突判定分支**：
   `mergeable=CONFLICTING` 或 `mergeStateStatus=DIRTY` → `alert_queue_blocked`
   （`reason_code = conflicting-<head_sha:0:12>`）并 `exit 0`；
3. `alert_queue_blocked` 的正文为该 reason 补**可执行动作**（解冲突 / 让位）。

同步调整：`resolve_queue_blocked` 从冲突判定**之前**移到**之后**（见「自我纠正」）。

## 缺陷确认（与 issue 一致，并补一条机制解释）

issue 的判断成立：六个 `alert_queue_blocked` 调用点全部以 `$failed_checks` /
`$unreported_checks` 为条件；`grep -n "CONFLICTING\|mergeable"` 在脚本内**零命中**。

**为何这是「互为盲区」而非漏一条 case**：

| 队首形态 | checks | 是否走自愈 | 是否告警（修复前） |
|---|---|---|---|
| 红 check | 红 | 否（纪律：不对红队首重基） | ✅ 告警 |
| 缺上报 check | 特殊 | 有界自愈 | ✅ 告警 |
| **绿 + CONFLICTING** | **全绿** | **否**（重基必失败） | ❌ **零告警** |

即：既不红、也不自愈、也不告警 ⇒ 只能靠人恰好去看队列（实测 #2584 卡住 43 个提交，
`ci/queue-blocked` 为空）。

## ⚠️ 我刻意**没有**采纳 issue 建议的实现方式（附依据）

issue 建议「直接消费 `queue_head_telemetry` 的 `reason_code`/`owner`/`actionable`」。
但该工具的**自身设计约束**明写（`tools/dev/queue_head_telemetry.py:14-15`）：

> **`reason_code` 是 advisory telemetry**：本工具与任何 workflow 都不得据其分支。
> 出现 `if reason_code == ...; then` 即意味着它已悄悄变成控制面契约。

若按 issue 建议让 workflow 消费并据其分支，**正好把 advisory 面升格为契约**——
与该工具刻意保持的边界相反。

**改取的做法**：告警侧**直接读同一批 GitHub 事实**（`mergeable`/`mergeStateStatus`），
与 checks 判定同源同层。这实现了 issue 想要的「一份事实、两个消费面、避免口径漂移」，
同时**不违反** telemetry 的 advisory 边界——两者各自判读统一的事实来源，而非一方依赖另一方。

> 注：本 PR 里的 `reason_code` 是 `alert_queue_blocked` 的**局部参数**（用于指纹与正文），
> 与 telemetry 的同名字段**无数据依赖**（实测脚本未 import 该工具，仅帮助文本引用其名）。

## 一处我在实施中**自己引入又修掉**的次序缺陷（留痕）

初版我把冲突判定放在 `resolve_queue_blocked` **之后**。而 `resolve_queue_blocked`
的作用是「队首不再被阻塞 → 关闭存量告警」——于是冲突队首会被
**「先关闭既有告警 → 再重新开一条」**，每轮 reconcile 抖动一次告警：既刷通知，
也让「同指纹零写入」的去重**彻底失效**。

我为此专门写了第三个用例 `test_conflict_alert_not_closed_then_reopened_each_run`
（断言冲突队首**不得**出现 `issue close` 调用）。它在我修正次序后通过；若把次序改回去即失败。

> 教训：**新增一个分支时，要检查它与其后既有「恢复/清理」逻辑的先后关系**——
> 恢复逻辑的语义是「已无阻塞」，提前执行会与新增的阻塞判定互相打架。

## Alternatives

- **消费 telemetry 的 `reason_code`**（issue 建议）→ 否决：见上，违反该工具的 advisory 边界。
- **只在 `failed_checks` 为空时才判冲突** → 否决：本形态 checks 本来就全绿，
  该条件恒真，等价于无谓限制；且「红 + 冲突」并存的队首该报哪一类需要显式取舍，
  本版按「红优先」（冲突判定在 checks 全绿之后）——因为红 check 是更直接的处置入口。
- **指纹沿用 `failed=`**（不引入 `conflicting-`）→ 否决：二者处置不同
  （冲突要人解冲突、红 check 要修 check），共用指纹会让正文互相覆盖、无法稳定区分。
- **把冲突判定也放进 telemetry 并让 workflow 读** → 否决：同上 advisory 边界；
  且 telemetry 是「零副作用解释器」，扩它做控制面职责会改变其定位。

## Verification

- `./scripts/run_pytest.sh tests/test_automerge_queue_alerts.py -q` → **33 passed**
  （既有 30 + 新增 3）；
- **红绿双向**：移除冲突判定块 → 新增 2 例**红灯**（第 3 例负向对照两种情况下都通过，
  符合预期——它断言「不冲突时不得开告警」）；
- **负向对照**：`test_clean_head_does_not_open_conflict_alert` —— 默认
  `MERGEABLE/CLEAN` 的绿队首**不得**开告警（防误报）；
- **fixture 兼容性**：为 `_head_detail()` 补 `mergeable/mergeStateStatus` 时
  **默认取真机正常形态**（MERGEABLE/CLEAN），使既有 30 个用例不受影响（实测全绿）；
- **相关套件**：`tests/ -k "queue or automerge or telemetry"` → **40 passed**；
- **telemetry self-test**：`python -m tools.dev.queue_head_telemetry --self-test`
  → **8 组红绿双向通过**（确认未破坏同族逻辑）；
- `ruff` 通过；`check_governance_surface.py --check` → S1–S14、S5x 全绿；`bash -n` 通过。

## Revisit

- **判定面仍不完整**：`mergeable` 在 GitHub 首次查询时可能返回 `UNKNOWN`（服务端尚在计算）。
  本版只在明确 `CONFLICTING`/`DIRTY` 时告警（**fail-quiet**）——即 UNKNOWN 不告警，
  留待下一轮 reconcile。这与既有 `MISSING_GRACE_SECONDS` 的取向一致；
  若实测出现「长期 UNKNOWN 导致漏报」，再评估加重试或宽限。
- **与 #2646 的告警面合并**：本 PR 的 `conflicting-<sha>` 与 #2646 的
  `credential-scope` 共用 `reason_code` 参数与指纹结构。若将来要统一成机器可读的
  `reason_code` 枚举（供其它消费面），应作为**独立议题**评估——注意届时不要顺手
  把它变成 telemetry 的契约（见上约束）。
- **`owner`/`actionable` 未接入**：issue 提到 telemetry 已有这两项。本版未引入
  （避免依赖 advisory 面）；正文已用人话给出同等信息（「人工动作：…」）。
