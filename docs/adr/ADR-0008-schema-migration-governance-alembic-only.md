# ADR-0008: 统一 Schema 迁移治理（Alembic Only）
- 状态：Accepted
- 优先级：P0
- 目标里程碑：M1
- 日期：2026-02-18
- 接受日期：2026-03-24
- 决策者：平台研发组
- 标签：数据库迁移, Alembic, 无畏重构

## 背景

当前存在三套并行行为：

- `Base.metadata.create_all`
- 启动时运行时 `ALTER TABLE` 补列
- Alembic 版本迁移

这会导致环境间 Schema 漂移和回溯困难，不利于持续演进。

## 决策

迁移治理统一为 Alembic 主导：

- 禁止在 `main.py` 中新增运行时 DDL。
- 禁止依赖 `create_all` 自动演进生产 Schema。
- 所有结构变更通过 Alembic 脚本管理并可回滚。
- 启动阶段仅做"版本检查与告警"，不做结构写入。

## 备选方案与权衡

- 方案 A：保持现状（多通道并存）。
  - 优点：短期改动少。
  - 缺点：长期数据一致性风险高。
- 方案 B：一次性强制切换 Alembic。
  - 优点：治理清晰。
  - 缺点：需要梳理历史差异并补齐迁移脚本。

## 影响

- 正向影响：Schema 可追溯、可审计、可回滚。
- 代价：迁移脚本编写成本上升，CI 需要增加迁移校验。

## 落地与后续动作

| 步骤 | 内容 | 状态 | 备注 |
|------|------|------|------|
| 第一步 | 冻结运行时 DDL 新增 | **已完成** | `main.py` 已移除 `create_all` 和 `ALTER TABLE`（commit 6befa34） |
| 第二步 | 补齐现有表结构到 Alembic 版本 | **已完成** | 30+ 迁移文件覆盖全量表结构演进；`post_processed_at` 列已在 `JobInstance`（见 `backend/models/job.py:32`） |
| 第三步 | CI 增加"迁移后模型一致性检查" | **已完成** | 双轨合并 Wave 7+8 完成后（2026-04-12），所有模型已统一为独立模块，`schemas.py` 已删除，一致性已验证 |

## 已知问题：ORM 模型双轨并行

> **解决 (2026-04-12)**：双轨并行已随合并 Wave 7+8 完成而彻底解决。`backend/models/schemas.py` 已删除，所有 ORM 模型已拆分为独立模块（host / plan / plan_run / job / user / audit / script 等）。前端也已切换到 `api.orchestration` / `api.execution` / `api.logs` 命名空间。原 FROZEN Phase 1 迁移已被 ADR-0020 的 5 阶段一次性切换替代。

<details>
<summary>双轨并行历史记录（2026-03-24 至 2026-04-12）</summary>

应用代码曾同时使用两套 ORM 模型：新模型（单数表名）和旧模型（`schemas.py`，复数表名）。Phase 1 迁移 `a1b2c3d4e5f6` 因会 DROP 旧表但旧模型仍被 20+ 文件引用而被标记为 FROZEN。后经 Wave 3a（recycler / report_service 迁移）、Wave 7+8（前端全量迁移 + tasks.py 拆分 + schemas.py 删除）逐步解决。

</details>

## Wave 3a 已完成项（2026-03-24）

### Recycler 迁移至 JobInstance

`backend/scheduler/recycler.py` 已完全重写，移除所有 `Task`/`TaskRun`/`LogArtifact` 依赖：

| 变更项 | 旧实现 | 新实现 |
|--------|--------|--------|
| 超时对象 | `TaskRun` (DISPATCHED/RUNNING) | `JobInstance` (PENDING/RUNNING) |
| 状态转换 | 直接 `run.status = FAILED` | `JobStateMachine.transition()` 含两步转换 |
| 设备锁释放 | `release_lock_sync(db, device_id, run.id)` | 同（run.id → job.id） |
| Host/Device 超时 | recycler 自行检查（可选跳过） | 完全移除，由 session_watchdog 独占处理 |
| Artifact 清理 | 删除 `LogArtifact` 行 + 物理文件 | 仅删除物理文件（StepTrace 行为审计记录，不删除） |
| Post-completion | 调用 `run_post_completion_async` | 主路径触发，`recycler` 仅做延迟补偿 |

