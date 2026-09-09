# ADR-0036 通知投递语义契约（起草）

Status: proposed
Class: architecture

## Decision

R11 台账（#1125）的三项——#1117 通知失败不触发 SAQ 重试、#1120 钉钉业务失败报成功、#1122 SMTP 无 timeout + 通知线程池无界——定性为**同一件未定义的东西**：通知投递语义。据此起草 [ADR-0036](../adr/ADR-0036-notification-delivery-semantics.md)（Proposed），并修订 [ADR-0011](../adr/ADR-0011-observability-and-alerting-evolution.md) 的范围边界。

**分工（不构成平行权威源）**：ADR-0011 = What to notify（通知什么/何时触发/如何路由）；ADR-0036 = How delivery behaves（成功/失败定义、retry owner、超时、幂等、同步异步边界、投递事实落库）。ADR-0011 前提未被推翻（其"仅锁定第一层指标基线"仍成立），但其范围从来不含投递语义，故不修订其决策内容、只补边界与指针。

**ADR-0036 的九项裁决**（只裁语义，不裁参数）：D1 `ACCEPTED = 渠道明确接受投递请求`（≠ DELIVERED，不写协议状态码）；D2 三态失败分类 `REJECTED_PERMANENT` / `REJECTED_TRANSIENT` / `UNKNOWN`；D3 网络投递必须有显式 deadline（数值属实现）；D4 重试由异步队列（SAQ）唯一负责 + **投递级幂等为成对硬约束**；D5 统一 retry 策略含退避与上限、UNKNOWN 计入；D6 投递结果必须落业务事实层（DB）且状态词表向前兼容；D7 at-least-once + 每通道去重键、明确不承诺端到端去重；D8 同步仅限管理员连通性测试；D9 渠道适配器统一归一化，新渠道不得自定义成功。

**关键推导（本次新增证据）**：SAQ 重试是同一 job / 同一 key / 同一 kwargs 重跑（`saq.job.Job.retryable` = `retries > attempts`），因此**仅靠 SAQ 单层重试无法满足 #1117 的"不重发已成功通道"**——retry owner 与投递幂等必须成对成立。另：`UNKNOWN` 不可恒等于 `REJECTED_TRANSIENT`，因其直接改变重复投递语义。

**挂起两项**（各有复议触发条件）：端到端送达回执 `DELIVERED`（触发：ADR-0025 D6 真实值班通道接入且渠道提供可验证回执）；Alertmanager 入站投递契约（触发：入站告警产生实际误报/重复告警处置需求）。

**涉及文件**：新增 `docs/adr/ADR-0036-notification-delivery-semantics.md`；修订 `docs/adr/ADR-0011-observability-and-alerting-evolution.md`（决策节补范围边界引用块、落地节补指针）；索引行 `docs/adr/README.md` + `docs/DOC-MAP.md`；本 note。

**跟踪 issue**：#1166（ADR-0036 评审与定稿）、#1165（DOC-MAP 断链 → `gov-surface` 红灯）、#1167（契约实现分解 D1–D9）。

## Alternatives

- **只修三个 Issue，不立 ADR**：三个修复会各自发明"成功"的定义；#1117 的"不重发已成功通道"仍无依据；下一个渠道接入时重犯。已发生一次的先例：ADR-0018 Phase 2 明写「API 路由中同步 dispatch 改为 `await queue.enqueue(...)`」（`ADR-0018:230`），实现退回 fire-and-forget 线程池（`notification_service.py:249-252`）。
- **修订 ADR-0011 承载投递语义**：驳回——其 Accepted 前提未被推翻，但范围从来不含投递语义；塞进去会让可观测性 ADR 变成投递权威源。
- **写成 Agent Note 而非 ADR**：驳回——跨渠道长期契约属方向级决策，按 `docs/notes/README.md` 分工归 ADR。
- **在 ADR 正文写协议状态码 / retry 次数 / 线程池大小**：驳回——渠道状态码属 adapter contract，参数属实现配置；写进 ADR 会让它随渠道数膨胀。

## Verification

- 静态核验（2026-09-08，基线 `ff997d6d`）：投递三路径、两层吞异常、状态码口径、`notification_logs` 无投递列、线程池无界、通知指标为 0、重试测试 mock 绕过真实路径——全部经 `file:line` 定位，写入 ADR §1.2。
- `python3 tools/dev/ai_work.py status` 前检 + `declare adr-0036-notification-delivery-semantics`（scope 含 `docs/adr/ADR-0036-*.md`、`docs/adr/ADR-0011-*.md`、`docs/notes/architecture`，`test_impact=indirect`）。
- 索引挂靠位已同步：`docs/adr/README.md` 清单行 + `docs/DOC-MAP.md` 架构 ADR 行（对齐 ADR-0029/0030 的「状态传播挂靠位」教训）。
- 门禁实测：`python3 tools/dev/check_governance_surface.py --check` → **全绿**（S1–S12、S5x）。起草中途曾报 1 项 BLOCK，指向**他人在窗未提交行** `docs/DOC-MAP.md:85 → ./adr/ADR-0035-agent-host-identity.md`（文件不存在）；该行已由 `docs-906-agent-secret-boundary` 处置，两次复核均绿（issue #1165 已关闭）。
- 本次为**文档变更**，不含代码，故未跑代码门禁；`check:quick` 与实现侧验证随 #1117/#1120/#1122 落地。
- **未完成（不计入已验证）**：ADR-0036 状态仍为 Proposed，待评审；实现侧（三态分类、投递幂等、deadline、持久化）尚无代码与测试。

## Revisit

- **编号**：ADR-0035 已被在窗 Execution `docs-906-agent-secret-boundary`（integration=READY，已提交 `ADR-0035-agent-secret-host-boundary.md`）占用，故本文改用 **0036**；若该 Execution 废弃，本文仍保留 0036（不复用旧编号）。
- **前置风险（他人 scope，已处置）**：本工作树中 `docs/adr/README.md` 与 `docs/DOC-MAP.md` 曾有**未提交**索引行指向 `adr/ADR-0035-agent-host-identity.md`（该文件不存在）。已由 `docs-906-agent-secret-boundary` 移除并经 `gov-surface` 复核全绿；本 Execution 未改动他人那两行。**残留提醒**：该移除目前仍是工作树未提交状态，合入前勿丢弃。
- **issue 关联**：declare 时 #1117（`fix-1117-notification-saq-retry`，CODING）、#1120（`fix-1120-dingtalk-errcode`，FINISHED）已被在窗 Execution 引用，故本 Execution 未绑 issue 号（scope 为纯文档，与代码修复无文件重叠）；本 note 以文字形式保留溯源。
- 端到端送达回执 / 入站契约成为真实需求时（触发条件见 ADR-0036 §2.3）。
