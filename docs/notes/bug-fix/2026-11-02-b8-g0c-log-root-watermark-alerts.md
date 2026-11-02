# B8 G0-c：15.4 日志根按绝对字节的水位告警（#3860）

Status: implemented
Class: bug-fix

关联：[#3800](https://github.com/DUElost/stability-test-platform/issues/3800)（批次 B8 载体，方案 v1.4）、
[#3860](https://github.com/DUElost/stability-test-platform/issues/3860)（本单元规格）、
[#3233](https://github.com/DUElost/stability-test-platform/issues/3233)（既有中心存储水位）。

## Decision

按 [#3800](https://github.com/DUElost/stability-test-platform/issues/3800) v1.4 §3 的 G0-c 与
[#3860](https://github.com/DUElost/stability-test-platform/issues/3860) 判据 1–4 实施。切盘后
`/mnt/stp-log` 是 CIFS 挂载，而既有 `host-storage` 三条规则只匹配 `ext4|xfs|btrfs`、且按百分比
判定；15.4 是 13T 共享盘（2026-11-02 已用约 80%），百分比无法表达「还剩几个 run」。因此新增
独立规则组 `host-log-storage`，只按 `mountpoint="/mnt/stp-log", fstype="cifs"` 的绝对可用字节判定：

| 规则 | 判据 | `for` | 级别 |
|---|---|---|---|
| `StabilityHostLogRootLowSpace` | `node_filesystem_avail_bytes < 800 * 1024^3` | 15m | warning |
| `StabilityHostLogRootCritical` | `node_filesystem_avail_bytes < 300 * 1024^3` | 5m | critical |

阈值取 **800 / 300 GiB**，依据 Owner 2026-11-02 在 #3860 的确认；换算成二进制字节是
858993459200 / 322122547200。按单 run 约 100 GiB，分别约等于 8 / 3 个 run。门禁要求
「阈值写成可调的规则常量」；Prometheus 规则文件没有命名常量机制，故在两个 `expr` 里用
`N * 1024 * 1024 * 1024` 表达，并在组头注释登记 `WARNING_AVAIL_GIB` / `CRITICAL_AVAIL_GIB`
与「不要写成 `800e9`」的十进制误差提醒。

critical 的 description 写明处置：按安全停止处理，暂停派发，由 Owner 逐个 run 确认归档或删除
（ADR-0062 D12 第 2 条）。组内两条 description 都不再指向已退役的硬链接手册；同一 PR 顺带把
`host-storage` 三条既有规则的 description 中指向该手册的指引改为「硬链接止血已退役」并指向
ADR-0062 D12 第 2 条，判据（表达式与阈值）未动。

`node-exporter.default` 未改。实测发行版 node-exporter 1.9.0 的默认
`--collector.filesystem.fs-types-exclude` 不含 cifs，且本仓现有
`mount-points-exclude=^/(dev|proc|sys|run|var/lib/.+)($|/)` 不排除 `/mnt`，CIFS 会被采集；
#3860 判据 4 的「同一 PR 放开」条件不成立。

涉及文件：`deploy/prometheus/alerts-host-resources.yml`、
`deploy/prometheus/alerts-host-resources.test.yml`。

## Alternatives

- **把两条规则塞进现有 `host-storage` 组**：该组的语义是「按比例的 ext4/xfs/btrfs 水位」，
  且 `tests/test_host_storage_alerts_3233.py` 把该组的告警集合钉死为三条；混入 CIFS 绝对字节
  会让两套口径共用一个组名，故另开 `host-log-storage`。
- **用百分比阈值**：13T 共享盘 80% 已用时百分比没有可解释的提前量；而 15.4 的容量单位是
  「run」，绝对剩余字节才能直接换算。
- **沿用 `host-storage` 的 `for` 梯度但不设 `for`**：单次 scrape 的瞬时抖动（CIFS 短暂返回 0）
  会直接触发 critical；沿用 15m / 5m 与既有磁盘告警的持续判定一致。
- **改 node-exporter 的 fstype / mount-points 过滤**：实测无需放开；改动反而会扩大采集面。
- **用 recording rule 暴露阈值再让告警引用**：Prometheus 没有命名常量，绕成 recording rule
  只会多一条常驻序列，不降低阈值维护成本。

## Verification

- `promtool check rules deploy/prometheus/alerts-host-resources.yml` → `SUCCESS: 9 rules found`
  （本机 promtool 2.53.3）。
- `promtool test rules deploy/prometheus/alerts-host-resources.test.yml` → `SUCCESS`。
  新增场景覆盖：800 GiB 整不触发 warning、700 GiB 触发 warning、300 GiB 整不触发 critical、
  250 GiB 同时触发 warning/critical、`fstype="nfs"` 不触发、`mountpoint="/mnt/other"` 不触发。
- 判别力对照：把 `origin/main` 的规则文件与本次场景文件放在临时目录跑 promtool，返回非零并点名
  `StabilityHostLogRootLowSpace`；把 warning 的 `800` 变异为 `1e12` 后同样变红并点名该告警。
- `tests/test_host_storage_alerts_3233.py tests/test_host_rule_scenario_coverage_3322.py`
  → `8 passed`。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (16 gates)`。
- 未部署、未重载生产 Prometheus；生效仍需控制面人工副本同步并 `POST /-/reload`。

## Revisit

- G0-b 切盘后核实 `/mnt/stp-log` 的 `fstype` 实际为 `cifs`；若挂载类型或挂载点变化，选择器需同步。
- 单 run 实际字节随 Monkey / AEE 配置变化时，重新校准 800 / 300 GiB 与「约 8 / 3 个 run」的注释。
- 若目标发行版的 node-exporter 默认 `fs-types-exclude` 改为排除 cifs，需在
  `node-exporter.default` 显式放开；同时评估 hard 挂载卡住时 scrape 阻塞的观测缺口。
