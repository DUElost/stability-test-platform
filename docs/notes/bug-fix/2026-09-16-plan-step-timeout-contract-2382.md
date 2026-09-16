# PlanStep.timeout_seconds 两套契约同源：未配置不写键 + 0 缺停滞钟给人话（#2382）

Status: implemented
Class: bug-fix

## Decision

同一个字段有两份互相矛盾的权威，本单把它们收成一条判据：**「未配置」= 不写这个键**。

- 入口 `PlanStepIn.timeout_seconds` 是 `Optional[int] = None`，DB 列 `nullable=True`
  （`backend/models/plan.py:105`），引擎 `_resolve_step_wall_clock` 明确支持
  `None → STP_STEP_WALL_CLOCK_SECONDS → 300s`（`backend/agent/pipeline_engine.py:93`）；
- 但 `$defs.step` 把 `timeout_seconds` 列为 `required`，且三处组装器**无条件写键**——
  于是严格按 OpenAPI 省略该字段的调用方一律 422。

`stall_seconds` 从一开始就走「未配置就不写键」，所以这个字段的修法被同族的另一个字段
遗忘了一次。具体落法：

1. **唯一判据函数** `apply_step_timing_fields(step_def, *, timeout_seconds, stall_seconds)`
   （`backend/services/plan_dispatcher_core.py:195`），三个组装点共用：ORM 派发路径
   `build_lifecycle_from_steps`、run 快照重放路径 `build_lifecycle_from_snapshot`、
   保存/预校验入口 `_assemble_lifecycle_for_validation`。两个时间字段进同一个函数，
   是为了不再出现「一处修法被另一个字段遗忘」。
2. **schema 放宽 required、但不写 default**（`backend/schemas/pipeline_schema.json`）。
   有效默认随宿主 env 变，把数字烤进 schema 会掩盖 `STP_STEP_WALL_CLOCK_SECONDS` 的
   fleet 覆盖能力；`description` 里写清回落链。
3. **前端同步**：`frontend/src/utils/api/types.ts:924` 的 `PipelineStep.timeout_seconds`
   改 `?: number | null`（AGENTS.md 硬不变量：前端 API 类型与后端 schema 同步）。
4. **422 文案**：`timeout_seconds=0` 缺 `stall_seconds` 时，jsonschema 只说
   `'stall_seconds' is a required property`，客户端猜不到规则从哪来。入口新增
   `_actionable_lifecycle_errors()` 翻译成「0 表示不限墙钟，必须同时配
   stall_seconds >= 1；…否则请改填明确的服务端墙钟（引擎默认 300s）」，无关报错原样透传。
   顺手删掉入口里两段 O(n²) 的 stall 回填循环——helper 已覆盖同一判据。

判据核心，也是最容易看错的一点：**「省略键」≠「写 `null`」**。`timeout_seconds: null`
被 `{"type": "integer"}` 直接拒（已实测），只有省键才表达未配置。所以修法必须在组装侧
省键，而不是放宽 schema 去吃 `null`。

## Alternatives

- **入口收紧为必填 `timeout_seconds: int = Field(300)`**：否决。会把 Agent 的 env 覆盖
  能力抹成一个计划事实，且需要前端与历史行迁移。
- **入口加 `Field(ge=0)`**：否决。值域权威是 schema（实测 `-5` 报
  `less than the minimum of 0`，原文案已可行动），加 `ge` 反而把 422 body 从
  `{code: INVALID_LIFECYCLE}` 变形成 pydantic 结构；grep 确认无既有测试依赖负数路径。
- **让 schema 接受 `null`**：否决。「谁都没配」与「显式配了个非法值」是两件事，合并就把
  契约模糊化重演一遍。
- **照 issue 建议写 `default: 0`**：否决，且这是本单唯一会**主动拆安全网**的选项——`0` 在本仓
  语义是「不限墙钟」（`communicate(timeout=None)`，子进程跑到退出为止）。给「未配置」挂 `0`
  的 schema default，等于所有没配墙钟的步骤从此没有总时长上限，与引擎
  `None → STP_STEP_WALL_CLOCK_SECONDS → 300s` 的回落链正好相反。issue 给的另一形态
  （入口收紧必填 `Field(300)`）同样不采：它把宿主级覆盖烤成计划事实。
