# 显式 TEST_DATABASE_URL 的机器护栏（#1300）

Status: implemented
Class: bug-fix

## Decision

#1300（R15-R01，设计风险 P1）：`backend/tests/conftest.py` 接受任意
`TEST_DATABASE_URL`，随后 `db_session` 执行**全表 TRUNCATE CASCADE**——默认
testcontainers 路径安全，但显式地址一旦误指生产库（最可能姿势：把运行时
`DATABASE_URL` 复制进 `TEST_DATABASE_URL`），就是生产事故。审查未验证实际生产
目标，按设计风险收口为机器护栏。

修复（两道闸 + 一个显式豁免）：

- 新增 `backend/core/db_url_guard.py::guard_test_database_url`：
  1. **隔离命名**：PostgreSQL 库名必须含 `test`（不区分大小写，如
     `stp_test`）——命名约定即护栏；
  2. **运行时配置比对**：与 `DATABASE_URL`（运行时/生产配置）完全相同 → 拒绝；
  3. 非 PostgreSQL scheme（sqlite 等）直接拒绝——测试套件按约定只跑 PG；
  - 豁免：`STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1` 显式绕过（记 loud warning）；
- conftest `_resolve_test_database_url` 在**显式配置路径**上调用护栏；
  **testcontainers 兜底路径不经护栏、行为不变**（验收标准 2）；
- 护栏模块按**文件路径** importlib 加载、不进 `backend.core` 包——conftest
  解析 TEST_DATABASE_URL 时 `DATABASE_URL` 尚未设置，包级导入会触发
  env_source 的配置解析提前失败（实测踩到）；
- 文档：`testing.md` 的 env 表补充护栏语义与豁免开关，强调 unset 走容器。

与 ADR-0033 的关系：无直接约束（测试基建立面，非工具接入契约）。

## Alternatives

- 只读探活（连上后查 server 版本/表数量再放行）：多一次生产库连接本身就是
  事故面，命名护栏在连接前拒绝更安全；
- 白名单已知测试主机：无法枚举开发机，维护成本高于收益；
- 只在文档强调不写代码护栏：issue 验收明确要求机器护栏，文档只是补充。

## Verification

- `pytest backend/tests/core/test_test_db_guard.py`：10 passed（模块名
  `db_url_guard`——`test_` 前缀会撞 repo-layout 门禁「test 文件必须在
  testpaths 下」，#1300 自验时踩到并已收口）——隔离命名通过
  （含大小写）/ 四种非 test 库名拒绝 / 非 PG scheme 拒绝 / 与 DATABASE_URL
  相同拒绝、不同通过 / 豁免 env 绕过且记 warning；
- **端到端**：`TEST_DATABASE_URL=...@192.0.2.10/production` 跑 pytest →
  conftest 加载即抛 `UnsafeTestDatabaseUrl`（含 unset/override 指引），
  TRUNCATE 不会发生；`.../stp_test` 正常路径 10 passed。示例主机取 RFC 5737
  文档保留段：拒因是库名不含 `test`，与主机无关（写实 RFC1918 地址会撞
  ip-leak 门禁）；
- `pytest backend/tests`：全量回归通过（CI 同款 `stp_test` 命名过护栏）；
- ruff 干净。

## Revisit

- 护栏只比对 URL 全等，不比对 host/库名相似度——复制后手改端口的误用挡不住；
  若出现真实险兆事件，再评估 host 黑名单或「DATABASE_URL host 相同即拒」；
- CI 的 `TEST_DATABASE_URL` 命名（`stp_test`）已符合约定，无需改 CI；
- 豁免 env 是全局开关，无「单次运行」粒度——若豁免被常态化使用，说明命名
  约定需要重议而不是继续豁免。
