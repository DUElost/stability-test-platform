# 文档退役与归档清单

> **最后更新**：2026-06-21（第三批次）  
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

## 1. 已 superseded — 已删除（2026-06-21 批次一）

| 路径 | 原因 | 替代 |
|------|------|------|
| ~~`docs/adr-0025-sprint2-log-archiver-implementation-plan-2026-06-15.md`~~ | tar 模式 LogArchiver，方案 C 已作废 | `design/2026-plan-c-storage-and-access.md` |
| ~~`docs/2026-04-25-idempotent-wandering-lightning.md`~~ | 与 `superpowers/plans/` 重复 | 已完成的 UI 计划可删 |
| ~~`docs/2026-04-26-idempotent-wandering-lightning.md`~~ | 同上 | 同上 |
| ~~`docs/stability-platform-integrated.md`~~ | 2026-04 旧整合稿，模型已变 | `design/00-system-overview.md` |
| ~~`docs/stability-test-platform.md`~~ | 同上 | 同上 |

---

## 2. 一次性 Sprint 计划 — 已移 `docs/archive/sprints/`（2026-06-21 批次二）

| 路径 | 替代 |
|------|------|
| ~~`docs/superpowers/plans/*`~~ → `archive/sprints/plans/` | `docs/design/` |
| ~~`docs/superpowers/specs/*`~~ → `archive/sprints/specs/` | `docs/design/03-frontend.md` 等 |
| ~~`docs/adr-0025-watcher-catchup-implementation-plan-2026-06-14.md`~~ | ADR-0025 D5 + agent tests |
| ~~`docs/superpowers/plans/2026-05-15-code-review-fixes.md`~~ | **已删除**（git history） |
| ~~`docs/plans/watcher-consolidate-aee-2026-05-27*.md`~~ → `archive/plans/` | `design/04-agent.md` |

`docs/superpowers/` 仅保留 [`README.md`](./superpowers/README.md) 重定向说明。

---

## 3. 评估/健康度快照 — 已移 `docs/archive/assessments/`（批次一）

| 路径 | 说明 |
|------|------|
| ~~`docs/main-chain-*`~~、`project-health-*`、`production-readiness-*` | → `archive/assessments/` |
| `docs/architecture-five-dimensional-assessment-2026-06.md` | **暂保留** — ADR-0025 背景 |

---

## 4. 设计快照 — 已移 `docs/archive/migrations/`（批次二）

精华已并入 `design/01`、`design/05`：

| 路径 | 替代 |
|------|------|
| ~~`docs/adr-0025-dedup-integration-design-2026-06-16.md`~~ | `design/01-execution-pipeline.md` §8 |
| ~~`docs/plan-block-step-migration.md`~~ | ADR-0020 + `design/05-data-model.md` |
| ~~`docs/plan-step-design-rationale.md`~~ | `design/05-data-model.md` §Plan |

---

## 5. 已归档目录（勿作实现依据）

| 路径 | 说明 |
|------|------|
| `docs/archive/stp-spec-pre-adr0020/` | Workflow/Tool 旧 spec |
| `docs/archive/dual-track-merger-v3.revised.md` | 双轨合并记录 |
| ~~`docs/archive/implementation-plan-adr0018*.md`~~ | → `archive/assessments-adr0018/` |
| `docs/archive/sprints/` | Sprint 任务快照 |
| `docs/archive/migrations/` | Plan/dedup 设计快照 |
| `docs/archive/plans/` | 专项计划（watcher-consolidate） |
| `docs/archive/openspec/` | OpenSpec 历史 spec（根 `openspec/` 仅重定向 stub） |
| `docs/archive/prototypes/` | UI 原型 HTML/PNG |
| `docs/archive/assessments-adr0018/` | ADR-0018 实施评估 |

---

## 6. 当前权威文档（勿删）

- `docs/README.md`、`DOC-MAP.md`、本文件  
- `docs/adr/`（Accepted ADR）  
- `docs/prd/`、`docs/design/`、`docs/acceptance/`  
- `docs/development/`、`docs/operations/`  
- `docs/project-vision.md`  
- `docs/production-minimum-deployment-checklist.md`、`preprod-drill-runbook.md`  
- `docs/linux-agent-ansible-runbook.md`、`wsl-linux-agent-setup.md`、`host-connectivity-verification.md`  
- `docs/architecture/non-adr20-followups.md`  
- `backend/agent/DEPLOY.md`  
- 根 `README.md`、`AGENTS.md`、`CLAUDE.md`

---

## 7. OpenSpec / UI 原型 — 已移 archive（2026-06-21 批次三）

| 路径 | 说明 |
|------|------|
| ~~根 `openspec/`~~ → `archive/openspec/` | 根目录仅留 [`openspec/README.md`](../openspec/README.md) 重定向 |
| ~~`docs/prototypes/`~~、`~~docs/design/*.html`~~ → `archive/prototypes/` | UI 原型，非规范 |
| ~~`docs/archive/implementation-plan-adr0018*.md`~~ → `archive/assessments-adr0018/` | 仅考古 |

**第三批已全部完成**；后续无计划中的文档退役批次。

---

## 8. 退役流程

1. 在 PR 描述中引用本表  
2. 删除或 `git mv` 到 `docs/archive/`  
3. 更新 `DOC-MAP.md`、死链  
4. 更新 GitHub Issue 链接
