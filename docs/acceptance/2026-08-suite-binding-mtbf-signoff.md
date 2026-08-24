# #404 实机验收 runbook：MTBF 套件绑定门禁端到端（D6 总验收信号）

- **跟踪**：[GitHub #404](https://github.com/DUElost/stability-test-platform/issues/404)
- **关联 ADR**：[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md) v1.6（P1 设计 §3）
- **CLI**：`tools/dev/mtbf-cases.py`（凭据约定见 AGENTS.md「Production access」）
- **文档状态**：**已实跑签字（2026-08-25 凌晨窗口，R1–R4 全过）**，实测值见 §4

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
**2026-08-25 实跑记录**：Plan 10（MTBF-专项-冒烟-P0）绑定 suite `MTBF-legacy`(id=1)，
Run #224 / 设备 395（AYCGNX6730000054，MLD_LX2，host 172.21.15.78）。

| # | 目标 | 步骤 | 期望 | 实测 | 锚点 |
|---|------|------|------|------|------|
| S1 | prepare 冻结 dispatch_suite | 派发后查 `plan_run.run_context` | 六字段齐且等于套件当前基线（`exported_sha256` 与 S1.3 输出一致） | ✅ #222/#224 六字段齐；frozen sha=`e782bf78…47af` == export 输出 | `services/suite_binding.py` `freeze_dispatch_suite` |
| S2 | 五步门禁放行 | 观察 PlanRun QUEUED→PRECHECK→RUNNING | 无 `suite_verify_failed`；`result_summary.reason` 为空 | ✅ #224 RUNNING（此前 #222 因 host 支撑文件缺失 script_verify_failed，属既有 sync 缺口，见 §5） | `admission_pump.plan_admission_task` Phase A0 |
| S3 | 参数自动注入 | 查 JobInstance `pipeline_def` 的 mtbf_* 步骤 params | `expected_testpoint_count`=启用计数、`project`=export_dir；未声明的 default_params 不受影响 | ✅ setup params = `{"project":"legacy","expected_testpoint_count":130}` | `plan_dispatcher_core.inject_suite_params` |
| S4 | **init trace 闭环（总信号）** | abort→teardown→finish 后读 NFS JSON `metrics.suite_sha256` 与门禁 `exported_sha256` 比对 | **逐字节相等** | ✅ `results/2026.08.15_06.23.23.401.json` suite_sha256 = `e782bf7814604dce1e2246558f6b89ab08550546c7a30aaf558688f3bb7347af` == 门禁基线 | `mtbf_finish` v1.4.0 / P0 设计 §5.3 |
| S5 | 审计全程 | `audit_logs` 按 resource_type 检索 | 含 `import` / case 更新 / `export` 各 ≥1 条 | ✅ create(247141)→import(247143)→update(test_case,247149)→export(247152)→export(247166)，另有 plan_updated(绑定)/plan_admission_failed(225) | `routes/suites.py` record_audit |
| S6 | 守卫精确化（反向演练） | Run #224 RUNNING 期间对同一套件 export-to-tool-dir | 409 `SUITE_RUNS_ACTIVE` 且 `force=true` 仍 409 | ✅ 双向 409，plan_run_ids=[224]，force 不豁免 | `active_run_ids_bound_to_suite` |
| S7 | 门禁反向演练 | 追加字节篡改中心存储 runtask.xml → 再派发 | PRECHECK→FAILED `suite_verify_failed` + `step=sha_mismatch`，detail 含双 sha 与修复路径 | ✅ #225 FAILED：expected=`e782bf78…` vs disk=`5e8057a3…`，remedy 提示重导；重导后 stale 清零 | `collect_suite_gate_error` 第 4 步 |

> **S7 注意**：改的是中心存储文件不是库——恢复以重导最干净
> （同时刷新双基线）。S7 会产生一条 FAILED Run，属预期验收痕迹。

## 3. 验收标准

| ID | 标准 | 通过判据 | 实测 |
|----|------|----------|------|
| R1 | 总验收信号 | S4 相等（`suite_sha256 == exported_sha256`） | ✅ |
| R2 | 托管链路零人工 env | 全程未设置/依赖 host 的 `STP_MTBF_EXPECTED_TESTPOINT_COUNT`（注入替代）；hot-update 同步清单已无该键 | ✅ |
| R3 | fail-fast 语义 | S6/S7 至少一项演练通过，错误 detail 含修复路径字段 | ✅ 两项均过 |
| R4 | 审计闭环 | S5 三类动作齐全；CLI 操作与页面操作在审计中不可区分 | ✅ |

## 4. 实测填空

```
日期/执行人：2026-08-25 00:15–00:35 CST / opencode（运营授权窗口）
PlanRun id：222(脚本校验失败,暴露sync缺口) / 223(user构建fail-fast,预期) /
            224(主验收RUNNING→abort收尾) / 225(S7反向FAILED)
设备 serial：395 AYCGNX6730000054 (MLD_LX2)   host ip：172.21.15.78
exported_sha256：e782bf7814604dce1e2246558f6b89ab08550546c7a30aaf558688f3bb7347af
init trace suite_sha256：（NFS JSON metrics）同上，逐字节相等
dispatch_suite 快照：{suite_id:1, suite_name:MTBF-legacy, exported_sha256:同上,
                     exported_content_sha256:1343c073…, apk_binding:null, export_dir:legacy}
注入 params（setup）：{"project":"legacy","expected_testpoint_count":130}
S6 409 响应 code：SUITE_RUNS_ACTIVE（force 亦 409）
S7 FAILED reason/step：suite_verify_failed / sha_mismatch
结论（R1–R4）：R1✅ R2✅ R3✅ R4✅
```

## 5. 冒烟副产品（发现项）

1. **`push_mismatched_scripts` 不推支撑文件**（#222 暴露）：轻量 sync 只推入口
   文件 + 硬编码 `_adb.py`，manifest 中 `_lib.py` 缺失时 verify 正确报错、
   push"成功"、reverify 仍失败 → fatal。治愈路径缺口，fallback 是整机
   hot-update（本次即用其解锁）。修复另起小 PR。
2. user 构建设备被 `mtbf_setup` v1.3.0 root 前置正确 fail-fast（#223）——
   设计行为，选设备须 userdebug（P0 验收设备 395 即是）。
