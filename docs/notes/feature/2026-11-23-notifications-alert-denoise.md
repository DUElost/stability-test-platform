# Alertmanager 通知流减噪：慢性待办路由至 `/hosts` + 重复 `firing` 就地折叠（S3）

Status: implemented
Class: feature

## Decision

1. **告警规则引入 `notify_surface` 路由标签**：
   - 将慢性设备侧待办告警 `StabilityHostAdbInterfacesMissing`（`deploy/prometheus/alerts-stability-platform.yml`）
     标注为 `notify_surface: hosts_only`，同步更新场景测试 `alerts-stability-platform.test.yml`。
   - `backend/services/notification_service.py::receive_alertmanager_alert` 识别
     `labels.notify_surface == "hosts_only"` 后跳过写入 `notification_logs`，使其保留在
     Prometheus 与 `/hosts` 工作台，不再灌入 `/notifications?tab=logs`。
2. **重复 `firing` 就地折叠（消除 `repeat_interval` 刷屏）**：
   - 对同一 `(alertname, target)`（按 `host_id` / `host` / `hostname` / `instance` 提取）
     在最新记录仍处于 `status == "firing"` 时，重复到来的 `firing` 更新原记录的
     `context.repeat_count`、`context.last_seen_at`、`severity` 与 `message`，不插入新未读行、
     也不重复推送 Socket.IO 铃铛；`resolved` 或恢复后的新一轮 `firing` 照常记录。
3. **`host_id` 告警直达 `/hosts?q=<host_id>`**：
   - `_resolve_alert_link` 补齐 `labels.host_id` 匹配（`Host.id == ident`），返回带筛选参数的
     `/hosts?q=<host_id>`，使通知中心条目一键定位到目标主机。

## Alternatives

- 仅修改生产机 `/etc/prometheus/alertmanager.yml` 丢弃低优先级路由：放弃。仓库代码侧无法
  单测锁定，且无法消除留在通知流中的高优先级告警每 3 小时重复产生多条未读日志的问题。
- 在 `notification_logs` 新增数据库唯一索引或列来做折叠：暂不做。使用既有 `context` JSON
  记录 `repeat_count` / `last_seen_at` 零迁移落地，满足当前规模且兼容既有读路径。

## Verification

- `scripts/run_pytest.py backend/tests/api/test_notifications.py tests/test_prometheus_alerts_contract.py tests/test_host_health_reason_surface.py tests/test_site_alert_scrape_surface.py -q`
- `scripts/run_gates.py check:quick`

## Revisit

- 切片 S4（xHCI / L3 自愈接线与 `StabilityHostSelfHealFuseOpen` 熔断告警）生效后，可评估把
  `StabilityHostAdbOfflineConcentration` 等进入自动自愈窗口的规则同样切为 `hosts_only`，
  通知栏彻底只保留熔断与无解硬件故障。
