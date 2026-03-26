# ADR-0005: 数据库策略（已废弃）
- 状态：Deprecated
- 日期：2026-02-18（2026-03-25 更新）
- 决策者：平台研发组
- 标签：数据库, 兼容层, 迁移策略

> **本 ADR 已废弃**：项目已确定使用 PostgreSQL 作为唯一数据库。
> 原 `database_adapter.py` 兼容层已于 2026-03-25 删除，详见下方清理记录。

## 背景（已废弃）

平台需在 MVP 快速可用与生产可扩展之间取得平衡：本地/小规模部署需要低门槛，生产并发又需要更强数据库能力。

## 决策（已废弃）

采用"SQLite 默认 + PostgreSQL 就绪"的双栈策略：

- 默认 `DATABASE_URL` 指向 SQLite，降低部署门槛。
- 通过 `database_adapter` 抽象方言差异：
  - PostgreSQL 支持 `FOR UPDATE SKIP LOCKED`
  - SQLite 退化为事务隔离 + WAL 模式
- 模型层统一 SQLAlchemy，保留 Alembic 迁移框架。

## 备选方案与权衡

- 方案 A：一开始强制 PostgreSQL。
  - 优点：生产能力更强，行为更一致。
  - 缺点：本地启动成本高，验证门槛高。
- 方案 B：当前方案（SQLite 起步 + PG 兼容）。
  - 优点：MVP 落地快，迁移路径清晰。
  - 缺点：存在方言差异与迁移治理复杂度。

## 影响

- 正向影响：兼顾开发效率与生产演进。
- 风险：当前存在 `create_all + 运行时补列 + Alembic` 共存，Schema 治理边界不清。

## 落地与后续动作

- ~~已落地：数据库适配器、PG 连接池配置、SQLite WAL 优化。~~
- 后续：见 `ADR-0008`，统一迁移治理为 Alembic 主导。

## 2026-03-25 清理记录

项目已全面切换到 PostgreSQL，原双栈兼容层不再有存在意义，执行以下清理：

| 操作 | 说明 |
|------|------|
| 删除 `backend/core/database_adapter.py` | SQLite/PostgreSQL 适配器抽象，无任何调用方 |
| 更新 `backend/scheduler/dispatcher.py` | 移除 adapter 导入，改用 SQLAlchemy 原生 `with_for_update(skip_locked=True)` |
| 更新 `backend/tests/concurrent/test_dispatcher.py` | 移除 adapter 引用，skip_locked 测试直接面向 PostgreSQL |

## 关联实现/文档

- `backend/core/database.py`
- ~~`backend/core/database_adapter.py`~~（已删除）
- `backend/main.py`
- `backend/alembic/env.py`
- `deploy/postgres/docker-compose.yml`
- `docs/production-minimum-deployment-checklist.md`
- [`ADR-0008`](./ADR-0008-schema-migration-governance-alembic-only.md) - 统一迁移治理
