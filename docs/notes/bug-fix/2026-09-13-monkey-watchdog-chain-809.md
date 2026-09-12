# monkey 看门狗链三缺口（#809）——v1.2.2 / v2.0.3 / v5.0.1 / v1.0.1

Status: implemented
Class: bug-fix

## Decision

本质问题（#809，B×3，老审查批 #827 总表项）：AIMonkey 双看门狗链（MonkeyTest.sh
+ aimwd/MonkeyWatchdog）有三处静默退化，叠加后"数小时无 monkey 仍每周期报绿"：

1. **aimwd 推不上设备**：bundle 中 `aimwd` 是 241B ASCII 脚本（内容是
   `exec app_process … MonkeyWatchdog`），而 `monkey_test` / `monkey_resource_push`
   都按 `is_dir()` 守卫——永远不推；连带两处 nohup 启动/重启空跑（rc 被忽略）。
2. **monkey_check 重启假成功**：`nohup sh … &` 的 shell rc 恒 0（脚本缺失/FBE
   未解锁/fork 失败都照样 0），v2.0.2 只看 rc 就报重启成功。
3. **monkey_test 推送失败不记 error / monkey_running 不作门禁**：黑名单/看门狗/
   媒体推送 rc 全部丢弃；启动后 3s 单次 ps 只写 metrics。

修复（版本目录不可变，全部新版本）：

- `monkey_test/v1.2.2`：aimwd 按 `is_file()` 推 `/data/local/tmp/aimwd` 并
  `test -s` 设备侧回验；启动后轮询 ps 确认 MonkeyWatchdog 在跑，未见计入
  errors；黑名单/看门狗/媒体/可选资源推送 rc 与 chmod rc 全部计入 errors；
  `monkey_running` 在 ≥15s 窗口复查，仍未见计入 errors（成功门禁）。
- `monkey_resource_push/v1.0.1`：aimwd 按 `is_file()` 推送并纳入
  `required_files`，post-check 统一 `test -f` + 非零大小；bundle 缺失即 fail。
- `monkey_check/v2.0.3`：`_restart_watchdog` 改为"rc 通过后，在共享 ≤60s 窗口
  内轮询 ps 见到 MonkeyTest.sh 与 MonkeyWatchdog 才算成功"；超时
  `output_result(False)` + exit 1。
- `monkey_launch/v5.0.1`：启动 aimwd 后同样 post-check MonkeyWatchdog，未见即
  fail——不再把单层看门狗记成启动成功。

**一处有意超出 issue 字面的耦合修复**：issue 只要求 check 轮询 MonkeyTest.sh；
但 aimwd 的 cmdline 是 `app_process … com.android.commands.monkey.utils.
MonkeyWatchdog`——**包含** `com.android.commands.monkey`。让 gap-1 的 aimwd
真正跑起来后，monkey_check 的 monkey 存活检测（`_ps_grep` 默认模式
`com.android.commands.monkey`）与 monkey_test 的 `grep monkey` 都会把看门狗
本身当成"monkey 在跑"：check 的"双亡→重启"分支永远进不去；test 的
monkey_running 门禁失真。故两处 monkey 进程检测统一 `grep -v MonkeyWatchdog`
排除，restart 成功条件同时要求两个进程——否则 gap-1 的修复会把 gap-2/3 的
修复原地架空。（该排除依赖我们启动的进程名，不依赖 APK 内部类名。）

## Alternatives

- **原地改旧版本**——禁止（AGENTS.md 版本目录不可变硬不变量 / ADR-0020 门禁）；
- **只改 is_file、不做启动后 ps 验证**——放弃：nohup rc 恒 0 正是本批要消灭的
  "假成功"形态；`test -s` 只证明文件在设备上，不证明进程跑起来（FBE 未解锁/
  权限/缺 APK 都可能启动即死）；
