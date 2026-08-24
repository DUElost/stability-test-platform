# #404 实机验收 runbook：MTBF 套件绑定门禁端到端（D6 总验收信号）

- **跟踪**：[GitHub #404](https://github.com/DUElost/stability-test-platform/issues/404)
- **关联 ADR**：[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md) v1.6（P1 设计 §3）
- **CLI**：`tools/dev/mtbf-cases.py`（凭据约定见 AGENTS.md「Production access」）
- **文档状态**：模板（待实跑填入实测值并在 issue 回填签字）

> **目标**：在一台 MTK 真机上完成「导入 → 改 1 条 → 导出 → 绑定派发」全链，
> 证明 precheck 五步门禁、参数自动注入、#402 在途守卫与审计留痕按设计工作。
> **总验收信号（ADR-0030 D6）**：init trace 的 `suite_sha256` ==
> 门禁比对 sha（`exported_sha256`）。

---

## 0. 部署窗口前置（2026-08-24 只读核验快照，实跑前复核）

| # | 项 | 期望 | 2026-08-24 快照 | 复核 |
|---|----|------|-----------------|------|
| P1 | 生产 DB `alembic_version` | `v8w9x0y1z2a3`（PR-B 迁移，事故前向修复已闭环） | ✅ 一致 | ☐ |
| P2 | 生产 DB `plan.suite_id` 列 | 存在（integer 可空） | ✅ | ☐ |
| P3 | backend 服务代码版本 | ≥ PR-E（`5e35c15`）；OpenAPI `PlanCreate.suite_name` 存在 | ❌ 跑 8-22 旧码（9 个 suite 端点在、suite_name 无）→ **需部署窗口重启** | ☐ |
| P4 | 脚本目录含 `mtbf_check/v1.3.0` | `{STP_SCRIPT_ROOT}/mtbf_check/v1.3.0/`（= 本仓库 checkout）+ catalog 已注册 | 目录随 main 就位；注册待服务重启后扫描 | ☐ |
| P5 | fleet `.env` 残留键 | 允许残留（抽样 3/3 台有 `STP_MTBF_EXPECTED_TESTPOINT_COUNT` 行）：绑定 Run 注入优先不受影响；v1.3.0 忽略；≤v1.2.0 行为同退役前。可顺手清理，非阻塞 | ✅ 已知悉 | ☐ |

```bash
# P3/P4 复核（部署后执行）
curl -s http://127.0.0.1:8000/openapi.json | grep -o '"suite_name"' | head -1
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/scripts | \
  python3 -c 'import json,sys; [print(r["name"],r["version"]) for r in json.load(sys.stdin)["data"] if r["name"]=="mtbf_check"]'
```

## 1. 前置

### 1.1 设备 / 机型

- 1 台 MTK 设备（userdebug/eng 工程包——`mtbf_setup` v1.3.0 对 user 构建
  `adb root` fail-fast，见 mtbf-api.md §1.5）；
- 设备归属项目与套件项目一致（D3b：项目套件要求 `device.project_id` 匹配；
  通用套件任意设备可跑）。最小路径用**通用套件**（不设 project_id）。

### 1.2 数据准备（全程走 CLI，即 D6「外部 agent 仅凭 API/CLI」口径）

```bash
cd /home/debian13/stability-test-platform
# 1) 导入 130 条生产快照（脱敏 fixtures 仅测试库用；实机用真实工具目录文件，
#    凭据警示见 mtbf-api.md §1.6——内容不进仓库/log/截图）
python tools/dev/mtbf-cases.py import --suite MTBF-legacy \
  --file /mnt/automation-toolkit/android-tools/stability_MTBF-Test/config/runtask.xml \
  --global-file /mnt/automation-toolkit/android-tools/stability_MTBF-Test/config/UiAutomatorTestData.xml
# 期望：testpoints=130

# 2) 改 1 条用例（PUT /test-cases/{id}，times 覆盖即可）
python tools/dev/mtbf-cases.py show --suite MTBF-legacy --case <任一 testpoint 名>

# 3) 校验 + 导出落工具目录（记录 exported_sha256 = 门禁比对基准）
python tools/dev/mtbf-cases.py validate --suite MTBF-legacy
python tools/dev/mtbf-cases.py export-to-tool-dir --suite MTBF-legacy
```

### 1.3 Plan 绑定与脚本版本

- Plan 步骤引用 `mtbf_setup@1.3.0`（init）/ `mtbf_check@1.2.0`（patrol，
  或升级 1.3.0）/ `mtbf_finish@1.4.0`（teardown）；
- `PUT /api/v1/plans/{id}` 带 `"suite_name": "MTBF-legacy"` 完成绑定
  （或创建时携带）。未绑定时 prepare 会记 WARNING `suite_unbound`
  （观测信号，见 Agent Note 2026-08-24-suite-cli-p1c.md）。

---

## 2. 验证步骤

每步执行后填入实测值。最小负载：1 PlanRun × 1 设备。

| # | 目标 | 步骤 | 期望 | 实测 | 锚点 |
|---|------|------|------|------|------|
| S1 | prepare 冻结 dispatch_suite | 派发后查 `plan_run.run_context` | 六字段齐且等于套件当前基线（`exported_sha256` 与 S1.3 输出一致） | ☐ | `services/suite_binding.py` `freeze_dispatch_suite` |
| S2 | 五步门禁放行 | 观察 PlanRun QUEUED→PRECHECK→RUNNING | 无 `suite_verify_failed`；`result_summary.reason` 为空 | ☐ | `admission_pump.plan_admission_task` Phase A0 |
| S3 | 参数自动注入 | 查 JobInstance `pipeline_def` 的 mtbf_* 步骤 params | `expected_testpoint_count`=启用计数（130）、`project`=`legacy`；未声明的 default_params 不受影响 | ☐ | `plan_dispatcher_core.inject_suite_params` |
| S4 | **init trace 闭环（总信号）** | 设备端 init 完成 NFS JSON 后，比对 `suite_sha256` 与门禁 `exported_sha256` | **逐字节相等** | ☐ | `mtbf_finish` v1.4.0 / P0 设计 §5.3 |
| S5 | 审计全程 | `SELECT action FROM audit_logs WHERE resource_type='test_suite' ORDER BY id DESC LIMIT 10` | 含 `import` / `update`（case PUT）/ `export` 各 ≥1 条 | ☐ | `routes/suites.py` record_audit |
| S6 | 守卫精确化（反向演练） | S4 通过后保持 Run RUNNING，对**同一套件**再 export-to-tool-dir | 409 `SUITE_RUNS_ACTIVE` 且 `force=true` 仍 409 | ☐ | `active_run_ids_bound_to_suite` |
| S7 | 门禁反向（可选，推荐） | 手改中心存储 `runtask.xml` 一个字节 → 再派发一次 | PRECHECK→FAILED `suite_verify_failed` + `step=sha_mismatch`；恢复文件 | ☐ | `collect_suite_gate_error` 第 4 步 |

> **S7 注意**：改的是中心存储文件不是库——恢复以 S1.3 重导最干净
> （同时刷新双基线）。S7 会产生一条 FAILED Run，属预期验收痕迹。

## 3. 验收标准

| ID | 标准 | 通过判据 | 实测 |
|----|------|----------|------|
| R1 | 总验收信号 | S4 相等（`suite_sha256 == exported_sha256`） | ☐ |
| R2 | 托管链路零人工 env | 全程未设置/依赖 host 的 `STP_MTBF_EXPECTED_TESTPOINT_COUNT`（注入替代） | ☐ |
| R3 | fail-fast 语义 | S6/S7 至少一项演练通过，错误 detail 含修复路径字段 | ☐ |
| R4 | 审计闭环 | S5 三类动作齐全；CLI 操作与页面操作在审计中不可区分 | ☐ |

## 4. 实测填空

```
日期/执行人：
PlanRun id：            设备 serial：        host ip：
exported_sha256：       init trace suite_sha256：
dispatch_suite 快照：
注入 params（setup/check）：
S6 409 响应 code：      S7 FAILED reason/step：
audit_logs 截图或 SQL 输出粘贴处：
结论（R1–R4）：R1☐ R2☐ R3☐ R4☐
```

> 完成后：把本文件改名去掉「模板」性质并回填 issue #404 / ADR-0030 修订记录
> （对齐 2026-07-aee-reconciler-mtk-signoff.md 先例）。
