# Agent 测试跨边界 import 收敛（#739 面①）——第一批：迁移 5 + 就地解耦 2

Status: implemented
Class: bug-fix

> **更名注记（2026-09-26）**：本笔记提到的 `test_pipeline_validator_parity_738.py` 已更名为
> `backend/tests/core/test_pipeline_validator_contract_738.py`（内容自 ADR-0054 起已从双端 parity 改为单实现 + 布局对拍；#3401 A1）。

## Decision

**收敛方向（owner 2026-09-20 裁决）：分批迁移到 `backend/tests/` + 横跨契约文件就地解耦。**
棘轮（`tests/test_agent_test_import_ratchet.py`）从 **14 → 7**，只减不增继续生效。

第一批（本 PR）：

| 原文件（`backend/agent/tests/`） | 去向 | 方式 |
|---|---|---|
| `test_adr0026_params.py` | `backend/tests/core/` | 迁移（纯控制面：`core.adr0026_params`） |
| `test_logging_setup.py` | `backend/tests/core/` | 迁移（纯控制面：`core.logging_setup`） |
| `test_app_scheduler_executors.py` | `backend/tests/scheduler/` | 迁移（被测对象是 `scheduler.app_scheduler` 执行器路由） |
| `test_leader_election.py` | `backend/tests/scheduler/` | 迁移（选主属控制面） |
| `test_socketio_redis_adapter.py` | `backend/tests/realtime/` | 迁移（SocketIO Redis 适配属控制面） |
| `test_step_log_batching.py` | 就地解耦 | agent 侧 5 例保留；控制面侧 2 例（`on_step_log` 服务端摄取）→ `backend/tests/realtime/test_step_log_ingest_contract.py` |
| `test_legacy_tool_cleanup.py` | 就地解耦 | agent 侧保留；跨包墓碑 2 例 → `backend/tests/test_legacy_tombstones.py` |

**为什么这样分**：边界守卫是**方向性**的（禁 agent → 控制面；反向合法）。被测对象是控制面模块的
用例，mock 掉被测对象等于不测——只能迁移；横跨两侧的契约用例（step_log 摄取、跨包共享来源）
拆出控制面侧、agent 侧留在原处。

**代价（须知情）**：迁出的用例退出 PR 门禁（`pr-agent-tests` 的 agent step），改由夜间
`backend-test` 与本地 `check:full` 覆盖——与仓库既有 CI 拓扑一致（`backend/tests` 本就不在
PR 路径）。这一点是选择「迁移」而非「就地解耦」的主要权衡。

**边界探针**：迁移前实测——把 conftest 的两个 `setdefault` 停掉后，套件在**收集期**即
12 文件 `RuntimeError: DATABASE_URL is not set`（`backend/core/__init__` 链）→ 反证了
「测试面越界不炸」只靠 conftest 占位（#2428）兜着，正是棘轮存在的理由。

## Alternatives

- **全量一次性迁移（14 文件 / ~3800 行）**：`test_saq_scan_pipeline` 1287 行 +
  `test_cron_scheduler` 408 行 + `test_p3_3_multi_instance` 397 行 + `test_mtbf_suite` 341 行
  + `test_login_lockout` 199 行 + `test_aee_metadata` 230 行 + parity 1 例——单 PR 不可审阅，
  且任一迁移失败会同时污染夜间路径。改分批，每批 5-7 个文件。
- **就地解耦全部 14 个**：对「被测对象就是控制面」的用例无意义（mock 被测对象）；
  仅对横跨契约文件成立——本批已按此处理 2 个。
- **承认现状（保留棘轮冻结、不迁移）**：零风险，但 #739 面① 不收敛，台账会永久挂着 14 条。
- **迁移到根 `tests/`（保住 PR 路径）**：仓库级离线子集要求不依赖 DB/testcontainer，而本批
  迁出用例多需要控制面 import 链（`DATABASE_URL`）→ 与离线子集契约冲突，且 PR job 的
  `DATABASE_URL` 与 `TEST_DATABASE_URL` 同值会触发 db_url_guard（#1547 成因）。弃。
- **迁移时顺带改测试内容**：弃——本批只搬位置/拆文件，不改断言，便于逐条对照。
  两个新文件的 docstring 只注明来源与迁移理由。

## Verification

- `pytest backend/tests/core/test_adr0026_params.py backend/tests/core/test_logging_setup.py
  backend/tests/scheduler/test_app_scheduler_executors.py backend/tests/scheduler/test_leader_election.py
  backend/tests/realtime/test_socketio_redis_adapter.py -q` → **30 passed**（新位置，无需 DB）；
- `pytest backend/tests/realtime/test_step_log_ingest_contract.py backend/tests/test_legacy_tombstones.py -q`
  → **4 passed**（控制面新家）；
- `env -i PATH="$PATH" PYTHONPATH=. pytest backend/agent/tests/test_step_log_batching.py
  backend/agent/tests/test_legacy_tool_cleanup.py -q` → **21 passed**（解耦后仍全绿）；
- AST 复核：两个解耦文件对 `backend.<非 agent>` 的 import 数为 **0**；
- `pytest backend/agent/tests/ -q`（`env -i`，全套件）→ **2204 passed**（迁移前 2238；
  差 34 = 迁出 30 + 拆出 4）；
- `DATABASE_URL=… JWT_SECRET_KEY=x pytest tests/test_agent_test_import_ratchet.py
  tests/test_agent_import_boundary.py tests/test_agent_env_selfsufficiency.py -q` → **14 passed**；
- `ruff check`（含全部改动文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick（12 gates）**；
- `python scripts/run_gates.py check:pr` → **[OK] check:pr（21 gates）**（含
  agent-tests / agent-tests-collect / repo-level tests / pr-migrate 空库迁移 + seed 身份对拍，
  与 CI 逐项重叠）。

## Revisit

- **剩余 7 个**（棘轮台账）：`test_saq_scan_pipeline`(5) / `test_cron_scheduler`(7) /
  `test_p3_3_multi_instance`(3) / `test_login_lockout`(2) / `test_aee_metadata`(1) /
  `test_mtbf_suite`(1) / `test_pipeline_validator_parity_738`(1，**有意**保留双端 parity)。
  下一批建议从 `test_login_lockout` / `test_mtbf_suite` /`test_aee_metadata` 起（小而纯）。
- **迁出用例的覆盖位**：若夜间 `backend-test` 对这批文件出现稳定失败，按「迁回 + 就地解耦」
  退一步；判断依据是失败是否与本批位置变化相关（对照组=纯净 main 同文件）。
- **棘轮不得回摆**：`test_no_new_file_imports_control_plane` 只允许减少条目；新增越界必须
  先迁移/解耦或在条目里写明理由（并说明为何不能迁移）。
- **parity 条目（#738）**：若将来 `pipeline_validator` 双端拷贝被合并成单一实现，该条目
  随 guard 一并退役。
