# B6-U5：开关机接入 powercycle_disarm 1.0.0 与 powercycle_finish 1.1.0

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.5** §3.0 / §3.6 / §4 U5
与 ADR-0061 D2/D6/D8（含 D2.5）实施 [#3730](https://github.com/DUElost/stability-test-platform/issues/3730)。
Draft PR 待 Planner 确认与独立复核；合入不等于生效（发包/scan/热更新/重指受 §5 整批闸门）。

## Decision

1. 新族 `powercycle_disarm` 1.0.0：参数 `mode`（`probe`/`disarm`）、`wait_device_online_seconds`。
   `disarm`：等待上线 → 写 `auto_resume=false`/`running=false` → 停服务 → root 优先回读；
   `probe`：只回读。stdout 结构化 `disarm_result`（六值）与 `readback`/`readback_summary`；
   读不到 → `readback_failed`，不得报无足迹。
2. `powercycle_finish` 1.1.0：只做结果收取；不再 `stop_task`、不再写 prefs。收取重试按
   `collect_attempts × collect_retry_wait_seconds` 预算（每次尝试前等上线）。
3. 共享 `device_return.ONBOARDED` **只追加** `power-cycle`：足迹清单、解除/收取脚本与最低版本、
   探针模式、`verified_device_classes`、预算秒数、`min_wall_clock_seconds` callable
   （disarm=`wait+60`；finish=`attempts×retry_wait+60`）。脚本包内
   `_VERIFIED_DEVICE_CLASSES` 与契约清单必须同步（包隔离不可运行时 import）。
4. 模板 `powercycle.json` teardown 改为
   `[powercycle_disarm 1.0.0 @660s, powercycle_finish 1.1.0 @420s]`，参数与预算对齐。
5. 按 script-version-lifecycle §A `--register` 追加 manifest；**不** `--publish`/scan。

## Alternatives

- 在 finish 内保留 stop 并以参数开关：违反 §3.6「解除与收取拆分」与 D2.3，未采用。
- 脚本运行时 import `device_return` 读已验证类别：tools_cache 隔离包不可靠，改为包内常量同步。
- 无真机时把三类登记为已验证：违反 D2.5，未采用；保留 pending。

## Verification

- 门禁：`check_script_packages`（36 族等价）；`check_tool_manifest --base origin/main` append-only；
  `check:quick`。
- 反例红→绿：finish 人为加回 `stop_task(` → `TestFinishNoDisarm` 2 failed；恢复后 2 passed。
- 定向：`test_powercycle_disarm_u5_3730` + finish/U1 相关 92 passed；真实 ONBOARDED API 墙钟 +
  pin + I1 34 passed；prefs + reaper/墙钟矩阵 85 passed。
- 真机：实施机 `adb devices` 空；MLD_LX2/LX3/NX2 三项 **pending**；
  `verified_device_classes=[]`。展锐不纳入。

## Revisit

- 真机资源到位后按 D2.5 补有/无足迹、重启后行为、读取失败识别；通过后同步更新契约与脚本常量
  （需新小版本）。
- 激活按 #3723 §5 整批闸门；不得因本单元单独发包/scan/热更新/重指在用 Plan。
- U3/U4 只消费本契约，不另建表；文件重叠时以当时唯一 head 协调。
