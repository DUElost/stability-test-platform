# AI Harness 适配基线

本文只定义各 AI Coding Harness 如何接入仓库现有规则，不定义新的并行执行、
Role、Scope 或 Registry 语义。跨 Harness 执行契约须由后续 ADR 与独立契约文档裁决。

## 权威边界

| 层 | 权威内容 | 位置 |
|---|---|---|
| 代码与测试 | 实际行为 | 仓库源码与测试 |
| 共享启动契约 | 总原则、跨模块硬不变量、安全红线、按需入口 | [`AGENTS.md`](../../../AGENTS.md) |
| Claude 入口 | 导入共享契约并提供按需路由 | [`CLAUDE.md`](../../../CLAUDE.md) |
| 完整设计 | 模块、协议、开发与运维细节 | [`docs/DOC-MAP.md`](../../DOC-MAP.md) |
| Harness 适配 | 加载路由、权限、钩子和工具特有格式 | 下表 |

Harness 适配层不得复制易变化的项目事实。根入口也不得重新积累领域细节；需要时应
指向上述按需文档或对应目录内的领域文档。

## 仓库内适配面

| Harness | 受版本控制的入口 | 当前职责 |
|---|---|---|
| Cursor | [`.cursor/rules/*.mdc`](../../../.cursor/rules/) | 常驻入口和按路径引导；格式见 [`cursor-rules.md`](../cursor-rules.md) |
| Claude Code | 根及目录内 `CLAUDE.md`、`.claude/settings.json`、`.claude/skills/` | 架构入口、领域上下文、权限和显式技能 |
| Codex | `AGENTS.md`、`.codex/hooks.json` | 共享约定入口和确定性检查钩子 |
| OpenCode | `AGENTS.md`；本地 `opencode.json` 不入库 | 共享约定入口；provider、模型和凭据属于本机配置 |
| Antigravity CLI | `AGENTS.md` | 当前没有专用的受版本控制适配文件 |
| 其他 Harness | `AGENTS.md` | 没有专用适配时，从共享约定和文档地图进入 |

Harness 的自动发现规则会随版本变化。新增专用适配前必须用对应版本实测加载行为；
不能仅凭文件名推断规则已经生效。

## 本地配置边界

- `opencode.json`、`.claude/settings.local.json`、嵌套 `.claude/plan/` 及 Harness
  缓存属于本地状态；
- provider API key、token、账号和个人模型配置不得复制进文档、规则或提交记录；
- 项目需要共享的安全限制、技能或钩子必须使用 `.gitignore` 明确放行的专用文件；
- 本地配置不能成为项目行为或架构约束的唯一来源。

## 修改顺序

1. 先确认代码、测试或权威文档中的现状；
2. 修改 `AGENTS.md`、`CLAUDE.md` 或对应领域文档；
3. 仅在 Harness 需要加载路由或专用格式时同步薄适配；
4. 运行治理面结构检查，确认链接、frontmatter 和门禁清单没有漂移。

`AGENTS.md`、`CLAUDE.md` 与 Harness 适配文件属于共享元文件，同一时间只由一个
Execution 串行修改。现有并行开发语义仍以
[`2026-09-04-multi-agent-parallel-convention.md`](../../notes/process/2026-09-04-multi-agent-parallel-convention.md)
为准，直至新的 ADR 正式取代它。
