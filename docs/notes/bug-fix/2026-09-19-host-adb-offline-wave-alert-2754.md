# 单 host 设备批量 adb offline 的观测面：per-host 分桶 gauge + 多数失活告警（#2754）

Status: implemented
Class: bug-fix

## Decision

**本单只补 #2754 三条缺口里的第 1 条（无告警），不动第 2 条（无自愈）。**

事实：2026-09-18 某台 host（#2754 正文点名；本文件与告警场景一律用合成地址表示，public 仓库不写真实资产）的 16 台里 **15 台 adb offline**，平台上零告警——
host 状态 ONLINE、心跳新鲜、共享盘挂载正常。fleet 级 `stability_device_online{status=...}`
只有**总数**，「聚集在一台 host 上的失活」在这个口径下与「fleet 里少了几台」不可区分。
本次靠人读日志 + 人工 `adb reconnect offline` 恢复（处置留痕在 #2754）。

补的层次：

1. `backend/core/metrics.py` 新增 `stability_host_device_adb_state{host_id, state}`；
2. `backend/api/routes/metrics.py` 新增 `_refresh_host_device_adb_gauges()`，挂在
   `/metrics` 拉取路径上，与 `_refresh_fleet_gauges` 同口径（拉取期现算、低基数、
   失败只跳过本组不拖垮整次抓取）；
3. `deploy/prometheus/alerts-stability-platform.yml` 新增 group
   `stability-platform-fleet-link` 与规则 `StabilityHostAdbOfflineConcentration`。

三条口径是刻意的，不是顺手：

- **退役 host 不进指标**（ADR-0038 D5「退役 = 不再是容量」）。否则退役机上残留的设备行
  会让告警永远盯着一台已不存在的机器，且没人会去处理它——观测面最常见的那种死信；
- **每台在册 host 的四个桶全部落值（含 0）**：缺 series 时 `max_over_time` / 比值分母
  没有基线，「从来没设备」与「刚掉光」不可区分，而后者才是要告的形态；
- **`adb_state` 是自由字符串，必须归进封闭词表** `device|offline|unauthorized|other`。
  不归桶有两个后果：`no permissions` / 空串 / 各版本 adb 的自造词各自成为标签值
  （基数交给运气），且告警选择器要逐值列举——**漏一个值就静默不告**，与 #1958
  （死锁四周零指标）、#1257（不存在的标签选择器）同形。

阈值按**当前 fleet 实测分布**定，不拍脑袋（只读查询，`application_name=diag-2754-*`，
先查 `information_schema` 再写查询——#2632 的 SOP 首步）：46 台在册 host，每 host 设备数
`min=3 / p10=11 / p50=16.5 / max=46`；事故现场是 15/16。

```
offline ≥ 5  and  offline/total ≥ 0.5  and  total ≥ 5     (for: 15m)
```

- `≥5` 绝对下限挡小 host 噪声（fleet 里确实有 3–4 台的 host）；
- 占比 ≥50% 表示「通道层事件」而不是个别设备毛病；
- `total ≥ 5` 挡住空 host / 刚接入未采到。

**已知天花板（写下来，不假装覆盖）**：3–4 台的小 host 全掉线不在本告警范围内；
放宽下限要先看这类 host 有多少，别在告警里猜。生产当前形态也一并记着：
**27 台 host 有 offline 设备、4 台是 100% offline**（63/102/65/20，合计 68 台）——
所以任何「fleet 级 offline 总量」型告警今天一上线就是红的，本单刻意不做那种规则。

## Alternatives

- **做自愈（平台侧自动 `adb reconnect offline`）**：不做。它是对**在跑设备**的写动作——
  同一 host 上可能正有 job 在 `adb push`/`pm install`，reconnect 会打断它们；
  「轻动作可恢复」的判断只在**空闲 host** 上成立，而平台无法从 adb_state 判断设备是否空闲
  （要引租约/执行态做门）。属方向级，留给 #2754/#722 裁决；本单只保证「看得见」。
- **把比率放 Grafana 不放告警**：否决。#2754 的失效正是「看得见但没人被通知」，
  面板不解决那一条（与 #2632 的「无拦截无留痕无告警」同形）。
- **复用 `device.status` 而非 `adb_state`**：否决。`status` 是平台派生口径（含 BUSY/ERROR
  语义），本单要盯的是 **adb 通道本身**；实测两者当前逐值对应
  （device↔ONLINE 604 / offline↔OFFLINE 231 / unauthorized↔ERROR 6），但那是巧合式一致，
  不是契约（`DeviceStatus.ERROR` 的注释自己写着「非物理离线」）。
- **每 host 一条 textfile 指标（node-exporter 侧）**：否决。数据本来就在控制面 DB 的
  `device.adb_state`（心跳写入），再造一个 agent 侧采集器等于把同一事实抄第二份——
  #2188/#2643 刚为这类事付过学费。

## Verification

- `promtool check rules deploy/prometheus/alerts-stability-platform.yml` →
  **SUCCESS: 23 rules found**
- `promtool test rules deploy/prometheus/alerts-stability-platform.test.yml` → **SUCCESS**
  场景含三台 host：事故态（15/16 → **必须告**）、健康态（2/26 → 不告）、
  **小 host 全掉线（4/4 → 不告）**——最后一条是绝对下限的判别力钉子，没有它把 `≥5`
  改成 `≥1` 也照样绿（`test_promtool_gate_detects_per_rule_threshold_drift` 依赖这种形状）
- `pytest backend/tests/api/test_metrics_host_adb_gauges.py backend/tests/api/test_metrics_fleet_gauges.py -q`
  → **4 passed**（分桶值/缺桶落 0、未知与空值归 `other` 且原始串不得成为标签值、
  退役 host 与孤儿设备不进指标）
- `pytest tests/test_prometheus_alerts_contract.py tests/test_alert_metric_producers.py
  tests/test_site_alert_scrape_surface.py tests/test_alert_count_claims_are_live.py
  tests/test_monitoring_asset_drift.py -q` → **78 passed**
  （新指标有真实生产者；规则不进站点清单 → `_SITE_INERT_RULES` 仍为空，不新增 #2643 债务）
- 未做（标 pending，不当作通过）：**部署侧生效**。本机 `/etc/prometheus` 是运维手改副本
  （见 `.bak-2026*` 文件），新规则要等它被同步过去才真的响——`tools/dev/check-monitoring-assets.py`
  会报漂移；此外**未在生产上执行任何写操作**（只读取证用 `application_name=diag-2754-*`）。

## Revisit

- **#2754 的第 2、3 条缺口未动**：自愈（需裁决 + 租约门）与「设备将死未死的中间态信号」
  （r426 该 host 2 台 push 全失败早于 offline 波，是前兆但 check_device 给不出可辨识信号）。
- **fleet 存量面另议**：今天 4 台 host 处于 100% offline（68 台设备），这是**库存/退役**问题
  不是波次问题；若要治理，判据应是「长期全 offline 的 host 清单」进日报，而不是告警。
- 若将来 #2755（链触发时机）落地后交接波次仍在，本规则的 `for: 15m` 与占比阈值是第一个
  可调旋钮——调之前先看 #2755 的实测波次形状，别只为了压红条改阈值。
