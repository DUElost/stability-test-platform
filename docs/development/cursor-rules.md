# Cursor 规则说明

> 架构与 ADR 权威源：根目录 [`CLAUDE.md`](../../CLAUDE.md)  
> 命令与测试速查：根目录 [`AGENTS.md`](../../AGENTS.md)

---

## 1. 分层关系

本仓库采用「单一事实源 + Cursor 薄适配层」：

| 层级 | 路径 | 读者 | 作用 |
|------|------|------|------|
| 共享策略 | `AGENTS.md` | 所有 AI 工具 / 开发者 | 命令、测试、架构摘要 |
| 项目约束 | `CLAUDE.md` | Claude Code、Cursor 等 | 架构不变量、ADR、开发陷阱 |
| Cursor 适配 | `.cursor/rules/*.mdc` | Cursor Agent / Chat | 按文件类型自动注入的精简规则 |
| 个人习惯 | Cursor Settings → User Rules | 仅本机 | 提交规范、PR 流程等 |

**维护原则**：改全局约定先改 `AGENTS.md` / `CLAUDE.md`，再按需同步对应 `.mdc`。不要在 rules 里复制整篇文档，避免漂移。

---

## 2. 规则文件一览

目录：`.cursor/rules/`

| 文件 | 激活方式 | 内容 |
|------|----------|------|
| `00-project-context.mdc` | `alwaysApply: true`（每次对话） | 架构不变量、状态机、方案 C 存储边界 |
| `backend-python.mdc` | `backend/**/*.py` | Pydantic v2、表名单数、pytest、dedup/NFS |
| `frontend-typescript.mdc` | `frontend/**/*.{ts,tsx}` | `types.ts` 同步、vitest、`@/` 别名、Watcher UI 测试 |
| `agent-runtime.mdc` | `backend/agent/**/*` | ADB、Watcher、Scan/Upload/SAQ |
| `agent-scripts.mdc` | `backend/agent/scripts/**/*` | ADR-0020 脚本目录、扫描语义、stdout JSON 链路 |

编辑 `backend/agent/scan_runner.py` 时会同时命中 `backend-python` 与 `agent-runtime`，属预期行为。

---

## 3. `.mdc` 格式

每个规则为 Markdown + YAML frontmatter：

```markdown
---
description: 简短说明（显示在 Rules 列表）
globs: backend/**/*.py
alwaysApply: false
---

# 标题

规则正文…
```

| 字段 | 说明 |
|------|------|
| `alwaysApply: true` | 每次 Agent 对话都注入 |
| `globs` | 打开/编辑匹配文件时注入 |
| `description` | Cursor Settings → Rules 中显示 |

建议单条规则 **≤50 行**、一事一文件。

---

## 4. 在 Cursor 中查看

1. **Settings → Rules → Project Rules** — 列出 `.cursor/rules/` 下所有规则及激活状态  
2. 打开 Agent 时，Chat 上下文会显示已附加的 rules  
3. 旧版根目录 `.cursorrules` 仍可用，但无 glob 能力；新项目请用 `.cursor/rules/`

---

## 5. 与 Claude Code 的对应

| Claude Code | Cursor |
|-------------|--------|
| `CLAUDE.md` | `CLAUDE.md` + `00-project-context.mdc` |
| `.claude/rules/` | `.cursor/rules/*.mdc`（带 `globs`） |
| User / project memory | Settings → User Rules + Project Rules |

---

## 6. 版本控制

`.cursor/rules/` **应提交到 Git**（`.gitignore` 已对 `!.cursor/rules/` 放行）。其余 `.cursor/` 目录内容（本地缓存等）仍被忽略。

新增或修改规则后，在 PR 中简要说明变更原因，并与 `CLAUDE.md` / `AGENTS.md` 保持一致。
