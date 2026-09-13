# 通知 context.link 约定 + Alertmanager 告警跳转推导（#625）

Status: implemented
Class: feature

## Decision

按 issue 推荐的**方案 2（通用 `context.link` 约定）**落地，并补齐 Alertmanager
源的实际推导使验收可达：

1. **约定字段**：`context.link`（站内路径，`/` 开头）为任意通知 source 可
   携带的跳转目标；前端 `notificationTarget` 对其**优先盲拼**（label「查看
   详情」），非 `/` 开头一律忽略（防外链注入），其余 event_type 映射保持
   不变且优先级在其后。
2. **Alertmanager 源推导**（`receive_alertmanager_alert`，best-effort 不阻断）：
   - `annotations.link` 显式标注的站内路径直接采用——运维在告警规则层钉
     任意目标的出口；
   - 否则取 labels 的 `host` / `hostname` / `instance`（`instance` 剥
     `:port`，兼容 node_exporter 形态）对 host 表做 `hostname`/`ip` 归一
     查找，命中 → `context.link = "/hosts"`（主机详情为页内抽屉、无独立
     路由，按验收「主机或设备页」落到主机列表页）；
   - 解析失败只记 debug，context 不带 `link` 键——**context 形状向后兼容**
     （既有消费方读不到该键时行为不变）。

## Alternatives

- 方案 1（dispatch 时把 labels 标识翻译成 host_id/device_id 专属字段）——
  每类新 source 都要新增约定字段 + 前端逐个加映射，扩展面大；`link` 单字
  段让前端只写一次。
- 前端按 event_type（alertname）逐个加 case——alertname 是开放集合（任意
  Prometheus 规则名），不可枚举。
- 等生产出现带主机标识的告警规则再做——约定与推导机制与具体规则无关，
  先落机制；规则侧（如 disk_usage 类补 host label）属运维配置，见 Revisit。

## Verification

- 后端 `backend/tests/api/test_notifications.py` 28 passed（新增 4 例）：
  `annotations.link` 直通、非 `/` 路径拒绝、`instance:9100` 剥端口命中
  seed 主机 → `/hosts`、无标识时不带 `link` 键。
- 前端 `notificationTarget.test.ts` 5 passed（vitest）：link 优先盲拼、
  外链忽略、link 压过 run_id 映射、无 link 回退既有映射、未知类型 null。
- `python scripts/run_gates.py check:quick` 7 gates 全绿。

## Revisit

- 验收句「disk_usage 类点击跳主机页」的**规则侧前提**：现有
  `deploy/prometheus/alerts-stability-platform.yml` 全部为控制面指标告警、
  不携带主机标识，且无 disk_usage 规则——需要运维在规则层补主机维度告警
  （labels 带 host/hostname/instance 或 `annotations.link`）后链路才实际
  出 link；机制已就位并经测试覆盖。
- 未来其它 source（job 报告、主机告警等）按约定直接写 `context.link`，
  前端零改动。
