# TESTING=1 下不加载生产/本地 dotenv（#884）

Status: implemented
Class: bug-fix

## Decision

#884（R01-F04）：`backend/main.py` 模块顶层无条件 `load_dotenv(.env.backend)`
与 `load_dotenv(backend/.env)`——生产配置文件存在时，导入 `backend.main` 的
测试会在 import 期把生产/本地环境变量（JWT、Redis、密钥等）注入测试进程；
`TESTING=1` 此前只跳过 lifespan，管不住 import 期。与
`docs/development/testing.md §2`「测试不得读取或复用 .env.backend」冲突
（影响边界：fixture 已覆盖数据库 URL 本身，风险在其余未覆盖配置）。

修复（门控收到**配置加载层**，issue 建议 1）：

- `backend/core/env_source.py` 新增 `load_app_dotenv(repo_root=None)`：
  `TESTING == "1"` 时直接返回（不加载任何 dotenv）；非测试环境保持原三层
  `override=False` 语义（进程环境最优先 → `.env.backend` → `backend/.env`）；
- `backend/main.py` 改走该函数（顶层 `from dotenv import load_dotenv` 移除）——
  TESTING 下 import 应用不再注入生产文件；
- 测试所需配置继续由 conftest 显式设置（JWT_SECRET_KEY / AGENT_SECRET /
  TEST_DATABASE_URL 等，本单未动）。

与 ADR-0033 的关系：无直接约束（测试隔离域，与 #1300 的 TEST_DATABASE_URL
护栏同族）。

## Alternatives

- 在 main.py 内联 `if os.getenv("TESTING") != "1":` 包住两行 load_dotenv：
  少一个函数但门控散在入口层，后续其他入口（worker/CLI）各自为政——
  收进 env_source 才能做单一事实源；
- 测试入口改为强制合成配置（issue 建议 2）：conftest 已显式设关键键，
  全面「合成配置」需要枚举全部生产键，收益小于成本；
- 让测试也读 `.env.backend` 但在 conftest 里 override：与文档口径直接冲突，放弃。

## Verification

- `pytest tests/test_env_source_testing_gate.py`：2 passed——TESTING=1 下伪造的
  `.env.backend`/`backend/.env`（哨兵键）不注入进程；非测试环境两个文件正常
  加载（生产路径不回归）；
- `pytest backend/tests`：全量回归（应用 import 路径为所有 backend 测试共用，
  单独验证 conftest 显式配置已足够支撑 TESTING 下 import main）；
- ruff（含 tests/）全绿。

## Revisit

- 其他入口（`backend/tasks` worker、`alembic/env.py`、CLI）是否也有 dotenv
  直载：若它们同样在测试路径被 import，需要各自收口——本单只收
  `backend.main`（issue 证据范围）；
- 若未来某个测试确实需要 `.env.backend` 里的键，正确做法是 conftest 显式
  设置该键（可见、可审计），而不是放开门控。
