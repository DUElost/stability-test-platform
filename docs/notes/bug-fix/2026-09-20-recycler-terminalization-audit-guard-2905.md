# recycler 异常收敛路径：审计一致性守卫 + 把「不写审计」钉成显式欠账（#2905）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2905`（本单）、`#2778`（同形守卫的先例：`backend/tests/test_audit_resource_type_guard.py`）、
  ADR-0019 Phase 4c / ADR-0022 D10（另两条路径的审计依据）、ADR-0044 D3 / ADR-0049（审计=持久证据、分层保留）、
  `#2694`（审计无界增长的关切）

## Decision

issue 的第一诉求是「先裁决再动代码，把选择写成决定」，第二是「补审计」，**真正的落点**是「加一条
一致性断言」。本单落**落点**那一条，把裁决原样交回：

1. **新增守卫** `backend/tests/test_recycler_terminalization_audit_guard.py`（纯 AST、离线、秒级，
   照 #2778 的形状）：`backend/scheduler/recycler.py` 里每个 `_mark_*` 函数**要么**调用
   `record_audit*`，**要么**出现在 `_AUDIT_EXEMPT` 且**理由非空**；豁免表**陈旧即红**
   （函数已写审计或已改名 → 条目必须删）。→「新增第四条同族路径、悄悄不写审计」从此**未知即红**。
2. **豁免表当前只有一条**：`_mark_running_timeout`（RUNNING→UNKNOWN），理由写成
   **「待裁决（#2905）」**并写明两种裁决各自的后续（写 ⇒ 补 `record_audit`、action 建议
   `job_running_timeout`，且**必须确认落进 ADR-0049 的 business 桶**，否则反而在保留策略上开缺口；
   不写 ⇒ 按同族形态把理由写进函数注释）。
3. **函数上写明欠账**：`_mark_running_timeout` 的 docstring 增一节，把「本函数不写审计、
   另两条同族函数都写、RUNNING→UNKNOWN 是最高频形态」摆明，并指向守卫与 issue——
   即 issue 说的「把选择写成决定」至少推进到「决定 = 未决，且有据可查」，不再是无痕的实现细节。

**不做**：不替 owner 决定「RUNNING→UNKNOWN 要不要持久证据」，也不顺手补上 `record_audit`
——那会先占掉裁决位，且 ADR-0049 的桶归属是配套问题。

## Alternatives

- **直接补 `record_audit`**：issue 明说「先裁决再动代码」；且这不是纯增益（写审计会加剧 #2694
  的无界增长关切，需要与 ADR-0049 的分层保留一起看）。否。
- **只加函数注释、不加守卫**：那正是 issue 说的失效形态——「第四条路径悄悄不写」仍然只能靠人发现。
  否。
- **把守卫扩到全仓所有终态化函数**：需要先有一个「哪些函数算终态化路径」的语义登记（跨模块命名
  不统一），与 #2778 的「只扫 `record_audit*` 调用点」不同。本单按 issue 范围只扫 `recycler.py`
  的 `_mark_*`；扩面记入 Revisit。
- **用运行时（测试里真跑回收器再看 audit_logs）替代静态判定**：要 PG + 造 PENDING/RUNNING 状态，
  成本高一个量级，且只在被跑到的那条路径上有效——静态枚举才能覆盖「所有 `_mark_*`」。否。

## Verification

- `python -m pytest backend/tests/test_recycler_terminalization_audit_guard.py -q` → **5 passed**；
  合面（本守卫 + `test_audit_resource_type_guard.py` + `backend/tests/scheduler`）→ **180 passed**；
- **4 处定向变异逐条回退即红**：删掉 `_mark_patrol_stall` 内的 `record_audit` → **1 failed**；
  新增一条不写审计的 `_mark_*` → **1 failed**；豁免表登记一个已写审计的函数 → **1 failed**；
  豁免理由整条掏空 → **3 failed**；
- **两条变异自身先失效过一次（记账）**：① 我先把 `def _mark_patrol_stall(` 改名（以为等于
  「抽掉审计」）——审计调用还在函数体内，判据自然不响；② 把理由写成相邻字符串字面量
  （Python 隐式拼接 ⇒ 非空）与留下悬空字面量（SyntaxError）。**变异失效时先看它是语法错还是
  「没改到那件事」**，与 #703①/#2718 的同类教训一致；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**。

## Revisit

- **裁决后的收尾**：owner 若裁「应写审计」⇒ 删 `_AUDIT_EXEMPT` 的 `_mark_running_timeout` 条目
  并补调用（**先确认 action 落进 ADR-0049 的 business 桶**，否则新 action 会掉进未分层状态）；
  若裁「豁免」⇒ 保留条目但把理由改写成正式依据（与另两条的 ADR 注释同形）。**陈旧即红**会
  强制这个收尾（写了审计还留着豁免条目，守卫会红）。
- **守卫的射程**：只认函数体内**直接**的 `record_audit*` 调用——经 helper 间接收敛的路径
  （例如某个 `_finalize()` 内部写审计）会被误判为「没写」；届时按需扩成「允许登记的间接收敛
  helper」而不是放弃静态判定。
- **扩到全仓的前提**：需要一个跨模块的「终态化路径」登记面（命名不一致是主要障碍）；在此之前，
  本守卫只在 recycler 上成立，别把它读成「全仓审计一致性已保证」。
- **`status_reason` 与指标的现状**：本单不改运行期行为；在裁决落地前，排障「这批 job 为什么
  集体 UNKNOWN」仍只能看 `status_reason` + `task_run_state_changes{timeout_type="running"}`
  （issue 建议 2 里那句文档补充，待裁决后一并做）。
