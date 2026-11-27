# 宿主侧 ADB 驱动开关机专项（`powercycle_adb_*` + `powercycle_adb.json`）

Status: implemented
Class: feature

## Decision

针对近期多机型开关机专项受阻于设备端 APK `platform` 签名错配、缺少 `android.permission.REBOOT` 权限以及厂商自启策略拦截 `BOOT_COMPLETED` 导致重启循环中断的问题，新增一套**宿主侧 ADB 驱动**的独立开关机脚本族与 Pipeline 模板，彻底摆脱设备端 APK 依赖：

- **`powercycle_adb_setup@1.0.0`**（`init`）：等待设备系统就绪（`get-state == device` 且 `sys.boot_completed == 1`），best-effort 设置插电常亮并解锁回主界面，读取内核 `/proc/sys/kernel/random/boot_id` 建立真重启基线，按 `STP_JOB_ID + serial` 落盘宿主侧状态文件（不在 `init` 发首轮重启，避免多机并行过 `INIT→PATROL` barrier 期间掉线）；
- **`powercycle_adb_patrol@1.0.0`**（`patrol`，建议间隔 `15s`）：以短时三态状态机（`IDLE → WAIT_REBOOT → WAIT_INTERVAL`）推进循环，不长期占用 Agent 执行 permit：
  - 使用 `adb -s <serial> reboot` 触发重启（无需 `platform` 签名、无需 `REBOOT` 权限、无需 root）；
  - 每次重启完成后核对 `/proc/sys/kernel/random/boot_id` 变化，杜绝未生效的假重启，并执行 `KEYCODE_WAKEUP` + `wm dismiss-keyguard` + 3 次 `input keyevent 82` + `KEYCODE_HOME` 解锁回主界面；
  - `wait_seconds` 严格定义为「开机就绪并到达主界面后，至下发下一次 `adb reboot` 的等待时长」（如 10s / 30s / 60s）；剩余等待 `<= inline_wait_max_seconds`（默认 15s）时内联短等后立刻触发重启以保证精度，大于阈值时先释放 permit；
  - 单轮等待开机超过 `boot_timeout_seconds`（默认 300s）或 `boot_id` 超过 `reboot_confirm_timeout_seconds`（默认 30s）未变化时，记录失败明细并输出 `patrol_fail=true` 立即终止 Job；达到 `test_times` 后输出 `patrol_complete=true` 立即进入 teardown；
- **`powercycle_adb_finish@1.0.0`**（`teardown`）：设备端零自启足迹、无需 disarm；短暂等待可能仍在途的重启回到在线态并解锁回桌面后，按与既有 `powercycle_finish` 兼容的结果 JSON 结构落盘至 `{STP_AEE_NFS_ROOT}/power-cycle/{project}/results/{run_id}.json`；
- **`backend/schemas/pipeline_templates/powercycle_adb.json`**：新增 `specialty_key="power-cycle"` 模板「开关机压测（宿主 ADB 驱动）」，并同步登记 `SCRIPT_TAG_REGISTRY`、`tool_manifest.json` 与模板守卫名单。

## Alternatives

- **在现有 `powercycle_setup` / `powercycle_check` / `powercycle_finish` 内新增 `driver="host_adb"` 分支**：现有 `powercycle_setup` 在 `backend/agent/contracts/device_return.py` 中已绑定为 `power-cycle` 设备端足迹步骤，强制要求 teardown 挂载 `powercycle_disarm` 并执行 APK/prefs 探针回读；复用族名会耦合 B6 归还契约并引入不必要的 disarm 复杂度。独立成族零回归、语义边界清晰。
- **单步长循环脚本跑完整轮次**：Agent `OperationScheduler` 默认单主机并发 permit 上限为 5，长阻塞步骤会饿死同主机其他设备；改用短时 `patrol` 步进 + 有界内联短等既保并发又不失间隔精度。
- **继续逐机型申请/维护 `platform` 密钥重签 APK**：每引入新签名或新厂商自启策略都需重复逆向与重签，无法根治；现有 APK 模板保留给需真关机 RTC 唤醒或原厂报告验收的场景。

## Verification

- `powercycle_adb_*` 单元与状态机全路径测试（`backend/agent/tests/test_powercycle_adb.py`）：覆盖短间隔 10s 内联闭环、长间隔 60s 释放 permit 控时、开机超时 `patrol_fail` 终止与失败落盘、`boot_id` 未变判定重启未生效、解锁命令序列及三族 `_lib.py` 字节同源校验。
- 模板与守卫测试：`tests/test_pipeline_template_script_pins_2865.py`、`tests/test_template_step_timeout_vs_script_budget.py`、`tests/test_agent_clock_stub_guard_3202.py`、`backend/tests/api/test_pipeline_templates_stages.py`。
- 静态包清单与快速门禁：`tools/dev/check_script_packages.py`、`tools/dev/check_tool_manifest.py --base origin/main`、`scripts/run_gates.py check:quick`。

## Revisit

- 合并至 `main` 后，需按 `script-version-lifecycle` SOP 在控制面执行 `--publish --packages-root /mnt/stp-aee/packages` 与 `POST /api/v1/scripts/scan` 完成三族 `1.0.0` 包发布与注册，再做单机真机小循环验证（如 3 轮 / 10s 与 30s 间隔）。
- 若后续需要为 `powercycle_adb_*` 在前端参数编辑器提供逐项中文说明，可在 `backend/schemas/param_docs/` 新增对应说明登记表（不影响已发布脚本包）。