- **在 `pipeline_validator` 里加文案分支**：否决。控制面与 Agent 各有一份字节相同的副本，
  `backend/agent/tests/test_pipeline_validator_parity_738.py` 已按「语义一致而非逐字相同」
  裁决过（#738）；在 validator 加分支会让两份立刻分叉，而 Agent 侧本就不需要这句人话。
  因此翻译写在**入口呈现层**。
- **顺手合并两份 validator**：否决，#738 的独立范围。
- **测试落 `backend/tests/services/`**：否决。根 `tests/` 才在 required check
  `pr-agent-tests` 的 PR 路径里（#1569 前移），`backend/tests/` 只在夜间全量 job
  （`backend-test`，`if: github.event_name != 'pull_request'`）。本单要防的正是「入口 422」，
  必须 PR 路径必拦。
- **测试顶层 `import backend...`**：否决。`backend.core.database` 在导入期解析
  `DATABASE_URL`，而根 `tests/` 无 conftest 注入；改用子进程探针显式喂假连接串，既不把本机
  （可能是生产）连接串带进子进程，也不把 env 固化到同进程其它用例——与
  `tests/test_plan_run_abort_import_contract.py`（#2372）同一手法。

## Verification

**route 层回归（issue 点名的那条：「按 OpenAPI 省略 timeout_seconds 仍能创建」）**
`backend/tests/api/test_plans_api.py::TestPlanCRUD::test_create_plan_omitting_timeout_seconds`
真 `POST /api/v1/plans` 断 201、读回 `timeout_seconds is None`（不许被偷偷塞成 0 或 300）；
`test_zero_timeout_without_stall_explains_the_gate` 断 422 文案含 `stall_seconds >= 1` 且不含
`is a required property`。落 `backend/tests/` 是**补充**而非替代——它只在夜间全量 job 跑，
PR 路径必拦的判据仍在根 `tests/`。

新契约文件 `tests/test_plan_step_timeout_contract.py`，23 例分三层：

- **schema 层**（纯 `jsonschema`，零 backend import）：省键通过、显式 `null` 仍拒、
  `-5` 仍拒、`0` 缺停滞钟仍被门禁拒、`0`+`stall=60` 通过、`default` 不许被烤进去。
- **接线层**（AST/文本）：step_def 字面量扫描面**钉死为 3**（防恒真）、三处都不许再写
  `timeout_seconds`/`stall_seconds`、helper 调用点 2+1、入口必须从 `plan_dispatcher_core`
  import（不许复制实现）、前端 `PipelineStep` 与 schema 同为可选。
- **行为层**（子进程探针 11 场景）：含本单的复现形状——`PlanStepIn` 只给必填字段，过去
  一律 422，现在通过；`timeout_seconds=0` 缺停滞钟的 422 文案含 `timeout_seconds=0` /
  `stall_seconds >= 1` / `300` 且保留 `lifecycle.init.0` 路径前缀；无关报错
  （空 version）逐字透传。恒真防护：`test_probe_covers_every_scenario` 钉住场景全集。

红绿双向（破坏性对照，一律 `/tmp` 备份 + 还原，不用 `git checkout`）：

| 变异 | 打红 |
|---|---|
| schema 改回 `required` | 8 例（schema 层 + 探针 + 前端同步） |
| helper 无条件写键（null 泄漏） | 6 例探针场景 |
| 入口组装点写回硬编码 | 接线例 + 2 例入口探针 |
| 去掉可行动文案包裹 | 文案场景 1 例 |
| `types.ts` 的 `PipelineStep` 改回必填 | 前端同步 1 例（tsc 抓不到，故需结构断言） |
| 快照路径不再共用 helper（行为等价） | 共用判据 1 例 |
| route 层：schema 改回 `required` | `test_create_plan_omitting_timeout_seconds` 红 |
| route 层：入口写回硬编码 `None` | 同上红（两条判据各自独立生效） |
| route 层：去掉文案包裹 | `test_zero_timeout_without_stall_explains_the_gate` 红 |

实跑命令与结果：

- `env -u TESTING -u JWT_SECRET_KEY -u DATABASE_URL python -m pytest tests/test_plan_step_timeout_contract.py -q` → 23 passed
- 同上跑整个 PR 路径子集 `pytest tests/ --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py` → **1228 passed**（clean env，证明探针不污染同进程）
- `pytest backend/tests/core/test_pipeline_validator.py backend/tests/services/test_plan_barrier_timeout.py -q` → 43 passed
- `pytest backend/tests/services/test_plan_dispatcher.py backend/tests/services/test_plan_dispatcher_device_validation.py backend/tests/api/test_plans_api.py backend/tests/api/test_plan_runs_api.py -q` → 158 passed
- `pytest backend/tests/api/test_plans_api.py -q` → **72 passed**（含本单新增 2 条 route 回归）
- rebase 到 `a789c99d` 后复跑：`pytest tests/`（同前两个 ignore）→ **1228 passed**；
  `pytest backend/tests/services/test_plan_dispatcher.py backend/tests/api/test_plans_api.py backend/tests/core/test_pipeline_validator.py backend/tests/services/test_plan_barrier_timeout.py -q` → **148 passed**；`check:quick` → **[OK] (10 gates)**
