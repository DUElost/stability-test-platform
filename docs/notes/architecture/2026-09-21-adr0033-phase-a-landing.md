# ADR-0033 Phase A：D0 门禁 + Contract 脚手架 + Jira 薄 ACL

Status: implemented
Class: architecture

## Decision

对齐 ChatGPT「确认ADR0033实施路线」Phase A 与 Epic #745 未落地的两道 CI 门禁：

1. **D0 新族门禁** `tools/dev/check_new_script_family.py`：相对 base 禁止新增
   `backend/agent/scripts/<family>/` 顶层目录；既有族新版本仍绿。
2. **D2 脚手架** `tools/dev/verify_tool_contract.py` + fixture：验证
   `--check-env` / `--context` / `--output-dir` / `summary.json` / 退出码命名空间；
   适用范围仍是**新族准入**，不强迫存量改造。
3. **Jira 薄 ACL** `backend/services/jira_vendor/`：从 `api/routes/dedup` 下沉
   `resolve_vendor_tool` / `build_jira_argv` / `load_vendor_tool_env`，对偶 B5
   `DedupMergeEngine` 样板（Phase A1）。

ADR-0033 升 **v1.7**（非决策变更）：落地状态行与索引同步。

### 明确不做

- Package Store / `tools_cache` / tar.gz / Phase 3
- 新建 `ToolRun` 表（先复用 JobInstance / PlanRun / Artifact / JiraRun / Script）
- PlanRun 日志事件 UI/API（Phase A3——需独立产品 scope）
- 新增 `STP_JIRA_*_DIR` 厂商键；开 auto-merge

## Alternatives

| 选项 | 为何不选 |
|---|---|
| 要求新族附 `tool_manifest.yaml` 即放行入仓 | 包存储未触发；manifest  alone 会变半成品债 |
| 本轮做 PlanRun log-events API | 产品面大；超出 Epic 门禁/Adapter 最小切片 |
| 把 Jira 全迁 Tool Contract | D2 按族准入；现态仍 legacy argv |

## Verification

- `python3 tools/dev/check_new_script_family.py --self-test`
- `python3 tools/dev/verify_tool_contract.py --self-test`
- `python3 -m pytest backend/tests/api/test_dedup_helpers.py backend/tests/services/test_jira_vendor_engine.py tests/test_adr0033_d0_d2_gates.py -q`
- `python3 scripts/run_gates.py check:quick`
- 分层：`jira_vendor` 不含 `backend.api.routes`

## Revisit

- Phase A3（PlanRun → LogEvent → Artifact → Jira Bundle UI/API）另开 issue
- §5.4 触发后按评估文最小切片做 Package Store
- 新合同工具族接入时对其 entrypoint 跑 `verify_tool_contract.py --entrypoint`
