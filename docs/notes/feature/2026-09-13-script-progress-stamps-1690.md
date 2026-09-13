# 长耗时脚本补齐 PROGRESS 打戳：三脚本首批（#1690）

Status: implemented
Class: feature

## Decision

#1690（#872 后续，Part 2）：#115 阶段 2 的契约「长耗时步骤必须打戳」在最新版
脚本里**大面积未落实**（实测 20+ 个 0 处打戳）。本单按裁决只做**三个最常用的**
（控制爆炸半径），其余逐批收敛：

| 脚本 | 新版本 | 打戳覆盖 |
|---|---|---|
| gpu_setup | v1.0.9 → **v1.1.0** | 三 APK 的 push（timeout=600）与 pm install 全程心跳；`pre_reboot` reboot/等待 boot_completed（最长 ~240s）轮询戳 |
| powercycle_setup | v1.0.2 → **v1.1.0** | AutoTestTool push/pm install（含重试）全程心跳 |
| fill_storage | v1.0.2 → **v1.1.0** | dd 填盘（最长 300s）全程心跳 |

实现（每版本自带 `_lib.py`/`_adb.py` 拷贝，不跨版本共享）：

- 新增 `progress_heartbeat(phase, interval=20s)` 上下文管理器：`start` +
  周期 `heartbeat`（含 `elapsed_s`）+ `end`，**seq 单调**（进程内锁计数器，
  与 #804 的 seq 判据对齐）；`progress_tick(phase, **extra)` 供轮询循环
  逐次打戳；
- 接线点：两个 `install_*` 的 push/pm install、fill_storage 的 dd、
  gpu_setup 的 reboot 等待循环；`capabilities.json` 三处均声明
  `["progress_stamps"]`（此前均未声明）；
- 心跳线程为 daemon（`stop.wait` 可即时唤醒；退出前 join ≤1s），不改变
  主流程时序与退出码。

与 #872 的分工：平台侧已把 barrier 续期放宽为「信任执行态」（PR #1691），
本单解决**另一个剖面**——步骤级 `stall_seconds` 停滞钟仍只认 PROGRESS 戳，
启用停滞钟的 Plan 下这些长步骤会被误杀。两处修复互补、缺一不可。

## Alternatives

- 一次性补全部 20+ 脚本：爆炸半径大、验证成本高，用户裁决首批三脚本；
- 用 `dd status=progress` 做真实字节进度而非固定心跳：更精细，但需要把
  `adb_shell_quiet` 改为流式读取（Popen reader 循环），改动面与风险都更大；
  固定心跳已满足「停滞钟活性」目标，真实进度留 Revisit；
- 平台侧调大 `STP_BARRIER_PROGRESS_STALE_SECONDS`：治标（换更慢步骤复发），
  且不解决 stall 钟剖面——已被 #872 方案取代。

## Verification

- `pytest backend/agent/tests/test_setup_progress_stamps_1690.py`：10 passed
  ——三个库的 heartbeat（start/心跳/end + seq 单调唯一）与 tick；两个
  `install_*` 的 push/install 段有戳且 push 确实发生；fill_storage 的 dd
  段在心跳上下文内执行；gpu_setup 的 pre_reboot 三阶段（reboot/wait/settle）
  有轮询戳；
- `pytest backend/agent/tests` 全量：见 PR 验证节；
- `check-script-version-immutability.py --base origin/main`：OK（旧版本原地
  未动）；ruf 全绿。

## Revisit

- 其余 17+ 个缺戳脚本（push_resources / monkey_resource_push / install_apk /
  monkey_launch / sleep_setup / mtbf_setup / gpu_finish 等）逐批收敛；
  **新脚本必须带打戳**（#115 契约）——可在脚本骨架/评审清单中固化；
- `dd status=progress` 的真实字节进度（替换固定心跳）作为可选增强；
- 心跳间隔 20s 与 `stall_seconds` 建议值（≥120s）的关系：若未来默认停滞钟
  收紧到 <20s，需同步调小间隔（当前上游建议值远大于 20s，安全）。
