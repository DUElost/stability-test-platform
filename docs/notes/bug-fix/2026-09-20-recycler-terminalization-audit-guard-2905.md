# recycler 异常收敛路径：补审计 + 审计一致性守卫（#2905）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2905`（本单）、`#2778`（同形守卫的先例：`backend/tests/test_audit_resource_type_guard.py`）、
  ADR-0019 Phase 4c / ADR-0022 D10（另两条路径的审计依据）、ADR-0044 D3 / ADR-0049（审计=持久证据、分层保留）、
  `#2694`（审计无界增长的关切）
- 裁决依据：[`#2905` 的 codex 实测包](https://github.com/DUElost/stability-test-platform/issues/2905)——触发量、
  覆盖真相、豁免路径成立性；其关键事实本单**已本地复核**（见 Verification）

## Decision

两件事，一件是裁决落地、一件是防止它再退化：

1. **补审计**：`_mark_running_timeout`（RUNNING→UNKNOWN）此前是同族三条路径里**唯一不写审计**的
   一条——`_mark_pending_timeout`（ADR-0019 依据）与 `_mark_patrol_stall`（ADR-0022 D10）都写。
   后果：审计面无痕，只剩 `status_reason` 与瞬时指标 `task_run_state_changes`——指标重启即失忆。
   现按同族形态补 `record_audit(action="job_running_timeout", resource_type="job_instance")`，
   details 记 `plan_run_id`/`device_id`/`old_status`/`reason` 与当次判定用到的触发面
   （`coordinator_deadline`/`execution_deadline`/`require_unreported`），`username="system"`
   （与 `_mark_patrol_stall` 一致）。
2. **新增守卫** `backend/tests/test_recycler_terminalization_audit_guard.py`（纯 AST、离线、秒级，
   照 #2778 的形状）：`backend/scheduler/recycler.py` 里每个 `_mark_*` 函数**要么**调用
   `record_audit*`，**要么**出现在 `_AUDIT_EXEMPT` 且**理由非空**；豁免表**陈旧即红**
   （函数已写审计或已改名 → 条目必须删）。→「新增第四条同族路径、悄悄不写审计」从此**未知即红**。

**决定性依据（本单复核过的那条）**：`JobStateMachine.transition` 无条件写
`job.status_reason = reason`（`backend/services/state_machine.py:41`），而 UNKNOWN 的 grace 到期由
`device_lease_reconciler.py:229/369` 落 FAILED、reason=`unknown_grace_timeout`——**原降判 reason
（running / coordinator / execution 哪条 deadline 断的）被覆盖**。即豁免路径「只查 `status_reason`」
在时间线走完后**不成立**；本审计是「当初为什么 UNKNOWN」唯一的持久痕迹。

**保留分层（写之前先确认的配套问题）**：`job_running_timeout` 不进 `SESSION_ACTIONS` /
`SECURITY_ACTIONS` 显式集 ⇒ 按 ADR-0049 D2 落 **business 默认桶（90d）**。
`backend/scheduler/audit_log_cleanup.py` 用的是 `AuditLog.action.notin_(SESSION | SECURITY)`，
对**新 action 封闭**——即新审计不会掉进「未分层」状态，这正是 #2694 那条关切的答案。
`_AUDIT_EXEMPT` 现为**空表**：三条路径都写了审计，空表本身也是判据的一部分。

**量级**：codex 实测包给出生产近 30 天该路径 **0 次**、现存 UNKNOWN **0 行**（`job_terminalized`
33,935 / `patrol_stall_detected` 4,350 是历史对照）⇒ 「写审计加剧无界增长」的隐忧在当下数据里是
零成本；新增行数上界 = grace 链出现频次，而它一旦出现本就属于要逐条有痕的量级。

**不做**：不改运行期判定逻辑（只加证据，不改谁超时）；不扩守卫射程到全仓（见 Revisit）。

## Alternatives

- **只加函数注释/豁免条目、不补审计**：即把「RUNNING→UNKNOWN 无持久证据」固化成设计——但同族
  两条都写、且它是最高频形态，豁免需要比「和另两条对齐」更强的理由，本单没有。
- **补审计但不加守卫**：那正是 issue 说的失效形态——「第四条路径悄悄不写」仍然只能靠人发现，
  且本次缺口本身就是这么来的（三条同族函数不一致，无人发现）。
- **用运行时（测试里真跑回收器再看 audit_logs）替代静态判定**：要 PG + 造 PENDING/RUNNING 状态，
  成本高一个量级，且只在被跑到的那条路径上有效——静态枚举才能覆盖「所有 `_mark_*`」。
  两者**并用**：本单的行为断言（`test_recycler.py`）覆盖运行期，AST 守卫覆盖全枚举面。
- **把守卫扩到全仓所有终态化函数**：需要先有一个「哪些函数算终态化路径」的语义登记（跨模块命名
  不统一），与 #2778 的「只扫 `record_audit*` 调用点」不同。本单按 issue 范围只扫 `recycler.py`
  的 `_mark_*`；扩面记入 Revisit。

## Verification

- `python -m pytest backend/tests/scheduler/test_recycler.py backend/tests/test_recycler_terminalization_audit_guard.py -q`
  → **29 passed**；合面（本守卫 + `test_audit_resource_type_guard.py` + `backend/tests/scheduler`）→ 见 PR；
- **变异逐条回退即红**：删掉 `_mark_running_timeout` 里的 `record_audit` →
  `test_running_timeout_transitions_to_unknown`（新行为断言）**与**
  `test_recycler_mark_paths_are_all_accounted_for`（守卫）**同时红**；还原即绿。
  即「补的审计」与「防退化的守卫」两面都被同一处删除打中，不是各自空转；
- **守卫自测的可注入性（本轮实际踩到）**：`test_detector_accepts_audit_or_documented_exemption`
  原先拿 `_mark_running_timeout` 当「活豁免表里的例子」——豁免撤空后它**按陈旧即红变红**。
  修法是给判据加可注入的 `exempt` 参数（自测用合成表），而不是把它改回依赖现行登记表：
  否则「把表清空」这种正当改动会连带打红自测；
- 早期一轮已验的守卫变异（保持有效）：新增一条不写审计的 `_mark_*` → 1 failed；豁免表登记一个
  已写审计的函数 → 1 failed；豁免理由整条掏空 → 3 failed；
- **裁决依据的本地复核**（不代替实测，只核事实面）：`backend/services/state_machine.py:41`
  确为无条件 `job.status_reason = reason`；UNKNOWN grace 落 FAILED 的两处调用点
  `device_lease_reconciler.py:229/369` 传的 reason 是 `unknown_grace_timeout` ⇒ 覆盖成立。
  触发量数字（30 天 0 次 / 现存 UNKNOWN 0 行）出自 codex 的只读实测包，本单**未复跑**
  （避免在生产库上做非必需查询），按其原始读数引用；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**。

## Revisit

- **守卫的射程**：只认函数体内**直接**的 `record_audit*` 调用——经 helper 间接收敛的路径
  （例如某个 `_finalize()` 内部写审计）会被误判为「没写」；届时按需扩成「允许登记的间接收敛
  helper」而不是放弃静态判定。
- **扩到全仓的前提**：需要一个跨模块的「终态化路径」登记面（命名不一致是主要障碍）；在此之前，
  本守卫只在 recycler 上成立，别把它读成「全仓审计一致性已保证」。
- **审计量的影响**：`job_terminalized` 已是近 30 天审计量的 76%（见 #2694 的实测）。本单新增的
  `job_running_timeout` 频次低于它（只在超时判定时写），且落 90d business 桶；若将来观测到它
  成为新的量级首位，处置方向是**分层保留**而不是回头删审计。
- **排障入口**：以后「这批 job 为什么集体 UNKNOWN」可以直接查 `audit_logs.action='job_running_timeout'`
  拿 `plan_run_id`/`device_id`/`reason` 与命中的触发面；`status_reason` 与指标降级为交叉验证，
  不再是唯一线索。
- **`status_reason` 的覆盖语义本身**（`state_machine.transition` 无条件覆盖上一跳 reason）不在本单
  范围：本单用审计解决**可追溯性**，没有改状态机语义——改它属执行状态机方向，需 ADR 裁决。
  本单落定后，审计已能回答「当初为什么 UNKNOWN」，这条收紧的紧迫性下降。
