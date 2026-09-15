# ADR-0043：中止宽限的请求主体同构（Abort Grace Subject Alignment）

- 状态：**Accepted** v1.0（实施已落地：2026-09-15，[PR #2165](https://github.com/DUElost/stability-test-platform/pull/2165)）
- 版本记录：v1.0 定稿（2026-09-15，owner 裁决三项全采纳，裁决记录 §9；由 [#2050](https://github.com/DUElost/stability-test-platform/issues/2050) 触发，[#1928](https://github.com/DUElost/stability-test-platform/issues/1928) 删除孤儿结构时指路要求「按 host 独立 grace 须先立 ADR」）；**v1.0 实施落地**（2026-09-15，[#2154](https://github.com/DUElost/stability-test-platform/issues/2154) / [PR #2165](https://github.com/DUElost/stability-test-platform/pull/2165)）：§5 切片 ①–④ 全部落地、D1/D2/D3/D4/D6 均有测试钉子（映射见 §8）；D3 的实现口径与正文文字存在**一处偏差，已记录在 §10 待 owner 追加裁决**（未裁决前不得据此再改实现）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-09-15
- 决策者：平台研发组（owner 裁决，2026-09-15）
- 标签：abort, 租约, 超时兜底, 共享状态, #2050, #1928, #1880, #2154
- 关联：[ADR-0021](./ADR-0021-script-content-alignment-gate.md)（维护窗口/升级门禁，host 级 abort 的调用方）、[ADR-0026](./ADR-0026-plan-execution-scaling.md)（规模化执行，reaper 归属面）、[ADR-0019](./ADR-0019-android-device-lease-and-capacity-scheduling.md)（设备租约 TTL/grace，**不同对象**，本 ADR 不裁决）、[ADR-0027](./ADR-0027-control-plane-horizontal-scaling.md)（多实例下 reaper 归属，本 ADR 不引入跨实例协调）

## 1. 背景

### 1.1 问题定性：宽限是被请求主体的属性，不是 run 的属性

abort 是 **request/ack** 协议：控制面请求中止 → Agent 以 job 终态确认 → 未确认者由
abort reaper 兜底推定失败。grace 的含义是「**给被请求者多久确认**」，所以它的**主体**
必须与**被请求的主体**一致。

现行实现把主体错位成 run 级：`abort_plan_run(host_id=H)` 只请求 H 的 PENDING/RUNNING
job（`requested_job_ids` 只含该主机的 job），却写 **run 级** `abort_requested.at`，且
**每次 host 级 abort 都重置**它（`plan_run_abort.py:445-472`，#1928 注记已如实写明）。

由此产生的不是一组独立缺陷，而是**共享可变时钟**的必然后果：run 级 `at` 是被 N 个
独立请求者共享的单一可变状态，任一 host 的写入都会改写其他所有主体的剩余宽限，且
「最后写入者赢」。

### 1.2 事实基线（2026-09-15 只读核验）

| 面 | 位置 | 现状 |
|---|---|---|
| 写 | `backend/services/plan_run_abort.py:445-472` | host 级 abort 写 run 级 `at`/`deadline_at` 并重置 |
| 消费 | `backend/scheduler/device_lease_reconciler.py:472-516` | reaper 只按 run 级 `at` 计时；#2050 后追加 `_abort_request_covers_job` 收窄候选集 |
| 已删除 | `plan_run_abort.py:160-165`（#1928） | `_record_host_abort_request`（写 `abort_requested_hosts` 但无调用方无消费者）已移除，原位注释要求先立 ADR |

**同一根因已付费四次**：#1880（host 作用域错位）→ #1928（孤儿数据结构）→ #2050（同 run
其他主机**从未被请求**的正常 job 被打成 UNKNOWN；UNKNOWN 期间保留 ACTIVE lease 占着设备，
且 `state_machine` 只允许 `UNKNOWN → {RUNNING, FAILED}`，迟到的 `COMPLETED` 也落 FAILED）
→ #2050 Revisit 的残留窗口（host 级 abort **之后**才被 claim 成 RUNNING 的该主机 job 不在
名单快照内，无人回收）。

#2050 收窄了**候选集**（不再误杀别的主机），但下列三项仍在，且它们的修正方向互相冲突，
无法靠继续打补丁收敛：

1. **N×GRACE 放大**：N 台主机依次热更新，每次 host 级 abort 重置整轮窗口 → 最多多等
   host 数 × `ABORT_ACK_GRACE_SECONDS`（默认 60s）；
2. **late-claim 无归属**：该 host 在 abort 后被 claim 的 job 无人回收（依赖名单快照刷新，
   run 级有 `plan_run_abort.py:551` 的刷新，host 级没有）；
3. **「最后写入者赢」**：某主体的宽限会被**其他**主体的后续请求延长，与「宽限是给被请求
   者的时间」的语义相反。

### 1.3 约束

- **不改 abort 的「请求」语义**：`requested_job_ids` 仍是「哪些 job 被请求过中止」的名单
  （#2050 的 `_abort_request_covers_job` 原样保留，与本 ADR 的**计时**语义正交）。
- `run_context` 是 JSONB，历史行无 `abort_requested_hosts` 键 → 迁移必须向后兼容：键缺失
  退化为现行为，**不得**变成「无人回收」。
- UNKNOWN 期间保留 ACTIVE lease 属既有隔离语义（ADR-0026 / 租约面），本 ADR 不动。
- 不裁决设备租约 TTL/grace（ADR-0019 面），不引入跨实例协调（ADR-0027 面）。

## 2. 决策

### D1（核心）：请求主体 ≡ 计时主体

宽限的计时真源与请求主体一一对应——**谁被请求，谁计时**。

- run 级 abort → 写 run 级 `abort_requested.at`/`deadline_at`（现行为不变）；
- host 级 abort → 写 `abort_requested_hosts[host_id].at`（+ `deadline_at`）；
- reaper 消费时按**被回收 job 所属主体**取时钟：host 级请求取该 host 的 `at`，run 级请求
  取 run 级 `at`；**两者并存时取更早的 deadline**（更早到期者先兜底，互不覆盖）。

### D2：宽限自**首次**请求起算，后续请求不重置

同一主体重复请求不延长宽限。直接消除 1.2-1（N×GRACE）与 1.2-3（最后写入者赢）。

### D3：主体内的 late-claim job 自动纳入

host 级 abort 之后才被 claim 成 RUNNING 的**该 host** job，只要该 host 的时钟仍在窗口内，
即视为被覆盖——不再依赖 `requested_job_ids` 快照刷新。这是「主体同构」而非「名单快照」
的必然推论，同时消掉 1.2-2 的残留窗口。

> run 级主体的同构实现（claim 路径刷新 `requested_job_ids`，`plan_run_abort.py:539-551`）
> 保留不变。

### D4：迁移与兼容

- 历史 `run_context` 无 `abort_requested_hosts` → 视为该键不存在，退化到 run 级时钟
  （现行为），不得变成「无人回收」；
- 已有 `abort_requested.at` 的 run：run 级时钟继续有效；
- **不回填历史数据**（`abort_requested_hosts` 只对新建请求写入）；
- 写入沿用 `_patch_run_context` + `_reload_run_context`（#793/#1552 先例：jsonb_set 分段写
  + ORM 视图 expire，避免整段读改写回抹掉并发写者的键）。

### D5：显式不做（各带复议触发器）

1. **不做 per-job 时钟**（§7-1）：job 不是请求主体，且状态数量放大两个数量级；
2. 不改 UNKNOWN 的后续语义（保留 ACTIVE lease、UNKNOWN grace 后 FAILED + release）；
3. 不裁决设备租约 TTL/grace（ADR-0019）：与 abort ack 是不同对象；
4. 不引入 fencing token / 跨实例协调（ADR-0027）：多实例下取时钟的一致性另主题。

### D6：可观测与验收

- 审计/状态原因需能区分回收主体（host 级 vs run 级），现有 `abort_ack_timeout` 不得把两者
  混为一类；
- 验收：
  1. 同一 run 两台主机依次 host 级 abort → 第二次不延长第一次的 deadline（反 N×GRACE）；
  2. host 级 abort 后被 claim 的该 host job，在窗口内仍被回收；
  3. 历史无键 run 行为不变（不变成无人回收）；
  4. 其他主机 RUNNING job 不被回收（#2050 三例保持通过）。

## 3. 备选方案与权衡

| 备选 | 内容 | 裁决 |
|---|---|---|
| A. 维持 run 级时钟 + 只收窄候选集（#2050 现状） | 改动最小 | **否**：§1.2 三项仍在，且修正方向互相冲突（要么重置要么不重置），无法靠补丁收敛 |
| B. per-host 时钟（D1/D2/D3） | 消除共享可变状态 | **采纳**（本 ADR） |
| C. per-job 时钟 | 最精确 | **否**（D5-1）：状态数量放大两个数量级；job 不是请求主体 |
| D. 只改「不重置」、保持 run 级（只做 D2） | 省掉新键 | **否**：主体错位未解——「按 host 取 at」与「late-claim 归属」都无从表达，且 run 级时钟对 host 级请求仍是共享可变状态 |

## 4. 影响

### 4.1 正面

- §1.2 三项从「靠补丁压制」变成「结构上不可能」——它们是同一个共享可变状态的三种表现；
- **新增 abort 主体时计时语义可直接套用同构原则**（device 级 / 站点级），零边际决策；
- reaper 的消费语义首次有单一真源：此前分散在 `abort_plan_run` docstring、#1928 的删除
  注释与已删除的孤儿函数三处，且口径不一致。

### 4.2 负面 / 接受的代价

- `run_context` 新增键（JSONB，无 DDL，但写入必须走分段写先例）；
- reaper 多取一层时钟并做「取更早者」判定，复杂度上升；
- 历史数据不回填 → 过渡期两套时钟并存（兼容分支由 D4 界定，有界）；
- 「不重置」是反直觉的（看起来像 bug），**必须有本 ADR 的明确接受 + §7 复议触发器保护**，
  否则会被后人当缺陷修回去。

### 4.3 兼容与回滚

- 分层落地（§5），每步独立可回滚；
- 最坏情况（host 级时钟写入失败/缺失）退化为 run 级时钟 = 现行为，不会「无人回收」，
  故回滚安全。

## 5. 落地与后续动作

1. ✅ 实施已由 [#2154](https://github.com/DUElost/stability-test-platform/issues/2154) 完成，
   落地 PR [PR #2165](https://github.com/DUElost/stability-test-platform/pull/2165)
   （2026-09-15 合入 `main`）；
2. ✅ 实施切片 ①–④ 全部落地：① 写入侧 `plan_run_abort.py`（host 级 abort 写
   `abort_requested_hosts[host_id]`，首次写入、后续只刷新 `reason`/`triggered_by`）→ ②
   reaper `_abort_reap_clock` 按主体取时钟、并存取更早者 → ③ late-claim 覆盖（**实现口径
   与 D3 正文有一处偏差，见 §10**）→ ④ 兼容分支与用例（测试映射见 §8）；
3. ✅ #2050 的 `_abort_request_covers_job` 保留为**名单语义**（run 主体的覆盖判据），与本
   ADR 的**计时语义**正交，两者并存；
4. ✅ 落地后回填已完成：`plan_run_abort.py` 的「按 host 独立 grace 须先立 ADR」指路注释改
   为指向本 ADR；`abort_plan_run` docstring 中 #1928「每次 host 级 abort 重置宽限」的注记
   已标注失效并写明替代语义。

## 6. Verification

- 单元：host 级时钟写入 / 不重置 / 取更早者 / 历史无键退化；
- 集成：§2-D6 四条验收；
- 回归：`backend/tests/scheduler/`、`backend/tests/services/test_plan_run_abort_aggregator_race.py`、
  `backend/tests/api/test_plan_run_abort_api.py` 全绿（#2050 的三例必须仍通过）。

## 7. Revisit（未触发前不得重提）

1. **新增第三种 abort 主体**（device 级 / 站点级）：必须复用 D1 的同构原则，不得新建第四种
   时钟形态；若无法复用，先修订本 ADR；
2. **键膨胀**：观测到 `abort_requested_hosts` 无界增长（host 数 × 请求次数）→ 评估清理/合并
   策略（**注意 D2：合并不等于重置宽限**）；
3. **与租约语义耦合**：若出现「abort 宽限内不回收租约」一类需求 → 与 ADR-0019 联审，不得
   单方面把两套 grace 合并；
4. **跨实例**：reaper 归属落地多实例（ADR-0027）后，复核「取更早者」是否需要实例间一致
   ——本 ADR 不引入跨实例协调。

## 8. 关联实现 / 文档

- 实现：`backend/services/plan_run_abort.py`（写）、`backend/scheduler/device_lease_reconciler.py`
  （消费）、`backend/core/job_timeout_config.py`（`ABORT_ACK_GRACE_SECONDS`）、
  `backend/services/state_machine.py`（UNKNOWN 转移约束）；
- 笔记：[`docs/notes/bug-fix/2026-09-15-abort-reaper-requested-scope-2050.md`](../notes/bug-fix/2026-09-15-abort-reaper-requested-scope-2050.md)（候选面收窄与残留窗口）、
  [`docs/notes/bug-fix/2026-09-14-audit3-misc-1928-1930-1931-1923.md`](../notes/bug-fix/2026-09-14-audit3-misc-1928-1930-1931-1923.md)（#1928 删除与 ADR 指路）；
- Issue：[#2050](https://github.com/DUElost/stability-test-platform/issues/2050)、
  [#1928](https://github.com/DUElost/stability-test-platform/issues/1928)、
  [#1880](https://github.com/DUElost/stability-test-platform/issues/1880)；
- 落地 PR：[#2165](https://github.com/DUElost/stability-test-platform/pull/2165)
  （2026-09-15 合入 `main`）。

### 8.1 测试映射（§2-D6 验收 ↔ 用例）

| 决策 / 验收项 | 用例 |
|---|---|
| D1 主体不串台（host 级只写 host 时钟、run 级不写 host 时钟） | `backend/tests/api/test_plan_run_abort_api.py`：`test_host_abort_writes_host_clock_not_run_level_at`、`test_run_level_abort_does_not_write_host_clock` |
| D1 host 时钟互不共享 | `backend/tests/scheduler/test_abort_reaper.py`：`test_host_clock_is_per_host_not_shared` |
| D1 并存取更早者 | 同上：`test_run_and_host_clocks_take_the_earlier` |
| D2 反 N×GRACE（后续请求不重置） | `backend/tests/api/test_plan_run_abort_api.py`：`test_host_abort_clock_not_reset_by_later_request` |
| D3 late-claim 覆盖 | `backend/tests/scheduler/test_abort_reaper.py`：`test_late_claimed_job_on_aborted_host_is_reaped` |
| D4 历史无键退化（不变成无人回收） | 同上：`test_legacy_abort_without_requested_ids_still_reaps`（既有，仍通过） |
| D6 主体可区分（审计不混为一类） | 同上：`status_reason` 断言 `abort_ack_timeout_host` vs `abort_ack_timeout`（3 处） |
| #2050 回归（其他主机 RUNNING job 不被回收） | 同上：`test_host_scoped_abort_spares_other_hosts_running_jobs` 等既有三例 |

## 9. 裁决记录（owner，2026-09-15）

三项**全部采纳**，本 ADR 由 Proposed 转 Accepted v1.0，D1–D6 全部生效：

| # | 待裁决项 | 裁决 | 理由 |
|---|---|---|---|
| 1 | D2「自首次请求起算、后续请求不重置」的反直觉代价 | **接受** | 换 §1.2-1（N×GRACE 消失）与 §1.2-3（不再「最后写入者赢」）；不重置是「宽限是给被请求者的时间」的必然推论，反直觉性由本 ADR 明确接受 + §7 复议触发器保护 |
| 2 | D3 的 late-claim 覆盖是否纳入本 ADR | **纳入** | 它是「主体同构」而非「名单快照」的推论，与 D1 同属一条语义；若下沉为实施细节，会在实施期被当作可选优化砍掉，从而留下 §1.2-2 的残留窗口 |
| 3 | 过渡期两套时钟并存（D4 不回填历史数据） | **接受** | 兼容分支由 D4 界定且**有界**：键缺失退化 run 级 = 现行为，绝不变成「无人回收」；不回填的代价仅为过渡期新旧行为并存 |

**未采纳的路径**：A（维持 run 级 + 只收窄候选集，#2050 现状）与 D（只做「不重置」、不引入 host 级时钟）均在 §3 否决——两者都保留主体错位，后者更让 D3 无从表达。

**后续**：实施由 [#2154](https://github.com/DUElost/stability-test-platform/issues/2154) 跟踪（本 ADR 只作裁决，不含实现），切片与验收见 §5 / §2-D6 → **已于 2026-09-15 由 [PR #2165](https://github.com/DUElost/stability-test-platform/pull/2165) 完成**（状态回填见 §5 / §8.1）。

## 10. 实施注记（#2154 / PR #2165，2026-09-15；**待 owner 追加裁决**）

PR #2165 落地时，D3 的覆盖判据按如下口径实现，**与 D3 正文「只要该 host 的时钟仍在窗口内」
存在一处偏差**：

- **实现口径**：host 主体的覆盖判据 = 该 host **存在** host 级时钟
  （`abort_requested_hosts[host_id].at` 有值），而**非**「时钟仍在窗口内」；
- **理由**：若严格要求「仍在窗口内」，late-claim 的 job 在 host 时钟过期后**永远**不会被
  覆盖——名单快照对 late-claim job 没有刷新通道（§1.2-2），判据会随之永久失效，等于把本
  ADR 要消掉的残留窗口固化成永久残留，与 D3 意图相反；
- **行为差异**：host 时钟过期后，该 host 上 late-claim 的 job 仍会被判为「时钟已过 grace」
  并回收（按正文文字它会被漏掉）；
- **取舍**：宁可多回收（转 UNKNOWN、保留 lease，属既有兜底语义），不可漏回收（残留窗口）；
- **待裁决**：请 owner 在 §9 追加一项裁决确认本口径；确认后升 v1.1 并把 D3 正文改为
  「host 主体的覆盖判据 = 该 host 存在 host 级时钟」。**未裁决前不得反向改实现**。
