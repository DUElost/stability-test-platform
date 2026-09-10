# PostgreSQL 独立部署模板

`docker-compose.yml` 是单机 PostgreSQL（可选 PgAdmin）的独立模板，用于本地 /
隔离环境一键起库。**生产控制面不使用本模板**：按
[最小部署清单](../../docs/production-minimum-deployment-checklist.md) 使用宿主机
PostgreSQL 或受控基础设施服务。

## 使用

1. 复制并填写本地 `.env`（已被 `.gitignore` 忽略）：

   ```bash
   cp deploy/postgres/.env.example deploy/postgres/.env
   ```

2. 设置 `POSTGRES_PASSWORD` 与 `PGADMIN_PASSWORD` 为强口令，例如
   `openssl rand -base64 24` 的输出；**留空或未设置时 compose 拒绝启动**，
   不再回退到内置口令。两个变量都必须设置：compose 插值在 profile 过滤前
   执行，即使不启用 admin profile，缺失的 `PGADMIN_PASSWORD` 也会导致
   整体启动失败；
3. 启动：

   ```bash
   docker compose -f deploy/postgres/docker-compose.yml up -d
   # 可选管理界面：
   docker compose -f deploy/postgres/docker-compose.yml --profile admin up -d
   ```

## 绑定与信任模型

- 两个服务默认只发布在 `127.0.0.1`（仅本机可达）；`POSTGRES_PORT` /
  `PGADMIN_PORT` 只调整端口，不改变绑定地址；
- **生产绑定要求**：不要把端口改绑 `0.0.0.0`。确需跨主机访问时，显式绑定到
  指定内网地址，并同时满足：强口令、防火墙来源白名单、TLS 或等价链路保护、
  `pg_hba.conf` 最小授权；
- PgAdmin 默认关闭（`profiles: [admin]`）；其凭据与会话安全等级低于数据库本身，
  不建议在生产暴露管理界面。
