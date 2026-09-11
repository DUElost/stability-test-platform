# 审计：文档 / 架构完整性（单轴证据报告）

Status: implemented
Class: process

- 仓库：`/home/debian13/stability-test-platform`，工作树活跃（审计期间 `find docs -name '*.md'` 在 615→618 间波动，计数为取样时点值）
- 范围：ADR 库存、ADR 落地证据、文档漂移门禁、DOC-MAP、矛盾、陈旧度、孤儿、验收可追溯
- 结论摘要：门禁**存在**（`tools/dev/check_governance_surface.py` S1–S13 在 required `lint` job）；`types.ts`↔后端 schema 同步**无任何门禁**（仓库自认 residual）；1 组硬不变量与代码矛盾（复数表名）；1 组 ADR 索引↔正文矛盾（ADR-0023）；1 个重复编号的未登记 Accepted ADR

## ADR INVENTORY

- `ls docs/adr/*.md | wc -l` = **37**（+`README.md`）；README 主表（`docs/adr/README.md:50-87`）登记 **36**（ADR-0001…0036）→ **1 个未登记文件**
- 状态计数（README 主表第 3 列）：**Accepted 31** / Superseded 3（0002、0009、0010）/ Deprecated 1（0005）/ Proposed 1（0036）/ **Rejected 0**
- 头部 `状态` 行 ↔ 主表状态**词级全一致**（S12 门禁强制）；`ADR-0022` 用 `| Status | Accepted |` 表头、`ADR-0035` 用粗体 `- **状态**：`，门禁均能解析
- 异常 A：**编号重复** —— `docs/adr/ADR-0031-appendix-phase3-core-write-tools.md` 与 `ADR-0031-platform-ai-assistant.md` 并存，违反 `docs/adr/README.md:21`「编号规则：按提交顺序递增，不复用旧编号」；附录自述 `- 状态：**Accepted**（#658 合入 main，2026-08-31）`，但 `grep -c 0031-appendix docs/adr/README.md docs/DOC-MAP.md` = `0 0`（未登记）
- 异常 B：主表「类型」列是自由散文，**无门禁校验**（见 C2）

| ADR | 主表类型列（README 行） | ADR | 主表类型列 |
|---|---|---|---|
| 0001 | 已实现 (:52) | 0019 | 已实现 Phase 1-6e (:70) |
| 0003 | 已实现 2026-03-16 更新 (:54) | 0020 | 预扩展/重构 (:71) |
| 0004 | 已实现 watchdog (:55) | 0021 | 已实现 C5a–C6 (:72) |
| 0006 | 已实现 (:57) | 0022 | 已实现 (:73) |
| 0007 | 已实现 (:58) | 0023 | **已实现** (:74) ← 与正文矛盾 |
| 0008 | 预扩展/重构 (:59) | 0024 | v1.1 例外契约化；v1.0 已实现 (:75) |
| 0011 | 第一层已实现 (:62) | 0025 | 已实现 Sprint 1–4 (:76) |
| 0012 | 第 1 层已实现 (:63) | 0026 | P0–P2 已收口 (:77) |
| 0013 | 已实现 (:64) | 0027 | P3-1..3 opt-in 落地 (:78) |
| 0014 | 已实现 (:65) | 0028 | 方案 A 生产生效 (:79) |
| 0015 | 已实现 (:66) | 0029 | M1→M4 已落地 (:80) |
| 0016 | 已完成 零残留 (:67) | 0030 | P0✅/P1✅/P2 核心✅ (:81) |
| 0017 | 已实现 (:68) | 0031 | 阶段二全栈 ✅ (:82)（附录未登记） |
| 0018 | 已实现 (:69) | 0032 | v0.7 P1 已合入 (:83) |
| | | 0033 | **未落地** (:84) |
| | | 0034 | Accepted v1.10 (:85) |
| | | 0035 | Accepted v1.2；实施未启动 (:86) |

## ACCEPTED ADR IMPLEMENTATION EVIDENCE（14 抽样）

