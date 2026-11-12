# NX3 开关机专项：prefs SELinux context 与 current_count=0 假健康（#3955）

Status: implemented
Class: bug-fix

载体 issue：#3955。

## Decision

两处修复，均在 powercycle 族树：

1. powercycle_setup / powercycle_check 的 _lib.push_prefs_xml 在 root push 后，
   把 shared_prefs 目录与 powercycle_runner.xml 的 SELinux context 对齐到应用
   data 目录（由 installd 按包 seinfo 打好）。chcon 后回读校验；enforcing 下
   读不到 context、chcon 失败或回读不一致都 raise，不把「prefs 可能读不到」当成功。
   非 enforcing 设备跳过；非 root 仍走原 run-as 分支。
2. powercycle_check 的判死条件移除 cycles_done==0 豁免：结果文件已有内容且
   mtime 停滞时，current_count 恒 0 也按 dead_grace_cycles 累计；首轮基线仍由
   result_bytes==0 / not was_online 兜住。

根因（NX3 P535AA，金丝雀 AT7QNX6B05000093）：

- AutoTestTool 用 Tinno platform 签名，进程域是 platform_app:s0:c512,c768。
  首次安装时 adb push 新建的 prefs 文件落到 app_data_file:s0:c203,c256,c512,c768
  （MLS category 多出 c203,c256）。category 不匹配 ⇒ 应用读 SharedPreferences
  得 EACCES（logcat: SharedPreferencesImpl: Cannot read ... EACCES）。
- setup 的 dumpsys 探测只看到短暂的 ServiceRecord，仍报 service_started=true；
  但应用读不到 running=true / auto_resume=true。首个 cycle 由 Activity 启动，
  重启后 BootCompletedReceiver 读 prefs 失败，走 "not running, skip boot resume"，
  循环停在 cycle 1，current_count 恒 0。
- powercycle_check 旧判死把 cycles_done==0 当作 boot 早期豁免，dead_streak 每轮
  清零，于是服务已死仍报 PATROL_SLEEP，永远不升级成失败。
- V552AA（LX2/LX3）同 APK 未暴露：这些设备上 prefs 文件已由应用自己创建/重写，
  标签是 app_data_file:s0:c512,c768；后续 adb push 覆盖已有文件时保留原标签。
  NX3 是首装/重装后的新文件，才踩中 push 的默认标签。

涉及：backend/agent/scripts/powercycle_setup/_lib.py、powercycle_setup.py、
backend/agent/scripts/powercycle_check/_lib.py、powercycle_check.py；
版本 powercycle_setup 1.2.10、powercycle_check 1.0.11。

## Alternatives

- 先启动应用让它自己创建 prefs，再 force-stop + push：能借应用写出的正确标签，
  但首次启动会带着默认参数进入测试，可能与 setup 要写的 test_times/running 冲突。
- 只 restorecon：file_contexts 按路径给 app_data_file，不知道包的 seinfo；
  无法得到 platform_app 需要的 category 集。
- run-as 写 prefs：platform 签名 shared-uid 包恒拒，既有 v1.2.1 已论证。
- 全局关闭 SELinux 或 deviceidle：对机队是过度变更，且掩盖真实权限问题。
- 保留 cycles_done==0 豁免，只靠 setup 后人工巡检：NX3 假健康已经证明不可靠。
- 用 prefs running 标志替代 result_bytes 门槛：需要扩展 _read_prefs_progress
  的返回契约；本次先修已能覆盖金丝雀形态（结果文件有旧 cycle 行），
  不扩大改动面。

## Verification

- 单测：./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_powercycle_prefs_selinux_nx3.py
  backend/agent/tests/test_powercycle_scripts.py
  backend/agent/tests/test_powercycle_prefs_3463_g1pc.py
  backend/agent/tests/test_powercycle_prefs_read_paths_3463_g1pc2.py -q
  → 114 passed。
- 新增用例钉住：chcon 目标取 data 目录 context、chcon 失败/回读不一致/未知
  SELinux 状态/context 读不到均 raise、非 enforcing 跳过、push_prefs_xml 调用对齐；
  current_count=0 且 mtime 停滞 → 2 个 grace 周期后判死；result_bytes=0 仍不累计。
- 真机金丝雀（AT7QNX6B05000093，主机 172.21.9.128）：
  用更新后的 powercycle_setup 1.2.10 全新 uninstall 后重跑，输出
  success=true / service_started=true / current_count=0；prefs 文件与
  shared_prefs 目录 context 均为 u:object_r:app_data_file:s0:c512,c768。
  随后 uptime 多次跨重启重置（142→24、111→29、91→29），结果文件
  cycle 1/100000（01:04:20）→ cycle 2/100000（01:06:27）→ cycle 3/100000
  （01:08:34），proc_alive=1、svc>=1；logcat 出现
  "Start proc ... for broadcast ...BootCompletedReceiver" 与
  "BootCompletedReceiver: resume after android.intent.action.BOOT_COMPLETED"，
  无 SharedPreferencesImpl EACCES。prefs current_count=3、running=true、
  auto_resume=true、test_times=100000。
- 更新后的 powercycle_check 1.0.11 在健康态输出 success=true、
  cycles_done=3、service_alive=true（未在活动金丝雀上强杀服务，避免污染
  平台 run 1320 的巡检状态；判死路径由单测覆盖）。

## Revisit

- 若目标 ROM 把应用改为 untrusted_app 或改掉 app_data_file 的 category 规则，
  对齐目标应继续取 data 目录 context；若 ls -Z 输出格式变化，需同步解析。
- 若 chcon 在某个 Android 版本被 SELinux 策略拒绝，应改为在安装阶段用
  installd/包管理器路径创建文件，而不是放开策略。
- sleep 族（同一 AutoTestTool、同一 push_prefs_xml 形态）可能有同源问题；
  本次未改，若 NX3 的 sleep 专项也出现 prefs 读不到，按同一方案处理。