- `env -i pytest backend/agent/tests/ -q`（剥掉 job env，照 #739 的收集守卫形态）→ 23 failed / 2095 passed；**同一命令、同一剥法在未改动的 main（`c2a6147b`）上 FAILED 名单逐字相同**（`diff` 为空，落在 `test_saq_scan_pipeline` / `test_cron_scheduler` / `test_legacy_tool_cleanup` / `test_p3_3_multi_instance`）。这只说明这些用例有 job env 依赖，不构成本单回归；带 env 的正确跑法见下条
- `env -u DATABASE_URL -u TEST_DATABASE_URL TESTING=1 JWT_SECRET_KEY=ci python scripts/run_gates.py check:pr` → **[OK] check:pr (19 gates)**（含 layering / invariant-diff / pollution / immutability / alembic-immutability / ip-leak / prom-alerts / agent-tests-collect / agent-tests / pr-migrate 空库迁移）
- `python -m ruff check` 三个改动文件 + 新测试 → All checks passed
- `env -u DATABASE_URL TESTING=1 python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**（含 ruff / eslint / tsc / knip；本机可能同时是生产库宿主，故显式剥 `DATABASE_URL`）
- 前端类型检查由 `check:quick` 的 `tsc` gate 实跑（`npm run type-check` = `tsc --noEmit && tsc --noEmit -p tsconfig.node.json`）→ 绿。展示层 `stepTiming.ts` 早已区分 `0→∞` / 缺省→默认 / `n>0` 三态，故类型放宽零涟漪——也正因 tsc 抓不到，前端判据走 AST/文本断言。

严重度判据（为什么可以只放宽 schema）：`validate_pipeline_def` 在 **Agent 侧只被
`install_selfcheck.py` 使用**——收到运行时 pipeline 不做 schema 校验；schema 文件虽经
`host_updater.py` / `stp_agent_priv install-schema` 下发，只用于自检。放宽 `required`
不会让老 Agent 拒绝计划。全仓只有一份 `pipeline_schema.json`。

## Revisit

- **两份 `pipeline_validator` 仍是重复实现（#738）**。本单刻意不碰，但文案写在入口意味着
  入口与 Agent 侧的「可行动程度」不对称——若将来 API 之外的路径（AI assistant、批量导入）
  也报这条错，需要把翻译提成一个共享的呈现层函数，而不是各处复制。
- **`build_plan_snapshot` 仍把 `None` 原样存进快照**（`backend/services/plan_dispatcher_core.py:569`）。
  这是有意的：快照存事实、组装时才表达「未配置」。历史 run 的快照里已有 `null`，重放走
  `build_lifecycle_from_snapshot` 会被省掉，不产生新的一致性风险。若将来要让 schema 表达
  默认值，前提是把 `STP_STEP_WALL_CLOCK_SECONDS` 收进 Plan 字段（它现在是宿主 env，不是
  计划事实）。
- **PR 路径与夜间全量的覆盖差（#2333 在治）**：本单把契约测试放 `tests/` 正是绕开这个洞；
  等 `backend/tests/` 进 PR 路径后，探针可以考虑改为 fixture 直跑（省下 ~1s 的子进程成本）。
- **前端 `PipelineStep` 是编辑器本地类型**（`frontend/src/utils/api/types.ts:1040` 声明 Plan 不再含 lifecycle JSON），
  真正会收到「缺 `timeout_seconds` 键」的响应是 `PlanRunPreview` 与 run 快照。展示层目前
  已按三态分流，若将来加新的消费方，判据应复用 `stepTiming.ts` 而不是各自 `?? 300`。
- **`plan_dispatcher_core.py` 的 import 位置**：`plans.py` 顶部 import 块本就无序
  （ruff 未开 isort），新增行按现状插在 `script_progress_capability` 之后。若将来开 I001，
  这里会一起被重排，不是本单判据。