| ADR | 规范主张（file:line） | 代码证据 | 测试/门禁证据 | 判定 |
|---|---|---|---|---|
| 0008 | `ADR-0008:20-27` 禁 `create_all` 演进生产 schema；结构变更全走 Alembic；启动仅告警 | `backend/alembic/versions/*.py`=119；`backend/scripts/check_schema_sync.py:1-16` 用 `alembic.autogenerate.compare_metadata` 断言 diff ⊆ 基线；`backend/main.py` 无 `create_all`；唯一 `create_all` 在 `backend/scripts/init_dev_db.py:1-3`（`DEV ONLY` + `_refuse_production`） | `tests/test_alembic_heads.py:12`、`tests/test_alembic_upgrade.py`、`backend/tests/test_schema_sync_guard.py`；CI required job `pr-migrate-empty-db` 跑 `check_schema_sync`（`ci.yml:265,308`） | 已实现+已测+CI |
| 0016 | `ADR-0016:26-28` 禁新增 BaseTestCase/禁桥接层 | `test_framework.py` 不存在；`grep -rn BaseTestCase backend/ frontend/src/` 仅剩 3 处脚本内注释（`backend/agent/scripts/gpu_setup/v1.0.5-1.0.7/gpu_setup.py:61`）+1 处 alembic 种子注释 | `backend/agent/tests/test_legacy_tool_cleanup.py:33-54` 断言禁用词（含 `test_framework.py`）不再出现 | 已实现+已测 |
| 0018 | `ADR-0018:22-62` SAQ/APScheduler/python-socketio；`:184-195` 不变量（Redis 仅队列） | `backend/main.py:317` ASGIApp；apscheduler 生命周期 `backend/main.py:218-275`；SAQ 指标 `backend/core/metrics.py:270-274`；Redis 定位 `docs/design/2026-storage-roles-and-aliases.md:20` | `backend/tests/tasks/test_saq_tasks.py`、`backend/tests/scheduler/*`；**Redis 角色不变量无门禁**（自认 residual：`docs/design/2026-08-governance-surface-protection.md:129`） | 已实现；部分已测 |
| 0020 | `AGENTS.md:15` 已发布脚本版本不可原地改/删 | `tools/dev/check-script-version-immutability.py`；运行时 422：`backend/api/routes/scripts.py:476-481` | `tests/test_script_version_immutability_gate.py`；CI **BLOCK** 步骤 `ci.yml:184-187`；`backend/tests/api/test_scripts_default_params.py` | 已实现+已测+CI 阻断 |
| 0021 | `ADR-0021:37-52,96-101` 派发门禁 + 平台 DB 为脚本内容唯一权威 + verify-once | `backend/services/plan_dispatcher_core.py:27-64`（`missing_scripts` fail-fast）；调用点 `plan_dispatcher.py:96`、`plan_dispatcher_sync.py:384,479`；`backend/core/metrics.py:642 record_dispatch_gate`；Agent RPC `backend/agent/script_verifier.py:66` + `backend/agent/socketio_client.py:152-167` | `backend/tests/services/test_plan_dispatcher_precheck.py`、`test_precheck_scripts.py`、`test_script_catalog_version.py` | 已实现+已测 |
| 0023 | `ADR-0023:39-71` D1 缺元数据 fail-fast；sha256 溯源观测面 | `backend/services/script_catalog.py:50-51 sha256_file`、`:204-217`；观测面类型 `frontend/src/utils/api/types.ts:2-3`（ADR-0021 host 活动作业快照） | 同上 catalog 测试矩阵 | D1 已实现+已测；**D2–D8 正文自述仍 Proposed**（`ADR-0023:3`） |
| 0024 | `ADR-0024:23-68` HttpOnly Cookie / CSRF Origin 白名单 / jti 黑名单 / 分类观测；`:144-175` v1.1 internal 例外 | `backend/core/security.py:220-249`（httponly/secure）、`:38 PRODUCTION_LIKE_ENVS`、`:76-83`（`AUTH_COOKIE_SECURE` 仅 `ENV=production` 强制）；`backend/core/csrf.py`（105 行）挂载 `backend/main.py:70,337`；jti 撤销 `security.py:183`、`api/routes/auth.py:394-402`、表 `revoked_refresh_token`（`alembic/versions/f1a2b3c4d5e6:23`） | `backend/tests/test_csrf_origin_middleware.py`（17 个用例）、**`backend/tests/test_agent_secret_guards.py:72,86` 正是 ADR-0024 v1.1 点名的两个用例**、`backend/tests/api/test_auth_cookie_session.py`、`test_refresh_token_blacklist.py` | 已实现+已测（本组证据最硬） |
| 0029 | `ADR-0029:428-462` D10 删 `device.project_id` 改 JOIN 派生；`project_model` 为成员唯一事实源 | `grep -rn project_id backend/models/*.py` → 仅 `plan.py:56`（可空）、`plan_run.py:55`（悬空快照）、`project_model.py:34`；**无任何 Device 模型列**；`backend/services/project_attribution.py:3`「归属 = device.model ⋈ project_model（活跃成员行）。无副本列」、`:20 resolve_project_id` | `backend/tests/services/test_project_attribution.py`、`backend/tests/api/test_project_routes.py` | 已实现+已测（D1/D4/D5/D7-D9 自述挂起） |
| 0030 | `ADR-0030:45-105` test_suite/test_case + 绑定门禁（D3b） | `backend/models/suite.py:37 test_suite`、`:98 test_case`、`backend/models/case_result.py:20 test_case_result`；`backend/services/suite_binding.py`；`backend/api/routes/suites.py` | `backend/tests/api/test_mtbf_suite_routes.py`、`backend/tests/services/test_suite_binding_gate.py`、`test_test_case_result_ingest.py` | 已实现+已测（JobArtifact report 白名单自述未做） |
| 0031 | `ADR-0031:45-60` T0–T3 自治分级 + 权限 ⊆ API 权限 | `backend/services/ai_assistant/{orchestrator,tools,actions,authz,t2b_allowlist,dispatch}.py`；`backend/api/routes/ai_assistant.py` | `backend/tests/services/test_ai_authz.py`、`test_ai_tools.py`、`test_t2b_allowlist.py`、`test_ai_dispatch.py`、`backend/tests/api/test_ai_assistant_endpoints.py` | 已实现+已测；附录 ADR 未登记（异常 A） |
| 0032 | `ADR-0032:31-115` platform 路由 + 双链路归档 | `backend/core/dedup_platform.py:5-6`（`mtk`/`unisoc`）；`backend/agent/aee/collectors/unisoc.py`、`backend/agent/aee/unisoc_reconciler.py`、`backend/agent/device_platform.py` | `backend/tests/core/test_dedup_platform.py`、`test_dedup_scan_merge.py`、`backend/agent/tests/test_unisoc_reconciler.py` | 已实现+已测（真机验收待） |
| 0033 | `ADR-0033:150-194` 自述「**落地状态：未落地**（Phase 2/3 零启动）」 | `find . -name 'tool_manifest*'` → 仅 docs（ADR/design/notes），**代码零命中** | 无 | 自述一致；D0 不入仓未独立核验 → **UNVERIFIED** |
| 0034 | `ADR-0034:97-108` 契约权威分家（ADR 定方向，契约文档定细则） | `tools/dev/ai_work.py`；`docs/development/ai/execution-contract.md:3` Living v1.12 == `docs/DOC-MAP.md:89` v1.12（S13 门禁） | CI `python tools/dev/ai_work.py --self-test`（`ci.yml:208-209`）；无语义单测；验收=附录 A 手工矩阵（`ADR-0034:151`） | 文档级已核验 |
| 0035 | `ADR-0035:21`「ADR 转 Accepted **不等于**实施已启动」；§4 目标=每主机凭据 | 无主机凭据哈希列/注册质询代码；仅 `backend/core/host_identity.py:10 ip_to_host_id`（IP 派生 ID ≠ §4 方案 A） | `backend/tests/test_host_identity.py`（5 用例，全为 IP-ID） | 自述一致（deferred）；实现按设计不存在 |

