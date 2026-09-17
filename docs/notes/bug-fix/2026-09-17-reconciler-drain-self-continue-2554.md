# 租约排空未完即自续轮：一次持锁内把工作做完（#2554）

Status: implemented
Class: bug-fix

## Decision

`#2531`（PR `#2548`）把 Phase 2 与 `stale_unknown` 从「处理一台就 `break`」改成
「一轮最多 `RECONCILER_DRAIN_BATCH=20` 台、一候选一事务」，速率从 4 台/分钟提到
~80 台/分钟。但**收口的第二半仍在设计里**：排空未完仍然要等下一个
`IntervalTrigger(seconds=15)` 才解下一批——于是 `cap` 这个**事务边界保护**被当成
**速率旋钮**来读，而它的两个方向都是错的：调大 `cap` 会连带拉长单轮持锁窗口（那正是
`cap` 存在的理由），调小 `RECONCILER_DRAIN_BATCH` 所在的周期会牵动 D5 与 abort 对账。

`#2548` 留下的一次性容量探针（testcontainers PG，同机）给出每台排空 ≈8–9ms 且
**线性**（20→200 候选：165–222ms → 1654ms，无超线性拐点）。按 `agent_api` 的
60 host × ~17 device ≈ 1000 台容量口径：默认 `cap=20` 时收口 1000 台 =
`ceil(1000/20)=50` 个 tick × 15s ≈ **12.5 分钟，而这 50 轮真正干活的时间合计 ≈9 秒**
（占空比 <1%）。12.4 分钟是躺在 `IntervalTrigger` 上等下一拍，期间设备一直
`DEVICE_BUSY`。这就是本单要砍掉的那 12.4 分钟。

改法（**只加循环，不动事务边界、不动互斥语义**）：

1. **同一次持锁内自续**：`device_lease_reconcile_once()` 在 `_reconcile_lock` 之内
   先读一次积压（`_record_unknown_backlog()`），把 `grace_expired` 交给
   `_drain_remaining_rounds()`；只要还有「已过宽限、正等着被解锁」的行，就立刻再跑
   一整组 `_reconcile_checks()`。每轮仍是四条检查各一个事务，**不得**退回尾部统一
   终态化（`#1172`/`#986` 的契约由 `#2548` 的独立提交钉子继续钉住）。
2. **判据用读数、不用截断标志**：`#2554` 的 issue 正文写的是「本轮发生截断就自续」，
   实现改成「本轮结束时 `grace_expired > 0` 就自续」。理由是截断标志只是「本轮撞到
   `cap`」，读不出**别的分支**留下的余量；而 `grace_expired` 与运维看到的
   `stability_reconciler_unknown_backlog{state}` 是同一个事实，拿它当循环条件就是拿
   运维口径当循环条件。副作用：`grace_expired == 0` 时不再空跑第二轮。
3. **三个出口，缺一不可**：① 排空；② 墙钟预算 `RECONCILER_DRAIN_MAX_SECONDS`
   （默认 5s = 默认周期的 1/3），到点即停并打
   `reconciler_drain_budget_exhausted remaining=<n>`，余量交回下一个 tick；
   ③ **无进展**（本轮读数不比上轮小）立刻让位并打 `reconciler_drain_no_progress`。
   第三条不是保险丝而是**防空转**：毒行（终态化反复失败、被别处持锁）会让每一轮都
   「有剩余但没进展」，没有这条出口就是每 tick 把 5s 预算烧满——33% 占空比的
   看不见但很努力地不进展。
4. **`0` 是关闭自续，不是配错**：`reconciler_drain_max_seconds` 的下界是
   `ge=0.0`（刻意区别于 `reconciler_drain_batch` 的 `ge=1`：后者为 0 会让解锁彻底
   停摆）。取 `0` 时一次调用只跑一轮，回到 `#2548` 的形状——这是留给现场的止血开关。
   **且此时不打 `budget_exhausted`**：把「运维主动关掉」报成「预算耗尽」会让一个开关
   伪装成每 15s 一次的告警。
