# 新增稳定性测试专项建单与开跑 Skill（stability-plan-launch）

Status: implemented
Class: process

## Decision

在 `.claude/skills/stability-plan-launch/SKILL.md` 新增共享工作区 Skill（经 `.agents/skills -> ../.claude/skills`
符号链接同步暴露给各 AI Harness），并同步更新 `docs/operations/new-specialty-onboarding-runbook.md`
§5 与 §7，将「稳定性测试专项开跑前必须在 `init` 阶段紧接 `check_device` 加入 `sync_device_time@1.0.1`」
固化为可执行 SOP，与 `vbus-flash-ops`（刷机前置）、`monkey-aee-dedup-handoff`（完结去重交付）构成
三段式作业闭环。

涉及文件：

- `.claude/skills/stability-plan-launch/SKILL.md`
- `docs/operations/new-specialty-onboarding-runbook.md`（仅在 §5 与 §7 补齐 `sync_device_time` 前置要求）
- `docs/notes/process/2026-11-26-stability-plan-launch-skill.md`

写作规范：新增与改动的 SOP 句子全部按 `docs/development/writing-conventions.md`（ADR-0059）书写与自查
（推荐术语、限定语、规则句强度词 `必须`/`不得`/`建议`/`可以` + 显式执行者、一步一动作、警告前置、
状态变更带 `预期：`、检查步骤带具体查法）。

## Alternatives

1. **仅更新 `docs/operations/new-specialty-onboarding-runbook.md`，不新增 Skill**：放弃。Owner 明确指出
   同步设备时间是执行稳定性测试专项前的必选前置动作，日常由 AI 协助新建/调整/触发稳定性专项 Plan 时
   需要可自动触发的按需 Skill 在操作路径上强拦截漏配。
2. **把步骤塞进 `monkey-aee-dedup-handoff` 或 `script-version-lifecycle`**：放弃。前者负责专项完结后的
   日志去重与 Jira 交付，后者负责脚本版本发包与退役，均不在「新建/开跑稳定性专项 Plan」触发时机上。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --base origin/main`（核验 S7 skill frontmatter
  `name` 与目录一致且 `description` 非空、S10 Agent Note 头部规范、链接完整性）；
- `venv/bin/python scripts/run_gates.py check:quick`（全部 16 项门禁）；
- 按 `docs/development/writing-conventions.md` §5 四项清单逐条人工自查新增与改动的句子。

## Revisit

- 若 `sync_device_time` 后续发布新版本（高于 `1.0.1`），同步更新本 Skill 与
  `docs/operations/new-specialty-onboarding-runbook.md` 中的头版本引用；
- 若 14 天观察窗后 `stp-skill-usage` 显示该 Skill 强信号零调用（HOLLOW），按 `docs/operations/README.md`
  §6 复评 description 触发词或决定归并至 runbook。
