# promtool 场景债清零：17/17 全覆盖 + 逐条判别力自证（#2236）

Status: implemented
Class: testing

## Decision

消化 #2151 显式记账的 10 条存量场景缺口（`_SCENARIO_COVERAGE_DEBT`），并把该单
验收判据 2「**逐条**红绿自证」从一次性人工动作升成**常驻用例**。

### 1. 10 条场景断言落在第二个 promtool 测试组，而不是塞进原组

原组（#1257 建立）承载「修过的规则」，样本彼此交织（同一 `eval_time` 上多个告警
互相可见）。新增组自带 `input_series`，只断言本组该响的告警——**断言面与样本面
一比一对应**，日后删改某条规则不会牵动无关样本。promtool 的 `tests:` 各项相互
独立（各自全新状态），不存在「两组的 counter 累加导致某条提前触发」的耦合。

### 2. 样本形状以「注册表 + 埋点侧常量」为准，不照抄告警表达式

这是 #2151 期间已经踩过的坑（表达式里的 `status`/`job` 标签是凭记忆写的、指标注册表
里根本没有）。本轮逐条追到生产者侧取值：

| 规则 | 标签形状来源 | 关键点 |
|---|---|---|
| 5 条 `increase(x[15m]) > 0` | `backend/core/metrics.py`（Counter 无标签） | `exp_labels` 只剩 `severity` |
| `StabilityDbDeadlockDetected` | `backend/core/database.py:236-243` 的 `engine_label` | 真实值是 `sync`/`async`，不是 `postgresql` |
| 2 条 CSRF | `backend/core/csrf.py:89-98` 常量 | `reason` 逐字相等；两条共用指标，靠 `reason` 分流 |
| `StabilityClaimLeaseFailedSpike` | 无标签 | `rate[5m] > 0.1` 是**每秒** → 每分钟 12 次 = 0.2/s |
| `StabilityDispatchGateSlow` | Histogram `buckets=[0.5…300]`、`outcome` | 全部观测落 `(30,60]` → P95≈58.5s；`_bucket`(各 le) + `_count` + `_sum` 三条齐给 |
| `StabilitySaqQueueDepth` | `app_scheduler.py:156` + `SAQ_QUEUE_NAME` 默认 | 规则**没有**标签匹配器，但生产者带 `queue_name="stp"` → `exp_labels` 必须写全，否则 promtool 判「多了一个标签」而这里是**少了一个标签**的错法 |

### 3. 判据本体保留，清单清空

`_SCENARIO_COVERAGE_DEBT = frozenset()`，但 `test_every_alert_rule_has_scenario_case`
四条断言一字未改——它是防回归棘轮，不是本次的临时脚手架；清空后「新增告警不补场景」
立即变红。

### 4. 新增逐条判别力用例（参数化，17 条）

`test_promtool_gate_detects_per_rule_threshold_drift` 每次**只**抬一条规则的阈值，
断言场景层红**且失败输出点名该告警**；另有
`test_alert_names_are_collected_for_per_rule_drift` 守住「参数化数据源解析退化 →
用例静默消失」。为什么必须单独有：整批式（全阈值抬到 `1e12`）任何一条失配都能让
整批绿转红，「某条用例断言了一个永不出现的形状」这类假覆盖在其中不可见。

## Alternatives

- **把 10 条塞进原组**：改动更小，但样本面与断言面失去一一对应，且原组已有
  8 条共享 `eval_time`，再叠 10 条会让「谁在什么时刻响」难以判断。否决。
- **只补场景、不加逐条判别力用例**（判据 2 以人工自证留痕了事）：那正是 #2151
  自己的做法，也是本单要消化掉的债的来源——一次性人工核对的结论会随规则增删失效。
  否决，改为常驻用例。
- **把 promtool 场景层提进 PR 路径**：#2151 已裁决「PR 门禁不引入第三方二进制依赖」
  （离线纯度由 `tests/test_offline_subset_guard.py` 锚住），本单不重议。
- **为 10 条各写一个 pytest 用例**（对齐 issue 里「用例数 9 → 19」的算术）：场景断言
  住在 promtool 文件里，一次 `promtool test rules` 全量执行；拆成 10 次调用只是把同
  一次求值重复十遍。逐条可见性由第 4 点的参数化用例提供（17 条独立 pytest 用例），
  故实测计数为 **9 → 27** 而不是 19。

## Verification

- 本机 `promtool 2.53.3`：`promtool test rules deploy/prometheus/alerts-stability-platform.test.yml`
  → **SUCCESS**（17 条断言全绿）。
- `pytest tests/test_prometheus_alerts_contract.py -q` → **27 passed**（原 9 passed）。
- **红绿自证**：只回退场景文件（`git show HEAD:...test.yml`）而保留新代码 →
  **11 failed**：`test_every_alert_rule_has_scenario_case`（清单已空但只有 7 条覆盖）
  + 恰好 10 条 `per_rule_threshold_drift[...]` 参数化用例（对应本单补的 10 条，
  一条不多一条不少）。恢复后 27 passed。
- CI 档（pinned promtool **3.13.3**）：本机未复现该版本，标 **pending**——
  由 `backend-test` job 的 `Run repo-level tests`（`PROMTOOL_REQUIRED=1`）确认；
  缺失即红，不会静默 skip。
- `python scripts/run_gates.py check:quick`：见 PR。
- 同步面：`docs/operations/README.md` §6 由「三层」改「四层」（新增逐条判别力），
  场景文件头部注释改述覆盖面；#2151 的 Note Revisit 条目补去向指针。

## Revisit

- 清单为空 **不等于**「场景层永远不需要维护」：新规则若不补场景，第一次 CI 就红。
  若将来规则数量增长到需要按组分文件，出口是按 `group.name` 拆多个 `.test.yml`
  并在 `ci.yml` 里循环调用，而不是把断言搬回 Python 里手写 PromQL 求值。
- `histogram_quantile` 那条依赖桶边界 `[0.5…300]`：若有人调整
  `dispatch_gate_duration_seconds` 的 buckets，本场景的 `(30,60]` 落点会失去意义
  （P95 可能落到别的桶）。结构层不会拦这种「桶改了、场景还绿但断言的不是那回事」，
  出口是把该条场景的期望 P95 也做成逐条判别力的一部分（需要 promtool 输出可解析的
  数值，目前它只给通过/失败）。
- 若 promtool 3.x 后续版本改变 `alert_rule_test` 的注解比对严格度，本文件里
  「注解逐字匹配」的断言会先红——那是预期行为（注解是告警可读性的一部分），
  不要为了变绿而删 `exp_annotations`。