- **用精确类名 grep（`com.android.commands.monkey.Monkey`）区分真 monkey 与
  看门狗**——放弃：类名是 monkey.apk 的实现细节，排除法只依赖看门狗进程名，
  语义更稳；
- **媒体推送失败只记 metrics 不 fail**——放弃：issue 明确要求 rc 计入 errors；
  部分资源缺失会改变 monkey 行为，"跑起来但资源不齐"比直接失败更贵；
- **monkey_check 重启只验证 MonkeyTest.sh（issue 字面）**——放弃：单层存活被
  记成全绿仍是本 issue 要消灭的形态（见 Decision 的耦合说明）。

## Verification

实际运行（worktree `/tmp/stp-809`，基于 `origin/main`）：

- `pytest backend/agent/tests/test_monkey_watchdog_chain_809.py -q` →
  **30 passed**（覆盖：aimwd is_file 推送/设备侧回验/推失败/缺失/目录形态、
  MonkeyWatchdog 未见即 fail、媒体部分失败→errors、monkey_running 门禁、
  wait_ps 轮询（重试命中/超时/排除子句）、check 重启四态（rc 非零/无 sh/无
  aimwd/双全）、check 主流程三态、launch 三态、resource_push 四态）；
- 相邻既有测试（v1.2.0 路由、v1.2.1 stdout 契约、monkey_setup v2.3.7、
  aimonkey_paths、legacy_tool_cleanup）→ **33 passed**（未设 `JWT_SECRET_KEY`
  时 legacy 2 例失败，属既有环境要求，与本次改动无关）；
- **反向验证（mutation testing）**：对 4 个新版本注入 8 个旧缺陷形态
  （is_dir 守卫回退 ×2、去掉 aimwd ps 验证、媒体失败静默、去掉 monkey 门禁、
  check 重启恒真、launch aimwd 验证恒真、check 检测不排除看门狗）→
  **8/8 均被测试捕获**；恢复后全量 30 passed；每轮清 `__pycache__`（含
  `backend/agent/tests/__pycache__`）避免旧字节码复用；
- `ruff check backend/agent/tests/test_monkey_watchdog_chain_809.py` →
  All checks passed（`backend/agent/scripts/**` 按 `ruff.toml` 属
  extend-exclude：版本目录不可变，lint 不覆盖）；
- `check:quick` → **7 gates 全绿**（ruff/eslint/tsc/knip/compileall/
  gov-surface/ai-work）。

未完成（pending）：

- 真机/隔离环境：aimwd 真实推送 + MonkeyWatchdog 的 ps 可见性（不同 Android
  版本的 `ps -ef` 字段差异、FBE 未解锁设备的失败路径）；
- 真机：check 重启双验证的最坏耗时（共享 60s 窗口）与 MonkeyTest.sh 拉起
  monkey 的时序——本批 monkey_running 门禁取 issue 指定的 ≥15s 窗口，
  若真机冷启动更慢需按实测数据调整。

## Revisit

- **monkey_launch 早退路径**（watchdog 已在跑 → `already_running` 直接成功）
  不检查 aimwd：单层存活仍会被记成功。本次按最小范围未动（该路径不启动任何
  进程，补行为会引入"启动副作用"式语义扩张）；若真机复盘显示该路径漏报退化，
  另立单；
- monkey_check 的"watchdog 活着但 monkey 不在 → 不干预"分支依赖 MonkeyTest.sh
  自愈循环，本批未加窗口验证（该层职责不在 #809 三缺口内），真机若见自愈失效
  需另单补；
- 旧版本（monkey_test ≤v1.2.1、monkey_resource_push v1.0.0、monkey_launch
  ≤v5.0.0、monkey_check ≤v2.0.2）保持原字节（ADR-0020），新行为只在新版本
  生效——脚本解析取最高版本，无需手工切换；
- `monkey_launch/v4.0.0` 的 stdout `[STP_MONITOR]` 风险已在 #808 的 Note 记录
  （同批相邻问题），建议仍按"stdout 只允许最终 JSON"纪律另开单。
