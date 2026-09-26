# #3159 host 设备面意图位 A 期（ADR-0038 v0.3 D9）

Status: implemented
Class: feature

## Decision

按 ADR-0038 v0.3 §7（2026-09-25 裁决，§7.7 A1–A5）落地「设备面意图（空置 / 人工清空）」A 期，
逐条对应关系：

- **D9.1 三列真源**：`host.emptied_at/emptied_by/emptied_reason`（迁移
  `f7a8b9c0d1e2`，单 head 链在 `d4e8f2a7c9b1` 之后；ORM 同批，禁 `Host.extra` 裸键）。
- **D9.2 互斥**：退役机置位 409；置位机走 retire 409 并提示先清除
  （§7.7 取向=拒绝并提示，实现落 `retire_host` 前置，幂等语义不变）。
- **D9.3 豁免机制**：push gauge `stability_host_device_intent{host_id, intent}`，
  词表封闭 `_DEVICE_INTENTS=("emptied",)` 并与规则选择器双向绑测试；
  只落值置位 host（1），未置位 host 无 series（unless 语义等价、基数只随置位数增长）；
  刷新并入 `/metrics` 拉取期家族（`_refresh_host_device_intent_gauges`），
  #2791 差集清理防 label child 冻结；**不过滤 host.status**——意图主场景是关机/移机
  （OFFLINE host 也暴露），消费方第一子句都要求 adb series（仅 ONLINE 存在），
  `and/unless on(host_id)` 天然收窄作用域，多暴露不产生误豁免；
  退役 host 不进指标（D5 同款）。
  规则侧：`StabilityHostUsbBlind` 与 `StabilityHostAdbOfflineConcentration` 整条表达式
  外层加同一 `unless on (host_id) (stability_host_device_intent{intent="emptied"} == 1)`
  （A2：两规则豁免；ControllerDead / LinkDegraded 不豁免）；原始数据不隐藏。
- **D9.4 入口**：`POST /hosts/{id}/device-intent`（reason 必填）/ `DELETE`（可选
  `?reason=` 记审计）；`require_admin` + `record_audit(strict=True)` fail-closed，
  对齐 retire/unretire 权限与审计级别；清除 = `emptied_at=NULL`，
  by/reason 保留最近一次（unretire 同惯例）。
- **D9.5**：不设自动过期；详情徽标「设备面已处置」（悬浮含 who/reason/豁免说明），
  与 retired 徽标同位渲染。
- **D9.6**：不碰派发 / 认领 / 安装 / 热更新（零改动）。
- **D9.8 陈旧告警**：新规则 `StabilityHostDeviceIntentStale`（warning，`for: 1h`）——
  置位 ∧ 账上回场 `adb_state=device` > 0 即响；只提示不自动清除。

## Alternatives

- 通用 `device_intent` 枚举列（§7.6 A1 备选）：已被 §7.7 否决——第二种意图出现即按
  §7.5-1 升级，届时三列可无损映射。
- `Host.extra` 承载：D4 明文否决（主心跳每拍重建 extra）。
- intent gauge 逐 host 落 0（对齐 adb 四桶全量）：不采——unless 语义下「无 series = 不豁免」
  等价，且 adb 桶全量是为了 `max_over_time` 基线，intent 无此消费形态。
- 清除走 `POST .../clear`：ADR 定形 DELETE；清除原因经 `?reason=` 可选记审计。

## Verification

- `promtool check rules`（39 rules）+ `promtool test rules` 全绿；新增场景组钉住：
  置位 ⇒ UsbBlind/OfflineConcentration 不 firing（对照组无 intent 照常 firing）、
  D9.8 置位 ∧ device>0 在 70m firing / 20m 不 firing、清除 = series 消失回 fire 形态。
- 新测试 `backend/tests/api/test_host_device_intent_3159.py`（12 例：置位/幂等/409×2/
  422/403/审计 fail-closed 回滚/清除保痕/幂等/gauge 暴露与 #2791 移除/OFFLINE 暴露/
  词表↔选择器双向绑）+ 回归 retirement 1801 / adb gauges / hosts API：92 passed。
- `tests/test_alert_metric_producers.py` + `test_prometheus_alerts_contract.py`：59 passed
  （producer AST 判据、promtool 场景、每规则必有场景）。
- `pr-migrate` 本地等价（check_pr_migrate.py，postgres:16 一次性容器）：空库迁移 +
  schema 比对 + seed 身份对拍通过；`alembic heads` 单 head `f7a8b9c0d1e2`。
- 前端：`ExpandableHostTable.test.tsx` 31 passed（新增徽标 2 例）；`tsc --noEmit` 干净。
- `scripts/run_gates.py check:quick`：16 gates 全绿。
- **pending（部署后）**：§7.3 验收 5——现网 4 台常亮 critical 中取 1 台置位做双向活体验证
  （该实例消失、其余 3 台继续 firing；清除后恢复）。需部署本 PR 后由有生产权限的人执行。

## Revisit

- 第二类设备面意图（检修 / 外借）出现时，按 §7.5-1 评估升级 `device_intent` 枚举。
- 「已置位机器真故障被静默」实例出现时，评估 `review_by` / 到期复查（§7.5-2）。
- B 期（#2962 设备退役）落地后复核 host 意图 vs device 退役两处口径（§7.5-3）。
- 迁移 revision ID 初版撞 `e5f6a7b8c9d0`（2399 修复迁移已占用）——已换 `f7a8b9c0d1e2`；
  后续建迁移建议先 `grep -r "候选ID" backend/alembic/versions/` 再落文件。
