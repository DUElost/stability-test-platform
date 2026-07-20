# 技术设计文档

与**当前代码**对齐的模块级设计。变更时请同步更新本目录及对应测试。

| 文档 | 内容 |
|------|------|
| [00-system-overview.md](./00-system-overview.md) | 部署拓扑、逻辑分层、领域摘要 |
| [01-execution-pipeline.md](./01-execution-pipeline.md) | Plan → PlanRun → Job 主链路（产品级流程） |
| [07-execution-protocol.md](./07-execution-protocol.md) | **硬契约**：状态机、abort ACK、snapshot、claim 门禁、schema |
| [02-backend.md](./02-backend.md) | 后端路由、服务、lifespan |
| [03-frontend.md](./03-frontend.md) | 路由、API Client、核心 UI |
| [04-agent.md](./04-agent.md) | Agent、Watcher、脚本、存储 |
| [05-data-model.md](./05-data-model.md) | PostgreSQL ORM、Agent SQLite |
| [06-realtime-and-background.md](./06-realtime-and-background.md) | SocketIO、APScheduler、SAQ |
| [2026-plan-c-storage-and-access.md](./2026-plan-c-storage-and-access.md) | ADR-0025 方案 C 存储与访问 |
| [2026-07-plan-execute-page-improvements.md](./2026-07-plan-execute-page-improvements.md) | Plan 执行页：Phase 1–6 + 走查债项迭代 A/B/C（§7 v2） |

返回：[文档中心](../README.md) · [DOC-MAP](../DOC-MAP.md)
