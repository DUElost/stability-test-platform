# 后端 import 边界合约（import-linter）取代 check_layering

Status: implemented
Class: architecture

## Decision

仓库根新增 `.importlinter`，用 import-linter 表达 5 条后端边界合约，接入 `layering` gate
（`scripts/run_gates.py`，并加入 `check:quick`）与 `ci.yml` lint job 的「分层检查」step；
删除 `tools/dev/check_layering.py`。

| 合约 | 约束 | 基线 |
|---|---|---|
| C1 控制面分层 | main > api.routes : scheduler : tasks : scripts > services > realtime \| api.schemas > models > core | 9 |
| C2 services 不直接依赖 HTTP | 禁 fastapi / starlette / api.routes / api.error_helpers / api.response | 36 |
| C3 控制面不直接 import backend.agent | agent 是独立部署单元 | 5 |
| C4 core 不直接依赖 web 框架 | 禁 fastapi / starlette | 6 |
| C5 services 子模块无环 | acyclic_siblings，含函数体内 import | 5 |

- 现存 61 条违规逐条写入各合约 `ignore_imports`，并开启 `unmatched_ignore_imports_alerting = error`：
  基线只减不增，修掉一条违规须同 PR 删掉对应行，否则合约失败；
- grimp 默认把函数体内 import 计入依赖图——#738 所说「局部 import 藏依赖」在这里可见：
  只看顶层 import 时后端没有环，计入局部 import 后有 6 个环（最大 20 模块）；
- 两个口径选择：`api.schemas` 视为 services 之下的 DTO 层（services→schemas 18 处不算违规，
  长期应迁 `backend/schemas`）；realtime 视为 services 之下的推送端口（以 `socketio_server`
  拆分后的形态为准，现存 realtime→services 2 处入 C1 基线）；
- 依赖：`import-linter>=2.15,<3.0` 进 `requirements-dev.txt`，lock 由
  `scripts/ci/regenerate-lock.sh` 重生成（新增 import-linter / grimp / rich / markdown-it-py / mdurl，
  既有 pin 不变）；CI step 取 lock 精确版本，与 ruff 同取法；
- 顺带更正两处文字引用（`docs/design/2026-08-governance-surface-protection.md`、
  `test_jira_vendor_engine.py` docstring）。

## Alternatives

- **扩写 check_layering.py**：弃——每加一条边界就要手写一段正则扫描 + self-test，
  而且看不见传递依赖和环；这正是治理面按事故逐条累积的模式。
- **一次性清零再接入**：弃——61 处涉及 30+ 文件，与接入门禁混在一个 PR 里评审面过大；
  基线棘轮让门禁今天就能拦住新增违规，存量按领域分批清。
- **把 api.schemas 列为 services 之上**：弃——会把 18 处 DTO 引用全部记为违规，
  掩盖真正的 HTTP 耦合（C2）；DTO 位置问题单独处理。

## Verification

- `python scripts/run_gates.py check:quick` → 17 gates 通过（`schema-at-head` 因无 DATABASE_URL 跳过）；
  `layering` 输出 5 条合约全部 KEPT；
- 根 `tests/` 离线子集（与 repo-tests gate 同口径）→ 1771 passed, 67 skipped；
- 棘轮双向反例（临时副本）：注入 `services.plan_wifi → api.routes.auth` → C1、C2 BROKEN；
  注释掉 `agent_upgrade_gate` 的 `api.error_helpers` import → 报 unmatched ignore；
- 全仓分析 837 文件，耗时约 0.3s。

## Revisit

- 任一合约基线降到 0：删掉该合约的 `ignore_imports` 段；
- 需要新增 ignore 行：视同上调棘轮，PR 描述写明 issue 号与终态出口；
- `tests/test_agent_import_boundary.py`（agent→控制面方向）可考虑并入本配置为 C6，
  前提是其 `_SHARED_ALLOWLIST` 的「模块体纯度」判据能等价表达。
