# 锁等待/保留观测面三处收口（多库作用域护栏 / 清理积压可判定 / 告警指标名自洽）

Status: implemented
Class: process

## Decision

承接 `#2104`（锁等待观测面）与 `#2105`（批大小杠杆）的 Revisit 三项，逐项落定。

### 1. 多库场景：**不加 label**，改加行为护栏

原 Revisit 的设想是「多库时给等待 gauge 加 label」。读代码后否决：该 gauge 由**抓取请求自己的
会话**（`bind = db.get_bind()`）上的聚合查询算出，SQL 里已带 `datname = current_database()`
——也就是说它**按构造只能描述本实例连的那个库**，多值 label 不存在第二个取值。真正需要守的是
另一件事：**那条过滤条件删掉就错**（`pg_stat_activity` 是全实例视图，同集群跑多个库很常见），
所以补一条**行为护栏**：在另一个库（`postgres`）里用 advisory lock 制造持续数秒的锁等待，
断言本实例 `/metrics` 的 `max_wait_seconds` 仍 < 1s。

护栏的两个刻意的选择：用 advisory lock 而非表行锁（不建表、不锁系统目录，对外零副作用，
且在 `wait_event_type='Lock'` 上与行锁同类）；断言 `< 1s` 而非 `== 0`（本库的 admission pump
有亚秒级瞬时等待，断言 0 会把护栏变成 flaky）。

### 2. 清理积压：补 `stability_retention_candidate_runs` + `stability_retention_batch_size`

原 Revisit 说「批大小调小后若清理跟不上需补积压 gauge」。这里把它提前做成**可判定**信号：
两个 gauge **成对**上报，`candidates >= batch_size` 即本轮批被填满 = 队列里还有到期 run
（持续成立即为积压）。只报其一，判据就得在告警表达式里硬编码 `plan_run_retention_batch_size`
——而它正是 `#2105` 那个**会被调小**的杠杆，硬编码值会随调整失真。

实现要点：上报点放在两个早退**之前**（清空后 gauge 必须回到 0，不许停在上一轮的非零值上
——会骗人的观测面比没有更糟）；用候选数而不是「已删除数」，因为 NFS 失败的 run 会被推迟重试
（已删除数会低估积压）。

### 3. 告警阈值：结构上可校准 + 指标名自洽守卫；真实校准**受阻于数据**

事实：30s 阈值落在直方图分桶边界上（`stability_retention_txn_seconds` 的 `buckets` 含
`30.0`），所以**结构上**可校准。真实校准需要生产分布，本轮做不到，因此交付两件事：

- **记录校准 procedure**：对 `stability_retention_txn_seconds` 取近 7 天的
  `histogram_quantile(0.99, sum by (le) (rate(...[30d])))`，与 30s 比较——p99 远低于 30s 则
  阈值维持（噪声低），接近则以 p99 的 2–3 倍重设，并同步 `for:` 窗口。
- **新增离线守卫** `tests/test_prometheus_alert_metric_names.py`：`deploy/prometheus/` 下
  告警与 promtool 单测中 `expr:` / `series:` 引用的 `stability_*` 名字必须都在
  `backend/core/metrics.py` 里声明。**派生序列**（`_bucket`/`_count`/`_sum`）按类型放行：
  只有基名仍是 `Histogram`/`Summary` 才合法——只按后缀放行会漏掉「把直方图改成 Gauge」这种
  让告警永不触发的情形。

## Alternatives

- **给 gauge 加 `datname` label**（第 1 项的原设想）：否决，理由见上（按构造只有一个取值，
  且要同步改所有看板与告警表达式）。
- **只写静态断言「源码里有 `datname = current_database()`」**：否决。它守的是代码形状而不是
  行为，而这类漂移（跨库污染）完全可以用真会话测出来。
- **积压只报一个 gauge**（候选数或批大小）：否决，理由见上（会硬编码一个会被调小的配置值）。
- **每次抓取现算积压**（`/metrics` 里跑 count）：否决。大表上每次抓取都多一条重查询，而且
  重复了清理自己刚做过的选择；用清理**自己**的观测值零额外成本。
- **用「已删除 run 数」表示积压**：否决，NFS 失败的 run 会被推迟重试，删除数会低估。
- **把告警散文（annotations）里的指标名也纳入检查**：本轮否决（散文允许迁移期保留历史名，
  扫进来会假红），代价已写入该文件的模块 docstring——若「值班指引漂移」成为问题再纳入，
  那时需要一份允许保留历史名的白名单。
- **接线 `promtool test rules`**：本轮不做。它需要下载 promtool 二进制，与 `pr-agent-tests`
  的离线纯度约束（`tests/test_offline_subset_guard.py`）冲突；上面那条离线名字自洽检查是
  当下比例合适的替代，语义校验仍待接线（见 Revisit）。

## Verification

| 项 | 结果 |
|---|---|
| 新根守卫 | **4 passed**（离线） |
| 负向对照 A：告警 `expr` 引用不存在的指标名 | 红：`…引用了未声明的指标名：{'alerts-…yml': ['stability_db_deadlock_total_typo']}` |
| 负向对照 B：把 `expr` 里引用的指标改名、YAML 没跟 | 红（1 failed） |
| 负向对照 C：把直方图派生序列指向一个 Gauge 基名 | 红：`…['stability_db_lock_waiters_bucket']`（基名存在但类型不是直方图） |
| 恢复后复绿 | 4 passed |
| 保留清理（含新用例） | **18 passed** |
| 锁等待（含新多库护栏） | **2 passed** |
| 根 `tests/` | **750 passed** |
| `ruff` / 治理面 / 差异面不变量 | All checks passed / `[OK]` / `[OK]` |

**两次对照设计错误的更正（如实记录）**：负向对照 B 第一版**没红**——我改的
`stability_db_lock_waiters` 只出现在告警**描述的散文**里，而检查刻意只覆盖 `expr`/`series`；
换成改 `expr` 里引用的 `stability_db_deadlock_total` 后如期变红，并顺手把这条边界写进模块
docstring。另外，新守卫最初对三个 `_bucket`/`_count` 引用**误报缺失**——正是这一报让我把
「派生序列」补成带**类型前提**的规则（比只放行后缀更严）。

## Revisit

- **`promtool test rules` 未接线**（本轮新发现）：`deploy/prometheus/alerts-stability-platform.test.yml`
  已写好语义级单测，但 CI 里没有任何步骤执行它 → 它会随规则演进而腐烂。接线需要 promtool
  二进制（下载 vs 离线纯度约束的取舍），是独立决策，未在本单擅自决定。
- **阈值校准**：procedure 已记录（见上），等生产分布数据；若长期拿不到数据，考虑把阈值改成
  从直方图反推（如 p99 的函数）而不是固定 30s。
- **散文引用未覆盖**：见 Alternatives 与 docstring 的边界说明；若值班指引出现过期指标名，
  再纳入并配白名单。
- **`/metrics` 的抓取成本**：本次两项新指标都来自清理自身（零额外查询）；若将来要在抓取期
  现算更多聚合，先量 `/metrics` 的 P95 延迟（它现在是单条聚合 + 跳过失败组）。
