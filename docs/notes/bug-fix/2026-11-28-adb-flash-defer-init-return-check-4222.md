# ADB 刷机流水线推迟 `init_return_check` 至刷机后执行（#4222 问题 3）

Status: implemented
Class: bug-fix

## Decision

针对 PlanRun #1762 现场（216 台 `*261117V108` 设备重启卡死在开机动画：`adb get-state=device` 且 `adb reboot` 可稳定在 ~3.2s 后枚举 `0e8d:2000` / `/dev/ttyACM0` 刷机口，但 `sys.boot_completed` 为空导致常规 ADB 刷机在进入 `flash_firmware` 前即被 `run_init_checks` → `powercycle_disarm` 100% 拦死），将 Agent 引擎的 `init_return_check` 推迟机制从仅限 `vbus_cycle` 扩展到 `adb_reboot` 整机刷机流水线：

- 保持 `is_vbus_flash_init` / `is_vbus_flash_lifecycle` 语义不变（控制面 `allow_device_offline`、`vbus_slots` 与 `is_vbus_flash` 展示契约不受影响，严格遵守 ADR-0063 D4）；
- 在 `backend/agent/contracts/vbus_flash.py` 新增 `is_deferred_flash_step` 与 `deferred_flash_init_prefix_len`：
  - 匹配 `[script:flash_firmware, ...]`（前缀长 1）与 `[script:flash_preflight, script:flash_firmware, ...]`（前缀长 2），`trigger_mode` 支持 `vbus_cycle`、`adb_reboot` 与缺省值；`flash_firmware` 被禁用（`enabled=False`）或 `trigger_mode` 非法时不推迟；
  - 首步为 `check_device` 等非刷机/预检步骤时仍保持先做残留检查；
- 在 `backend/agent/pipeline_engine.py`：
  - `_execute_lifecycle` 命中 `deferred_flash_init_prefix_len(init_def) > 0` 时，先执行刷机前缀，成功后再调用 `_check_init_returns()`，通过后再执行剩余 `init` 步骤；
  - `STARTED` 前的丢租约/取消阻断扩展到 `is_deferred_flash_step(step)`；
  - 纯宿主预检 `script:flash_preflight` 不计入设备侧动作 `_init_steps_started`，预检失败或预检后丢租约时本 job 不触发 teardown。

## Alternatives

- **仅放宽 `is_vbus_flash_init` 覆盖 `adb_reboot`**：会连带让控制面把普通 ADB 刷机当成 `vbus_cycle`（放行 `OFFLINE` 设备、走 `vbus_slots=1` 认领通道），破坏 ADR-0063 D4 边界，不可取。
- **在 `flash_firmware` 脚本内新增开关参数**：要求升级脚本版本并重发包，且 `run_init_checks` 发生在 Agent 引擎调用脚本之前，脚本参数无法单独绕过引擎前置检查；复用现有 ADR-0063 D5 推迟结构最小且零脚本版本变更。
- **跳过刷机作业的 `init_return_check`**：会破坏 ADR-0061 D6「新 job 消费设备前核验残留」的纵深防御；推迟到刷机完成并重新开机 `sys.boot_completed=1` 后执行，既解开开机卡死设备的恢复死锁，又保留刷后核验闭环（含 `skip_if_current=true` 跳过刷机的情形）。

## Verification

- 在 `.wt/fix-4222-adb-flash-defer-init-check` 通过内存保护 runner 执行 `backend/agent/tests/test_vbus_init_check_order_3892.py`、`backend/agent/tests/` 全量，以及 `scripts/run_gates.py check:quick`（具体运行结果见 PR 验证记录）。

## Revisit

- 本 PR 仅收口 `#4222` 问题 3（ADB 刷机推迟 `init_return_check`）；`#4222` 问题 1（`powercycle_check` 隔段开机卡死检测）与问题 2（teardown `job_running_timeout` 恢复误重跑 `init`）保留在 `#4222` 后续独立 PR 处理。
- 合入后需按 `control-plane-deploy` SOP 部署控制面并热更新目标 Agent 主机（本改动触及 `backend/agent/` 载荷，会更新 Agent 期望 digest），随后即可对欠账 override 放行后的卡死设备直接派发 ADB 恢复刷机 Plan。
