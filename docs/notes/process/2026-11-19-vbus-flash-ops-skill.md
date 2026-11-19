# 新增 vbus-flash-ops 工作区 Skill

Status: implemented
Class: process

## Decision

在 `.claude/skills/vbus-flash-ops/SKILL.md` 新增共享工作区 Skill（经 `.agents/skills -> ../.claude/skills` 符号链接同步暴露给 Antigravity / Claude Code / Codex / Cursor），按需提供 `trigger_mode="vbus_cycle"` 的前置拓扑/槽位核查、每批 ≤ 2 台串行推流节奏、控制面 I/O 护栏、`journalctl -k` 真假切电时间指纹分诊，以及批次收尾 `0000 off` 端口恢复供电 SOP。

## Alternatives

- 将规则直接写入根 `AGENTS.md`：会占用常驻上下文预算（`defaultRulesBudget`），而 VBUS 刷机仅在设备刷机运维会话触发，更适合按需披露的 Skill。
- 并入既有 `.claude/skills/diagnose-device-stall/SKILL.md`：该 Skill 面向运行期设备离线 / PlanRun 卡死 / L1–L4 ADB 与 xHCI 排障，混入关机态 `vbus_cycle` 前检与切电指纹会混淆触发时机并使指令膨胀。

## Verification

- 在 `.wt/skill-vbus-flash-ops` 中完成 `git add` 后运行 `tools/dev/check_governance_surface.py --check --base origin/main`（核对 S7 `.claude/skills/*/SKILL.md` frontmatter `name` 与目录一致且 `description` 非空）、`tools/dev/check_invariant_diff.py --base origin/main`、`tools/dev/ai_work.py --self-test`、`tools/dev/check-internal-ip-leak.py --check -q`。
- 按 `docs/development/writing-conventions.md` §5 自查 Skill 正文与 `description`。

## Revisit

- 当 #4063 确定「VBUS 隔离端口平台可见性与自动恢复」或「关机态 VBUS 上电不进 `0e8d:2000`」新机制并随新版本脚本上线时，同步更新 `.claude/skills/vbus-flash-ops/SKILL.md` 第三、四节与 `docs/operations/honor-flash-runbook.md` §10。
