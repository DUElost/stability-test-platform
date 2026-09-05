# Cursor 规则说明

> 跨 Harness 启动契约与硬不变量：根目录 [`AGENTS.md`](../../AGENTS.md)
> Claude 导入与按需路由：根目录 [`CLAUDE.md`](../../CLAUDE.md)
> 跨 Harness 适配基线：[`ai/harness-adapters.md`](./ai/harness-adapters.md)

---

## 1. 分层关系

本仓库采用「单一事实源 + Cursor 薄适配层」：

| 层级 | 路径 | 读者 | 作用 |
|------|------|------|------|
| 共享启动 | `AGENTS.md` | 所有 AI 工具 / 开发者 | 总原则、跨模块硬不变量、安全红线、按需入口 |
| Claude 入口 | `CLAUDE.md` | Claude Code、Cursor 等 | 导入共享契约并提供按需路由 |
| Cursor 适配 | `.cursor/rules/*.mdc` | Cursor Agent / Chat | 常驻入口与按路径加载路由 |
| 个人习惯 | Cursor Settings → User Rules | 仅本机 | 提交规范、PR 流程等 |

**维护原则**：项目事实只在权威文档维护；`.mdc` 指向相关权威入口，不复制环境变量
默认值、命令参数、状态机或实现摘要。Harness 共通边界见
[`ai/harness-adapters.md`](./ai/harness-adapters.md)。

---

## 2. 规则文件一览

目录：`.cursor/rules/`

| 文件 | 激活方式 | 内容 |
|------|----------|------|
| `00-project-context.mdc` | `alwaysApply: true`（每次对话） | 根文档、文档地图与 Harness 基线入口 |
| `backend-python.mdc` | `backend/**/*.py` | 后端约束、测试与设计文档路由 |
| `frontend-typescript.mdc` | `frontend/**/*.{ts,tsx}` | 前端约束、脚本与设计文档路由 |
| `agent-runtime.mdc` | `backend/agent/**/*` | Agent、AEE、跨进程契约文档路由 |
| `agent-scripts.mdc` | `backend/agent/scripts/**/*` | 版本化脚本契约与退役规则路由 |

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

建议单条规则 **≤30 行**、一事一文件；只保留 Cursor 加载所需的路由信息。

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
如果规则需要复述项目事实，应优先把该事实放回权威文档并在规则中链接。
