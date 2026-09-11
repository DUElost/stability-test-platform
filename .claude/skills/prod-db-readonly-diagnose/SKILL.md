---
name: prod-db-readonly-diagnose
description: 生产 / 本机业务库只读诊断 SOP（凭据来源、直连姿势、红线）。触发时机：需要查生产库真实数据（任务卡死、状态不一致、对账、数据核对），或在生产机排障需要直连业务库时。
---

# 生产库只读诊断 SOP

读取并严格执行 `docs/operations/production-diagnostics.md`（权威细节）；本 skill 只列
触发、顺序与红线。

## 执行前置检查

- [ ] 确认问题必须查库（先看 UI / API / 日志能否回答）
- [ ] 使用仓库 `venv/bin/python`（本机无裸 `python` 可依赖）
- [ ] 连接串只从仓库根 `.env.backend` 的 `DATABASE_URL` 取——**不打印、不持久化**

## 标准作业流程（SOP）

1. 只读观测优先：`SELECT` + `LIMIT`；写操作与 DDL 一律不走本路径
2. `venv/bin/python` + psycopg 3 直连：连接串经环境变量传入脚本，勿写入命令行参数或
   临时文件
3. 需要管理 API 时：`/api/v1/auth/token` 取 token（`AGENT_SECRET` 用 `.env.backend`
   的生产值），带着同一环境源
4. 结论只回填「事实 + 建议」；修复动作走代码 / 迁移 / PR 流程

## 后置验证

- 回查确认本次会话未执行任何非 `SELECT` 语句（必要时交叉审计日志）
- 确认输出、日志与 PR diff 中不含连接串、密码、token 或主机清单内容

## 踩坑守卫（负向约束）

- **`backend/.env` 不含生产 `DATABASE_URL`**：从那里找会指向不存在的库；且其
  `AGENT_SECRET` 是陈旧值——诊断 auth 问题一律以 `.env.backend` 为准；
- 本机 PostgreSQL 可能就是生产 `stp`：**禁止**用生产库代替测试库、**禁止**在生产库
  试跑迁移（`alembic upgrade` 等）；
- `.env.backend` / `backend/.env` / Agent `.env` 职责不同，不得互相代用；
- 凭据不得进入代码、文档、日志与 PR diff。
