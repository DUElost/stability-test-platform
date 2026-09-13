# sleep_check/gpu_check 离线豁免分支 KeyError——离线反而必判失败（#1693）

Status: implemented
Class: bug-fix

## Decision

#814（f4681e16，sleep_check v1.0.3 / gpu_check v1.0.8）引入的离线豁免分支
以 ``pass`` 跳过写入：新 Job 首拍（job_id 重置后 state 仅含 ``job_id``）
遇设备离线走该分支，随后判死比较对 ``state["dead_streak"]`` 的**无条件读**
抛 KeyError——被 ``main()`` 捕获报 failure。即：本该容忍离线的豁免分支，
使「设备离线」从「不判死」变成「必判失败」，恰是 #814 要消除的反面。
2026-09-13 变更审计以 /tmp 对照实跑定性 HIGH；本 PR 首拍反事实复现
（v1.0.3 ``KeyError('dead_streak')``、v1.0.4 正常）。

修复（按 ADR-0020 新版本表达，已发布版本字节不动）：

1. **sleep_check v1.0.4**：离线分支同样把 ``dead_streak`` 归一入 state
   （``int(state.get("dead_streak", 0))``，保持现值、不累计），保证判死
   比较前键必然在场。其余判定语义与 v1.0.3 一致。
2. **gpu_check v1.0.9**：同型修复，v1.0.8 其余判定（GPU_RUN_END 完成检测 /
   提前崩溃判定 / protobuf 字段级解析）不动。
3. **seed 迁移**（``y8z7a6b5c4d3`` / ``z7a6b5c4d3e2``，链于 x9y8z7a6b5c4）：
   注册新版本行并停用 v1.0.3 / v1.0.8。停用前执行内嵌的 plan_step 引用
   护栏（#942 裁决「遇引用即失败」，自包含不 import 服务层）——生产
   plan_step 仍引用旧版时迁移 abort 并给出重指指引，而非静默打断 dispatch。
   content_sha256 = 新版本 entry 脚本字节 sha256。

## Alternatives

- 判死比较改 ``state.get("dead_streak", 0)``——同样闭合 KeyError，但离线
  分支仍不落盘，state 文件缺键的形态残留，未来任何新增读取点会再次踩雷；
  分支内归一使「三分支后键必然在场」成为不变量。
- 原地修 v1.0.3 / v1.0.8——违反 ADR-0020 不可变门禁，且在途 Plan 的
  precheck 期望 sha 永久失配。
- 不出 seed、只走 scan 通道（#814 Note 的口径）——本单 base 上最近的两次
  版本 bump（eedab1e7、x9y8z7a6b5c4）均配套 seed；空库链 upgrade head 的
  终态应直接是修复版激活，故随 PR 出 seed 并以 #942 护栏约束停用。

## Verification

- ``tests/test_check_exemption_protobuf_814_746.py`` 扩至 14 passed：新增
  行为级 7 例（真实 ``_run`` + adb 打桩）——fresh state 离线首拍不崩不判死
  且键归一、离线不累计、恢复在线正常累计至 grace 判死、存活清零（sleep/gpu
  对称）+ 新旧版本并存断言。
- 反事实对照：同场景驱动 v1.0.3 复现 ``KeyError('dead_streak')``。
- 一次性 PG16 容器（5433 非生产端口，非生产库）：空库 ``alembic upgrade
  head`` 全链通过；``check_schema_sync`` exit 0（7 项基线噪音、0 漂移）；
  downgrade 往返两 seed 干净；插入引用 v1.0.3 的 plan_step 后 upgrade 以
  「seed migration aborted: … sleep_check 1.0.3 ×1」abort，重指后重跑至
  head，终态 v1.0.4 / v1.0.9 active。
- 相邻面：``backend/agent/tests/test_check_state_per_job.py``、
  ``test_sleep_scripts.py``、``test_gpu_scripts.py``、
  ``backend/tests/test_script_reference_check.py`` 共 98 passed。
- ``python scripts/run_gates.py check:quick`` 7 gates 全绿。

## Revisit

- 生产 plan_step 若仍钉 sleep_check v1.0.3 / gpu_check v1.0.8：迁移将按
  #942 护栏 abort——重指到 v1.0.4 / v1.0.9 后重跑（PR 与 issue 已注明）。
- 空库链中 gpu_check v1.0.5 保持 active（v1.0.6/7/8 从未出 seed，f4681e16
  亦未出）——既有链缺口，归 #735 版本膨胀治理，本单不越界。
- #1695（gpu_check protobuf 字段级解析 PASS→FAIL 回归，同源于 f4681e16）
  仍开放，属不同缺陷面，留待并行认领。
