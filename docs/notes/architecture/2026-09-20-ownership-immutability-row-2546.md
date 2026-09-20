# Ownership 表补「不可变契约范围」行 + §6 采纳台账补登两稿

Status: implemented
Class: architecture

## Decision

#2546 已 Closed（结论：方案 A 落地）。本单是关单后的**最小 follow-up**，只动
`docs/design/2026-semantic-ownership.md`，做三件事：

1. **补行 `script-version-immutability`**（F-1 机制化）。此前该契约在**确权表内无行**，
   且核实到一个更硬的事实：`ADR-0020` 正文 **0 命中**「不可变 / immutable」——即
   「已发布脚本版本不可原地修改或删除」这条契约在 **Accepted ADR 面上没有承载体**，
   真源实际是按需文档 `docs/development/script-versioning.md` 的 `## 已发布版本不可变`
   与 `AGENTS.md` **总原则** `:15-16`（S11 第 12 锚，`bd7453d4` 于 2026-09-20 补入）。
   owner 因此指向 development 真源，**不**指向 0020（避免把推断写成归属）。
2. **把 0039 的 Accepted 转换绑进 S15**：新行的复议触发器写明——ADR-0039 转 Accepted 时
   必须**同 PR**把 owner 改指 0039 D1 + 同步 `AGENTS.md` 总原则行与
   `check-script-version-immutability.py` 判据。§7 增补触发 1（升 Accepted ADR）
   与 S15②（锚命中恰 1）合起来，使「漏同步」从口头纪律变成会红的检查。
3. **补登两稿并登记一条对立案方自己的否决**：§6 依据行原只列 6/8 稿（漏 0b6e98 CodeBuddy、
   7f3504 claude-code，二者均含阻断级修正）；同时 §6.2 登记
   「S6 `RESIDENT_BUDGETS` 作本表膨胀上界」这一**由立案方在 issue 与 N 系列中提出的建议
   被 2 源否决**（S6 语义 = 常驻启动入口预算，本表是按需文档），并记录其推荐替代
   （S15 内「本表 ≤ N 行」自卡）**实测尚未实现** → 开放缺口，不在本单擅自补机制。

**不做**：不改 ADR-0039 正文——其幽灵 `plan_step.script_sha` 已由在飞 PR **#2929** 处理，
本单避开同一文件（§8 只做状态登记）。

## Alternatives

| 选项 | 否决理由 |
|---|---|
| owner 指 `ADR-0020`（0033 §5.1 把它称作病根） | 0020 正文无该主张；照「谁像病根」挂 owner = 复现 c42fb9/54cd71 判过的「凭编号推断归属」错误 |
| 等 ADR-0039 转 Accepted 时再补行（§5「Proposed 不强制入表」的字面读法） | 那正是 F-1 的成因：第一个动它的人临时决定 owner 是谁。补行的 owner 指向**现行**真源，0039 只是触发器，与 §5 不冲突 |
| 本单顺手补 S15 行数自卡 | 属新机制且改治理面脚本；先登记缺口，等 owner 裁决（§6.2 已写明） |
| 顺手改 ADR-0039 `:66`/`:149` 的「硬不变量」字样 | 与在飞 #2929 同文件冲突风险 > 收益；改为 §8 登记残留 + 触发器挂行 |

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --check`（S1–S15）：结果见 PR；
  新行锚 `docs/development/script-versioning.md :: ## 已发布版本不可变` 命中数单独核为 **1**。
- 真载体核实：`PlanStep.script_name/script_version` = `backend/models/plan.py:95-96`；
  `step_trace` = `backend/models/job.py:95-96`；`Script.content_sha256` = `backend/models/script.py:19`；
  `plan_step.script_sha` 在 `backend/models/*.py` **0 命中**（= 幽灵，佐证 #2929 与本单 §8 登记）。
- `AGENTS.md` 章节归属复核：`awk '/^## /{sec=$0} /不可原地修改或删除/{print NR": "sec}'` →
  `15: ## 总原则`，非硬不变量（与 0b6e98/54cd71/7f3504 三稿一致，纠正立案方初稿 F-1 的归节错误）。
- 本次为 docs-only：`test_impact` 登记为 `indirect`（declare 未显式传 `--test-impact`，取缺省值；
  实际不跑单测，验证面 = 治理门禁脚本自身）。

## Revisit

| 条件 | 动作 |
|---|---|
| ADR-0039 转 Accepted | 同 PR：`script-version-immutability` owner 改指 0039 D1 + 改 `AGENTS.md` 总原则行 + 改 `check-script-version-immutability.py` 判据 + 收 §8 的 `:66`/`:149` 残留 |
| 0039 长期停在 Proposed（>30 天） | 由 owner 裁「继续 Proposed 还是撤」；期间 `AGENTS.md` 与 development 文档维持现行口径 |
| 确权表行数增长 | S15 自卡（≤N）仍是缺口：若表越过 ~50 行仍未落地，按 §6.2 推荐的「表内自卡」补一次治理面 PR |
| 新的「唯一权威」字面双标出现 | 按 §4.4：加表行 + 逐对收口，不指望门禁判红 |
