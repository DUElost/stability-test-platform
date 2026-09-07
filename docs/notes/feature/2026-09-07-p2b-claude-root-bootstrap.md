# P2b：Claude 根 bootstrap 供给（wrapper 注入）与加载矩阵终验

Status: implemented
Class: feature

## Decision

ADR-0034 §2.7 P2 的根供给方案落地（#857 的可用缓解；上游 import 解析缺陷本体仍开放）：

1. **根供给 = `tools/dev/claude_with_root.sh`（调用方注入，已验证）**：wrapper 固定 `claude -p --append-system-prompt "$(cat $(git rev-parse --show-toplevel)/AGENTS.md)"`——子目录 cwd 下双题探针 **Q1（根契约）/Q2（scoped）双绿**（2026-09-07 实测）。这是当前环境唯一可验证的根供给方式；
2. **SessionStart hook 方案被否决（本环境）**：实测本机 tinno 构建的 Claude（-p 与 TUI 双模式，标记文件诊断）**完全不执行 hooks**——按「仓库只写已验证现状」纪律不加死配置；hook 作为 stock 构建候选留档于本 Note 与 #857；
3. **scoped 真身头部加根指针（纵深防御）**：`backend/agent/AGENTS.md` 头部注明「根启动契约住仓库根 `AGENTS.md`，改共享层前先读它」——经 symlink 薄壳进入 Claude 子目录上下文，与 wrapper 互补；
4. **Cursor 双份加载去重评估（结论：接受现状）**：Cursor 同时加载 AGENTS.md 真身与 CLAUDE.md symlink（Q3=两次，G2 补测）；Cursor 无针对 rules 文件发现的文件级排除机制可配置，scoped 体量小（1.2/4.8KB）且属上游加载行为——不改形态，维持 G2 既定处置；
5. **加载矩阵终验（P2 验收项）**：汇总三源实测构成完整矩阵——
   - 根契约可见：根 cwd 全 4 家 ✓（09-06 矩阵）；子目录 cwd：Codex/Cursor/OpenCode ✓（G2 验收 Q1）、Claude ✗→**经 wrapper ✓**（本 Note）；
   - scoped 真身可见（子目录 cwd）：全 4 家 ✓（G2 验收 4/4）；
   - **结论：根 bootstrap + scoped 内容双边可见在全部四家达成**（Claude 经 wrapper）。

## Alternatives

- **SessionStart hook（settings.json）**——放弃（本环境）：标记文件实测 -p 与 TUI 均不执行 hooks；未验证配置不入库。
- **wrapper 内嵌完整根内容快照**——放弃：快照必与真身漂移；运行时 `cat` 恒取最新。
- **改根层为 symlink 全局方案**——不重议：ADR §4 已裁（S8 锁 import；待 #857 上游修复后独立评估）。

## Verification

- wrapper 探针：`backend/agent` cwd 下 `claude_with_root.sh` 双题 **Q1=是 Q2=是**（实测两次一致）；
- hook 否决证据：标记文件诊断 -p/TUI 均未触发；settings.json 已恢复原状（仅 permissions.deny）；
- 治理门禁 S1–S11+S5x 全绿；scoped 真身 17 行（S6 预算内）；shell 脚本语法 sh -n 通过；
- pending：stock Claude Code 构建（非 tinno 代理）下 hook 与 import 行为复验——随 #857 上游确认。

## Revisit

- #857 上游修复后：根层 import 恢复即退役 wrapper 的注入分支（保留透传形态）；
- P2 收尾后进入 P3 drift gate（status 的 drift/overlap 输出作 advisory 数据源）。
