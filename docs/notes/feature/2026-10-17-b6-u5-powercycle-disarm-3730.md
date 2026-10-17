# B6-U5：开关机接入 powercycle_disarm 1.0.2 与 powercycle_finish 1.1.0

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.6** §3.0 / §3.6 / §3.6.1（含 **§3.6.1.3**）/ §4 U5
与 ADR-0061 D2/D6/D8（含 D2.5）实施 [#3730](https://github.com/DUElost/stability-test-platform/issues/3730)。
Draft PR 待 Planner 再确认与独立复核；合入不等于生效（发包/scan/热更新/重指受 §5 整批闸门）。

## Decision

1. 新族 `powercycle_disarm`：**1.0.2**（§3.6.1.3 probe/disarm 区分；1.0.0 / 1.0.1 保留在
   manifest，不原地改包）。参数 `mode`（`probe`/`disarm`）、`wait_device_online_seconds`。
   `disarm`：等待上线 → 写 prefs → 停服务 → root 优先回读；
   `probe`：只回读。stdout 结构化 `disarm_result`（六值）与原始 `readback`/`evidence`。
2. **类别语义（v1.6 §3.6.1 / D2.5）**：
   - 原始 `readback`/evidence 可读失败仍记 `readback_failed`（诊断）；
   - 已验证类别才可输出正式 `confirmed` / `readback_failed`；
   - 未验证 + **disarm 已完成**（无可信读数或原始 clear）→ 正式 `executed_unverified`；
   - 动作失败/未完成 → 依 D1 记真实失败，不得冒称已执行；
   - 未验证 **probe**（含原始 clear）→ 正式 `failed` / `success=false`，不得把
     `executed_unverified` 当可信通过（§3.6.1.3）。
3. `powercycle_finish` 1.1.0：只做结果收取；不再 `stop_task`、不再写 prefs。
4. 共享 `device_return.ONBOARDED` **只追加** `power-cycle`；`disarm_min_version=1.0.2`；
   `verified_device_classes=[]`（真机 pending，不得虚填）。
   共享 mapper 可选 `category_verified`（默认 `None` 保持 U1 六值）；不改 cancel/失租/permit/停机。
5. 模板 `powercycle.json` teardown pin：
   `[powercycle_disarm 1.0.2 @660s, powercycle_finish 1.1.0 @420s]`。
6. 按 script-version-lifecycle §A `--register` 追加 1.0.2；**不** `--publish`/scan。

## Alternatives

- 在 finish 内保留 stop 并以参数开关：违反 §3.6，未采用。
- 无真机时把三类登记为已验证：违反 D2.5，未采用；保留 pending。
- 原地改写 1.0.0/1.0.1 包：违反 ADR-0051 append-only，改为追加 1.0.2。
- 未验证 probe clear 报 `executed_unverified`/`success=true`：违反 §3.6.1.3，已在 1.0.2 纠正。

## Verification

- 门禁：`check_script_packages`；`check_tool_manifest --base origin/main` append-only；`check:quick` OK。
- §3.6.1.3 反例：`test_planner_unverified_probe_clear` 修前 **1 failed**（`success=True` /
  `executed_unverified`）→ 修后 **1 passed**；定向
  `test_planner_u5_category_contract` + `test_powercycle_disarm_u5_3730` + 反例 **22 passed**；
  相关批（含 U1 / scripts / 墙钟 / 模板 pin）**115 passed**。
- 真机：MLD_LX2/LX3/NX2 三项 **仍 pending**（无独立测试设备）；
  `verified_device_classes=[]`。展锐不纳入。

## Revisit

- 真机资源到位后按 D2.5 补有/无足迹、重启后行为、读取失败识别；通过后同步更新契约与脚本常量
  （再追加小版本）。
- 激活按 #3723 §5 整批闸门；不得因本单元单独发包/scan/热更新/重指在用 Plan。
