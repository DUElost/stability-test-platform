# PostgreSQL 独立部署模板加固（#1262 / R14-F16）

Status: implemented
Class: bug-fix

## Decision

本质问题：`deploy/postgres/docker-compose.yml` 是「原样可启动」的独立模板，却
内置了可用口令（`stability_password`、PgAdmin `admin`），且两个服务端口默认
绑定全部接口——网络可达即形成未授权访问面。审计提出于 2026-09-10，实施于
2026-09-11。加固三处：

1. 口令改为必填插值 `${VAR:?msg}`（`POSTGRES_PASSWORD` / `PGADMIN_PASSWORD`）：
   未设置或为空时 compose 在插值阶段拒绝启动，错误信息给出处置指引；
   `.env.example` 对应值清空并注明生成方式（`openssl rand -base64 24`），
   `DATABASE_URL` 示例改用 `<password>` 占位；
2. 发布端口加 `127.0.0.1:` 前缀，默认仅本机可达（与根开发 compose 既有惯例
   一致）；`POSTGRES_PORT` / `PGADMIN_PORT` 仅调整端口，不改变绑定地址；
3. 新增 `deploy/postgres/README.md` 写明绑定与信任模型：生产不使用本模板
   （回链最小部署清单），确需跨主机时显式绑定内网地址并满足强口令、防火墙
   来源白名单、TLS 或等价链路保护、`pg_hba.conf` 最小授权。

实测约束：compose 插值发生在 profile 过滤之前（本机 Compose 2.26.1，
`config` 与 `up` 同路径），未启用 profile 的服务中的必填变量同样被校验。
因此 `PGADMIN_PASSWORD` 即使不启用 admin profile 也必须设置——这是「不引入
弱默认值」与「PgAdmin 可选」不可兼得时的取舍；README 与 `.env.example` 均
显式说明。

## Alternatives

- **PgAdmin 移到独立 override 文件（`docker-compose.admin.yml`）**——放弃：
  能保留「不启用即可不设口令」，但既有 `--profile admin` 用法会静默退化为
  只起 postgres（无提示），且新增第二份文件与用法；单文件 + 两个必填变量的
  行为偏差更小、无静默变化；
- **只加固 postgres、保留 PgAdmin 默认 `admin`**——放弃：审计项明示
  「可选 PgAdmin 类似」，弱默认口令在启用 profile 时同样形成暴露面；
- **PgAdmin 口令留空、交给容器入口失败阻断**——放弃：错误发生在 pgadmin
  镜像入口（信息弱且依赖镜像行为），compose 插值层阻断更早、提示更明确；
- **引入 `POSTGRES_BIND_ADDR` 等绑定地址变量**——放弃：当前无合法的动态
  绑定需求，加变量等于提供一个「不改文件即可全网暴露」的开关，与加固方向
  相反；出现真实跨主机需求时再按需引入；
- **口令默认值改为 `changeme` 等醒目占位**——放弃：仍是弱口令且原样可启动，
  没有阻断力。

## Verification

实际运行（worktree `/tmp/stp-1262`，2026-09-11）：

- `pytest tests/test_deploy_postgres_hardening.py -q` → **5 passed**（新增：
  两必填变量插值形态、无 `:-` 回退与弱口令字面量、两服务发布端口 loopback
  前缀、`.env.example` 必填项留空）；
- `pytest tests/ -q` → **107 passed**；
- `ruff check .` → All checks passed；
- `docker compose -f deploy/postgres/docker-compose.yml config`：
  - 未设口令 → exit 15：`required variable POSTGRES_PASSWORD is missing a value: ...`；
  - 只设 `POSTGRES_PASSWORD` → exit 15：报 `PGADMIN_PASSWORD` 缺失；
  - 双口令 + `--profile admin` → 渲染 `host_ip: 127.0.0.1`（5432 / 5050 两处）；
- `check:quick` → **7 gates 全绿**（ruff / eslint / tsc / knip / compileall /
  gov-surface / ai-work）。

未完成（pending）：

- 实际 `docker compose up` 起库与连通性：本机为生产控制面宿主，不启动新容器；
  插值与端口渲染行为已由 `config` 覆盖。

## Revisit

- 若 Compose 后续版本改为「仅插值激活 profile 的服务」，可把
  `PGADMIN_PASSWORD` 的强制收窄回 admin profile（同步 README / .env.example）；
- 若出现合法的跨主机直连数据库需求，再引入绑定地址变量，并与 `pg_hba.conf`、
  防火墙模板的默认拒绝同步。
