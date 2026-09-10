# Agent 脚本版本与退役

本文是版本化脚本目录、参数分层、扫描和退役的开发契约。架构决策见
[`ADR-0020`](../adr/ADR-0020-plan-step-one-shot-migration.md)。

外部工具（原厂 / 专项工具）的**源码入仓边界与接入形态**另见
[`ADR-0033`](../adr/ADR-0033-tool-kit-ecosystem-integration.md)：新工具族必须走
Tool Contract + 包存储，既有工具族的新版本目录允许继续 legacy 形态（D0 分级准入）
——本文的版本目录约定对已入仓脚本族持续有效。

## 目录与扫描

```text
<STP_SCRIPT_ROOT>/<name>/v<version>/<entry>.{py,sh}
```

- 一级目录是脚本名，二级目录以 `v` 开头；扫描器只识别 `.py`（python）与
  `.sh`（shell）两种后缀（`script_catalog._SUPPORTED_SUFFIXES`）——`.bat/.cmd`
  等 Windows 批处理**不受支持**（历史文档曾宣称支持，2026-09 按实现收口，
  #1029）；
- 入口是首个非 `_` 前缀的可识别脚本；
- `_` 辅助模块在入口扫描时跳过，但仍受版本目录不可变门禁保护；
- 扫描结果：`created`、`skipped`、`conflicts`、`deactivated`；
- `STP_SCRIPT_ROOT` 必须显式配置；扫描机与运行机不同时另设
  `STP_SCRIPT_RUNTIME_ROOT`。

## 已发布版本不可变

`script.content_sha256` 是扫描时冻结的期望值。原地修改已发布版本只会产生 conflict，
不会更新数据库基线；引用该版本的 Plan 会在 precheck 阶段
`script_verify_failed`，self-heal 也无法修复磁盘内容与数据库期望值的失配。

正常修改必须创建新版本。CI 和本地门禁：

```bash
python tools/dev/check-script-version-immutability.py --base origin/main
```

`POST /scripts/scan?force_rebaseline=true` 只用于契约已经被外部破坏后的恢复：仅 admin
可调用，有 RUNNING、QUEUED 或 PRECHECK PlanRun 时返回 409。不能作为日常改版路径。

## 种子迁移治理（#942 裁决 A）

数据迁移里的种子逻辑（INSERT/UPDATE `script` 表、停用旧版本）**不受服务层
保护**（alembic 内裸 SQL），因此负有与 API 同构的引用检查义务：

- 对**已存在**的 `(script_name, script_version)` 做任何写操作（UPDATE
  `default_params`/`param_schema`、停用 `is_active`）前，必须先查
  `plan_step` 引用；
- 引用数 > 0 → 迁移**失败**（RuntimeError 带重指指引）——与 API 层
  `_ensure_script_can_be_deactivated` 的 409 同构；操作者重指 plan_step 或
  以新建版本表达参数变化后重跑；
- **禁止**裸 `UPDATE script SET default_params ...` 与无引用检查的
  `UPDATE script SET is_active = false ...`；
- 全新版本行的 INSERT 不受此约束。

模板权威源：[`backend/services/script_seed_governance.py`](../../backend/services/script_seed_governance.py)
（`raise_if_version_referenced` / `raise_if_any_version_referenced`）。迁移
**自包含**原则下不 import 服务层——把该文件当前实现**内嵌**进迁移文件，并
在迁移 docstring 注明复制来源与复制日期。带数据行为测试见
`tests/test_script_seed_governance.py`。

裁决与论证：[`docs/design/2026-09-08-seed-migration-governance.md`](../design/2026-09-08-seed-migration-governance.md)。

## 参数分层

已存在版本的 `default_params` 不可原地修改；API 返回 422。需要修改默认参数时使用：

```text
POST /api/v1/scripts/{name}/versions
```

派发参数来源：

1. 脚本版本的 `default_params`；
2. PlanStep `step.params` 对用户声明键的覆盖；
3. WiFi 资源池向 `connect_wifi` 或 `monkey_setup.params.wifi` 补齐未声明字段；
4. 管理套件按冻结的 `dispatch_suite` 向 `script:mtbf_*` 注入测试点数量与项目。

Pipeline action 唯一格式是 `script:<name>`。执行链：

```text
文件 → scripts scan → DB script → PlanStep
  → default_params ⊕ step.params/资源注入 → pipeline_def
  → Agent ScriptRegistry → subprocess → stdout JSON
  → step_trace → JobStatus → aggregator
```

## 退役与删除

退役前的只读诊断：

```bash
python -m backend.scripts.check_unreferenced_script_versions [--json] [--name flash_firmware]
```

它按 `PlanStep.script_name + script_version` 统计配置引用。候选版本通过
`PUT /api/v1/scripts/{id}` 设置 `is_active=false`；仍被 Plan 引用时接口返回
409 `SCRIPT_STILL_REFERENCED`。

退役保留版本目录，只让版本退出活动目录。不要删除历史版本目录：删除会触发不可变
门禁，也会破坏历史 PlanRun 的重放与追溯。

`refs == 0` 只代表没有当前 Plan 配置引用，不代表没有历史运行。退役前还应查看
`GET /api/v1/scripts/{id}/usage` 的 `run_count`、`success_rate` 和 `versions_used`；
配置与近期运行两个维度都为零时更稳妥。
