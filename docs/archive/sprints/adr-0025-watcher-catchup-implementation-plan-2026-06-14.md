# ADR-0025 Sprint 1 实现计划：Watcher 无人值守续航（加固 RESUME 重挂路径）

> 日期：2026-06-14
> 关联：[ADR-0025](./adr/ADR-0025-phase4-architecture-alignment.md) D5、ADR-0018（Watcher 主线）、ADR-0019（Device Lease + Recovery Sync）
> 预计：3-5 天
> 前置结论：见本文 §1（推翻了 ADR-0025 初版「独立 catchup_on_startup 重挂」设计）

---

## 1. 背景与设计结论

### 1.1 目标

让真实稳定性专项在数小时到数天的无人值守长跑中，**Agent 重启后崩溃检测（Watcher）能可靠恢复**——这是 `docs/project-vision.md` 原则 1「专项执行闭环可稳定无人值守运行」的承重项，也是平台「放着跑、崩溃替你盯住」核心承诺的兑现前提。

### 1.2 关键勘察结论：watcher 已随 RESUME 自动重挂，无需独立重挂

逐行核实恢复链路（2026-06-14）后，**否定 ADR-0025 初版设想的 `manager.catchup_on_startup(active_jobs)` 独立重挂**。实际链路：

```
Agent 重启
 → reconcile_on_startup()         清理上次残留 watcher_state→stopped   (manager.py:439-485)
 → run_recovery_sync_if_needed()  上报本地 active_jobs                 (main.py:754；agent/job_session 中的 run_recovery_sync_if_needed)
 → 后端 recovery_sync 判定         RESUME + job_payload                (agent_api.py:1668-1678 UNKNOWN 复活 / 1709-1723 正常)
 → execute_recovery_actions_impl  resume_job(payload)                  (main.py:243-275)
 → executor.submit(run_task_wrapper, payload)                          (main.py:738-749，与正常 claim 同一入口 main.py:827)
 → job_runner.run_task → JobSession.__enter__()                        (job_runner.py:181-197)
 → manager.start() → 重新挂载 DeviceLogWatcher                          ← 崩溃检测已恢复
```

独立重挂是**冗余且有害**的：

- **违反 watcher 不变量**：watcher 必须绑定 JobSession，`stop(drain=True)` 须在释放设备锁前由 JobSession 调用（`manager.py:11-12`）。独立重挂会造出无 pipeline 执行、无锁释放协调的**孤儿 watcher**。
- **会打断恢复**：`manager.start` 有按 serial 的 `already_running` 守卫（`manager.py:213-220`）。独立 catchup 先挂一个，随后 RESUME 驱动的 `JobSession.__enter__ → manager.start` 同 serial → 抛 `WatcherStartError`，**反而中断恢复**。

**因此本 Sprint 改为：验证并加固既有 RESUME 路径**，把当前「隐式可用但无保证」的续航固化为「有测试守护、无僵尸洞、可观测」的能力。

### 1.3 非目标（明确排除）

- **不**新建 `manager.catchup_on_startup` 重挂逻辑（理由见 §1.2）。
- **不**解决「RESUME 从 `run_task_wrapper` 顶部重跑导致 patrol 中途 Job 重做 init」——这是 Job 恢复语义问题（ADR-0019/0022 范畴），比 watcher 续航更大，单独立项。
- **不**触碰水平扩展 / LogArchiver / 运维收口（ADR-0025 D1/D4/D6）。

---

## 2. 工作项

### T1 — enable.py 默认值统一（1 行，消除歧义）

| 项 | 内容 |
|----|------|
| 文件 | `backend/agent/watcher/enable.py:11` |
| 现状 | `global_on = os.getenv("STP_WATCHER_ENABLED", "false").lower() == "true"` |
| 改为 | 默认值 `"false"` → `"true"`，与 `main.py:69` 一致 |
| 影响 | 当前 `watcher_subsystem_enabled() = global_on OR plan_default`，`plan_default` 默认 true，故默认已启用——本改动消除「main.py 默认 true、enable.py 默认 false」的表述歧义，并修正「显式 `STP_WATCHER_PLAN_DEFAULT=false` 时仍应遵从全局默认开」的语义 |
| 风险 | 无功能破坏；新增一条单测断言默认开 |

