# Issue #4222 Problem 1：powercycle_check v1.0.13 补齐开机卡死抽检、有界零进展与持续离线兜底

Status: implemented
Class: bug-fix

## Decision

针对 Issue #4222 问题 1（设备卡死在开机动画时 `adb get-state == device` 且 `result_bytes == 0`，导致旧版 `powercycle_check` 整轮 24h 上报 `success: true` 直到 teardown 才爆发），发布 `powercycle_check` `v1.0.13`（同步更新 `tool_manifest.json` 与 `powercycle.json` 模板），在保持正常主机并发零额外压力的前提下补齐三道早停边界：

1. **开机就绪窗口抽检（`boot_check_interval_seconds=120`，`boot_hang_timeout_seconds=600`）**：
   - 当 `service_alive()` 为真时直接跳过探测并清零挂起计时，对健康压测设备零额外 ADB 开销。
   - 仅当 `device_online()` 为真且 `service_alive()` 为假时，按间隔抽检 `getprop sys.boot_completed`；连续超过 `boot_hang_timeout_seconds`（默认 600s）仍未就绪即以 `patrol_fail=True` 提前结束 Job，并上报结构化诊断进度。
2. **打破 `result_bytes == 0` 永久豁免（`zero_result_timeout_seconds=900`）**：
   - 以时间锚 `zero_result_since` 替代永久清零：仅在超时前保留开机早期未建文件宽限，超时后若仍无服务存活且零进展，即正常累计 `dead_streak` 直至触发判死，不再被 `not was_online` 交替离线冲刷归零。
3. **持续 ADB 离线有界失败（`max_offline_seconds=1800`）**：
   - 以 `offline_since` 记录连续 `device_online() == False` 起始时刻，超过 `max_offline_seconds` 后发出 `patrol_fail=True`，避免掉线/黑屏/USB 失联静默空转至配额结束。

## Alternatives

- **每个 patrol 周期无论是否 `service_alive()` 都执行 `getprop sys.boot_completed`**：会对全部健康在跑设备增加多余 ADB 探测并拖慢高密度主机；改为仅在 `not alive` 时按间隔抽检，只在异常/启动窗口触发。
- **直接删掉 `result_bytes == 0` 豁免**：会破坏正常设备冷启窗口前几个周期不误杀的既有契约，故改用有界时间窗 `zero_result_timeout_seconds`。

## Verification

- 运行 `tools/dev/check_script_packages.py --register powercycle_check 1.0.13`、`check_script_packages.py`、`check_tool_manifest.py --base origin/main`、`scripts/run_pytest.py`（`test_powercycle_scripts.py`、`test_check_state_per_job.py`、`test_powercycle_r30_flavor.py` 等）与治理面检查；实际运行结果见 PR 描述。

## Revisit

- 本 PR 合入后按 `script-version-lifecycle` 执行站点发包（`--publish --packages-root /mnt/stp-aee/packages`）与 `POST /api/v1/scripts/scan`，并结合后续长稳 Plan 验证开机卡死 / 离线异常可按配置窗口及时早停。
