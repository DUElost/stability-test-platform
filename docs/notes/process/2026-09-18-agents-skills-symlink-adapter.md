# Agent Note：`.agents/skills` symlink 薄适配（G5 收窄）

- Status: implemented
- Class: process
- Date: 2026-09-18

## Decision

skills **真身仍在** `.claude/skills/`（已入库、Claude Code 自动加载）。为让
Codex（官方扫 `.agents/skills`）与 Cursor（亦发现该路径）稳定读到同一批 SOP，
入库 **`.agents/skills` → `../.claude/skills` 的 symlink**，并在 `.gitignore`
只放行该 symlink、禁止再提交 `.agents/` 实体拷贝。

这是 G5（「`.agents/` 单家目录」）的**收窄落地**：多消费方可读，但不把真身迁出
Claude 目录、不做 notes 搬家。全量「真身迁 `.agents/` + `.claude/skills` 改
symlink」仍留待真有必要再评估（见 deepseek 研究 note G5 / ADR-0034 Revisit）。

## Alternatives

- **直接合入未跟踪的 `.agents/skills` 实体拷贝**——放弃：与 `.claude/skills`
  双真相；本地副本曾把 `.claude/settings.json` 误改成 `.Codex/settings.json`。
- **真身迁到 `.agents/skills`，`.claude/skills` 改 symlink（上游模式）**——暂缓：
  Claude 仍是本仓 skills 主消费方与既有路径；symlink 反向成本更低。
- **只改文档、不入库路径**——放弃：Codex 不扫 `.claude/skills`，无适配则读不到。

## Verification

- `readlink .agents/skills` → `../.claude/skills`；经 symlink 可读
  `add-api-endpoint/SKILL.md` 等。
- `git check-ignore` / status：仅跟踪 symlink，无实体 skill 文件进 index。
- 文档：`harness-adapters.md` Cursor / Claude / Codex 行已写明真身与 symlink。

## Revisit

- 若 Claude 侧废弃 `.claude/skills` 自动发现、或团队决定 skills 中立目录唯一，
  再做真身迁移（G5 全量）。
- 若出现第二份实体 `.agents/skills/*`，按漂移处理：删实体、恢复 symlink。