### T2 — 堵 RESUME 无 job_payload 僵尸洞（后端为主，Agent 防御）

**问题**：`agent_api.py:1709-1723` 正常 RESUME 分支：

```python
job_payload = None
if job is not None:
    job_payload = await _build_recovery_job_payload(db, job, ...)
job_actions.append(_RecoveryAction(... action="RESUME", job_payload=job_payload, ...))
```

当 `job` 行缺失（被删等）时 `job_payload=None`，Agent 侧 `execute_recovery_actions_impl` 的守卫 `if resume_job is not None and isinstance(a.get("job_payload"), dict)`（`main.py:256`）不触发 → **job 经 `register_active_job`（main.py:248）登记为 active，但 pipeline 永不恢复、watcher 永不重挂 → 僵尸 active_job + 续航失效**。

| 步 | 文件 | 改动 |
|----|------|------|
| T2.1 | `backend/api/routes/agent_api.py:1709-1723` | `job is None` 时不发 RESUME，改发 `ABORT_LOCAL`（`reason="resume_job_row_missing"`）并释放 lease，与同段 terminal/mismatch 分支一致 |
| T2.2 | `backend/agent/main.py:243-256` | 防御：RESUME 动作 `job_payload` 非 dict 时打 `WARN recovery_resume_missing_payload job=%d`，并**不**调 `register_active_job`（避免僵尸），让后端下一轮 reconcile 收敛 |
| 测试 | `test_recovery_executor.py` | 新增「RESUME 无 payload → 不登记 active + 告警」用例；后端 `test_recovery_sync_*` 新增「job 行缺失 → ABORT_LOCAL」用例 |

### T3 — catchup 可观测（区分 resume 重挂 vs 全新 claim）

让运维能在日志中看到「续航确实发生」，便于排障与灰度验证。

| 步 | 文件 | 改动 |
|----|------|------|
| T3.1 | `backend/agent/main.py:256-265` | resume 提交时给 payload 打标：`resumed_payload["recovery_resumed"] = True` |
| T3.2 | `backend/agent/job_runner.py:181-197` | 透传该标记到 JobSession（或在 watcher 启动成功后，依据标记打结构化日志 `watcher_catchup_reattach job_id=%d serial=%s watcher_id=%s`） |
| 说明 | Agent 无独立 `/metrics` 暴露面，本项落在结构化日志即可；如需中心指标，可经 `JobSessionSummary.to_complete_payload` 既有通道带出一个 `recovery_resumed` 布尔，由后端桥接（可选，非本 Sprint 必须） |

### T4 — 端到端续航测试（核心交付）

把 §1.2 链路固化为防回归保证。复用既有测试基建（manager 支持 `prober_factory` / `watcher_factory` 注入，`manager.py:153-156`；`_reset_for_tests` 模拟重启，`manager.py:110-119`）。

| 步 | 文件 | 用例 |
|----|------|------|
| T4.1 | `backend/agent/tests/test_recovery_executor.py` | RESUME + job_payload → `resume_job` 被调用且 payload 透传 fencing_token / serial / `recovery_resumed`（扩展既有用例） |
| T4.2 | `backend/agent/tests/test_job_session_e2e.py` | 「重启续航」E2E：`_reset_for_tests()` 模拟重启 → `reconcile_on_startup()` 把残留 state 标 stopped → 用 stub watcher_factory 走 RESUME→`run_task`→`JobSession.__enter__`→`manager.start` → 断言同 serial watcher 重新 active、`watcher_state` 重建、无 `already_running` 冲突 |
| T4.3 | `backend/agent/tests/test_job_session_e2e.py` | 重挂后信号续流：重启后 emit 的 log_signal 经 `(job_id, seq_no)` 幂等被后端接受，且与重启前不重号（验证 `emitter` 构造时 `next_log_signal_seq_no()` 恢复 MAX+1，`emitter.py:67-68`） |
| T4.4 | `backend/agent/tests/test_manager.py` | 回归：`reconcile_on_startup` 仅清理残留、不重挂；确认未引入正向挂载副作用 |

