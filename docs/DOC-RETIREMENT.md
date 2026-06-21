# 文档退役与归档清单

> **最后更新**：2026-06-21  
> 目的：标记**已完成迁移**或**一次性**文档，便于后续删除或移入 `docs/archive/`，避免与权威文档双轨。

**操作前**：确认内容已并入 `docs/design/`、`docs/acceptance/`、`CLAUDE.md` 摘要或 ADR。

---

## 图例

| 状态 | 含义 |
|------|------|
| **可删** | 内容已 superseded，删除无信息损失 |
| **移 archive** | 有历史价值，移入 `docs/archive/` |
| **保留** | 仍为权威或活跃跟踪 |
| **待合并** | 精华未完全迁入 design/acceptance |

---

## 1. 已 superseded — 已删除（2026-06-21 批次）

| 路径 | 原因 | 替代 |
|------|------|------|
| ~~`docs/adr-0025-sprint2-log-archiver-implementation-plan-2026-06-15.md`~~ | tar 模式 LogArchiver，方案 C 已作废 | `design/2026-plan-c-storage-and-access.md` |
| ~~`docs/2026-04-25-idempotent-wandering-lightning.md`~~ | 与 `superpowers/plans/` 重复 | 已完成的 UI 计划可删 |
| ~~`docs/2026-04-26-idempotent-wandering-lightning.md`~~ | 同上 | 同上 |
| ~~`docs/stability-platform-integrated.md`~~ | 2026-04 旧整合稿，模型已变 | `design/00-system-overview.md` |
| ~~`docs/stability-test-platform.md`~~ | 同上 | 同上 |

---

## 2. 一次性 Sprint 计划 — 完工后移 archive 或删

| 路径 | 状态 | 替代 |
|------|------|------|
| `docs/superpowers/plans/2026-06-20-sprint2-watcher-hdd-logarchiver.md` | Sprint 2 进行中 | `design/2026-plan-c-*` + #32 |
| `docs/adr-0025-watcher-catchup-implementation-plan-2026-06-14.md` | Sprint 1 已落地 | ADR-0025 D5 + agent tests |
| `docs/superpowers/plans/2026-06-05-plan-run-detail-redesign.md` | C5 已落地 | `design/03-frontend.md` |
| `docs/superpowers/plans/2026-04-25-editor-execution-ui-redesign.md` | 已落地 | `design/03-frontend.md` |
| `docs/superpowers/plans/2026-05-15-code-review-fixes.md` | 一次性 | git history |
| `docs/superpowers/plans/2026-05-06-non-adr20-architecture-debt.md` | 债务快照 | `architecture/non-adr20-followups.md` |
| `docs/plans/watcher-consolidate-aee-2026-05-27*.md` | 已并入 reconciler | `design/04-agent.md` |

---

## 3. 评估/健康度快照 — 已移 `docs/archive/assessments/`（2026-06-21）

| 路径 | 日期 | 说明 |
|------|------|------|
| ~~`docs/main-chain-fragility-analysis-2026-05-23.md`~~ | 2026-05 | → `archive/assessments/` |
| ~~`docs/main-chain-remaining-work-implementation-plan-2026-05-25.md`~~ | 2026-05 | → `archive/assessments/` |
| ~~`docs/project-health-and-remaining-work-2026-05.md`~~ | 2026-05 | → `archive/assessments/` |
| ~~`docs/production-readiness-assessment-2026-05-23.md`~~ | 2026-05 | → `archive/assessments/` |
| `docs/architecture-five-dimensional-assessment-2026-06.md` | 2026-06 | **暂保留** — ADR-0025 背景引用 |

---

## 4. 已归档目录（勿作实现依据）

| 路径 | 说明 |
|------|------|
| `docs/archive/stp-spec-pre-adr0020/` | Workflow/Tool 旧 spec（2026-05-07 归档） |
| `docs/archive/dual-track-merger-v3.revised.md` | 双轨合并完成记录 |
| `docs/archive/implementation-plan-adr0018*.md` | ADR-0018 实施评估 |
| `openspec/` | 含 builtin/Workflow；见 [`openspec/README.md`](../openspec/README.md) |

---

## 5. 待合并后退役

| 路径 | 待并入 |
|------|--------|
| `docs/adr-0025-dedup-integration-design-2026-06-16.md` | `design/01-execution-pipeline.md` §dedup + Sprint 4 design |
| `docs/plan-block-step-migration.md` | ADR-0020 + `design/05-data-model.md` |
| `docs/plan-step-design-rationale.md` | `prd/00-platform-overview.md` 或 design §Plan |

---

## 6. 当前权威文档（勿删）

- `docs/README.md`、`DOC-MAP.md`、本文件  
- `docs/adr/`（Accepted ADR）  
- `docs/prd/`、`docs/design/`、`docs/acceptance/`  
- `docs/development/`、`docs/operations/`  
- `docs/project-vision.md`  
- `docs/production-minimum-deployment-checklist.md`、`preprod-drill-runbook.md`  
- `docs/linux-agent-ansible-runbook.md`、`wsl-linux-agent-setup.md`、`host-connectivity-verification.md`  
- `backend/agent/DEPLOY.md`  
- 根 `README.md`、`AGENTS.md`、`CLAUDE.md`

---

## 7. 退役流程（建议）

1. 在 PR 描述中引用本表行号  
2. 删除或 `git mv` 到 `docs/archive/`  
3. 更新 `DOC-MAP.md`、`README.md` 中死链  
4. 若 GitHub Issue 仍链旧路径，改链新文档