5. **读数口径随节奏改**：`reconciler_unknown_backlog` 的 `eta_min_seconds` 从此带
   `eta_basis=`——`selfcontinue` 时它只是「纯等 tick」的悲观上界（真实进度看
   `reconciler_drain_selfcontinued rounds=/remaining=/elapsed_ms=`），`tick_only`
   才是原来的承诺。同一个数字在两种节奏下含义不同，不标注就会把「已经快好了」读成
   「还要 12.5 分钟」。每轮自续都刷新一次 gauge，所以「停在哪儿」始终有人能看见。

## Alternatives

- **调大 `RECONCILER_DRAIN_BATCH`**（issue 正文明令禁止的方向）：放弃——那把 `cap`
  从边界保护改成速率旋钮，单轮持锁窗口与 `_reconcile_lock` 被占用的时长一起变长；
  而且对「积压 10 台」和「积压 1000 台」给出同一个赔率，自续 + 预算才是按剩余量自适应。
- **调小 `RECONCILER_INTERVAL_SECONDS`**：放弃——`#2531` 已裁决过同一方向（速率不该由
  tick 频率承担），且全局周期会挤压 D5/abort 对账。
- **自续改用一次性 `DateTrigger` 立刻再排一个 job**：放弃——那会把「一次持锁」变成
  「一次持锁 + 一次调度往返」，而 `reconciler_skip_previous_still_running` 从**背压**
  退化成**竞态噪声**（第二个 job 会看见第一把锁还在手里）；互斥语义恰恰是本单的
  不变量（issue 判据第三条）。
- **只判预算、不判无进展**（相信 `grace_expired` 单调下降）：放弃——见 Decision 3。
- **`budget == 0` 也打 `budget_exhausted`**（issue 判据第二条的字面形状）：放弃并
  留下记录——判据同时写了「压到 0（或极小）」，本单用**极小预算**满足它
  （`test_drain_budget_is_a_hard_stop_and_zero_disables` 前半段：`0.01s` ⇒ 只跑一轮
  + 照常报剩余量），用 `0` 表达「关闭」并禁止那条告警。
- **把预算检查放进 `_reconcile_checks()` 内部（每检查之间判）**：放弃——粒度更细但会把
  四条检查从「一组」拆成「可中断序列」，Phase 1/D5 半路被切走属于另一个语义。
  现在最坏超额一轮（`cap=20` ⇒ ≈180ms 量级），换来「不会在一组检查中间丢下候选」。

## Verification

- 新增 6 条用例（`backend/tests/scheduler/test_device_lease_reconciler.py`，
  该文件 27→33）：
  1. `3→1→0` 三档剩余量 ⇒ 自续 3 轮（并断言 `_reconcile_checks()` 真被调用 3 次）；
  2. 无进展 ⇒ **只跑 1 轮**就收手，且留下 `reconciler_drain_no_progress` 读数；
  3. 预算硬止损：每轮都有进展时 `0.01s` 预算只允许 1 轮 + 报剩余量；`0` ⇒ 0 轮、
     一次检查都不许多跑、且不得报成 `budget_exhausted`；
  4. 无进展优先于烧预算：同一轮里两条出口同时可触发时，必须走无进展（断言
     `budget_exhausted` **没有**出现）；
  5. **端到端（真 PG + 真聚合）**：6 台 / `cap=2`，**一次**
     `device_lease_reconcile_once()` 收完全部 6 台（6 台 FAILED + 租约 RELEASED），
     并断言期间真的发生了自续、且自续全程**持有同一把 `_reconcile_lock`**；
  6. `eta_basis` 随 `RECONCILER_DRAIN_MAX_SECONDS` 开关切换（两种口径各自钉住）。
  第 5 条为什么是 6 台而不是 issue 里写的 3 台：一次 `_reconcile_checks()` 里
  Phase 2 与 `stale_unknown` **各自带 cap**，`cap=2` 时单个 pass 最多收 4 台——
  3 台根本不会留下余量，也就不会触发自续。判据要成立必须超过 `2×cap`，这条已写进
  用例 docstring 防止下一个人再按 3 台改回去。
