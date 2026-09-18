# 诊断只读角色 `stp_ro`：把 SOP 里那句「用专用只读角色」变成存在的东西（#2632 缺口②）

Status: implemented
Class: bug-fix

## Decision

**先说这条欠账的真实性质**：#2632 的三个缺口里，①（留痕）与 ③（测试库越界）都已由代码
修掉，剩下 ②。而 `production-diagnostics.md` 从第一天起就写着「临时诊断用专用只读角色」——
**全仓没有任何东西创建这个角色**。这类漂移不报错、不变红，只在下一次有人真要手查时
把人推回 `stp`（应用共享凭据）或 `postgres`（超级用户），也就是把缺口②的原因原样保留。
所以本单的正题不是「写一份 SQL」，而是**让 SOP 指着一个存在的东西**。

交付三件：

1. **`deploy/postgres/diag-readonly.sql`**（幂等、可重复执行）
   - `CREATE ROLE stp_ro WITH LOGIN PASSWORD NULL`：建成**无口令态**——跑完脚本不会
     顺手得到一个可用凭据，口令必须由运维在库侧 `\password` 设（红线：口令不入仓）；
     显式写全 `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION`
     与 `CONNECTION LIMIT 3`（诊断不该吃应用的连接预算；NOINHERIT 保证将来被加进某个组
     也不会白拿权限）；
   - 三条会话参数才是「误操作半径」真正的闸：`default_transaction_read_only=on`、
     `log_statement='all'`（**本角色每条语句都进服务端日志**——#2632 抱怨的「无留痕」
     从此不靠人自觉）、`statement_timeout=60s` + `lock_timeout=5s`；
   - 授权只有 `USAGE` + `SELECT`；**不含** `USAGE ON SEQUENCE`（诊断不需要 nextval）；
   - 末尾一段 `DO` 自检：库里若已存在非读授权、或两条会话参数没落地，直接
     `RAISE EXCEPTION`——「跑了但没生效」不接受。
2. **SOP 落到那条红线上**（`docs/operations/production-diagnostics.md`）：角色名、
   创建命令、三件事各自解决什么，以及最重要的一句
   **「角色不存在时停下来报缺，不要退回 `stp`/`postgres`」**。凭据来源表加一行。
3. **`tests/test_diag_readonly_role_contract.py`**：静态判据 + **真实 PG16 行为判据**
   （testcontainers，隔离实例，与 `tests/test_alembic_upgrade.py` 同一先例）。

## Alternatives

- **只补文档、不写脚本**：否决。文档指着一个不存在的角色比不写文档更糟——它给了
  「已经有正道」的假象（与 #2488「装了但不加载」、#2643「规则装了但恒不触发」同形）。
- **给 `stp` 加只读约束 / 直接改应用凭据权限**：否决。应用要写，收紧它会炸生产；
  而且那是改变别人的权限面，不属诊断脚本的职责。
- **不建角色，改用 `pg_hba` 限制来源**：不解决归属与半径两条中的一条（归属靠
  `application_name` 与角色名，半径靠只读事务），且 `pg_hba` 在本仓属运维手改面
  （`deploy/postgres/README.md` 明写生产不用 compose 模板），不在本单射程内。
- **把口令写进 `.env.backend` 由脚本 `\password` 注入**：否决——口令入仓是硬红线，
  且 `psql` 变量插值会把口令留在 shell 历史与 CI 日志里。
- **`ALTER DEFAULT PRIVILEGES`（本单最关键也最安静的一条）可以不做**：否决。
  只授「当前所有表」时，下一次 alembic 建的表对 `stp_ro` 就是 `permission denied`，
  而人的反应不会是「去补授权」，是**退回用 `stp` 手查**——缺口原地复活且无人记账。
  `FOR ROLE` 必须指向**建表属主**（生产为 `stp`）而不是诊断角色自己，指错时静态检查看
  不出来，所以行为判据里专门建了一张「脚本执行之后才出现的表」来证明它真的生效。

## Verification

**测试分两个文件**（`tests/test_offline_subset_guard.py` 的约定：凡 import
testcontainers 的文件必须进 `ci.yml` / `scripts/run_gates.py` 的 `--ignore` 名单，
归夜间全量 `backend-test`）：

