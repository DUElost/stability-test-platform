# ADR-0038 ②：retire / unretire API（Cordon 前置 + 审计 fail-closed）（#1801）

Status: implemented
Class: architecture

## Decision

按 ADR-0038 v0.2 实现分解 **②/6（退役 API）**；依赖 ① 的四列（本分支基于 ① 的
`feat/1800-host-retirement-migration`，堆叠 PR）。

**端点**（`backend/api/routes/hosts.py`）：`POST /api/v1/hosts/{id}/retire`、
`POST /{id}/unretire`，admin 鉴权 + 审计；请求体即 ① 交付的
`HostRetireIn` / `HostUnretireIn`（`retire_reason` 必填、`min_length=1`）。

**业务逻辑下沉 `backend/services/host_retirement.py`**（延续 #1520 的服务层方针）：

- **锁序与 claim 一致**：`_locked_host()` 先对 `host` 行
  `select(Host).with_for_update()`（同 `agent_api._claim_jobs_for_host:396-401` 的
  首把锁），复检与写入同处一个事务——杜绝「复检通过 → claim 先落地 → retire 覆盖」；
- **Cordon 前置（D2）**：活跃 Job（`host_upgrade_gate.ACTIVE_JOB_STATUSES`
  口径，与派发/升级门禁同源）或**在途 PlanRun 引用**（`plan_run_host` ⋈
  `plan_run.status ∈ {QUEUED, PRECHECK, RUNNING}`，含未准入的 QUEUED）→ 409，
  detail 区分两类原因；
- **显式不检查**：`status`（D2 明示；离线主机也应能退役）与 `DeviceLease`
  （退役不删行，「退役一台正被租用的主机」必须可完成，租约由
  `device_lease_reconciler` 回收）——两条均以代码注释写在
  `_assert_no_inflight_work` 的 docstring 里；
- **幂等**：重复 retire/unretire 原样返回当前态——不重写 who/when/reason、
  不重复审计（重复调用理由无常量语义）；
- **unretire 写回**：清 `retired_at`；`retired_by`/`retire_reason` 保留为**最近一次
  退役痕迹**；`retire_alerted_at` 清零（新生命周期允许 ⑤ 的告警再响一次）；
- **审计 fail-closed**：audit_logs 是事件真源。为此给
  `backend/core/audit.record_audit` 增加 `strict: bool = False` 通道
  （`strict=True` 时跳过「缺表降级」，异常直接冒泡），退役路径调用
  `strict=True`——审计写不进去则整个事务失败，状态不落库。默认 `False`
  保持既有容错语义不变（其他调用点零影响）；
- **审计内容**：who/when/reason + before/after 快照（含 D6 的 `boot_id` /
  `agent_instance_id`，换机与心跳漂移可复盘）。

## Alternatives

- **把前置直接写成 `DELETE` 的复用（含 ONLINE 拒绝）**：弃——D2 明示 `status`
  不进前置；且 DELETE 还检查历史依赖（正是退役要承接的场景）；
- **不锁行、只在写入前复查**：弃——与 claim 并发时会出现「复查通过后被 claim
  抢先」的窗口；同一行锁是最小且与既有路径同构的做法；
- **把 DeviceLease 纳入前置**：弃——见 Decision；退役语义允许带在租状态退出使用，
  且租约本就有回收器；
- **审计沿用 `record_audit` 默认（缺表降级）**：弃——ADR D2 要求审计失败即事务
  失败；降级会让「审计表缺失环境」静默退役，事件真源缺失而状态已变；
- **`retire_alerted_at` 不在 unretire 清零**：弃——清零是 ⑤ 的语义前提
  （「重新退役后仍能响一次」）；保留旧戳会让新生命周期内的告警被静默去重。

## Verification

- **API 级 15 例**（`backend/tests/api/test_host_retirement_api_1801.py`，真实 PG）：
  正常退役 + 审计快照（before/after/身份字段）、活跃 Job 409、在途 Run
  ×{QUEUED,PRECHECK,RUNNING} 三参 409、幂等（时间戳一致 + 单条审计）、reason
  空 422、非 admin 403、unretire 清标记 + 保留痕迹 + 审计、unretire 无前置
  （在途 RUNNING 也能解除）、unretire 幂等、审计失败回滚；
- **`strict` 通道 2 例**：缺表时 `strict=True` 抛错、默认仍降级返回 None；
- **锁序 1 例**：独立连接持 `host` 行 FOR UPDATE → 另一会话 `retire_host` 在
  `lock_timeout=300ms` 内失败（证明取同一把行锁），释放后同调用成功（正对照）；
- **反例实证**（移除守卫 → 对应用例转红）：
  - 注释掉 `_assert_no_inflight_work(...)` → **4 failed**（活跃 Job + 3 个在途状态）；
  - 去掉 `record_audit` 的 `or strict` → `test_strict_reraises_missing_table`
    **failed**；
- **回归**：`test_hosts.py`（37 例）+ 本文件 → **52 passed**；
  `ruff` → All checks passed；`check:quick` → **7 gates OK**。

未做（按 issue 边界）：DELETE 预检不变、claim 过滤（④）、列表/统计过滤（③）、
前端入口（⑥）。

## Revisit

- **堆叠依赖**：本 PR base = ① 的 `feat/1800-host-retirement-migration`；① 合入后
  GitHub 会自动把 base 改到 `main`（或手动改），届时 diff 只剩 ② 的文件；
- **`_INFLIGHT_RUN_STATUSES` 的口径**：本单取 QUEUED/PRECHECK/RUNNING（与容量
  口径一致）。若 ④ 的 claim 过滤引入别的状态集，需要把两处收敛为同一常量
  （当前显式重复，便于两边独立演进）；
- **幂等与审计**：重复调用不产生审计（无状态变化）。若运维需要「有人尝试过重复
  退役」的痕迹，需另立审计动作（属告警/可观测面，非本单）；
- **心跳联动（⑤）**：本单已为 `retire_alerted_at` 定好清零语义与快照字段，
  ⑤ 实现时直接消费；
- **claim 侧并发用例的完整形态**：本单用「同行锁竞争」确定性证明锁序；若要端到端
  跑 claim API × retire 并发，需构造设备/容量前置，属 ④ 联调范围（issue 已注明
  「与 ④ 联调锁序」）。
