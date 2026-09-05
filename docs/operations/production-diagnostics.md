# 生产控制面只读诊断

本文记录生产控制面进行临时只读诊断时的凭据来源和安全边界。部署步骤见
[`production-minimum-deployment-checklist.md`](../production-minimum-deployment-checklist.md)；
测试隔离要求见 [`../development/testing.md`](../development/testing.md)。

## 安全边界

- 优先只读查询；写操作必须通过代码、迁移和 PR 流程；
- 不得把密码、token、私钥、连接串或主机清单内容写入代码、文档、日志或 PR diff；
- 不得用生产数据库代替测试数据库；
- 不得在生产数据库上试跑迁移；
- `.env.backend`、`backend/.env` 和 Agent `.env` 的职责不同，不得互相代用。

## 凭据来源

| 用途 | 来源 | 约束 |
|---|---|---|
| Agent fleet SSH | `/home/debian13/hosts.ini` 的 `[android]` 与 `[android:vars]` | 清单是本地敏感文件；规模以当前内容为准 |
| Backend 数据库 | 仓库根 `.env.backend` 的 `DATABASE_URL` | 本机 PostgreSQL 可能就是生产 `stp`；只读 SELECT 优先 |
| 控制面管理员 | 仓库根 `.env.backend` 的 `STP_ADMIN_USER`、`STP_ADMIN_PASSWORD`、`AGENT_SECRET` | `backend/.env` 不是生产凭据源 |

控制面本机使用仓库 `venv/bin/python` 和 psycopg 3。需要调用管理 API 时，先从
`/api/v1/auth/token` 获取 token，并使用同一生产 env 源中的 `AGENT_SECRET`；
不要把解析出的值打印或持久化。

## 文件边界

以下文件或目录是本机状态，不进入 Git：

- `.env.backend`、`backend/.env`、`backend/agent/.env`
- `/home/debian13/hosts.ini`
- `opencode.json`
- 私钥、token 与 Harness 本地凭据配置

Claude Code 的项目设置只禁止修改部分凭据文件；允许读取是为了支持经授权的只读运维。
这不构成读取授权，仍须由当前 Requirement 明确需要。