## DRIFT GATES

**EXISTS（非"无门禁"）**：`tools/dev/check_governance_surface.py` —— 13 条确定性规则，在 required `lint` job 运行（`.github/workflows/ci.yml:114` 定义 `lint`；`:205` `--self-test`、`:206` `--check`）。相关规则：`S2` 根治理文档/DOC-MAP/Harness 适配说明的相对链接目标必须存在（自述「实测发生过 DOC-MAP 断链」`check_governance_surface.py:11`）；`S5` required checks 文档↔workflow 互检（`AGENTS.md:71`）；`S11` AGENTS.md 硬不变量锚点逐条在场（防整条删除，覆盖 `AGENTS.md:15-31` 九条）；`S12` ADR 头部状态/版本 ↔ README 主表 / DOC-MAP / M7 看板；`S13` 执行契约版本 ↔ DOC-MAP。**实跑**：`python3 tools/dev/check_governance_surface.py --check` → `[OK] 治理面结构检查通过（阻塞项全绿：S1–S13、S5x）`，exit 0。
其他：`tools/dev/check_invariant_diff.py`（Pydantic v1 / 复数表名 / 裸 pytest，**仅 PR 新增行**，`lint` job）；`tools/dev/check-script-version-immutability.py`（BLOCK，`ci.yml:184`）；`backend/scripts/check_schema_sync.py`（required `pr-migrate-empty-db`，`ci.yml:308`）。

