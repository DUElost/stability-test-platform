# powercycle_disarm@1.0.11：R30 变体重启后 adbd 回落 non-root 时先尽力 adb root

Status: implemented
Class: bug-fix

## Decision

在展锐 P525（`Release_user_root`）实机冒烟中止验收中发现：设备在开关机重启后 `adbd` 会回落为
`shell`（`uid=2000` / `non-root`），而厂商原厂 R30 `AutoTestTool` 为非 debuggable 系统应用，
`run-as com.tinno.autotesttool` 会被拒。由于 `powercycle_disarm@1.0.10` 在读/写
`restart_preference.xml` 前未调用 `try_adb_root()`，teardown 阶段返回
`permission_model=non-root; readback=readback_failed; stop_error=R30 prefs 读取被拒/未知`。

为此在 `powercycle_disarm/powercycle_disarm.py` 中新增最小修复并登记 `powercycle_disarm@1.0.11`：

- 仅当 `apk_flavor == "r30"` 且 `not is_root()` 时，在设备上线（`boot_completed == 1`）并进入执行期后、
  读/写 `restart_preference.xml` 之前调用 `_lib.try_adb_root()`（尽力提权，已 root 时零额外 ADB 调用，
  无法提权的 `user` 构建仍按 fail-closed 逻辑失败报错）；
- 默认 `apk_flavor == "stp"` 路径保持零改动，不改变既有 STP 设备或测试行为；
- 同步更新 `backend/agent/tests/return_frozen_3729.py` 的 `1.0.8` 冻结包还原块，保持既有 B6/U4
  归还契约测试全绿。

## Alternatives

- 在 Plan 的 `teardown` 阶段前置插入独立 `ensure_root` 步骤：需要修改每个使用 R30 的 Plan 编排，
  且无法覆盖 `probe` 或手动调用 `powercycle_disarm` 的路径，否决。
- 对 `stp` 变体也无条件调用 `try_adb_root()`：会破坏既有 STP `non-root` / `run-as` 路径与只读探针假设，
  引入非必要 `adbd` 重启，否决。

## Verification

- 新增单测 `test_disarm_r30_attempts_adb_root_when_non_root_after_reboot`（`backend/agent/tests/test_powercycle_r30_flavor.py`）：
  覆盖 R30 `disarm` / `probe` 在 `non-root` 下先调 `try_adb_root()` 随后成功读清 `restart_preference.xml`，
  以及默认 `stp` 路径在 `non-root` 下不触发 `try_adb_root()`；
- 运行 `backend/agent/tests/*powercycle*.py`、`test_disarm_u8_3752.py`、`test_return_debts_u4_3729.py`、
  `test_return_init_u4_3729.py`、`tests/test_pipeline_template_script_pins_2865.py`、
  `tests/test_agent_clock_stub_guard_3202.py`、`check_script_packages.py`、`check_tool_manifest.py`
  及 `scripts/run_gates.py check:quick` 全通过。

## Revisit

- 后续若 #3989 模板豁免收口且 R30 成为平台一级验证类别，可将 `powercycle_disarm` 模板钉扎同步升级到最新已发布版本。
