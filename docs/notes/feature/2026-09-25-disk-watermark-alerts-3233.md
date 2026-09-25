# 磁盘水位告警：趋势给提前量、比例给底线（#3233）

Status: implemented
Class: feature

关联：[#3233](https://github.com/DUElost/stability-test-platform/issues/3233)（告警/备份/恢复三件套，本单是其中「磁盘水位」一项）、
[#3230](https://github.com/DUElost/stability-test-platform/issues/3230)（台账；复审 C2：生产保留期停用）、
`deploy/prometheus/alerts-host-resources.yml` 文件头（同一「阈值来自现网分布」口径）。

## Decision

2026-09-25 只读核实：中心存储 `/mnt/stp-aee`（sda1，916G）7 天内从 46% 涨到 88%，最近平均每天约 59 GB。
加载的 39 条告警里**没有任何磁盘或备份规则**，而 Alertmanager 唯一的 receiver 又回打平台自身。
所以离写满只剩 2–4 天时，没有任何人会收到提醒。当天 owner 手工删除了 134 GB 可再生的 jira 提取副本，
解了燃眉之急。

新增 `host-storage` 规则组，放在主机资源角色文件里（只引用 node-exporter 指标，理由同该文件头）：

| 规则 | 表达式要点 | 级别 |
|---|---|---|
| `StabilityHostFilesystemFillingUp` | `predict_linear(avail[6h], 72h) < 0` **且**剩余 < 50%，`for: 1h` | warning |
| `StabilityHostFilesystemLowSpace` | 剩余 < 20%，`for: 15m` | warning |
| `StabilityHostFilesystemCritical` | 剩余 < 10%，`for: 5m` | critical |

阈值来自 09-18 → 09-25 的 7 天回测（5 分钟步长）：

- **趋势判据**在 **09-18 23:15** 首次满足，比写满提前 6 天多；
- **「剩余 < 50%」门槛**用来消除根分区 09-21 一次短时集中写入造成的 2.2 小时误报；
- **20% 阈值**在 09-23 20:05 首次满足；
- **10% 阈值**在回测期内从未满足（最低 12.1%）。

选择器 `fstype=~"ext4|xfs|btrfs"` 在本机恰好匹配 `/` 与 `/mnt/stp-aee`。

## Alternatives

- **只要比例阈值**：20% 那条比写满只早两天；趋势判据早了 6 天多，提前量才是这类告警的价值所在。
- **趋势判据不加门槛**：回测里根分区有 2.2 小时误报（空盘上的短时集中写入）。
- **用绝对 GiB**：两块盘大小相差一倍，写入速率又与盘大小无关；比例配合趋势已经足够。这与内存规则
  「绝对 GiB」的理由不同：那边是 swap 常态占用让百分比失真。
- **放进平台规则文件**：node-exporter 指标会撞上 `test_alert_metric_producers`（只认本仓自有指标前缀），
  理由同本文件头。

## Verification

- `promtool check rules`：本机 2.53.3 与 CI 固定的 3.13.3 均为 **7 rules SUCCESS**。
- 场景 `deploy/prometheus/alerts-host-resources.test.yml` 覆盖三组：
  - 趋势判据触发，而根分区同样陡降时不触发（50% 门）；
  - 剩余 15% 持平时，只有 20% 那条触发；
  - 剩余 5% 时 critical 触发。

  两个版本的 promtool 都通过。首版按 15 分钟采样，超出了 5 分钟回看窗口，`for` 永远凑不满，因此改为 1 分钟采样。
- `tests/test_host_storage_alerts_3233.py`（PATH 优先 promtool 3.13.3 且 `PROMTOOL_REQUIRED=1`）：**6 passed**，其中：
  - 3 条逐条阈值变异，都让场景层变红并点名对应告警；
  - 每条规则都有正向场景。
- 站点安装与发布包相关的 6 个测试文件 231 passed；`python scripts/run_gates.py check:quick` 16 gates OK。

## Revisit

- **生效需要一次独立动作**：控制面人工副本同步 `alerts-host-resources.yml` 并 `POST /-/reload`。它与
  #2959 手册 Step 2 的平台规则同步属于同一次动作，默认不在本 PR 内。
- 告警出口仍是单点（receiver 回打平台自身），平台或整机故障时照样收不到。带外通道是 #3233 的另一项，
  本 PR 不覆盖。
- 去重与分层保留（L1/L2）落地后，按新的写入速率重新回测趋势窗口（6h）与门槛（50%）。