**ABSENT**：
- markdownlint / markdown-link-check / doc-lint：`grep -rniE "markdownlint|link-?check|doc-?lint" .github/workflows/` → **0 命中**；S2 只覆盖根治理文档+DOC-MAP，全库无链接门禁（自建检查见下节 6 处断链）
- **`frontend/src/utils/api/types.ts` ↔ 后端 schema：无测试、无 CI 步骤**。`grep -rniE "openapi|type.?sync|schema.?drift|contract.?test" tests/ scripts/ .github/workflows/` → 0 命中；`scripts/`、`tools/` 内无任何 openapi 生成器。仓库**自认**该缺口：`docs/design/2026-08-governance-surface-protection.md:134`「前端 `types.ts` 与后端 schema 同步 | **无（手维护；typecheck 只查 TS 内部）** | `frontend/package.json` 无生成器 | **residual**（review 兜底）」。`AGENTS.md:31` 据此陈述了一条无人强制的硬不变量；`types.ts` 2076 行纯手维护，最后改动 2026-09-11
- 同表自认 residual：ASGI 入口（`:126`）、Redis 角色（`:129`）、`python -m`（`:135`）

## CONTRADICTIONS / STALENESS

- **C1 表名（硬矛盾）**：`AGENTS.md:29`「数据库业务表名使用单数」 vs 7 张生产复数表：`backend/models/audit.py:14 "audit_logs"`（`:11` 注释「Keeps `__tablename__ = "audit_logs"` (plural) to match the existing」）、`device_lease.py:24 "device_leases"`、`notification.py:35/48/62 "notification_channels"/"alert_rules"/"notification_logs"`、`schedule.py:24 "task_schedules"`、`user.py:11 "users"`。门禁是差异面的：`tools/dev/check_invariant_diff.py:21` 只拦「`backend/migrations/**` create_table/CREATE TABLE 引用复数表名」 → 存量复数表被祖父化，但 AGENTS.md 表述为绝对不变量、无任何文档登记该例外
- **C2 ADR 索引 ↔ 正文（硬矛盾）**：`docs/adr/README.md:74`「ADR-0023 … Accepted … **已实现**」 vs `docs/adr/ADR-0023-script-traceability.md:3`「Accepted（D1 已实现；**D2-D8 仍 Proposed**）」。S12 只比对 status 词（`check_governance_surface.py:27-28` 自述），该类落在类型/落地列的矛盾**静默通过 CI**
- **C3 编号重复 + Accepted 孤儿 ADR**：ADR-0031 双文件（异常 A）；主表准入规则 `README.md:21` 禁止复用编号；附录在 README/DOC-MAP 中 0 引用，唯一入链 `docs/notes/feature/2026-08-31-ai-assistant-t0-deep-read-pr-a.md:8` **是断链**（`../adr/...` 从 `docs/notes/feature/` 解析到不存在的 `docs/notes/adr/`）
- **已检查、无矛盾**：ASGI 入口 —— `AGENTS.md:23` == `docs/design/00-system-overview.md:56` == `02-backend.md:45` == `06-realtime-and-background.md:16` == 代码 `backend/main.py:317 python_socketio.ASGIApp(sio_server, _fastapi_app)`；Redis 角色 —— `AGENTS.md:27` == `docs/design/2026-storage-roles-and-aliases.md:20` == `design/06:60-64`；端口 —— React 5173（`frontend/vite.config.ts:76` == `design/00:21`）、API 8000（`local-development.md:87`）；env 名 —— `docs/development/environment-variables.md`（132 行，≥40 个 `STP_*`）抽查无冲突（全量集合差 **UNVERIFIED**，未计算）
- **Python 版本（文档 vs 工具链）**：`README.md:69-70`「Python 3.10+（推荐 3.11）」、`local-development.md:13`「3.10+」 vs `ruff.toml:10 target-version = "py311"`、CI `python-version: "3.11"`（`ci.yml:53,130,248,294,340`）、`Dockerfile.backend:1 FROM python:3.11-slim`；全仓无 `requires-python`；本机 venv 为 3.13.5 → 文档声明的下界（3.10）无任何门禁与锁背书
- **陈旧度**：`docs/DOC-MAP.md:3`「**最后更新**：2026-09-06」，但同文件 `:57` 链接 `reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_705379.md`，`git log -1 --format=%cs -- docs/DOC-MAP.md` = 2026-09-10 → 头部日期落后自身内容与最后一次提交
- **最差漂移**（`git log -1 --format=%cs -- <doc>` vs 其引用的代码路径）：`docs/design/2026-07-plan-execute-page-improvements.md` doc=**2026-08-06** vs 引用 `frontend/src/utils/api/types.ts`=**2026-09-11**（**36 天**）；`docs/operations/incident-2026-07-31-script-sha-drift-dispatch-outage.md` 2026-08-13 vs 2026-09-08（26 天）；`docs/design/2026-device-log-event-implementation-spec.md`、`2026-plan-c-storage-and-access.md` 2026-08-24 vs 2026-09-11（18 天）。权威树（design/development/operations/ADR）整体较新（≤7 天），因 S12/S13 把 ADR 与契约版本钉进了门禁

