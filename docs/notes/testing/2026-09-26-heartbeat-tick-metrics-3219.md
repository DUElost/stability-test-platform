# #3219：Agent 心跳整轮与分段耗时观测

Status: implemented
Class: testing

## Decision

Agent 在本地累计固定阶段的耗时分桶，并随**下一次**心跳把快照放入
`system_stats.heartbeat_timing`；控制面从在线 Host 的 `extra` 镜像到 `/metrics`。
阶段包括整轮、启动间隔、发现、并发探测总耗时、单设备快探/慢探的本轮最大值、
磁盘采样、报文准备、HTTP 及重连回调。另报慢探和磁盘采样的本轮设备数，
用来解释某一轮变慢是否遇到采样。没有设备序列号标签。

分桶和阶段词表在 Agent 与控制面之间共享，快照带版本号。无快照或格式错误的
Host 不产生零值序列；退役 Host 与回退旧版 Agent 后清除旧序列。Agent 重启后
累计值归零，控制面以 Gauge 镜像它们，查询时按计数器使用 `rate`。
部署次序为控制面先、Agent 后；旧 Agent 不会产生这些序列。

## Alternatives

- 控制面只统计心跳请求耗时：无法区分 ADB 快探、慢探、磁盘采样和本地排队。
- 每设备一条指标：150 host × 25 device 时序数量随设备扩展，也会暴露设备标识；
  改为每 host 的固定阶段与单设备最大值。
- 只传最近一轮耗时：无法直接求 p50/p95/p99，且一次未收到心跳就丢失样本；
  使用本地累计分桶。

## Verification

- Agent 定向测试覆盖分桶边界、整轮间隔和下一拍传输；既有并发探测、慢探、
  磁盘采样和设备发现测试确认签名兼容。
- 控制面 `/metrics` 测试覆盖累计分桶、due 数与退役清理；仪表盘契约测试确认
  查询的指标存在生产者。
- 观测口径：
  `histogram_quantile(0.99, sum by (host_id, le) (rate(stability_agent_heartbeat_phase_seconds_bucket{phase="tick_total"}[30m])))`
  得到每 host 的整轮 p99；将 `0.99` 改为 `0.5` / `0.95` 得到 p50 / p95。
  `tick_interval` 观测实际两轮起点间隔，不把配置的心跳间隔当成实测。
- 该改动只建立观测面。**25 台真机负载、采样窗与阈值尚未验证**，因此不关闭 #3219。

## Revisit

在一台实际接入 25 台设备的 host 上完成稳态和慢探/磁盘采样 due 窗口实测，
记录 `tick_total`、`tick_interval` 与各阶段 p50/p95/p99、应答失败和在线状态；
先从实测预算确定告警阈值，再决定是否优化探测并发或采样策略。
固定词表上限为每 host 10 阶段 × (17 分桶 + sum/count) + 2 due 序列，
150 host 最多约 2.88 万条时序；发布后核对 Prometheus 实际基数和抓取负荷。
