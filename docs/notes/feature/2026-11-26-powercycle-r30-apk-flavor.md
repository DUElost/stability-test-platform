# powercycle 四件套支持 Tinno 原厂 R30 AutoTestTool 变体（apk_flavor="r30"）

Status: implemented
Class: feature

## Decision

部分机型（如展锐 P525 系列）的固件使用独立 platform 签名，且构建无 `su`，因此无法在设备端安装
STP 魔改版 `AutoTestTool.apk`（含 `com.mediatek.schpwronoff.powercycle.PowerCycleService`），
而项目交付的对应 platform 签名 APK 为 Tinno 原厂 R30 版本（`com.tinno.autotesttool` 含
`.reboot.RebootService`，具备 `android.permission.REBOOT: granted=true`）。

通过逆向与真机实测验证，原厂 R30 的 `.reboot.RebootService`（`android:exported="true"`）完整支持
命令行 `am start-foreground-service` 参数启动、写入 `restart_preference.xml`、
`BootBroadcastReceiver` 开机自动续跑以及向 `ShutdownTest/<on_off_filename>` 写入结构化每轮/汇总结果。

因此，在 PowerCycle 四件套脚本中新增可选参数 `apk_flavor`（默认 `"stp"` 保持向后零回归，
可选 `"r30"`），并登记新版本：

- `powercycle_setup@1.2.11`：支持 `apk_flavor="r30"`（可由 step params / `STP_POWER_CYCLE_APK_FLAVOR` /
  `test-config.properties` 的 `apk.flavor` 指定）；授予 `SYSTEM_ALERT_WINDOW`、清理或读取
  `restart_preference.xml`（兼容 `reset_count=False` 续跑）、以 Intent extras 启动 `.reboot.RebootService`
  （`reboot_fill_size=0`、`SENSOR_NUM_CHECK=-1`、`power_off_wakeup_or_reboot=false`）并探测 `RebootService` 存活；
- `powercycle_check@1.0.12`：支持 `r30` 下的 `RebootService` 存活检查、`restart_preference.xml`
  （`restarted_times`/`Num`）进度读取、`ShutdownTest/powercycle_result.txt` grep/收尾检测与窗口暂停/恢复；
- `powercycle_disarm@1.0.10`：支持 `r30` 下读取 `restart_preference.xml` 的 `reboot_service_going` 足迹、
  停服并清零 `reboot_service_going=false` / `Num=0` 后回读验收；
- `powercycle_finish@1.1.1`：支持拉取 `ShutdownTest/powercycle_result.txt` 并解析原厂 R30 结果格式
  （逐轮 `The N time implement result---PASS/FAIL`、`screenOnCheck`/`screenOffCheck`、汇总 `Total`/`Fail`）；
- `backend/schemas/pipeline_templates/powercycle.json`：更新 `powercycle_setup`、`powercycle_check`、
  `powercycle_finish` 版本钉扎（`powercycle_disarm` 沿用 #3989 既有模板豁免钉扎）。

## Alternatives

- 走原厂 R30 的 `StressRebootBroadCastReceiver` / `StressRebootService`：反编译确认其 `SHUT_DOWN_TIME`
  硬编码为 5 秒（无法配置 30 秒间隔），且 `onStartCommand` 强依赖 `Messenger` Parcelable extra（命令行调用 NPE），否决。
- 单独新建一套 `powercycle_r30_*` 脚本族与模板：会复制大量安装重试、就绪门、窗口收取、disarm 证据模型代码，
  造成两套逻辑长期漂移；用 `apk_flavor` 参数在现有四件套内按变体分派是最简方案。
- 向平台方申请新平台私钥重签 STP 魔改 APK：依赖外部签名审批周期且每个新 platform 签名都需要重复重签；
  支持原厂 R30 变体可直接复用厂商已签名交付的 `AutoTestTool` APK。

## Verification

- 实机逆向与闭环验证（展锐 P525AA，原厂 R30 platform 签名 APK，2 轮重启 / 15s 等待）：
  - `am start-foreground-service -n com.tinno.autotesttool/.reboot.RebootService` 自动完成 2 次
    `pm.reboot("autotesttool")` + `BootBroadcastReceiver` 续跑；
  - 完结后 `restart_preference.xml` 自动清零 `reboot_service_going=false`；
  - `ShutdownTest/<filename>` 输出 `Total :2 / NotExecuted :0 / Pass :2 / Fail :0` 及逐轮 `PASS`。
- 自动化校验：
  - `backend/agent/tests/test_powercycle_r30_flavor.py` 覆盖 R30 四件套核心路径；
  - 全量既有 `backend/agent/tests/*powercycle*.py`、`test_disarm_u8_3752.py`、
    `tests/test_pipeline_template_script_pins_2865.py`、`tests/test_agent_clock_stub_guard_3202.py`、
    `check_script_packages.py`、`check_tool_manifest.py`、`scripts/run_gates.py check:quick` 全通过。

## Revisit

- 待 `powercycle_disarm` #3989 豁免收口时，可将 `powercycle.json` 的 `powercycle_disarm` 一并切到最新已发布版本。
- 若后续厂商发布新版 R30/R31 更改 `RebootService` extras 名称或结果行格式，需同步补充对应实机样本到
  `test_powercycle_r30_flavor.py` 并以新版本登记。
