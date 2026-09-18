# Agent Note — serial 冲突设备按设备拒绝：prepare 剔除 + 准入物化 FAILED（#2649）

Status: implemented
Class: feature

## Decision

占位/重复序列号设备（`is_placeholder_serial` 判据，#1356 既有原语）在派发链
按**设备**拒绝，手动与定时同一流程（owner 2026-09-18 裁决）：

1. `_classify_dispatch_devices_sync` 新增拒因 `serial_conflict`——置于
   `host_retired`（永久配置事实）之后、暂态（offline/error/lease/job）之前，
   持久原因优先于暂态；不进 `_FATAL_DISPATCH_REASONS`（不整 run 拒绝）。
2. `prepare_plan_run` 将 serial_conflict 设备从目标集合**剔除**（不进 target
   快照 / host 投影 / drift 终检，不拖其它设备连坐），拒因记入
   `run_context.dispatch_rejected_devices`；全部设备都冲突时结构化拒绝。
3. 准入 `admission_transaction` 物化阶段把被剔除设备写成 **FAILED
   JobInstance**（status_reason=序列号冲突中文说明，pipeline_def 取 run 快照
   满足 NOT NULL；永不进 claim 循环），同步 bump total/terminal/failed 计数
   ——结果列表可见「Fail + 原因」，run 终态化与汇总自然包含。
4. 前端选机面：`ReadinessDevice.serial_suspect` 透传（`DeviceOut`/`Device`
   类型 #1356/#2032 已具备）→ `DispatchCockpit` 顶部新增 `SerialConflictBanner`
   提示「N 台将被拒绝执行并标记失败」；用户仍可发起，后端流程兜底。

## Alternatives

1. serial_conflict 归 fatal（整 run 400）——正是现状痛点：run 423 一台占位
   serial 设备把 568 台整窗阻断（device_host_drift FATAL 的根因就是双 host
   争抢同一行）。按设备拒绝是裁决语义。
2. 心跳/注册层直接拒绝占位 serial 建档（改 upsert 键）——影响面大（存量行
   的 host 归属、dedup 管线按 serial 的路由），另立治理单做（注册拒绝 + 归属
   翻转告警升级）；本单只做派发面止损。
3. 前端弹窗强阻断——与裁决不符（「如果还是进行开始运行测试，可以直接拒绝」
   即允许发起、按设备拒绝）。

## Verification

- `python -m pytest backend/tests/services/test_plan_dispatcher_device_validation.py
  test_plan_dispatcher.py test_plan_dispatcher_precheck.py
  test_admission_queue_step2/3/4.py test_plan_run_dispatch_retry.py` →
  173 passed（新增 `TestSerialConflictDispatch` 6 用例：分类判据与短路序、
  prepare 剔除不连坐、全冲突拒绝、准入物化 FAILED job + 计数）。
- `npx vitest run SerialConflictBanner.test.tsx ExecuteCommandBar.test.tsx`
  → 7 passed；`npx tsc --noEmit` → clean。

## Revisit

- 注册层治理（拒绝占位 serial 建档 / host 翻转计数告警）另立单；#2569 已加
  双分支漂移日志，若翻转可观测性仍不足再升级。
- 若未来出现「克隆真序列号」（非占位但跨 host 争抢）的派发期实证，考虑把
  判据扩展到翻转计数而非仅 is_placeholder_serial。