## ORPHANS

- `find docs -name "*.md" | wc -l` = **618**；`docs/archive` = **110**；`docs/notes` = **357**
- 链接图（仅 markdown 链接）：**425/617** 无任何入链；**391/617 既无入链也无出链（真孤儿）**；`docs/archive` 中 **80/110 为真孤儿**
- 例子：`docs/architecture/non-adr20-followups.md`、`docs/archive/openspec/changes/archive/2026-02-23-task-pipeline-realtime-logs/proposal.md`、`docs/design/README.md`、`docs/development/README.md`、`docs/acceptance/README.md`（均无入链，只能靠目录导航到达）；`docs/notes/architecture/2026-09-0*.md` 系列 8 篇无入链，含 ADR-0024 v1.1 当作背景证据引用的 `2026-09-08-internal-secure-cookie-exception.md`
- 全库相对链接自检：**951 条中 6 条断链**（全部在 `docs/notes/`，含指向 ADR 的两条：`2026-08-31-unisoc-toolkit-73-463-alignment.md:6 → ../adr/ADR-0032`、`2026-09-08-notification-delivery-semantics-adr0036.md:8 → ../adr/ADR-0036` 与 `ADR-0011`，均为层级写错；另 `2026-09-10-script-bloat-endgame-feasibility.md:8`、`2026-08-31-toolkit-android-tools-g15-alignment.md:6`）

## ACCEPTANCE

- `docs/acceptance/` = 8 文件。**有**需求→测试映射，但限于 Sprint 4 / 签字文档，非 PRD 全域
- `2026-plan-c-sprint4.md`：37 条 `AC-S4-*`，列 = `ID | 场景 | 前置 | 期望结果 | 自动化 | 状态`（`:21` AC-S4-01 → `backend/agent/tests/test_scan_runner.py` → PR #35），即「验收判据 + 验证方法（测试文件::用例）+ 证据（PR）」
- `00-platform-smoke.md`：24 条（`AC-CI-01…` + 主链），列 = `ID | 检查 | 命令/位置`；`2026-plan-c-sprint2-3.md` 30 条
- 3 份签字文档（aee-reconciler-mtk / adr-0028-phase3-mtk / suite-binding-mtbf）为叙述式 runbook（0 条 AC ID，各 11–13 处证据引用）
- 缺口：`grep -rn "PRD-" docs/acceptance/*.md` → **0 命中**，无 PRD 需求 ID → AC → 测试的追溯链（PRD 仅作头部指针，`00-platform-smoke.md:3`）；证据多以行内代码呈现而非超链接（sprint4：14 处代码路径 vs 6 条 md 链接），且**没有任何门禁校验被引用的测试路径是否仍存在**（S2 不覆盖 acceptance/）
- 实测计数（`grep -cE '^\s*[-*] \[[ x]\]|^\| *AC-'`）：判据行 **91**（smoke 24 + sprint2-3 30 + sprint4 37；3 份签字文档 0）；可定位测试/命令证据引用 **92** 处（smoke 11、aee 13、adr-0028 5、suite-binding 11、sprint2-3 13、sprint4 26、real-device 13）→ 数量级 1:1，但粒度不一（部分指向测试文件、部分指向 PR/runbook 章节）

