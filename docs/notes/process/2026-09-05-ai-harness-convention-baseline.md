# AI Harness 约定基线整理

Status: implemented
Class: process

## Decision

在新的 AI Coding Execution ADR 之前，先完成 Harness 约定与常驻上下文整理：

- `AGENTS.md` 降为所有 Harness 的最小启动契约，只保留总原则、跨 Harness 硬不变量、
  安全红线和按需入口；
- `CLAUDE.md` 只导入最小 `AGENTS.md` 并提供按需路由，不再独占硬不变量或导入
  `docs/DOC-MAP.md`；
- 依赖、测试、PR/CI、生产诊断、脚本版本和 scan/upload/merge 细节迁入按需文档；
- `docs/development/ai/harness-adapters.md` 登记各 Harness 的受控入口、本地配置边界
  与修改顺序；
- `.cursor/rules/*.mdc` 收敛为按路径加载路由，不再复制易变化的命令参数、环境变量
  默认值、状态机和实现摘要；
- Claude 权限、Skills、Codex hooks 保持原有行为；OpenCode provider、模型和凭据配置
  继续作为本机状态排除在 Git 外。
- `frontend/.claude/plan/` 中的一次性布局计划移入 `docs/archive/plans/`，并统一忽略
  嵌套 `.claude/plan/`，避免会话草稿再次被误当成项目规则。
- 治理门禁将根入口与 Harness 适配预算改为阻塞约束：AGENTS ≤80 行/8KB、
  CLAUDE ≤60 行/6KB、每个 Cursor rule ≤30 行/3KB，并为 Harness 总索引和 scoped
  CLAUDE 设置独立预算；CLAUDE 只能 `@import` AGENTS，根文件章节使用启动级白名单。
- 提交前只报告实际运行的验证与结果，pending 不冒充通过；Agent Note 新建前检查
  supersession，2026-09-05 起的新 note 由门禁校验 Status/Class 与目录一致。

本次整理不引入 Role、Scope、Registry 或新的并行执行状态。现有并行开发语义仍由
`2026-09-04-multi-agent-parallel-convention.md` 决定，直至后续 ADR 正式取代。

涉及文件：

- `AGENTS.md`、`CLAUDE.md`、`.gitignore`、`tests/test_local_artifacts_gitignore.py`
- `.cursor/rules/*.mdc`
- `.claude/skills/control-plane-deploy/SKILL.md`
- `backend/agent/CLAUDE.md`
- `docs/development/cursor-rules.md`
- `docs/development/ai/harness-adapters.md`
- `docs/development/{dependencies-and-quality,repository-workflow,script-versioning}.md`
- `docs/design/2026-scan-upload-merge-contract.md`
- `docs/operations/{production-diagnostics,device-lease-emergency-release}.md`
- `docs/README.md`
- `docs/archive/plans/frontend-layout-optimization.md`
- `tools/dev/check_governance_surface.py`、`tools/dev/gov_evals_cases.yaml`
- `docs/design/2026-08-governance-surface-protection.md`

## Alternatives

- **先写新 ADR，再整理 Harness 入口**：放弃。现有适配层已有重复和漂移，直接传播
  新契约会把旧问题带入更多入口。
- **在每个 Harness 配置中复制完整项目规范**：放弃。工具格式不同不等于项目事实
  应有多份副本；复制会继续产生第二事实源。
- **删除所有按域 Cursor rules**：放弃。按路径提示应读取哪些领域文档仍有价值，
  问题在于复制易变化事实，不在加载路由本身。
- **继续把 scan/merge、生产访问和 CI 细节常驻在 AGENTS**：放弃。此前
  `RESIDENT_CONTEXT_AUDIT_2026-08-27.md` 的 C1–C4 保留裁决适用于低并发单 Harness；
  多 Harness 每次启动重复加载时，注意力成本高于按需读取成本。本决定取代其中关于
  根文件体量的保留结论，不改变领域契约本身。
- **顺便调整 Claude 权限或 Codex hooks**：放弃。这会改变工具行为，超出语义保持
  的 Phase -1 范围。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --check`
- `venv/bin/python tools/dev/check_governance_surface.py --self-test`
- `venv/bin/python tools/dev/run_gov_evals.py --self-test`
- `venv/bin/python -m pytest tests/test_local_artifacts_gitignore.py -q`
- `venv/bin/python scripts/run_gates.py check:quick`
- 检查 `.cursor/rules/*.mdc` frontmatter 与文档链接；
- 检查 Git diff，确认没有 Role、Scope、Registry 或新状态语义。

审计时本机可用 Harness 版本为 Claude Code 2.1.259、Codex CLI 0.153.0、
OpenCode 1.18.25、Cursor Agent 2026.09.02；Antigravity CLI 未安装，因此只登记
“没有仓库专用适配”，不宣称已验证其自动发现行为。

整理后常驻入口实测：AGENTS 63 行/3815B、CLAUDE 26 行/978B、Cursor alwaysApply
规则 15 行/611B，合计 104 行/5404B。硬不变量在不增加总常驻体量的前提下对所有
Harness 可见；领域细节保留在按需文档，不以删除知识换体积。

治理行为 eval 的基线尝试使用 `python3 tools/dev/run_gov_evals.py`；本机 Claude CLI
所有调用均在返回答案前退出，因此该结果只能标记为运行环境不可用，不能作为文档
行为回归结论。

## Revisit

- Harness 升级改变项目规则自动发现或 hooks/settings 格式时；
- 新增受版本控制的 OpenCode、Antigravity 或其他 Harness 专用适配时；
- 后续 ADR 建立唯一 Execution Contract 后，需把本文的入口矩阵接到该权威文档，
  但仍不得在各适配层复制完整契约。
