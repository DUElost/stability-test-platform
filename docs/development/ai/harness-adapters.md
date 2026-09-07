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
| Antigravity CLI | 无（`agy 1.1.26 -p` 实测不自动发现任何仓库规则文件） | 规则供给走调用方前置 `tools/dev/agy_with_rules.sh`（2026-09-07 实测，见下） |
| 其他 Harness | `AGENTS.md` | 没有专用适配时，从共享约定和文档地图进入 |

Harness 的自动发现规则会随版本变化。新增专用适配前必须用对应版本实测加载行为；
不能仅凭文件名推断规则已经生效。

**Antigravity CLI 实测（2026-09-07，`agy -p` 非交互）**：根与嵌套 `AGENTS.md`、
`CLAUDE.md`（含 symlink）、`GEMINI.md` 均不自动加载（随机探针串+引文诊断+运行日志
+stream-json init 四重证据；`agy agents` 空、无全局配置目录）——该 Harness 无仓库
文件的自动加载通道，规则供给用 `tools/dev/agy_with_rules.sh "<prompt>"`（根规则拼
prompt 前缀）。机制层：规则装载走 `declarative_config_loader.go` 的
`prompt section "user_rules"`（`~/.gemini/antigravity-cli/settings.json` 无此字段，
空被 skip）——装载清单里没有约定文件通道，非路径问题。

**分层装载实测（2026-09-07，`-p` 模式）**：**全局层生效、workspace 层不装载**——
在 `~/.gemini/GEMINI.md` 与 `~/.gemini/AGENTS.md` 放唯一探针串后（可逆实验，测后
删除），agy `-p` 会话上下文**两个都出现**；而 workspace 层（信任/非信任路径 ×
仓库根/嵌套的 `AGENTS.md`/`GEMINI.md`/`CLAUDE.md` symlink）仍全部不装载。[官方迁移
文档](https://antigravity.google/docs/cli/gcli-migration/) 声称会解析 active
directory 的 `GEMINI.md`/`AGENTS.md`——与 workspace 层实测冲突，待上游确认。
**冲突澄清前，仓库规则供给一律走 `agy_with_rules.sh` 前置**；全局层通道属
**本机个人配置**（影响所有 agy 会话与所有项目），不放仓库规则（本地配置边界）。另：**headless（-p）模式未 allow 的工具一律 auto-deny**（报错原文
指引 `permissions.allow` 机制）；`--dangerously-skip-permissions` 在本构建触发
Agent execution terminated——作为 coding agent 使用需先在**本机** settings.json
配置 `permissions.allow`（本机配置边界，不入库），并注意该模式本构建稳定性存疑。

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
4. 涉及并行 Execution（Registry 登记、scope 声明、集成窗口）时，按
   [`execution-contract.md`](execution-contract.md) 执行；
5. 运行治理面结构检查，确认链接、frontmatter 和门禁清单没有漂移。

`AGENTS.md`、`CLAUDE.md` 与 Harness 适配文件属于共享元文件，同一时间只由一个
Execution 串行修改。并行执行语义的权威源是
[`ADR-0034`](../../adr/ADR-0034-multi-harness-execution-contract.md)
（Accepted v1.0）与 [`execution-contract.md`](execution-contract.md)；
[`2026-09-04-multi-agent-parallel-convention.md`](../../notes/process/2026-09-04-multi-agent-parallel-convention.md)
已被取代，其元文件串行化与派生视图实践经契约 §9 过渡条款保留。

## P2 Adapter：会话启动动作（上下文供给，非路由）

会话由开发者选择启动（选择权原则）；Adapter 只负责让该会话**知晓自身 Execution
与集成窗口**。Registry CLI：`tools/dev/ai_work.py`（规范见
[`execution-contract.md`](execution-contract.md) §2–§5）。

| 时机 | 动作 | 所有 Harness 通用 |
|---|---|---|
| 会话启动（在 worktree 内） | `python tools/dev/ai_work.py whoami` | 输出自身 Execution 状态与**入向 overlap**（他人在窗记录覆盖本 worktree scope）；无记录则提示 declare |
| 编码中（长会话） | `python tools/dev/ai_work.py heartbeat --id <R>` | 纯心跳（= 无参 `update`）：刷自身 `last_seen` + GitHub reconcile；P2 起由 wrapper 定时调用，`last_seen` 据此升格为可靠 liveness 信号 |
| 开 PR / scope 变化 | `python tools/dev/ai_work.py update --id <R> --pr <N> [--scope ...]` | 登记 PR、派生 integration、覆写声明 |
| 编码停止 | `finish --id <R> --pr <N>` / `finish --id <R> --abandon` | 语义见契约 §3.3 transition table |

- `whoami`/`status` 严格只读（观察不改变被观察状态）；只有带 identity 的写命令
  （declare/update/finish）刷新自身 `last_seen`；
- 各 Harness 的自动加载差异（Codex/Cursor/OpenCode 读 scoped `AGENTS.md`；
  Claude 经 `CLAUDE.md` symlink 薄壳）见本文件上方适配面表与 ADR 附录 A；
  **Claude 子目录下根启动契约不自动加载（#857）**——非交互会话用
  `tools/dev/claude_with_root.sh "<prompt>"`（已验证的根供给 wrapper，
  2026-09-07 子目录双题探针 Q1/Q2 双绿）；交互 TUI 从仓库根启动，或遵循
  scoped 真身头部的根指针人工读根 `AGENTS.md`。
