# Agent 单测回归自闭环：清干净环境收集崩溃并加门禁（#739 第 1 节）

Status: implemented
Class: bug-fix

## Decision

#739 §1.1：`pytest backend/agent/tests/ -q` 在无环境变量的干净环境下**收集期**
直接崩溃（`RuntimeError: JWT_SECRET_KEY environment variable must be set`），
破坏 `backend/agent/AGENTS.md` 的「agent 测试自闭环、无控制面依赖」承诺。
本单修复两处越界导入并加门禁锁死：

1. **`test_agent_api_fencing_contract.py` 整体迁移**：
   `backend/agent/tests/` → `backend/tests/api/`。该文件 8 个用例全部测
   `backend/api/routes/agent_api.py` 的路由与 schema（`JobStatusUpdate` /
   `StepTraceIn` / `_CoordinatorHeartbeatIn` / fencing token 拒绝语义），是控制面
   契约测试放错了目录——移动后归入控制面套件（8 passed），不是 mock 掉依赖。
2. **`test_step_log_batching.py` 删除重复用例**：该文件 7 个用例是 agent 侧
   （socketio 批量送日志 / StepTraceWriter），只有 `test_suggested_log_rate_limit_scales`
   测的是控制面 `heartbeat._suggested_log_rate_limit`，且与
   `backend/tests/api/test_heartbeat_backpressure.py::test_suggested_log_rate_limit_scales_with_fleet`
   同函数同断言（输入值不同但覆盖同一缩放/夹取逻辑）。删除该用例与其导入，
   文件回到纯 agent 依赖。
3. **新门禁 `agent-tests-collect`**（`scripts/run_gates.py`，入 `check:pr`）：
   `env -i PATH="$PATH" PYTHONPATH=. <PY> -m pytest backend/agent/tests/ --collect-only -q`
   ——剥离全部环境变量（含 `JWT_SECRET_KEY` / `TESTING` / `DATABASE_URL`）后只收集。
   控制面 import 链（`backend/api/routes/__init__` → `auth` → `core.security` 的
   **模块级** JWT 硬检查）一旦被 agent 测试重新引入，收集期即红（本机 1.7s）。
   CI 对应物：`ci.yml` `pr-agent-tests` job 新增同名 step，并在
   `tools/dev/check_governance_surface.py` 的 `GATE_TO_CI_ANCHOR` 登记
   `("ci.yml", "Collect agent tests in clean env")`（S5x 要求新门禁声明 CI 对应物）。

**为什么门禁选「收集制」而不是「运行制」**：收集期是这次事故的实际故障面（开发者
未设环境变量时 `pytest` 直接 exit 2、整目录不可用）；运行制的干净环境要求被 4 个
文件挡住（见 Revisit），且运行 2 分 40 秒不适合放在 PR 路径的每次推送。
收集制 <2s、能拦住「重新引入控制面 import」这一**结构性回归**，与 issue 评论里
给出的 CI 门禁方案一致。

## Alternatives

- **在两处 import 上加 `pytest.importorskip` / 条件跳过**：弃——跳过会让「agent
  测试越界」长期隐身，正是本 issue 要消除的形态；fencing 测试本身有效，问题只是
  目录归属；
- **给 agent 测试 conftest 兜底设置 `JWT_SECRET_KEY`**：弃——把门禁要防的依赖
  固化进测试基建，等于宣布放弃自闭环承诺；
- **迁移全部 9 个引用控制面的 agent 测试文件**：弃（本轮）——其中 5 个文件只在
  **未被执行的代码路径**里 import 控制面（干净环境运行不报错），9 个全迁会白丢
  PR 路径覆盖；只迁/改真正在本轮故障面上的两处；
- **门禁改为「干净环境全量运行」**：本轮不取——见 Decision 与 Revisit（4 个文件
  需先迁出/改造，属测试拓扑调整，涉及 PR 覆盖取舍，留独立裁定）。

## Verification

- **红绿对照（收集期，干净环境）**：
  - 修复前 → `2 errors during collection`，exit 2（`test_agent_api_fencing_contract`
    / `test_step_log_batching`，RuntimeError: JWT_SECRET_KEY）；
  - 修复后 → **1858 tests collected（1.7s），exit 0**；
  - **反例**：把 `test_step_log_batching.py` 的越界 import 放回 → 门禁命令
    `1 error during collection`，exit 2；恢复后 exit 0；
- 迁移文件落位：`pytest backend/tests/api/test_agent_api_fencing_contract.py -q`
  → **8 passed**；
- 标准环境下 agent 全量（无回归）：`TESTING=1 JWT_SECRET_KEY=test-secret
  pytest backend/agent/tests/ -q` → **1858 passed**（160s）；
- 门禁接入：`python scripts/run_gates.py check:pr` → **[OK] check:pr (15 gates)**
  （新 `agent-tests-collect` 在列；`pr-migrate` 因本机拉不到 postgres:16 镜像
  显式 SKIP，以 CI 为准）；
- 治理面：`python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿
  （含新锚点登记）。

## Revisit

- **运行期仍未自闭环（本单未闭环的部分）**：干净环境**运行** agent 全量仍有
  **23 failed / 1835 passed**，集中在 4 个文件（实测）：
  | 文件 | 失败数 | 耦合形态 |
  |---|---|---|
  | `test_saq_scan_pipeline.py` | 16 | 运行期 import `backend.tasks.saq_tasks` 等控制面模块 |
  | `test_p3_3_multi_instance.py` | 4 | 运行期 import `backend.realtime` / `backend.scheduler` |
  | `test_legacy_tool_cleanup.py` | 2 | 测试体内 `import backend.api`（:138） |
  | `test_cron_scheduler.py` | 1 | 运行期 import 控制面 `cron_scheduler` 助手 |

  三条出口（需裁定，涉及 PR 覆盖取舍）：**(a)** 迁往 `backend/tests/`——归位准确，
  但这 4 个文件将退出 PR 路径（`backend/tests/` 只在夜间 `backend-test` 跑）；
  **(b)** 迁往根 `tests/` 离线子集——保住 PR 路径，但那批测试需要 `JWT_SECRET_KEY`
  等环境，本地 `repo-tests` 门禁要相应带上 env（并把「离线=零环境依赖」的口径说清）；
  **(c)** 逐文件改造（如 `test_legacy_tool_cleanup.py` 的单点 `import backend.api`
  可换成更轻的断言），成本最高但覆盖面损失最小；
- **本单门禁只守收集期**：若采纳 (a)/(b)/(c) 完成运行期解耦，应把门禁升级为
  「干净环境全量运行」（成本 160s，需评估是否放 PR 路径还是夜间）；
- **其余 5 个文件仍引用控制面模块**（`test_app_scheduler_executors` /
  `test_leader_election` / `test_mtbf_suite` / `test_socketio_redis_adapter` /
  `test_step_log_batching` 的轻度引用）：当前在干净环境**运行**不报错（未走到
  触发路径），属潜伏面；运行期门禁落地时会被一并暴露。
