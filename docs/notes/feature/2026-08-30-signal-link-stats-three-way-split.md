# 链接率拆为三分类 + 新增告警口径（#528）

- **日期**：2026-08-30
- **关联**：Issue [#528](https://github.com/DUElost/stability-test-platform/issues/528)；Epic #527；#556（sweep）

## 决定了什么

`aggregate_signal_link_stats` 在原有字段之外，把「未链接且可链接」的集合拆成三桶，
并新增一个可告警的比率：

| 字段 | 含义 | 能否修 |
|---|---|---|
| `not_yet_archived` | 该 job **没有任何** `device_log_event` 行 | 不能 —— 按 ADR-0028 事件仍在 Agent 本地，尚未归档 |
| `unlinkable` | job 有 DLE，但没有 `signal_seq_no` 等于本 signal `seq_no` 的行（如 reconciler 发现的 UNKNOWN 事件，不源自 signal） | 不能 —— 结构上就没有可关联的对象 |
| `unlinked_fixable` | 存在匹配的 DLE 却没回填 `device_log_event_id` | **能** —— 唯一真故障，`signal_link_reconcile` sweep 正是修它 |
| `fixable_link_rate` | `linked / (linked + unlinked_fixable)`，分母为 0 时取 1.0 | 告警用 |

不变式：`unlinked_linkable == not_yet_archived + unlinkable + unlinked_fixable`。

两个比率并存且**故意不合并**：

- `link_rate`（旧）：分母含全部 AEE/VENDOR_AEE signal，会因「尚未归档」而偏低。
- `fixable_link_rate`（新）：只算「已链接 + 真故障」，是唯一适合设阈值的数字。

实现是一条 SQL 三个 `FILTER` 计数，两个 `EXISTS` 都吃
`idx_device_log_event_job_signal_seq (job_id, signal_seq_no)`，不额外加索引。

## 为什么这么定

生产实测（#556 上线后）：AEE signal 已链接 1540、未链接 1137，`link_rate` 报 0.575。
把 1137 拆开：

| 细分 | 条数 |
|---|---|
| job 无任何 DLE 行 | 1109 |
| job 有 DLE 但 `signal_seq_no` 对不上 | 28 |
| 有匹配 DLE 却没链上（真故障） | 0 |

**0.575 里没有一条是真故障** —— 它是一个「尚未归档」占比，无论怎么修链接逻辑都
提不上去。拿它当告警阈值会得到恒红的告警。而真故障那一桶当时是 0，sweep 也确实
没有积压可排。

## 放弃的备选

- **只改分母为「job 有 DLE 的 signal」**（原方案 A）：那 28 条的 DLE 的
  `signal_seq_no` 全为 NULL，永远链不上，会变成常驻假红灯。
- **只统计终态/已归档覆盖的 signal**（原方案 B）：语义更严，但要等终态才出数，
  且把「未归档」的判定绑到 DLE 的 `state` 上，与 PlanRun 生命周期耦合。
- **只报一个比率、不拆桶**：一个数字装不下三种状态，运维无法区分「没检测到」、
  「没归档」、「没登记」。
- **删掉旧的 `link_rate`**：前端与看板已在用，改名属破坏性变更；保留并注明
  「非故障率」成本更低。

## 如何验证

```bash
unset TEST_DATABASE_URL
JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/services/test_log_observation.py \
  backend/tests/scheduler/test_signal_link_reconciler.py \
  backend/tests/api/test_plan_run_log_events.py -q
```

- `test_signal_link_stats_splits_unlinked_into_three_buckets`：三桶各 1，且三桶之和
  守恒于 `unlinked_linkable`。
- `test_fixable_link_rate_ignores_not_yet_archived`：**只有「尚未归档」时**
  `link_rate=0.0`（旧口径误报）而 `fixable_link_rate=1.0`（新口径正常）—— 这条
  直接钉死本 Note 的核心诉求。
- `test_signal_link_reconciler.py`：sweep 后 `unlinked_fixable` 归 0、
  `fixable_link_rate` 回 1.0 —— 指标与修复闭环。

## 何时重议

- 生产 `unlinked_fixable` 长期 > 0：说明 sweep 排不动，先查
  `signal_link_reconcile_done` 的 `scanned` 是否顶在 batch 上限。
- `unlinkable` 占比持续上升：说明 reconciler 建的 DLE 越来越多不源自 signal，
  需要回到 #214 的关联键契约上讨论。
- 前端要展示这三桶时，优先用 `fixable_link_rate` 做健康灯，`link_rate` 只作参考。
