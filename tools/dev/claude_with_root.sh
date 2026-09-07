#!/bin/sh
# claude_with_root.sh —— Claude 子目录会话的根启动契约注入（ADR-0034 P2b / #857）。
#
# 背景：Claude Code 从子目录启动时根 CLAUDE.md 的 @AGENTS.md import 不解析
# （#857）；本机 tinno 构建亦不执行 SessionStart hooks（-p 与 TUI 均实测）。
# 调用方注入是当前唯一已验证的根供给方式（2026-09-07 实证：子目录 cwd 下
# Q1 根契约/Q2 scoped 双绿）。SessionStart hook 方案留档为 stock 构建候选。
#
# 用法：claude_with_root.sh [其余 claude -p 参数...] "<prompt>"
#   （本脚本固定 -p 非交互形态；交互 TUI 请从仓库根启动或先读根 AGENTS.md）
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not a git repo" >&2; exit 1; }
exec claude -p --append-system-prompt "$(cat "$ROOT/AGENTS.md")" "$@"
