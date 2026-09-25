# crash artifact 丢失可见：心跳上报 → 控制面 Gauge → 两条告警（#3217）

Status: implemented
Class: feature

关联：[#3217](https://github.com/DUElost/stability-test-platform/issues/3217)（本单，来源 2026-09-23 审计 F08）、
[#3230](https://github.com/DUElost/stability-test-platform/issues/3230)（台账 R0，复审 A04）、
ADR-0018 5B2（artifact「失败即丢」契约，本单不改）、#1257（「有账面、无告警」型静默失效）、
#2873（per-host 推式 Gauge 的退役子项治理）。

## Decision

目标是让「丢了多少」在控制面可查、可告警。不改 5B2 的丢弃语义，也不做「零丢失」：零丢失意味着给
JobArtifact 建持久 outbox，属于方向级决策，按 #3217 的写法，触发条件是「目标 fleet 下产物完整率达不到约定 SLO」。

三段同批合入，避免只做一半：

1. **Agent 出口**：`ArtifactUploader.heartbeat_counts()` 随 `get_outbox_counts()` 上报 4 个进程级累计键
   （`artifact_submits_total` 以及 `artifact_dropped_{submit,promote,post}_total`）。三条丢失路径分开报，因为
   成因与处置不同：
   - 提交即丢：未启动、已 stop、坏载荷或队列满，属于容量与背压问题；
   - promote 失败：LOCAL 到共享根的 promote 失败，属于存储面问题；
   - POST 失败：登记请求出异常或返回非 2xx，且不重试，属于网络与后端问题。

   #3217 原文只点了「队列满」与可选的 promote 失败，但 POST 失败同样是终态丢失，不报等于留一个静默口。
   `submits` 是完整率的分母。
2. **控制面**：心跳路由加显式分支 `record_agent_artifact_upload`，写入两个 per-host Gauge：
   `stability_agent_artifact_submits{host_id}` 与 `stability_agent_artifact_dropped{host_id,stage}`，
   并经 `_note_host_child` 纳入 #2873 的退役清理。**缺键或非法值只跳过、不写 0**：旧 Agent 不带这些键，
   写 0 会让升级窗口里的旧主机被读成「零丢失」。
3. **告警**：按成因拆两条（#3026 口径），`increase(...[15m]) > 0` 且 `for: 5m`，均为 warning：
   - `StabilityAgentArtifactSubmitDropped`：`stage="submit"`；
   - `StabilityAgentArtifactDeliveryFailed`：`stage=~"promote|post"`。

   promtool 场景用真实标签形状，覆盖三类情形：两条各自触发；promote 恒为 0 时不触发；Agent 重启使累计值
   回落（5 → 0）且之后无新丢失时**不误报**。

## Alternatives

- **#3217 原写的「新 Counter」**（控制面按心跳算差值再 `inc`）：差值要记「上一次看到的值」，控制面重启
  就丢；多实例（ADR-0027）时心跳分流到不同实例，各实例会重复累加差值。Gauge 承载 Agent 的累计值、在
  PromQL 里按计数器语义读（`increase()` 把回落当作重置），控制面无状态，也不怕多实例。
  名字不带 `_total`（那是 Counter 的保留后缀）。
- **只写进 `host.extra`**（现有 `scan_shard_register_failure_total` 就是这样）：正是 #1257 型「有账面、
  无告警」。本单要求显式分支，且用变异证明：去掉分支后 10 个用例变红。
- **一条告警覆盖三个 stage**：处置入口不同（查队列深度与突发量，还是查共享存储挂载与中心可达性），
  合并会让值班先猜成因。
- **缩短键名以压到 100B**：会与既有 `*_total` 命名不一致；实测最坏 150B，约为每拍心跳设备明细的 1%，
  单独立本单预算（< 160B）。
- **调大 256 或改无界队列**：#3217 非目标。无界只是把丢弃换成内存增长，并掩盖下游背压。

## Verification

- **四数对拍**（#3217 验收一）：真实 `ArtifactUploader`，worker 不消费，队列容量 4，三档注入：
  - 临界以下：注入 3，丢 0；
  - 恰好触顶：注入 4，丢 0；
  - 超限：注入 7，丢 3。

  每档中注入数、Agent `stats`、控制面 Gauge、`host.extra`（`GET /hosts/{id}` 的数据源）四者逐档相等
  （`backend/tests/api/test_heartbeat_artifact_drop_3217.py`）。
- **载荷预算**（验收二）：紧凑 JSON 最坏 150B、典型 132B，用例断言 < 160B。
- **变异**：
  - 去掉心跳显式分支：10 个用例变红；
  - 缺键改为写 0：2 个用例变红；
  - 不登记 per-host 子项：退役清理用例变红；
  - 去掉 Agent 心跳接线：接线用例变红。
- **promtool**：场景在本机 2.53.3 与 CI 固定的 3.13.3 下都通过；`tests/test_prometheus_alerts_contract.py`
  等告警契约（含两条新规则的逐条阈值变异自证）通过。
- `tools/dev/check-monitoring-assets.py` 本机结果：平台规则副本 drift，属于待执行的 #2959 手册 Step 2
  （现网规则同步是独立授权动作，不在本单内）；其余 8 项为已登记的既有遗留。
- 其余测试结果见 PR 描述。

## Revisit

- 生效需要两步独立动作：Agent 机队分发（与 #3251、#3169 合为一批）与现网规则同步（#2959 手册 Step 2）。
  两步都完成之前，控制面看不到这两条告警对应的数据。
- 有了实测完整率之后，按 #3217 的触发条件裁决是否为 JobArtifact 建持久 outbox（方向级，另立 ADR）。
- `scan_shard_register_failure_total` 等仍只落 `host.extra` 的累计键，是同型「半套」；需要告警时按本单
  同样的方式接入，不要只加 extras 键。