## RISK

- **P1** `types.ts`↔后端 schema 硬不变量（`AGENTS.md:31`）零强制且被自认 residual；2076 行手工维护 → API 契约静默漂移，前端仅能查 TS 内部一致性
- **P1** ADR 索引「类型/落地」列无门禁：`README.md:74` 称 ADR-0023 已实现，而 `ADR-0023:3` 自述 D2–D8 仍 Proposed（S12 有意不看该列）
- **P1** ADR-0031 编号重复 + 未登记 Accepted 附录、可用入链为 0：一份生效规范实际不可达
- **P2** 单数表名不变量（`AGENTS.md:29`）被 7 张生产复数表违反，门禁仅差异面，例外未登记
- **P2** 无全库链接/文档 lint：`docs/notes/` 6 条断链（2 条指向 ADR），层级写错长期未发现
- **P2** 391/617 文档为链接图孤儿（80 篇 archive，约 24 篇非 archive，含子 hub README 与 ADR-0024 背景 Note）→ 主题代码变更时无维护信号
- **P3** 验收证据不可机验、无 PRD→AC 追溯，被引用测试路径可静默失效
- **P3** Python 下界声明（3.10+）与工具链 pin（py311 / CI 3.11 / venv 3.13.5）不一致；DOC-MAP 头部日期落后自身链接与提交 4–5 天
- **P3** ASGI / Redis / `python -m` 三条硬不变量自认 residual（结构自证或仅人工 review）

## EVIDENCE COMMANDS

```bash
ls docs/adr/ | wc -l; sed -n '50,87p' docs/adr/README.md
grep -m2 -E '^\s*[-*]?\s*(状态|Status)' docs/adr/ADR-*.md
python3 tools/dev/check_governance_surface.py --check          # → [OK] … S1–S13，exit 0
grep -rniE "markdownlint|link-?check|doc-?lint" .github/workflows/   # → 空
grep -rniE "openapi|type.?sync|schema.?drift|contract.?test" tests/ scripts/ .github/workflows/  # → 空
sed -n '126,135p' docs/design/2026-08-governance-surface-protection.md  # residual 覆盖图
grep -rn "__tablename__" backend/models/*.py | grep -E 's"'   # 7 张复数表
grep -rn project_id backend/models/*.py                        # device 无 project_id（ADR-0029 D10）
sed -n '74p' docs/adr/README.md; sed -n '3p' docs/adr/ADR-0023-script-traceability.md
find docs -name '*.md' | wc -l; find docs/archive -name '*.md' | wc -l
git log -1 --format=%cs -- docs/DOC-MAP.md
git log -1 --format=%cs -- docs/design/2026-07-plan-execute-page-improvements.md   # 2026-08-06
git log -1 --format=%cs -- frontend/src/utils/api/types.ts                          # 2026-09-11
grep -rn "PRD-" docs/acceptance/*.md                           # → 空（无 PRD→AC 追溯）
```

## Decision

对文档/架构完整性进行单轴证据审计，结果以本 Note 记录，不在审计范围内发起修复。
P1–P3 风险项（`types.ts` 零强制、ADR-0023 D2–D8 仍 Proposed、ADR-0031 编号重复）
作为发现列入，修复由后续独立 PR 承接，避免将审计发现与修复方案混入同一提交。

## Alternatives

- 直接在对应 ADR/DOC-MAP 内联修复：会模糊审计基线与修复时间线，不利于后续验收追踪。
- 开 Issue 登记：无法在 `docs/notes/process/` 里保留可追溯的全量证据命令，选择 Note 形式。
- 拆分为多篇 Note（每条风险一篇）：当前 P1–P3 项存在交叉证据，合并审计降低冗余。

## Verification

所有引用均含 `file:line`；§EVIDENCE COMMANDS 内的命令可独立复现。
审计时 `python3 tools/dev/check_governance_surface.py --check` → `[OK] … S1–S13，exit 0`（见 §DRIFT GATES 实跑行）。

## Revisit

任一 P1 风险被修复后（`types.ts` 同步门禁落地、ADR-0023 D2–D8 状态更新、ADR-0031 附录登记），
重跑本节证据命令核验结论仍有效；或在下一次季度文档整体审查时重审孤儿/漂移计数。
