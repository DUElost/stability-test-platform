# 批次 Web 工作面改为 Owner 逐批选择

Status: implemented
Class: process

## Decision

ADR-0058 的角色继续按“职责 / 工作面”定义，不绑定具体产品。批次 Planner 与 Reviewer 的具体 Web 工作面由 Owner 按批次或复核对象人工选择，可使用 ChatGPT Web、Claude Code Web 或其他满足能力要求的工作面；更换工具不修改 ADR，也不新增 Registry 字段。

Planner 每批使用新的独立会话。会话可以位于 STP Project 中，但 Project 内历史聊天只作为共享背景与资料定位入口，不能替代当前 `main`、issue / PR、Accepted ADR 与现行治理文档的重新取证。旧会话里的修法、分组、判断或模型结论只有在当前权威源中存在可追溯依据，或在本批重新推导后，才能进入方案。

Reviewer 同样不绑定产品。同一会话切换 prompt / 角色不构成独立复核；普通复核允许“同一 STP Project + 独立新会话 + 独立取证”。数据丢失、安全、难回退、架构 / 治理实验以及对 Planner 质量本身的验证，优先使用与被复核工作面不同的 Web/Harness，以降低共享上下文锚定。

以上只是工作面选择与取证纪律：不新增标签、Registry 状态、CI 门禁或机械流程。只有在批次实验或复盘需要统计时，才在批次 issue / 复核记录中留下实际工作面事实。

本次同步以下现行文档：

- `docs/development/repository-workflow.md`
- `docs/development/ai/batch-planning.md`
- `docs/development/ai/batch-review.md`

## Alternatives

- **固定 Planner = Claude Code Web、Reviewer = ChatGPT Web**：拒绝。它把当前工具选择误写成角色契约，限制 Owner 的 Harness 选择权，也与 ADR-0058“职责按工作面而非具体工具划分”的方向不一致。
- **同一 STP Project 的不同聊天视为完全隔离**：拒绝。项目级共享上下文可能形成锚定；独立性的最低保证应来自新会话与重新取证，而不是假设聊天之间完全失忆。
- **所有复核一律要求不同产品 / Harness**：拒绝。普通复核的额外隔离收益不足以覆盖长期治理成本；更强隔离只按风险优先选择。
- **把隔离级别写入 Registry / 标签 / CI**：拒绝。当前没有实测证据支持新增机械治理面，保持 ADR-0058 D6 的治理预算。

## Verification

- 修改前检查目标文档没有开放 PR 重叠。
- 仅修改 Planner / Reviewer 的承载与上下文取证纪律；不修改 ADR-0058、ADR-0034、Execution Registry、FIFO auto-merge 或 CI 规则。
- `batch-planning.md` 与 `batch-review.md` 仅提升 Living 文档版本并增加工作面选择原则；现有批次方案、派单、复核输出、激活与验收流程保持不变。
- PR CI 作为 Markdown / governance surface 的最终机械校验。

## Revisit

出现以下任一情况时重议：

- 同 Project 独立新会话仍出现可归因于共享上下文的复核失真；
- 跨 Web/Harness 的额外隔离长期产生明显成本，却没有发现能力收益；
- 实测需要机器化记录工作面才能回答返工率、复核收益或冲突归因问题。

重议前不新增 Registry 字段、标签或门禁。
