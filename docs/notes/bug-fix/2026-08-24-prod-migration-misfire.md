# 生产库误执行迁移事故（alembic upgrade head 未核目标）

Status: resolved（前向修复：涉事迁移随 #404 PR-B 立即合入）
Class: process

## 事故

2026-08-24，验证 `v8w9x0y1z2a3`（plan.suite_id）迁移时在 `backend/` 目录直接执行
`alembic upgrade head`。`resolve_database_url` 按设计解析到 `.env.backend`
→ **生产库 stp（127.0.0.1:5432）**。AGENTS.md 明文禁止此操作
（「迁移试验：禁止对生产库执行 alembic upgrade 试跑」），执行前未确认连接目标。

**应用到生产的迁移**：
1. `u7v8w9x0y1z2`（flash v1.2.0 种子）——main 已合入、本就待部署的迁移被提前应用；
   停用 flash 脚本 v1.0.0/v1.0.1。
2. `v8w9x0y1z2a3`（plan.suite_id）——当时**尚未提交到任何 git ref**。

## 影响评估（只读核验）

- flash 步骤引用数 = **0**（无 Plan 使用 flash 脚本）→ 停用零功能影响；v1.1.0 保持 active
- `plan.suite_id` 可空列对运行中服务不可见（其模型元数据无此列）→ 零运行时影响
- **真实风险**：prod `alembic_version` 指向仅存在于本地未提交工作区的 revision——
  窗口期内任何部署跑 `upgrade head` 会因找不到版本号而失败

## 补救

前向修复：PR-B（含该迁移）立即走完验证并合入 main，version 与代码一致后窗口关闭。
不选择 downgrade：多一次生产 DDL，且 flash 种子本就待部署。

## 防线（即刻生效）

1. **任何 alembic 命令前先跑连接目标断言**：
   `venv/bin/python -c "from backend.core.env_source import resolve_database_url as r; u,s=r(); print(s)"` ——
   输出含 `.env.backend` 即为生产库，验证类操作必须改用显式
   `DATABASE_URL=postgresql+psycopg://…@127.0.0.1:5432/<scratch>` 覆盖或 testcontainer。
2. 迁移验证一律走 CI / testcontainer（conftest 的 create_all 不验迁移，
   需要 upgrade/downgrade 冒烟时用一次性容器库）。
3. 本机即生产机是常态而非例外——「这只是开发环境」的直觉在本仓库不成立。

## 何时不重议

若未来部署脚本改造为迁移前打印脱敏目标并要求二次确认，第 1 条可降级为提示；
第 2、3 条与工具无关，长期有效。
