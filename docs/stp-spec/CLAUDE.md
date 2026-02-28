# Stability Test Platform — Claude Code 开发规范入口

## 项目概述

分布式稳定性测试平台，支持 **40+ Linux Host Agent × 1000+ Android 设备** 规模。
核心设计原则：**声明式编排（静态蓝图）+ 分布式执行（动态运行）彻底解耦**。

## 文档导航

在开始任何模块开发前，必须先阅读对应的规范文档：

| 你要开发的内容 | 必读文档 |
|---|---|
| 整体架构、实体关系、状态机 | [`architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md) |
| Server 端 API、业务逻辑 | [`backend/BACKEND.md`](backend/BACKEND.md) |
| 数据库 Schema、迁移规范 | [`backend/DATABASE.md`](backend/DATABASE.md) |
| Redis Stream / MQ 规范 | [`backend/MQ.md`](backend/MQ.md) |
| Host Agent 实现规范 | [`agent/AGENT.md`](agent/AGENT.md) |
| 前端页面与交互规范 | [`frontend/FRONTEND.md`](frontend/FRONTEND.md) |
| 部署、环境变量、Docker | [`infra/INFRA.md`](infra/INFRA.md) |

## 技术栈约定

```
后端 (Server):   Python 3.11 · FastAPI · SQLAlchemy 2.0 · PostgreSQL 15
消息队列:        Redis 7 (Redis Stream)
Agent:           Python 3.11 · 本地 SQLite (WAL 模式)
前端:            React 18 · TypeScript · Tailwind CSS · WebSocket
容器化:          Docker Compose (开发) · K8s (生产)
```

## 关键约定（全局生效）

1. **所有工具引用使用 tool_id**，禁止在任何业务代码中出现裸 `script_path` 字符串
2. **状态机转换必须经过 `JobStateMachine` 类**，禁止直接 UPDATE job 状态字段
3. **Agent 侧所有 Step Trace 写入 SQLite 必须在事务内完成**，写入后再更新 `last_ack_id`
4. **消息分两类 Topic**：`stp:status` (高优先级) 和 `stp:logs` (低优先级)，禁止混用
5. **API 响应格式统一**：`{ "data": ..., "error": null }` 或 `{ "data": null, "error": { "code": ..., "message": ... } }`

## 开发顺序建议

```
1. infra/     → 搭环境，跑通 DB + Redis
2. backend/   → 实体模型、状态机、API
3. agent/     → Pipeline Engine、Tool_Registry、MQ 上报
4. frontend/  → 看板、调度入口、日志流
```
