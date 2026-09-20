# per-host adb gauge 的 host liveness 门：幽灵行不再让 offline 告警恒响（#2754 收口补丁）

Status: implemented
Class: bug-fix

## Decision

给 `_refresh_host_device_adb_gauges`（#2754 观测半边，PR #2766/#2802 复核评论点名）补
第四道口径——**只统计 `status=ONLINE` 的 host**：

- 缺陷链：`Device.adb_state` 仅在心跳上报该设备时刷新 → agent 停报的 host，其设备行
  **冻结在最后一次上报值**（多为 offline）；生产已积累 ~220 台这样的幽灵设备行
  （last_seen 停在数周前）。原查询只排退役不排失活 →
  `StabilityHostAdbOfflineConcentration` 对每台死 host 恒 firing——新告警被存量噪声
  淹死，等于没有告警；
- 实现取**生产者侧门**而非告警表达式侧 `and on(host_online)`：门在取数处，与退役
  排除同族；OFFLINE host 的旧 label child 由 #2791 的差集机制 remove（不冻结值），
  复用了已被两拍自证过的清理路径；
- 职责边界保持：host 整体下线的可见性归 `stability_host_online` fleet gauge 一族，
  本 gauge 只回答「活着的 host 上设备批量不可达」——正是要保的新信号（.81 形态：
  host ONLINE、心跳新鲜、15/16 offline，恰在门内）。

## Alternatives

- **expr 侧 `unless on(host) (stability_host_online == 0)`**：弃——把 liveness 语义
  塞进每条表达式，规则面重复且 promtool 测试面要跟着长；生产者单点门一处生效；
- **设备级 last_seen 门（per-device 新鲜度）**：暂缓——设备级陈旧已被心跳的
  「不在 list 即标 offline」处理（heartbeat.py:136-143，live host 的完整列表语义），
  再叠一层设备时间窗会让 .81 波里「真 offline」与「未上报」不可分；幽灵问题本质
  在 host 级；
- **清库删幽灵设备行**：弃——D6 明确退役/历史设备行保留为锚点；数据治理是另一议题
  （若有 open 单再连）。

## Verification

- `test_metrics_host_adb_gauges.py` → 5 passed（新增两拍用例：ONLINE 时可见 →
  翻 OFFLINE 后 series 消失，防冻结值）；
- `test_read_api_auth + test_alert_metric_producers + test_prometheus_alerts_contract`
  → **114 passed**（指标名/生产者/规则契约无回归）；
- `check:quick` 12 门禁绿；告警 expr 未动（门在数据源侧）。

## Revisit

- #2754 三条系统性缺口至此：告警✓（#2766）自愈✓（#2773）前兆可辨识✓（#2815 D0
  check_device v1.0.1）+ 本补丁的幽灵门——PR 合入后可闭单；D0 上机分发与
  plan_step 重指是运维动作（owner），不在代码面；
- 若 fleet 侧要求「死 host 的设备数也有视野」，那是 host_online gauge + 设备行
  stale 盘点另一条观测线，别回塞进本 gauge 的语义里。
