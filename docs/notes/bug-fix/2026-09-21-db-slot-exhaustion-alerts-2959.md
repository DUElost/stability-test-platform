# 连接槽耗尽要能单独看得见：`slots_exhausted` + 两条告警（#2959 缺口①）

Status: implemented
Class: bug-fix

关联：[#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（生产实测来源）、
[ADR-0047](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（容量取向 D1–D6，本单**不裁决**，
其 D5 就是"先采基线、再在事件侧加告警"）、
[#703 第③面（这三条序列的出处）](./2026-09-17-db-pool-and-abort-fanout-signals-703.md)、
#1958/#1959（死锁计数与锁序家族的同族判读）、#1257（"引用不存在的标签选择器"）。

## Decision

**先说清这是补谁的账**：#703 第③面（PR #2571）是我自己埋的。当时 `classify` 只分两类
（`timeout` / `error`），所以 09-20 生产上 **1211 次 `TooManyConnectionsError` 全部落进
`kind="error"`**——序列有、计数对，但**分不出"PG 已经不接受新连接"和"某个查询挂了"**。
那三种成因的处置完全不同，混在一起等于没埋。本单补的就是我自己留的这一格。

1. **新增 `kind="slots_exhausted"`，而不是新建一条指标。** 取连接失败已经有一个事件点
   （`Pool.connect()` 包装），换指标只会多一个"要不要看"的问题；把成因做进 `kind` 值域，
   面板与告警的写法都不变。判据是 SQLSTATE **53300**（`too_many_connections`）——
   数值不是凭记忆：`asyncpg.exceptions.TooManyConnectionsError.sqlstate` 与
   `psycopg.errors.TooManyConnections.sqlstate` 在本机两驱动上都实测为 `53300`。
   另加**消息兜底**（`remaining connection slots are reserved` / `too many connections`），
   沿用 `_is_deadlock` 的既有纪律（驱动版本差异时 sqlstate 可能不在最外层）。
2. **分类必须沿异常链找**，因为取连接失败会被 SQLAlchemy 包一层：只判最外层的结果是
   "分类代码上线了、生产上永远归 `error`"——一种最坏的假绿。故 `_iter_exception_chain`
   走 `__cause__/__context__`（限深 6，防自引用链），并且**端到端用例**从
   `Pool.connect()` 包装点验收到 `failure` 参数真的带上了 kind。
3. **两条告警，不是一条。** 槽耗尽（PG 侧拒绝）与池排队超时（应用侧饱和）在
   同一时刻可能只有一个成立，合成一条就会互相掩盖：`StabilityDbConnectionSlotsExhausted`
   与 `StabilityDbPoolCheckoutTimeout`，各自 15m `increase(...) > 0`、`for: 5m`。
   动机与 #2104 完全同形——那次把死锁消掉之后，代价转移到"普通锁等待"，于是补了
   `StabilityDbLockWaitSustained`；这次是"计数有了但没人被通知"。
4. **`kind` 值域两侧同步做成门禁**（本单最有价值的一条防复发）。
   `metrics._DB_POOL_CHECKOUT_FAILURE_KINDS` 是白名单，未列入的值**静默折叠回 `error`**；
   于是"只在一边加 kind"不会报错，只会让告警永不响。故加
   `test_classifier_output_domain_matches_metrics_whitelist`（分类器产出集合 ⊆ 白名单）
   与 `test_metric_label_is_not_folded_for_new_kind`（真去注册表里确认子序列出现）。
   场景测试同理：它喂的是**真实 kind 标签**，所以规则里选一个代码不会产出的值，
   promtool 直接不通过（就是 #1257 那一类）。
5. **不改任何容量参数。** `STP_DB_POOL_SIZE` / `MAX_OVERFLOW` / `pool_timeout` 一字未动——
   那属 ADR-0047 的 D1/D2，未裁决。本单只把"发生过的没人知道"这一格关掉；
   #2959 的定量事实（两池合计 180 vs 可用 97）留在 ADR 里继续待裁。

## Alternatives

- **只加告警、沿用 `kind="error"`**：否决。要么把 error 全量报（噪声大到没人看），
  要么靠日志文本匹配（`promtail` 侧解析 `TooManyConnections`）——后者让告警依赖日志格式，
  是"绿而空"的另一个入口。
- **新建独立指标 `stability_db_slot_exhausted_total`**：否决。事件点同一个、维度多出一套，
  面板/告警要各看一处；`kind` 值域本来就是为这种情况设计的。
- **加水位告警 `db_pool_checked_out / 90 > 0.9`**：否决（本轮）。分母 90 是**未裁决的**池配置，
  把它写进规则等于用一条告警替 ADR-0047 做决定，且改 env 即失真。事件侧两条已经覆盖
  "真的借不到"这件事，水位只适合做趋势面板。
- **顺手把 `pool_timeout` 设成 5s（#2959 方向里的一条）**：否决。那是 D2 的方向级取舍
  （决定失败形态与用户可感行为），不属"补可见性"这一单；混进来会让本 PR 的判据从
  "能否分辨"变成"我们已选边"。
- **只改 `metrics.py` 白名单、不加分类器**：否决，那会让新 kind 永远拿不到值（折叠回 error）。

## Verification

- `pytest backend/tests/test_database_config.py -q` → **18 passed**（本单新增 4 条）。
- `promtool check rules` → **SUCCESS：31 rules**；`promtool test rules` → **SUCCESS**
  （新规则各有一条场景，且 `exp_labels` 断言 alert 保留 `engine`+`kind`——定位时一眼看出哪个池）。
- `pytest tests/test_prometheus_alerts_contract.py tests/test_grafana_dashboard_contract.py -q`
  → 见 PR 表（含「每条规则都有场景断言」「选择器标签必须真的会被产出」两道恒跑门禁）。
- `ruff check backend`、`python scripts/run_gates.py check:quick`：见 PR。
- **判据判别力（5 处回退/变异，全红后恢复全绿）**：

| 变异 | 期望被谁抓到 | 实测 |
|---|---|---|
| 白名单漏收 `slots_exhausted`（只改一边） | 两条防漂移钉子 | **2 failed** |
| 分类器去掉槽耗尽分支 | 分类判据 | **3 failed** |
| 包装点写死 `failure="error"`（不传分类） | 端到端用例 | 红 |
| 告警规则选代码不会产出的 kind（`slot_exhausted`，少一个 s） | promtool 场景 | 红 |
| 恢复 | — | **18 passed + promtool SUCCESS** |

第 4 行是本单想留的那条：它证明"规则写错了标签值"不再靠人眼抓。

**未做（明确不属于本单）**：#2959 缺口②（#777 R-02 的旧叙事仍写 #1516 修复前的"同步池仅 15"）
与缺口③（abort/超时收尾与尖峰的**因果**未证——本单不替它下因果结论）；
改 #2959 正文里说的"抬 `max_connections` / 收 `max_overflow`"任一项（ADR-0047 待裁）。

## Revisit

- **告警响了之后做什么**：本单只解决"知道"。第一次在生产亮起来时，按 `engine` 分清是
  async（Agent 终态回传路径，#2959 现场）还是 sync（调度/后台），再回到 ADR-0047 D1/D2。
  若两周内一次没亮，也要回来看是不是**基线采集面**有问题（比如 `prometheus.yml` 没抓这条），
  而不是直接读成"没问题"——那正是本单消灭的那个形态。
- **面板仍缺**：Grafana 里没有池/槽的图（#1258 撤面板后未恢复）。现在有了可告警的事件序列，
  恢复面板的判据已具备；读法（`kind` 三条怎么同屏、是否分 sync/async 双行）属面板设计判读。
- **`classify_pool_checkout_failure` 目前只服务池包装点**。若将来别的取连接入口
  （自建 pool、第三方扩展）出现，需复用同一分类，否则又会出现"部分路径不产 kind"。
- ADR-0047 至今无跟踪载体（#703 已关）；已在本单的 PR/issue 评论里把 #2959 指为它的事实来源。
