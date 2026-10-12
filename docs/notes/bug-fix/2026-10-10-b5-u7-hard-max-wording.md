# B5-U7：对齐 hard-max 继承文案（#3670 / #3653）

Status: implemented
Class: bug-fix

## Decision

按 #3653 §3.7 / §4.2 U7 与 #3670 限定的三文件 scope，清理 #3668 通过复核时留下的两处陈旧 hard-max 文案，对齐已 Accepted 的 ADR-0060 v1.1（#3652 裁决落地于 #3668）。不重新裁决模型，不改校验条件、类型/默认/范围、错误码结构、写入行为或登记表除 meaning 外的字段。

- `backend/api/routes/plans.py`：`PlanCreate.barrier_max_wait_seconds` 注释改为「正整数=显式硬顶；None=继承主机 `STP_BARRIER_MAX_WAIT_SECONDS`（未设/非法回落 1800s；环境变量非正值才表示无硬顶）」；`PlanUpdate` 保留「同 PlanCreate」。`_validate_assembled_lifecycle` 删除「null 才是无上限」，保留显式硬顶与协调器存活窗同判说明；422 `INVALID_BARRIER_CONFIGURATION` message 中 `barrier_max_wait 留空=…` 片段替换为「继承主机安全策略（STP_BARRIER_MAX_WAIT_SECONDS → 1800s；环境变量非正值才表示无硬顶）」。
- `backend/schemas/plan_settings.json`：`scope=plan,path=["barrier_max_wait_seconds"]` 的 meaning 定为「从首次等待开始计时。正整数为计划显式硬顶；未设时继承主机安全策略，不是关闭硬顶。」；删除「与 ADR 原文冲突 / 见 #3652 / 本批按现行代码展示」过渡句；`unset_state` / `fallback_chain` / `write_boundary` / `ui_editable` / `semantic_anchors` 未改。
- 本 Note 路径取仓库约定目录 `docs/notes/bug-fix/`（#3653/#3670 派单写作 `bugfix`，与 notes README 布局不一致；Class 与目录须一致）。

1800s 只作回落链说明，不宣称任一 host 已确认实际值。脚本版本与模板 pin：无。生效随 B5 §5 一次控制面部署。

## Alternatives

- 一并改 ADR / Agent / 前端 types 或重开 #3668 复核：拒绝。模型与指定注释已由 #3668 落地；本单元只清残留文案。
- 新增镜像文案测试或 CI 守卫钉死字面串：拒绝。#3653 §4.2 U7 明确不新增；现有 barrier 不变式与登记表结构测试承接行为保护。
- 把 meaning 写进「无硬顶」或把 1800s 写成已确认运行值：拒绝。与 Owner #3652 裁决及 ADR-0060 v1.1 冲突。

## Verification

开工 `origin/main` SHA：`43dfc764a8881fabfed7979b3a40e6662f3c6120`。隔离库：`TEST_DATABASE_URL=postgresql+psycopg://stp_test:***@127.0.0.1:5432/stp_u7_test`（库名含 test；本环境无 `.env.backend`）。本环境无 Docker 守护进程；pytest 前在 workload cgroup 写入 `memory.max=6G`、`memory.swap.max=0` 以满足 `run_pytest.py` 边界。

- `./scripts/project_python.sh scripts/run_gates.py check:quick`：exit 0，`[OK] check:quick (16 gates)`。
- `./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_plan_barrier_invariant_2948.py backend/tests/services/test_plan_settings_registry.py -q`：exit 0，**7 passed**，1 warning。
- `npm --prefix frontend run type-check`：exit 0。
- `./scripts/project_python.sh scripts/run_pytest.py tests/ -q`：汇总 **2292 passed，78 skipped，5 failed，11 errors**（约 193s）；失败与错误均为 `docker.errors.DockerException`（无 Docker socket）：`tests/test_alembic_upgrade.py`、`tests/test_seed_default_params_face_3289.py`、`tests/test_diag_readonly_role_pg.py`、`tests/test_script_seed_governance.py`。同入口单测 `tests/test_alembic_upgrade.py::test_alembic_upgrade_head_from_empty_on_postgres_17` 确认 **EXIT:1**。**不能写成通过**（与 #3653 §8.3 U2 根 tests 欠项同类）。
- 对账：相对 main 仅三文件；`plans.py` 仅注释/422 message；登记表仅 meaning；旧句「不设硬顶」「null 才是无上限」「与 ADR…冲突 / 见 #3652 / 本批按现行代码展示」已撤除；`unset_state=env_fallback` 与回落链含 1800 未改。受影响前端 vitest：不适用。

## Revisit

若运行期 hard-max 解析链（`pipeline_engine` / host env）变更，或 Owner 再次裁决 NULL 语义，须同步本注释、422 片段与登记表 meaning，并区分文档修订与 L4 已确认值。
