# Agent Note: Agent 终态 outbox 积压告警（#743 Part 1）

Status: implemented
Class: bug-fix
Issue: #743

## Decision

按 `#743` 期望 1 新增告警规则（落在 `deploy/prometheus/alerts-stability-platform.yml` 的
`stability-platform-agent` 组）：

```yaml
      - alert: StabilityAgentTerminalOutboxBacklog
        expr: stability_agent_outbox_pending{type="terminal"} > 0
        for: 30m
        labels:
          severity: warning
```

**阈值取自 issue 原文**（「`> 0` 持续（如 `>30m`）」），未自行发明。同时在
`alerts-stability-platform.test.yml` 补一条 promtool 场景，证明该规则**在真实标签形状下确实可触发**
（不是"写了就算"）。

### 一处**必须偏离 issue 原文**的地方

issue 写的选择器是 `stability_agent_outbox_pending{outbox_type="terminal"}`，但注册表里该指标
的标签名是 **`type`**（`backend/core/metrics.py`：`agent_outbox_pending = Gauge(…, ['host_id', 'type'])`）。
**照抄 issue 原文会被本仓的契约测试直接拦下**——`tests/test_prometheus_alerts_contract.py`
的结构层（恒跑）校验「表达式里的指标名与标签名必须存在于注册表」，未知标签即红。

## Alternatives

- **同时做期望 2（`POST .../jobs/*/complete` 404 速率告警）**：本次**不做**。控制面目前**没有**
  可用的接口级指标——`record_api_request` 在生产调用计数为 0（该事实亦被 `#737` 列为「死打点」，
  且 `#737` 对该组的验收写的是「**激活或清理**」，属需先裁定的二选一）。没有指标就写不出
  "速率异常"的规则（写了也永不触发，正是本仓所述"虚假可观测性"）。故 404 速率侧留给
  「先裁定 `record_api_request` 激活或清理」之后，或另立 access-log 侧的 recording rule。
- **新增 Alertmanager 路由**：不做。本规则沿用既有 `severity: warning` 标签，随现有路由生效；
  `deploy/prometheus/alertmanager.yml` 被 README 标为「草案待挂载」，改路由属部署动作而非本单。
- **只写规则、不写场景**：不选。本仓 `alerts-stability-platform.test.yml` 的存在意义就是
  「用真实标签形状的样本证明规则可触发」；且本机 **promtool 可用**，场景**能真的跑**，
  不跑就写等于没有证据（见同日 process note 的「测量纪律 2」）。

## Verification

- `promtool check rules alerts-stability-platform.yml` → **SUCCESS: 13 rules found**（12 → 13，语法层）✓
- `promtool test rules alerts-stability-platform.test.yml` → **SUCCESS** ✓
  = 规则在 `host_id/type` 真实标签形状下、`>0` 持续到 35m 时**确实触发**，且 `exp_labels` /
  `exp_annotations` 与规则逐字一致（注解折叠为单行后逐字比对）✓
- `pytest tests/test_prometheus_alerts_contract.py -q` → **3 passed** ✓
  （结构层：指标名 `stability_agent_outbox_pending` 与标签名 `type` 均在注册表内）
- `check:quick` → 见 PR。
- **未验证（诚实标注）**：告警在真实 Alertmanager 上的**实际送达**（路由为草案、未挂载）；
  仅验证到"规则可触发 + 标签/注解契约成立"。

## Revisit

- **期望 2 未覆盖**：`POST .../jobs/*/complete` 404 速率告警依赖「接口级指标是否存在」这一前置裁定
  （关联 `#737` 的死打点组）。裁定后本组可再加一条规则，或改用 access log 侧的 recording rule。
- `docs/operations/control-plane-db-maintenance.md`（`#744`，同日交付）里「中心侧可见信号」一节
  应在本告警上线后补上**告警名与阈值对照**，让值班能从告警直接跳到处置步骤——该 note 已把
  这一点列为其 Revisit。
