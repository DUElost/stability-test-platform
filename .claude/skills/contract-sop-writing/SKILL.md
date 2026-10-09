---
name: contract-sop-writing
description: 契约类文档与 SOP 的写作约定（ADR-0059：术语推荐名与限定语、规则句强度词 必须/不得/建议/可以、SOP 一步一动作与警告前置）。触发时机：新写或改动 AGENTS.md、执行契约、ADR 的「决策」节、docs/design/*-contract.md，或 .claude/skills/*/SKILL.md、docs/operations/ 流程文档、docs/ 根目录的 runbook / checklist 中的句子。
---

# 契约类文档与 SOP 的写作约定

按顺序执行以下步骤：

1. 读取 `docs/development/writing-conventions.md` 全文。
2. 按其 §1 判断本次改动的文件是否在适用范围内。如果不在，停止本流程。
3. 只对本次新写或改动的句子执行其 §2–§4。
4. 提交前，按其 §5 自查清单逐项核对，并在 PR 描述「文档」节勾选对应项。

不得为满足约定改写本次未触碰的句子，也不得改写 `AGENTS.md`「硬不变量」节的 S11 锚定原文。
