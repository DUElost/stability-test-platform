# #857 仓库侧绕过：根 CLAUDE.md 转 AGENTS.md symlink（G2 上移）

Status: implemented
Class: process

## Decision

根 `CLAUDE.md` 从「@AGENTS.md import 薄壳」转为**指向 AGENTS.md 的 symlink**
（G2 真身+薄壳决策从 scoped 层上移到根）。动机：#857 是 Claude Code 上游缺陷
（ancestor CLAUDE.md 的 @import 仅 cwd 级生效，子目录会话拿不到根契约）——
symlink 形态内容直读，@import 通道从仓库**完全消失**，该缺陷在任何启动形态
（根/子目录 × TUI/-p）下都无法命中。

配套：

- **AGENTS.md 按需入口 +4 行**（原 CLAUDE.md 私有的路由指针并入：执行状态机
  07-execution-protocol / 存储角色 / 环境变量清单 / ADR 状态——symlink 不能
  带私货），66→70 行仍在 S6 预算（80）内；
- **S8 门禁改双形态**（`check_claude_entry_form`）：指向 AGENTS.md 的
  symlink（零 import）或经典「恰含 @AGENTS.md import」二选一合法；symlink
  指错真身报错；CLAUDE.md 为 symlink 时 S6/S9 豁免（内容即 AGENTS.md，由
  其同名检查覆盖，不按 CLAUDE.md 更紧预算重复计）；
- **adapters 文档**：`claude_with_root.sh` 降级为后备（tinno 构建异常/
  symlink 失效时）；#857 保持 open 作**上游哨兵**（探针对照行监测 @import
  机制本身，与仓库供给解耦）。

## Alternatives

- **维持现状（wrapper + 指引）**：可用但子目录 TUI 不走指引时根契约缺失，
  且仓库仍有一条路径依赖上游修复；
- **SessionStart hook 注入**：已留档否决（tinno 构建不执行 hooks）；
- **根 CLAUDE.md 复制全文（copy 而非 symlink）**：双份事实源，S11 锚点与
  S6 预算的治理对象分裂，违背薄壳裁决；
- **等上游修复**：被动；symlink 形态在修复后依然合法（双形态门禁），无返工。

## Verification

- **实测（#857 场景活体）**：`backend/` 子目录裸跑 `claude -p`（无 wrapper、
  无 --append-system-prompt）→ 准确复述 AGENTS.md 硬不变量第一条
  （socketio.ASGIApp 单点组合）并引用 `../AGENTS.md`——ancestor symlink
  加载送达完整根契约；
- `check_governance_surface.py --self-test` 13 规则全绿（S8 新增 3 样例：
  symlink 合法/指错真身/双形态互斥）；
- `--check` 全绿（S8 双形态接受 symlink、S6/S9 豁免生效、S1/S2 对
  symlink 内容正常）；git 以 mode 120000 记录 typechange；
- `check:quick` 全绿；AGENTS.md 70/80 行预算内。

## Revisit

- 探针 `claude-subdir-plain` 对照行若报「上游已修复」（@import 机制恢复），
  可评估是否回归经典 import 形态（预期：不回归——symlink 已验证且更简），
  届时 #857 可关；
- Windows/不支持 symlink 的 checkout：git 默认落为含路径文本的普通文件，
  行为等价于旧 @import 形态的降级——若成为实际工作环境再议
  （scoped 层 symlink 已有同类先例）；
- wrapper 退役：连续批次无 fallback 命中后可删除 `claude_with_root.sh`。
