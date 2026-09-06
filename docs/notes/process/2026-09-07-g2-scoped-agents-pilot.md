# G2 试点：scoped AGENTS.md 真身 + symlink 薄壳

Status: implemented
Class: process

## Decision

执行 ADR-0034 §3 的 G2 迁移试点（`backend/agent/` → `backend/agent/aee/`）：

1. `backend/agent/CLAUDE.md` → **`backend/agent/AGENTS.md`（真身，内容中立）**；`CLAUDE.md` 变为指向真身的 **git symlink（mode 120000）**——真身只此一份，Claude 自动加载链经薄壳读到同一内容；
2. `backend/agent/aee/CLAUDE.md` 同样处理；
3. 真身内自引用 `aee/CLAUDE.md` → `aee/AGENTS.md`；外部引用同步：`2026-scan-upload-merge-contract.md:54`、`2026-07-aee-reconciler-mtk-signoff.md`（历史留档的路径引用，保导航）；
4. checker：S6 `RESIDENT_BUDGETS` 与 S2 `link_files` 条目改为真身路径（symlink 薄壳不单列——读同一内容，双列无意义）；根 `AGENTS.md`「开始任务时」第 2 条措辞更新为「scoped `AGENTS.md`（Claude 侧为其 symlink 薄壳）」。

**验收探针**（cwd=`backend/agent/`，禁用工具双题：Q1=根契约『## 总原则/## 提交前』可见性、Q2=scoped 真身『Agent 侧 scan / upload』标题可见性）：

| Harness | Q1 根契约 | Q2 scoped 真身 | 结论 |
|---|---|---|---|
| OpenCode 1.18.25 | 是 | **是** | ✅ 根+scoped 双边可见（逐级发现） |
| Claude Code 2.1.259 | 否（#857 已知） | **是** | ✅ scoped 经 symlink 薄壳可见——迁移前 Claude 子目录 scoped 供给为零，本迁移补上 |
| Cursor Agent 2026.09.02（补测） | 是 | **是** | ✅ 根+scoped 双边可见；**Q3=两次：Cursor 同时加载 AGENTS.md 真身与 CLAUDE.md symlink，同一内容双份入上下文**（见下方新发现） |
| Codex 0.153.0 | pending | pending | 5h 限额（01:20 重置）；机制=上午 4/4 矩阵实测的嵌套 AGENTS.md 发现，同构 |

本迁移对 **aee/** 的同构扩展（真身+薄壳+checker 条目）已一并落地，探针未单独重跑（同目录同机制）。

**补测新发现（2026-09-07，Cursor）**：`AGENTS.md` 真身与 `CLAUDE.md` symlink 并存时，Cursor **双份加载**同一内容（Q3=两次）——即 anthropics/claude-code#6235 讨论的 symlink 去重问题形态。评估：scoped 真身体量小（agent 1.2KB / aee 4.8KB），双份 token 代价有限且属上游工具加载行为（deepseek 上游同构形态同样并存）；Claude 只读薄壳（单份）不受影响。处置=记录不改形态：P2 Adapter 设计时评估各 Harness 是否支持排除重复文件（如 Cursor rules/ignore 配置），或将薄壳改「最小占位」需先解决 Claude 内容传递（依赖 #857 修复），本试点维持 symlink。

## Alternatives

- **真身 + `@import` 薄壳**——放弃：#857 实证 @import 子目录不解析（-p 与 TUI 双模式）；symlink 文件系统层生效与 cwd 无关。
- **薄壳单独内容而非 symlink**——放弃：双份内容必漂移，正是治理面要防的形态；上游 deepseek-harness 同构先例。
- **只迁 backend/agent 不动 aee**——放弃：aee 是同目录树下紧邻 scope，同构动作一次做完成本最低。
- **历史 acceptance 文档不改引用**——放弃：路径改名后留旧引用会误导导航；只改路径引用、不改历史结论。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --check`：S1–S11+S5x 全绿（S6 校验真身 14 行/1.2KB 与 aee 71 行、S2 校验真身内链接）；`--self-test` 12 规则全绿；`ruff` 通过；
- `git ls-files -s`：真身 mode 100644、薄壳 mode 120000（symlink）✓；
- 验收探针实测见上表（OpenCode/Claude live 双绿；Codex/Cursor pending 如实标注）；
- pending：Codex 限额恢复后补测（探针协议与命令见 ADR 附录 A）；PR CI 六项 required checks 以实际运行为准。

## Revisit

- Codex/Cursor 补测：任一会话在限额恢复后用同协议在 `backend/agent/` 重跑（10 分钟内）；
- P2 Adapter：根 bootstrap 供给方案（Claude 子目录根契约不可见，#857）——Adapter 就绪时解决；
- 试点验收后按 ADR §3 评估是否推广至其他需要 scoped 上下文的目录。