### T5 — 回归

| 项 | 命令 / 范围 |
|----|------------|
| Agent 全量 | `python -m pytest backend/agent/tests/` 全过（重点 recovery / job_session / manager / patrol_recovery） |
| 后端 recovery | `python -m pytest backend/tests/ -k recovery` 全过 |
| 确认无孤儿 | 检视 T4.2 日志：重启后同 serial 仅一个 active watcher，无 `already_running` WARN |

---

## 3. 执行顺序与依赖

```
T1（enable.py，独立）
T2（后端 RESUME 降级 → Agent 防御）  ← 后端先改，Agent 防御跟上
T3（打标 → 日志）                    ← 依赖 T2 的 main.py 改动区域，合并改
T4（测试）                           ← 依赖 T1/T2/T3 落地
T5（回归）                           ← 最后
```

建议提交粒度：T1 单独一提交；T2+T3 一提交（同在 main.py / agent_api.py 恢复区域）；T4+T5 一提交。

---

## 4. 验证标准（对应 ADR-0025 §验证 Sprint 1）

1. **续航链路有测试守护**：T4.2/T4.3 通过——重启 → RESUME → watcher 重挂 → 信号续流且不重号。
2. **僵尸洞封堵**：T2 用例通过——RESUME 无 payload 不再产生僵尸 active_job。
3. **默认值一致**：T1 单测通过——`enable.py` 与 `main.py` 默认行为一致。
4. **可观测**：日志可见 `watcher_catchup_reattach`，区分 resume 重挂与全新 claim。
5. **无回归**：T5 Agent + 后端 recovery 全量绿，无 `already_running` 冲突。

---

## 5. 风险与回退

| 风险 | 缓解 |
|------|------|
| T2 后端把本应 RESUME 的 job 误判为 ABORT_LOCAL | 仅在 `job is None`（行确实缺失）时触发，与现有 terminal/mismatch 分支同级；加用例覆盖 job 存在时仍 RESUME |
| T1 默认开导致未预期主机启用 watcher | 当前默认已实际启用（`plan_default` true）；如需关闭仍可显式 `STP_WATCHER_ENABLED=false STP_WATCHER_PLAN_DEFAULT=false`；本改动不改变既有部署默认行为 |
| 测试依赖真实 adb/设备 | 全部用 `prober_factory` / `watcher_factory` stub，无需真机；与 `test_job_session_e2e.py` 既有风格一致 |
| 整管线重跑（init 重做）在真实 patrol 中放大 | 本 Sprint 不解决，已在 ADR-0025 D5 标注为单独立项；测试中用最小 pipeline 规避 |

---

## 6. 关键代码索引

| 位置 | 作用 |
|------|------|
| `backend/agent/watcher/enable.py:9-13` | `watcher_subsystem_enabled()`（T1） |
| `backend/agent/main.py:226-310` | `execute_recovery_actions_impl`（T2/T3） |
| `backend/agent/main.py:738-751` | `_resume_recovered_job_impl` → `run_task_wrapper`（恢复入口） |
| `backend/agent/job_runner.py:181-219` | `run_task` 中的 JobSession 启动块 |
| `backend/agent/job_session.py:125-161` | `JobSession.__enter__` → `manager.start`（watcher 重挂点） |
| `backend/agent/watcher/manager.py:177-365` | `start`（含 `already_running` 守卫） |
| `backend/agent/watcher/manager.py:439-485` | `reconcile_on_startup`（仅清理，不重挂） |
| `backend/api/routes/agent_api.py:1545-1758` | `recovery_sync` 端点（T2.1） |
| `backend/api/routes/agent_api.py:142` | `_build_recovery_job_payload` |
| `backend/models/job.py:158` + `agent_api.py:1293` | `(job_id, seq_no)` 幂等（续航前提，已落地） |
