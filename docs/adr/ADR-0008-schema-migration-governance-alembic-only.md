# ADR-0008: 统一 Schema 迁移治理（Alembic Only）
- 状态：Proposed
- 优先级：P0
- 目标里程碑：M1
- 日期：2026-02-18
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
- 启动阶段仅做“版本检查与告警”，不做结构写入。

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

- 第一步：冻结运行时 DDL 新增。
- 第二步：补齐现有表结构到 Alembic 版本。
- 第三步：CI 增加“迁移后模型一致性检查”。

## 关联实现/文档

- `backend/main.py`
- `backend/alembic/env.py`
- `backend/alembic/versions/`
- `docs/production-minimum-deployment-checklist.md`