Recycler 已在 `main.py` 启用（`start_recycler()`），与 session_watchdog 并行运行，查询的 status 值不重叠。

### Report Service 脱离旧模型

`backend/services/report_service.py` 已移除 `TaskRun` fallback 路径：

- 删除 `from backend.models.schemas import LogArtifact, Task, TaskRun`
- `compose_run_report()` 仅走 `JobInstance` → `_compose_job_report()` 路径
- `_load_risk_summary_from_artifacts()` 签名从 `List[LogArtifact]` 改为 `list`（duck-typing）

### Orchestration 端点预留

`backend/api/routes/orchestration.py` 新增 3 个 501 stub 端点：

| 端点 | 用途 | 替代的旧端点 |
|------|------|-------------|
| `GET /workflow-runs/{run_id}/jobs/{job_id}/report` | 单 Job 报告 | `GET /runs/{run_id}/report` |
| `POST /workflow-runs/{run_id}/jobs/{job_id}/jira-draft` | Job JIRA 草稿 | `POST /runs/{run_id}/jira-draft` |
| `GET /workflow-runs/{run_id}/summary` | Workflow 聚合概览 | 无（新增） |

旧端点暂时保留作为 interim 通道，待新端点实现后移除。

## Post-Completion Pipeline 迁移（已完成主链路）

> **状态**：主链路已落地，补偿边界已收敛
> **完成日期**：2026-04-07

### 当前实现

`JobInstance` 已具备报告缓存所需列：

- `report_json`
- `jira_draft_json`
- `post_processed_at`

`backend/services/post_completion.py` 已以 `JobInstance` 为事实源生成并缓存报告与 JIRA 草稿：

```python
job.report_json = report_dict
job.jira_draft_json = jira_draft_dict
job.post_processed_at = datetime.utcnow()
```

其中 `post_processed_at` 是 post-completion 的唯一幂等标记，用于避免重复生成报告、重复发通知或被补偿路径重复触发。

### 当前职责边界

- `agent_api.complete_job()` 是 Agent 终态收敛的主路径
  - 负责终态 transition、终态快照持久化、workflow 聚合、设备锁释放
  - 主路径完成后触发 `run_post_completion_async(job_id)`
- `MQ consumer` 属于补偿路径
  - 仅在自己实际完成终态推进时触发 post-completion
- `recycler` 不再直接触发 `run_post_completion_async(job.id)`
  - 只负责超时场景下的状态补偿、聚合、锁释放和 WS 广播
  - 对于终态后仍未完成 post-processing 的 Job，基于 `post_processed_at IS NULL` 在宽限期后执行延迟补偿

### 治理约束

- `agent_api.complete_job` 是 Agent 终态后的主处理入口
- `post_processed_at` 是 post-completion 的唯一幂等标记
- `recycler`、`watchdog`、`MQ consumer` 均属于补偿路径，不得绕过状态机强制写终态
- 补偿路径只有在自己实际完成状态推进时，才允许触发后续副作用

## 关联实现/文档

- `backend/main.py` — 已移除运行时 DDL
- `backend/alembic/env.py`
- `backend/alembic/versions/` — 30+ 迁移文件覆盖全量 schema 演进（含 ADR-0020 Plan/PlanRun 切换、ADR-0019 DeviceLease、ADR-0022 patrol heartbeat 等）
- ~~`backend/models/schemas.py`~~ — 旧模型（已删除，双轨合并 Wave 7+8）
- `backend/models/host.py` / `plan.py` / `plan_run.py` / `job.py` / `script.py` — 各领域独立模块（替代旧 workflow.py / tool.py）
- ~~`backend/models/workflow.py`~~ — 已删除（ADR-0020）
- ~~`backend/models/tool.py`~~ — 已删除（tool catalog 移除）
- `docs/production-minimum-deployment-checklist.md`
