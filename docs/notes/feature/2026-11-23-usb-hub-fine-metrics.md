# 零权限 USB/Hub 精细指标与 Host 诊断字段导出（S1）

Status: implemented
Class: feature

## Decision

1. **Agent 侧零权限 sysfs Hub 拓扑采集**（`backend/agent/device_discovery.py::inspect_usb_hub_topology`）：
   - 纯文件只读扫描 `/sys/bus/usb/devices/`，统计外接级联 Hub 总数（`cascade_hubs`）、
     无驱动绑定的半枚举 Hub 数（`unbound_hubs`，即对应 `lsusb -t` 的 `Driver=[none]/0p`）以及
     落在无非 Hub 外设总线上的未绑定 Hub 数（`unbound_idle_bus_hubs`，区分无在用设备的 SS
     失效分支与在用侧 Hub 异常）。目录不可读时返回 `None`（未知 ≠ 0）。
2. **内核 USB 故障数值化统计**（`backend/agent/contracts/kernel_usb_faults.py`、`backend/agent/kernel_usb_faults.py`）：
   - 保持既有 `usb_host_controller_dead` / `usb_link_degraded` 判定口径不变；
   - 新增 4 类与刷机/测试抖动强相关的数值计数（`disconnects`、`resets`、`enum_errors`
     `-32/-22`、`hub_port_errors` `hub_ext_port_status failed` / `disabled by hub`），与
     `link_errors`、`cable_suspect` 组成封闭键集 `KERNEL_ERROR_COUNTER_KEYS`，经
     `KernelUsbWatch.window_summary()` 输出最近 1 小时滑动窗聚合；通道非 `ok` 时返回 `None`。
3. **心跳与控制面指标/API 导出**：
   - `capacity_reporter.compute_capacity` 仅在非 `None` 时附带 `usb_hub_topology` 与
     `usb_kernel_errors_1h`（纯观测，不参与 `effective_slots` 或 `health.status` 打闸）；
   - `backend/core/metrics.py` 与 `backend/api/routes/metrics.py` 新增 5 组 per-host Gauge
     （`stability_host_usb_devices`、`stability_host_adb_interfaces`、`stability_host_usb_cascade_hubs`、
     `stability_host_usb_unbound_hubs{scope}`、`stability_host_usb_kernel_errors_1h{kind}`），
     沿用未知不落 0 与离线/退役差集 `.remove()` 契约；
   - `HostOut`（`backend/api/schemas/host.py`、`backend/api/routes/hosts.py`）显式暴露
     `health_probe` 摘要，并同步 `frontend/src/utils/api/types.ts`（含 `capacity` 新增字段类型），
     为切片 S2（`/hosts` 故障工作台）提供结构化数据源。

## Alternatives

- 将 `uhubctl` 轮询放进心跳直接采集过流（`oc`）标记：放弃。现场实测失效 SS 分支会把一次
  `uhubctl` 拖慢至 20~30s 且需要 root 提权，直接放进 5s 心跳循环会拖垮心跳与刷机锁；
  sysfs 拓扑扫描耗时毫秒级、零提权，已能直接捕获 `Driver=[none]` 半枚举 Hub。
- 立刻基于新数值指标新增自动打闸或 Prometheus 告警规则：暂不做。先让数值指标在生产积累基线，
  待切片 S2（`/hosts` 工作台）与 S3（通知减噪）配合落地后按实测分布校准阈值。

## Verification

- Agent 套件：`scripts/run_pytest.py backend/agent/tests/test_usb_hub_fine_metrics.py backend/agent/tests/test_kernel_usb_faults.py backend/agent/tests/test_capacity_reporter.py backend/agent/tests/test_device_discovery.py -q`
- 控制面与契约守卫：`scripts/run_pytest.py backend/tests/api/test_metrics_usb_hub_gauges.py backend/tests/api/test_metrics_host_health_gauges.py tests/test_host_health_reason_surface.py tests/test_frontend_api_types_sync.py -q`
- 静态门禁：`scripts/run_gates.py check:quick`

## Revisit

- 生产运行 1 周收集 `stability_host_usb_kernel_errors_1h` 与 `stability_host_usb_unbound_hubs`
  真实分布后，复评是否对持续高错误率主机增加刷机派发隔离或新增 Hub 退化告警阈值。
