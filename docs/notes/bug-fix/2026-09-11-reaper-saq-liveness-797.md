# precheck_reaper v2 重排前查 SAQ 存活（#797）

Status: implemented
Class: bug-fix

## Decision

大 fleet（60+ host）下 admission Phase A 含逐 host RPC 校验 / SSH 推脚本
（`_verify_scripts_phase`），单次准入超过 `PRECHECK_ACTIVE_STALE_SECONDS`
属常态；`reconcile_stale_precheck_v2` 仅凭 `precheck_started_at` 超时即重排，
3 轮耗尽 `MAX_ADMISSION_REQUEUE_ATTEMPTS` 后判
`admission_requeue_exhausted` FAILED——**一次本可成功的健康慢准入被永久
假失败**（V1 路径有 `get_saq_job_state_sync` / `is_worker_alive_sync` 检查，
v2 没有）。

修复（在 #987 锁复读 + stale 复查通过之后、attempts 判定之前）：

- 构造 `admission:{run_id}:{attempt_id}`（与 `admission_pump` 入队 key 同源）
  查 SAQ job；
- `queued`（待 worker 取）或 `active` 且 worker 存活 → **跳过本轮**
  （不计 attempts、不重排）；日志留痕；
- job 缺失（丢队列）或 worker 已死 → 走既有重排/失败路径不变。

重排后旧 SAQ job 醒来会因非 owner 静默退出（#987 锁复读保证 attempt 一致），
故无并发双准入——本修复消除的是「可重复假失败」而非竞态。

## Alternatives

- **拉长 stale 窗口/加大 attempts**——放弃：治标；fleet 规模无上界时任何
  固定窗口都可能被合法慢准入超过，存活探测才是判据；
- **v2 完全复用 V1 的候选查询**——放弃：V1 语义（enqueue_key/dispatch_state）
  属已退主路径；v2 只借用同样的两个 helper。

## Verification

- **反例实证**：回退实现保留测试 → active+live / queued 两用例失败（存活
  保护缺失时被重排）；修复版全绿；
- 新增用例（`test_admission_queue_step2.py::TestAdmissionReaperLiveness` +3）：
  active+worker 存活跳过（status/attempts 不变）/ queued 跳过 / **worker 死
  亡仍重排**（保护不误放）；
- `step2 + step4 + scheduler/` 全套 **124 passed**（含 #987/#989 相关回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 疑似 A 定性（原单「机制未完全证实」）：大 fleet 慢准入触发路径已由本单
  的候选语义闭环；若生产仍出现该 FAILED，用 reaper 日志
  （`admission_reaper_skip_saq_pending` / `skip_worker_alive` 对比
  `admission_reaper_requeued`）定位是「SAQ 真丢」还是「worker 真死」；
- SAQ 状态 `aborted`（显式中止）仍走既有重排——与 #987 语义一致。
