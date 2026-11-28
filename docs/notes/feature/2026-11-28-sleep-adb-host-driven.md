# 宿主侧 ADB 驱动休眠唤醒专项（`sleep_adb_*` + `sleep_adb.json`）

Status: implemented
Class: feature

## Decision

在 `INFINIX-X6728` 休眠唤醒专项（PlanRun `1756`，10 台）实跑复盘中，沿用设备端 `AutoTestTool.apk`（`sleep_setup` / `sleep_check` / `sleep_finish`）的设备因 APK 签名非系统 `platform`（`c24808bd` / 普通 `userId`），在 Android 15 上进入休眠后 `SLEEP_TEST_ALARM_WAKE` 无法唤醒设备，10 台设备全员停滞在 `0–2` 轮 `mWakefulness=Asleep` 达 48 小时，而 `sleep_check` 仅检测进程存活未判文件停滞导致假绿。

比照已验证稳定的 `powercycle_adb_*` 模式，新增**宿主侧 ADB 驱动**的独立休眠唤醒脚本族与 Pipeline 模板：

- **`sleep_adb_setup@1.0.0`**（`init`）：等待设备就绪，配置关闭插电常亮（`svc power stayon false` / `stay_on_while_plugged_in 0`）并设超长系统亮屏超时，唤醒解锁至桌面核验 `mWakefulness=Awake`，初始化宿主侧状态文件（按 `STP_JOB_ID + serial` 隔离，`WAIT_WAKE` 起跑）；
- **`sleep_adb_patrol@1.0.0`**（`patrol`，模板默认间隔 `30s`）：
  - `WAIT_WAKE`：亮屏保持 `wake_seconds`（默认 `60s`，剩余 `<= inline_wait_max_seconds` 时内联补齐）后发 `input keyevent KEYCODE_SLEEP`，通过 `dumpsys power` 回读确认 `mWakefulness` 进入 `Asleep`/`Dozing`；
  - `WAIT_SLEEP`：休眠保持 `sleep_seconds`（默认 `300s`，期间若意外自唤醒则记 `sleep_anomalies` 并重新息屏）；到期发 `KEYCODE_WAKEUP` + 解锁回桌面并核验 `mWakefulness=Awake`（带重试），达标返回 `patrol_complete=true`，唤醒失败输出 `patrol_fail=true` 立即终止；
- **`sleep_adb_finish@1.0.0`**（`teardown`）：设备端零 APK/prefs/Alarm 足迹、无需 disarm；收尾唤醒解锁并恢复 `svc power stayon usb`，按与 `sleep_finish` 兼容的结果 JSON 结构落盘至 `{STP_AEE_NFS_ROOT}/sleep/{project}/results/{run_id}.json`；
- **`backend/schemas/pipeline_templates/sleep_adb.json`**：新增 `specialty_key="sleep"` 模板「休眠唤醒压测（宿主 ADB 驱动）」，并登记 `SCRIPT_TAG_REGISTRY`、`tool_manifest.json` 与模板守卫。

## Alternatives

- **沿用设备端 `AutoTestTool` APK 休眠唤醒并只补 `sleep_check` 停滞检测**：即便补齐判死告警，在未取得与 ROM 一致 `platform` 签名的 `user` 版本上依然跑不动，无法解决本质问题。
- **修改既有 `sleep_setup` / `sleep_check` / `sleep_finish` 混入宿主模式**：保留现有设备端 APK 族给需脱机/原厂报告的旧机型；独立 `sleep_adb_*` 族与 `sleep_adb.json` 模板零回归且无设备端足迹。

## Verification

- `backend/agent/tests/test_sleep_adb.py`：覆盖 `setup -> WAIT_WAKE -> WAIT_SLEEP -> 2 cycles PASS -> finish`、唤醒失败触发 `patrol_fail`、休眠中途意外唤醒记录异常并重睡、以及三族 `_lib.py` 字节同源校验。
- 守卫与包清单：`tools/dev/check_script_packages.py`、`tools/dev/check_tool_manifest.py --base origin/main`、`tests/test_pipeline_template_script_pins_2865.py`、`tests/test_template_step_timeout_vs_script_budget.py`、`backend/tests/api/test_pipeline_templates_stages.py`、`scripts/run_gates.py check:quick`。

## Revisit

- 合入 `main` 后按 `script-version-lifecycle` SOP 执行 `--publish --packages-root /mnt/stp-aee/packages` 与 `POST /api/v1/scripts/scan`，并在 10 台 `INFINIX_X6728` 上先单机冒烟、再按 `5m/1m` 恢复 7 天压测。
