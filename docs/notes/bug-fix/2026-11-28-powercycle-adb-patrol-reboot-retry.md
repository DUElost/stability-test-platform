# `powercycle_adb_patrol@1.0.1`：瞬态 `adb reboot` 抖动有界补发重试

Status: implemented
Class: bug-fix

## Decision

在 PlanRun 1765（10 台 `INFINIX_X6728` 7 天宿主侧 ADB 开关机压测）实跑取证中，设备 `901`（`MTK0002503011642`，Job `211167`）在连续稳定完成 **606 轮**真实重启后，第 607 轮下发 `adb reboot` 瞬间遭遇单次 USB/adbd 瞬态抖动，返回 `rc=1`（`error: device 'MTK0002503011642' not found`），命令未送达设备；随后设备立刻在线但 `boot_id` 自然未变，v1.0.0 在等待 `30s` 后直接按 `reboot_not_effective` 终止了整条 7 天 Job。

本次在 `powercycle_adb_patrol` 登记新版本 **`1.0.1`**，同步钉扎模板 `backend/schemas/pipeline_templates/powercycle_adb.json`：

- 新增可选参数 `reboot_retry_attempts`（默认 `2`，`>= 0`，支持环境变量 `STP_POWER_CYCLE_REBOOT_RETRY_ATTEMPTS` 与状态继承）；
- 当 `WAIT_REBOOT` 阶段超过 `reboot_confirm_timeout_seconds` 仍观测到设备在线且 `cur_boot_id == last_boot_id` 时：
  - 若 `reboot_retries_used < reboot_retry_attempts`：补发一次 `adb reboot`，重置 `reboot_sent_at`，累计 `reboot_retries_used` / `reboot_retry_count` 并输出 `observed="reboot_retry_<N>"`，不中断当前轮次；
  - 若已耗尽全部重试仍未生效：才按 `reboot_not_effective` 输出 `patrol_fail=true` 终态失败，并在报错中带上总尝试次数 `attempts`、最后 `rc` 与 `err` 片段；
- 每轮新发重启及确认真重启（`boot_id` 变化）后自动清零 `reboot_retries_used`。

## Alternatives

- **在 `trigger_adb_reboot()` 发出瞬间按 `rc != 0` 立即循环重试**：部分设备收到 `adb reboot` 后 USB 断链极快也会返回非零 `rc`，若按 `rc != 0` 盲目重试可能对已重启设备重复下发命令；以「设备在线且 `boot_id` 未变」作为权威事实判据再补发，既零误触发又安全兜底。
- **不重试，靠人工巡检补跑**：7 天长跑单台约 5000–6000 轮，全批偶发 1 次瞬态 ADB 抖动即中止作业，运维打断成本过高。

## Verification

- `backend/agent/tests/test_powercycle_adb.py`：新增瞬态 `adb reboot` 失败补发后 `boot_id` 变化自愈用例与耗尽 3 次（1+2）仍未生效终态失败用例。
- 守卫与包清单：`tools/dev/check_script_packages.py`、`tools/dev/check_tool_manifest.py --base origin/main`、`tests/test_pipeline_template_script_pins_2865.py`、`tests/test_template_step_timeout_vs_script_budget.py`、`backend/tests/api/test_pipeline_templates_stages.py`、`scripts/run_gates.py check:quick`。

## Revisit

- 合入 `main` 后按 `script-version-lifecycle` SOP 执行 `--publish --packages-root /mnt/stp-aee/packages` 与 `POST /api/v1/scripts/scan`，并将存量 Plan `259` / `260` 的 `powercycle_adb_patrol` 步骤重指至 `1.0.1`（当前在跑的 PlanRun `1765` / `1768` / `1769` 快照不受影响）。