- `tests/test_diag_readonly_role_contract.py`（静态，7 条，PR 路径即跑）：口令不得入仓；
  `GRANT` 目标只能是 `stp_ro` 且权限 ⊆ {SELECT,USAGE,REFERENCES,TRIGGER}；不给
  `PUBLIC`/应用属主加东西；无 `GRANT OPTION`/`ADMIN OPTION`/`SUPERUSER` 形态；
  `default_transaction_read_only` 与 `log_statement='all'` 两条会话参数在场；
  `ALTER DEFAULT PRIVILEGES FOR ROLE` 必须指向**建表属主**而非诊断角色自己；
  脚本体必须是**纯 SQL**（无 `\` 元命令，否则没法程序化执行——`ON_ERROR_STOP` 属调用约定）；
  SOP 红线段落里必须同时出现角色名、可执行创建命令与「缺角色即停」，且角色名在 SOP 出现
  ≥3 次（表格与红线两处必须同名）；最后一条钉住行为文件在场。
- `tests/test_diag_readonly_role_pg.py`（真 PG16，6 条）：读旧表；**属主新建的表无需重跑
  脚本即可读**；`INSERT`/`CREATE TABLE` 被拒；**只读闸独立于授权**（故意先 `GRANT INSERT`
  再写，仍须被会话参数拒）；`pg_db_role_setting` 两条参数落地；`pg_default_acl` 挂在 `stp` 上；
  连跑三次幂等。

结果（全部实跑）：

- `pytest tests/test_diag_readonly_role_contract.py tests/test_diag_readonly_role_pg.py -q`
  → **13 passed**；`pytest tests/ -q` → **1590 passed**
- `tools/dev/check_governance_surface.py --check` → S1–S15、S5x 全绿；
  `ruff check tests/ scripts/run_gates.py` 通过；`check-internal-ip-leak.py` 通过；
  `ci.yml` YAML 解析通过（8 个 job）
- **变异自证**（每条都必须红，实测逐条红）：
  | 变异 | 结果 |
  |---|---|
  | `FOR ROLE stp` → `FOR ROLE stp_ro`（指错属主） | 3 failed（**只有真库能抓到语义**） |
  | 整段删掉 `ALTER DEFAULT PRIVILEGES` | 3 failed |
  | 删掉 `log_statement = 'all'` | 静态 1 failed + 行为 6 errors（**脚本内 DO 自检 RAISE，响亮的失败**） |
  | 给 `stp_ro` 加 `UPDATE` | 2 failed |
  | 脚本体里塞回 `\set ON_ERROR_STOP on` | 6 errors + 静态红 |
  | SOP 全文改角色名 / 删创建命令 / 删「缺角色即停」 | 各 1 failed |
- **pending（不当作通过）**：① **在生产库执行该 DDL**——建角色是对生产库的写操作，必须由
  运维授权执行；本单全程未对生产做任何写（只读取证带 `application_name=diag-2632-*`）；
  执行后按脚本末尾 NOTICE 与 SOP 自检。② `scripts/run_gates.py check:quick` 的 eslint 步
  在本 worktree 跑不起来（无 `node_modules`）——本单 diff 不含 `frontend/`，按 pending 计。

## Revisit

- **#2632 建议 3 的尾巴仍在生产侧**：`probe@stp_probe` 密码认证失败 ×4（某监控探针凭据失效）
  ——那是凭据轮换动作，不是代码能收的；`stp_test` 连库尝试那一半已由缺口③
  （conftest 拒载指向 loopback 的测试库）挡住。
- **`log_statement='all'` 的日志量**：只对 `stp_ro` 生效，诊断量很小。若将来出现「用该角色
  跑批量对账脚本」的用法要重新评估——那种用法本就该走代码与迁移，而不是诊断角色。
- **`GRANT CONNECT ON DATABASE` 刻意不写**：当前 PUBLIC 默认可连，写了会把库名钉进脚本。
  若站点侧收紧 `pg_hba`/库权限，需要按站点补一行，并同时补进 `FOR ROLE` 那条的自检。
- **诊断面的终态**：角色 + `application_name` + 猜 schema 告警三件事凑齐后，
  「谁在生产库上跑了什么」才第一次可归因。真正还缺的是**结构化查询审计**（pgAudit 一类），
  那属另一方向的选型，不在本单射程内——别把本单读成「审计已解决」。
