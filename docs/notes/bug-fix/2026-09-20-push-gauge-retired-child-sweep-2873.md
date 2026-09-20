# 推式 per-host gauge 的退役 child 差集清理：7 组补上 remove（#2873）

Status: implemented
Class: bug-fix

## Decision

#2791 只修了拉取侧 `host_device_adb_state` 一组；本单补齐 `core/metrics.py` 里
**7 组推式 per-host gauge**（reconciler burst/unresolved、watcher present、
agent outbox、host_operation×3）——prometheus_client 的 label child 常驻
registry，host 退役后写点不再触达 = 故障值永久冻结，`StabilityUnisocUnresolvedBacklog`
一类读它们的告警恒 firing。

机制取「**写侧记账 + 拉取端清扫**」，不逐个包 `.labels()`：

- `core/metrics.py`：`_note_host_child(gauge, host_id, rest)` 在 5 个既有 set/record
  包装函数内随写登记（这 7 组的写入**全部**走这些包装——grep 证实零旁路，
  bookkeeping 一处不漏）；`sweep_stale_host_gauge_children(live)` 按
  `(gauge, host_id, 其余 label 值)` 位置元组 remove（7 组 labelnames 全部
  `host_id` 打头已逐一核对，`remove` 是位置参数）；
- `api/routes/metrics.py`：`/metrics` 拉取链在三个 `_refresh_*` 之后加
  `_sweep_push_host_gauge_children(db)`；DB 抖动/registry 异常只 warn 跳过本轮，
  下周期自愈——清理是卫生动作，不拥有让 /metrics 500 的权力。

**live 口径 = 在册（`retired_at IS NULL`），比 adb gauge 的 liveness 门更宽**：
短暂掉线 host 的末值（outbox 积压、unresolved 快照）有操作意义，不该被清扫；
只有「退役/移出在册」（不再是容量，ADR-0038 D5）才清。这是与 #2754 门刻意不同
的边界，注释写明。

## Alternatives

- **各写点包一层带 remove 的 setter**：弃——写点散布 heartbeat/completion，
  逐处包装漏一处即回到恒 firing；本文件收口点记账一处生效；
- **live 用 status=ONLINE（跟 adb 组同口径）**：弃——推式 gauge 语义是
  「该 host 最后一报」，短暂 OFFLINE 恰是要看的状态（退役才是「不再存在」）；
- **靠进程重启清 registry**：弃——重启周期远长于告警窗口，且把正确性寄托在
  运维动作上。

## Verification

- 新文件 `test_metrics_push_gauge_sweep_2873.py` → 2 passed：三形态（单 label、
  双 label type、双 label platform 之一 + 多指标同 host 全清）逐断言「退役 →
  下一次拉取消失」且**邻 host 不受波及**；第二例钉「取消退役→新值能回来」
  （remove 不吃后续 set）；
- 关联回归：adb gauges/alert producers/promtool 契约/dual_write/read_api_auth
  → **194 passed**；`check:quick` 12 门禁绿（inner-imports 棘轮当场拦过一次
  函数内 import，已提升——棘轮工作正常）。

## Revisit

- 若未来新 per-host 推式 gauge 在别处定义并绕过这 5 个包装函数写 `.labels()`，
  本机制不覆盖——约定：新推式 per-host gauge 必须走 core/metrics 的包装写入
  （或入 `_PUSH_HOST_GAUGES` 记账），评审清单加一条；
- platform 桶基数若失控（退役 host × 多 platform），sweep 的 pop 集合自然回收，
  无需额外限幅。