- `tests/test_settings_scheduler.py::test_drain_knobs_bounds`：两个旋钮的下界语义
  分开钉（`drain_batch` 的 `0`/`2000` 必须响亮失败、`drain_max_seconds` 的 `-1` 失败
  但 `0` 必须可用）。
- `backend/tests/scheduler/test_device_lease_reconciler.py` + `tests/test_settings_scheduler.py`
  **42 passed**（收集数核对：33 + 9；此前一次 35 是加用例前的旧计数，不是「绿而空」）。
- `backend/tests/scheduler/` 全目录 + 离线契约面（`test_settings_scheduler.py` /
  `test_env_example_parity.py` / `test_env_inventory.py` /
  `test_alert_metric_producers.py` / `test_prometheus_alerts_contract.py`）
  **198 passed**（testcontainers PG，未连生产库）。`#2548` 的三条钉子（`[2,2,1]`
  速率律、后序候选被堵时前序已独立提交、积压三桶）继续全绿。
- 变异自证 7 条，全部 on-target（红条为断言级，未使用本仓不认的 `--timeout`）：
  `M1` 去掉预算判断 ⇒ 预算用例红；`M2` 去掉无进展出口 ⇒ 两条无进展用例红，各自
  **空转满 30s 预算**才被止损（60.88s 的失败时长本身就是那条出口要防的东西）；
  `M3` `ge=0`→`ge=1` ⇒ 边界用例红；`M4` 删掉 `device_lease_reconcile_once` 里的自续
  调用 ⇒ 端到端用例红；`M5` `budget <= 0`→`budget < 0`（`0` 不再关闭自续）⇒ 预算
  用例红（「关闭不得报成耗尽」那条断言正是这一刀的钉子）；`M6` 每轮不重读 gauge ⇒
  自续轮数用例与预算用例同时红；`M7` 把 `eta_basis` 写死成 `selfcontinue` ⇒
  口径用例红。
- **未完成（pending）**：`python scripts/run_gates.py check:quick` 与 dev 隔离栈的
  端到端墙钟复测（`#2531` Revisit 第 1 条，本单不代替它）。

## Revisit

- **5s 这个默认值是从容器 PG 的 ≈9ms/台推的**（≈550 台 / 次持锁）。生产上每台候选的
  终态化还要叠真实链式派发、dedup 入队与多 job 共享同一 PlanRun 的锁排队，同样的 5s
  覆盖的台数只会更少。缺的是**每轮自续的耗时分布**（现在只有 `elapsed_ms` 日志，
  没有直方图，属 `#2365` 背景任务耗时面）。若 `reconciler_drain_budget_exhausted`
  在现场成为常态，第一动作是查单候选成本，不是调大预算——调大预算等于拉长持锁，
  与 `#2531` 的 `le=1000` 上界是同一个权衡。
- **无进展判据是「不比上轮小」**，对「每轮只解 1 台、共 500 台」这种病态慢速**有进展**
  的形状不设防，只能靠预算兜住。真出现那种形态时应该加的是「进展速率低于阈值」这条
  出口，而不是把预算取消。
- **自续只对 `grace_expired` 判据**。Phase 1（RUNNING 过期 → UNKNOWN）与 D5/abort
  对账不参与自续条件，也不被本单改变节奏；`#2531` 的 Revisit 已经记过「Phase 1 不设
  预算」，本单没有把这个缺口填上，只是没让它更糟。
- **`_reconcile_lock` 与预算都不跨进程**。ADR-0027 的多实例形态真落地时，需要的是
  leader election 下的**单一排空者**，而不是每个实例各自自续 5s——届时本函数的
  正确性不变，但「谁在干活」需要重新定义。
