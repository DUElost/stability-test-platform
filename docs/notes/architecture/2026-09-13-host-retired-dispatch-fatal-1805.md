# ADR-0038 ④ 切片一：派发 fatal 归位 + 判据遮蔽修正（#1805）

Status: implemented
Class: architecture

## Decision

#1805 是 ④/6（派发/控制面收口），横跨 10+ 文件（dispatch / claim / scan 扇出 /
Socket.IO / hot-update / install / upgrade-gate / precheck / admission / 批量工具），
含 10 场景 mutation 纪律。本 PR 先落**风险最高、也最基础的一刀**：把退役纳入
**派发 fatal 判据**并修正**判据遮蔽**——其余面按垂直切分留后续 PR（见 Revisit）。

**1. 分类器 fatal-first**（`services/plan_dispatcher_sync.py`）：

- `_classify_dispatch_devices_sync` 的查询增读 `Host.retired_at`；
- 在 `no_host` 之后、**任何设备级暂态判定之前**插入 `host_retired` 分支
  （entry 形如 `{"id": device_id, "reason": "host_retired", "host_id", "host_status"}`）。
  原阶梯把 `device_offline/device_error` 排在 host 判定之前——「离线设备 + 退役主机」
  会短路成可重试的 `device_offline` 并进 QUEUED 等待，**永久退役事实被暂态遮住**
  （评审 182d4e-R01 点名的实现易错点）；
- `_FATAL_DISPATCH_REASONS` 增 `host_retired`：prepare 侧结构化拒绝（400），
  不进排队；V2 准入终检（all-or-nothing 复查）同样按 fatal 处理。

**2. 准入终检显式 `HOST_RETIRED` 收敛**（`services/admission_pump.py`）：

- 终检 fatal 且含 `host_retired` 时，`_FatalAdmission("HOST_RETIRED", {...})`
  而非通用 `devices_unavailable_at_admission`——对应 ADR D-2/D5bis「QUEUED/PRECHECK
  快照不删、不静默缩目标集合，以显式 HOST_RETIRED 原因收敛」；
- FAILED + 审计：`fail_plan_run_admission` 已有 `record_audit(action=
  "plan_admission_failed", details={"reason": "HOST_RETIRED", ...})`（既有路径），
  本单只改 reason 取值；
- PlanRunHost 冻结投影与 PlanRun 快照**不删**（测试断言 RowCount 不变）。

## Alternatives

- **把 `host_retired` 当作可重试（QUEUED 等待 unretire）**：弃——ADR D-1 明示退役是
  永久判据；等待 unretire 会让退役主机在解除前一直占着准入队列，且与「退役=不再使用」
  的语义冲突（评审 R01 亦要求区分于 `host_offline`）；
- **只在 prepare 过滤、不改 fallback 顺序**：弃——正是遮蔽问题本身；
- **在准入终检沿用通用 reason、只把明细塞 detail**：弃——D-2 要求「显式 HOST_RETIRED
  原因收敛」，run 的 `result_summary.reason` 是运维第一落点，必须可辨；
- **本次一并做 claim/scan/WS/控制面拒绝/in-flight 收敛**：弃——跨 10+ 文件、含
  mutation 纪律，仓促合并会引入派发语义风险；按垂直切片分次交付（本次已含准入
  终检这条最接近派发主链的收口）。

## Verification

- **新增 5 例**：
  - `test_plan_dispatcher_device_validation.py::TestHostRetiredDispatchGate`（4 例）：
    分类器遮蔽对照（退役 + 设备离线 → `host_retired` 而非 `device_offline`）、
    在线设备的退役分类（含 host_id 回传）、`prepare_plan_run` 结构化拒绝且**不产生
    QUEUED 行**、`host_retired` 已登记为 fatal；
  - `test_admission_queue_step4.py::test_retired_host_fails_fatal_with_host_retired`：
    排队后主机退役 → 终检 `_FatalAdmission(reason="HOST_RETIRED")` → FAILED 且
    `result_summary.reason == "HOST_RETIRED"`、**PlanRunHost 快照保留**、审计
    `plan_admission_failed` 落库；
- **反例实证**：
  - 从 `_FATAL_DISPATCH_REASONS` 去掉 `host_retired` → **3 failed**（prepare、
    fatal 登记、准入 HOST_RETIRED）；
  - 移除 fatal-first 分支 → **3 failed**（两个分类器用例 + prepare）；恢复后全绿；
- **回归**：派发/准入 5 个套件（device_validation + admission step3/step4 +
  precheck + dispatch_retry）→ **95 passed**；
- `ruff` → All checks passed；`check:quick` → **7 gates OK**。

## Revisit

- **本单未覆盖的 ④ 面**（按 issue 逐条对照，留后续 PR）：
  1. **claim 收口**：`agent_api._claim_jobs_for_host` 需叠加 `retired_at IS NULL` +
     可区分信号（对照 `claim_skipped_host_maintenance`）；当前退役主机上的**既有**
     Job 仍可被认领执行（新目标已被 prepare/终检拦住，但已物化的 Job 不受影响）；
  2. **scan/archive 扇出**：`plan_run_scan_scope.py` + `saq_tasks` / `plan_runs` /
     `dedup` / `ai_assistant.plan_run_ops` 的调用点需区分「历史证据集合」与
     「可发新命令的目标」，回收类放行需 `skipped_retired` 显式标记；
  3. **Socket.IO / reload-config / watcher 切换**：`dedup.py` reload-config 补 host
     存在性与退役判据；
  4. **控制面动作拒绝**：hot-update（`hosts.py`）、install（`:793-825`）、
     upgrade-gate（`agent_api.py`）、批量工具 `batch_hot_update.py --direct`；
  5. **预检/准入前置副作用**：`precheck/runner.py` 与 `admission_pump` 的 SSH/SFTP
     推脚本发生在终检之前（评审 R01 指出的「尚未产生 Job ≠ 尚无控制面副作用」），
     过滤点需前移；
  6. **10 场景 mutation 纪律**：issue 要求按 182d4e-F7 的场景表逐点 mutation 验证，
     本单只做了 fatal 两点的 mutation（fatal 登记 / fatal-first 顺序）；
- **In-flight 收敛的另一半**：② 的 Cordon 已阻止「有在途 Run 时退役」，本单兜住
  「排队后退役」的准入终检；「RUNNING 中退役」按定义不可达（Cordon 拦截），
  若将来放宽 Cordon 需重新评估；
- **Prometheus/告警**：派发拒绝是否需要对 `host_retired` 单独打点（区分于
  not_found/no_host），留观测面独立评估。
