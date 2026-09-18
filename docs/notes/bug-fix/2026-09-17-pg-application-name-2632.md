# PG 连接带 `application_name`：让「谁连的生产库」可归因（#2632 缺口①）

Status: implemented
Class: bug-fix

## Decision

#2632 从 PG 日志读出的三条缺口里，**①「无来源信息」**是仓库侧可修的：日志只有
`user@db`，事后无法回答「那条猜 schema 的 SQL 是哪条链路发的」。修法是让**每条由代码
建立的连接自报用途**：

- `backend/core/database.py` 新增 `db_application_name()`：
  `TESTING=1` → `stability-tests`，否则 `stability-backend`（用**既有**的 `TESTING`
  开关，**不新增 env 键**——避开 `.env*.example` 的 parity 门禁成本）；
- 两个 kwargs 工厂各带上（**驱动写法不同，是实测过的坑**）：
  - 同步（psycopg / psycopg2）：`connect_args={"application_name": …}`（libpq 顶层参数）；
  - 异步（asyncpg）：`connect_args={"server_settings": {"application_name": …}}`；
  - SQLite 路径不加（`connect_args` 根本不出现）。
- **SOP 同步**：`production-diagnostics.md` 的「凭据来源」补一条——临时 psycopg 连接请带
  `application_name="diag-<用途>"`（只写用途，不写主机/凭据）。控制面与测试进程由代码自动带。

**本单只覆盖缺口①**，另两条属运维/凭证面，如实留证（见 Revisit）：
②超级用户与应用共享凭据被用于手工查询（误操作半径与归属）；③`stp_test` 被解析到生产
PG 实例（这次库不存在、连接即拒——**是运气不是防线**）。

## Alternatives

- **A. 加 `STP_DB_APPLICATION_NAME` env 键**：否决。要同步 8 个 `.env*.example` 与
  parity 门禁（#737 那轮刚建），而 `TESTING` 已经能把「服务 / 测试」这两类分开——
  判据要的是"哪个用途"，不是"任意可命名"。
- **B. 在 PG 侧开 `log_connections` 再由外部解析**：不属本仓（ops 配置），且只解决
  "有没有记"，不解决"记的是不是可读的用途名"。
- **C. 对「猜 schema 的 SQL 错误」做告警**：需要 PG 日志进入监控面（本仓的 Prometheus
  面不消费 PG 日志），属另一条链路——先在缺口①上把来源做出来，才谈得上按来源告警。
- **D. 顺手处理 `probe@stp_probe` 认证失败**：不做（监控探针凭据属运维配置，本仓不可见）。

## Verification

- **测试**：`backend/tests/core/test_db_application_name.py` → **5 passed**：
  - 同步 kwargs 带 `application_name`；异步走 `server_settings`（**两种驱动写法各一条**）；
  - SQLite 路径不带 `connect_args`；
  - `TESTING` 有无决定 `stability-tests` / `stability-backend`；
  - **真连一次读回来**：用本模块 kwargs 建引擎连同一隔离库 → `SHOW application_name`
    等于期望值（静态断言证明不了 PG 真收到）。
- `backend/tests/core/` 全量 **86 passed**；`ruff check` 通过。
- **写这条测试时连踩两坑（都在测试里修正并留注释）**：`db_session` 的引擎由 fixture
  自建、不带这些 kwargs（拿它断言会把"没到 PG"误判成实现没写）；`str(URL)` 会把口令
  打码成 `***`（重建引擎认证失败）→ 用 `render_as_string(hide_password=False)`，进程内用、不外泄。

## Revisit

- **缺口②（凭据面）**：超级用户被用于日常手工查询、应用共享凭据的误操作半径与归属问题——
  属凭证策略（谁能用哪个角色连库），需要运维裁决；本单只让**来源可读**，不改变权限。
- **缺口③（测试边界）**：`postgres@stp_test` 那次连接尝试的**发起方仍未定位**（PG 日志无
  客户端应用名——正是缺口①的后果）。本单落地后，同类尝试会带上 `stability-tests` 或
  `stability-backend`，届时可反查是哪条链路；**在那之前不要假定它已被防住**：库名护栏
  （`db_url_guard`）只管"库名像不像测试库"，不管"连的是哪个实例"。
- **shell 里的 `psql`/临时脚本**：代码路径已带名，命令行路径只能靠 SOP 约定（本单已写）；
  若再出现「无来源」的连接，说明约定没被遵守，应在团队侧收敛而不是回到代码里做拦截。
