#!/bin/sh
# agy_with_rules.sh —— Antigravity CLI 的仓库规则前置供给（ADR-0034 附录 A / P2）。
#
# 实测（2026-09-07，agy 1.1.26 -p 非交互）：不自动发现 AGENTS.md / CLAUDE.md
#（含 symlink）/ GEMINI.md——根与嵌套均否，引文诊断确认上下文仅 Gemini 系统
# 指令 + USER_SETTINGS。仓库规则只能由调用方前置到 prompt。
#
# 用法：agy_with_rules.sh "<prompt>"（内部 exec agy -p "<根 AGENTS.md>\n\n---\n<prompt>"）
# 注：根 AGENTS.md 会整段进入本次 prompt（体量 ~3.8KB）；上游提供 agent/配置
# 装载机制后本 wrapper 退役。
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not a git repo" >&2; exit 1; }
RULES="$(cat "$ROOT/AGENTS.md")"
exec agy -p "$RULES

---

以下是本次用户问题：

$*"
