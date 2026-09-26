# compose 口令插值不再静默回落：`${VAR:?}` + 弱口令告警（#3353）

Status: implemented
Class: bug-fix

## Decision

**`docker-compose.yml` 的两个口令类键改为强制引用（`${VAR:?}`），未设即启动报错；弱口令
在 seed 时显式告警。** 背景（#3353 实测）：`${VAR:-默认}` 只读**根目录 `.env` 与 shell**，
不读 `env_file` 的 `.env.server`；而 `environment:` 优先于 `env_file`。于是按文档把随机口令
写进 `.env.server` 的人，实际生效的是 compose 默认值——`POSTGRES_PASSWORD=change-me-local`、
`STP_ADMIN_PASSWORD=admin123`（第二套隔离栈复现：file/container 口令 sha 不一致 →
用文件里的口令登录 401，写进 `.env` 后 200）。这是**部分键静默失效**，比全失效更难发现。

落点：

1. `docker-compose.yml`：`postgres.POSTGRES_PASSWORD` 与 `server.STP_ADMIN_PASSWORD` 改
   `${VAR:?提示}`（提示直接写明「写进根目录 `.env`，不是 `.env.server`」）；
   `DATABASE_URL` 里嵌的口令引用同步去掉默认值；`STP_ADMIN_USER`/库名等非机密键保留 `:-` 默认。
2. `backend/scripts/init_dev_db.py`：`STP_ADMIN_PASSWORD == "admin123"` 时打印
   `dev_db_admin_WEAK_PASSWORD` 告警（compose 已 fail-closed，但 host 直跑/存量 `.env`
   仍可能带着默认值进来——不让弱口令静默成为控制台口令）。
3. `.env.server.example`：删掉误导性的 `STP_ADMIN_PASSWORD=admin123`；三个由 compose
   组装/强制的键改为**注释态登记**（保留键名占位，明确「在本文件写值不生效」）——
   既满足 env 清单门禁（`example_keys` 认注释态条目），又不给抄模板的人一个可用弱口令。
4. `docs/development/local-development.md`：quickstart 里补「先写根目录 `.env`（两个口令必填）」
   与一段「两个 env 源的分工」说明（插值只读 `.env`/shell；`environment:` 覆盖 `env_file`）。
5. `backend/tests/test_deployment_files.py`：把钉旧形状的断言（`:-admin123`）改为钉新形状
   （强制引用 + 不得回归弱默认值）——旧断言正是「口径改了、机械载体没回扫」的那类红；
   否定断言走 `SourceGuard.of_repo_path(...).anchored(...).assert_absent(...)`（#2639 棘轮），
   禁止裸 `assert ... not in compose`（PR CI 的 `pr-agent-tests` 并行 gate 会拦）。

## Alternatives

- **删掉 `environment:` 三行、改靠 `env_file` 单源**：会让 `DATABASE_URL` 的容器内地址
  （`@postgres:5432`）与宿主直连场景重新耦合；issue 已列为不建议。
- **只删弱默认值、不强制（`${VAR}`）**：空值会以空字符串进容器（`POSTGRES_PASSWORD=`），
  启动可能成功但语义更糟（空口令/后续 500），「没配」仍不是显式错误。
- **把 `.env.server` 当作插值来源（compose `--env-file`）**：会把「模板」与「插值源」
  合成一个文件，运行时其余键与超集口令混在一起；且要求所有人改调用方式（`docker compose
  --env-file .env.server up`），比修 compose 更侵入。
- **弱口令只在文档里提醒**：`init_dev_db.py` 是唯一会「把弱口令写成可用凭据」的地方，
  在它这里告警才覆盖 host 直跑路径。

## Verification

| 命令 | 结果 |
|---|---|
| `pytest tests/test_compose_required_env_secrets_3353.py backend/tests/test_deployment_files.py tests/test_dev_bootstrap_seed.py -q` | **31 passed**（8 连跑无 flake） |
| `pytest tests/test_source_scan_anchor_ratchet.py -q`（#2639 棘轮，跟进修 PR CI 红） | **10 passed** |
| `pytest backend/tests/test_deployment_files.py -q --noconftest`（跟进后） | **19 passed** |
| `pytest tests/test_compose_required_env_secrets_3353.py -q`（含 docker 真值用例） | **5 passed** |
| `docker compose config`（本机真值）：未设口令 → rc=15 + 中文提示；两键齐备 → rc=0 | 手工复核两次 |
| `python tools/dev/env_inventory.py --check` | **[OK] 271 个读取名** |
| `scripts/run_gates.py check:quick` | **[OK] check:quick（16 gates）** |

新增门禁（`tests/test_compose_required_env_secrets_3353.py`）：

- 静态：两个口令键必须含 `:?`、不得带 `admin123`/`change-me-local`；`DATABASE_URL` 的引用同理；
  模板**生效行**不得声明 `STP_ADMIN_PASSWORD` 或带弱值（注释态登记允许）；
- 自证：把历史形状（`:-admin123` / `:-change-me-local`）与旧模板喂回同一判定函数，必须变红；
- 文档：`local-development.md` 必须写明「compose 插值」与「不读 `.env.server`/`env_file`」；
- `init_dev_db.py` 必须有 `password == "admin123"` 比较与 `dev_db_admin_WEAK_PASSWORD` 标记；
- docker 真值（本机有 docker 才跑）：`docker compose config` 分别只设其一时，报错必须点名
  另一个缺失变量；两键齐备必须 rc=0。**注意**：compose 报哪一个缺失变量取决于服务迭代顺序
  （实测非确定），所以判据只钉变量名，不钉自定义提示文案——第一版钉文案，8 跑里红了 2 次。

## Revisit

- **口径的下一跳**：若将来把 compose 的插值源显式化（`--env-file` 或把口令移进 `env_file`
  单源），本门禁的判据要跟着换（现在是「`:?` + 无弱默认值」的形状判据）。
- **弱口令集合**：当前只认 `admin123`（compose 的历史默认值）。若出现第二个默认口令
  （例如站点安装链的 `STP_INITIAL_ADMIN_PASSWORD` 默认值），把集合抽出来共用。
