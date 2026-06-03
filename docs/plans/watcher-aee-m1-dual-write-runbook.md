# M1 AEE 双写对账 Runbook

> 目标:验证 reconciler(新)与 patrol `scan_aee`(旧)双写期,reconciler 的 AEE 捕获
> **不漏报(< 5%)、不重复**(对应方案 §4.4 / §11.3 C / T1-4)。验证通过方可进 M2(关 patrol)。
> 配套工具:`backend/scripts/aee_dual_write_recon.py`(只读 DB 对账)。

---

## 0. 环境现状(2026-05-30 核查)

| 项 | 现状 | 影响 |
|----|------|------|
| 后端 | ✅ 在跑(:8000) | — |
| 真机 | ✅ host 7(17×Infinix_X6851)/ host 3(TECNO_KO5)在线心跳 | 可做真机验证 |
| reconciler | ❌ `STP_WATCHER_AEE_RECONCILE_ENABLED` 未配=false | **必须先开启**(Step 1) |
| AEE Plan | ❌ DB 无含 `scan_aee` step 的 Plan(模板仅在 `pipeline_templates/`) | **必须先建 Plan**(Step 2) |
| PlanRun | ❌ `plan_run` 表为空 | 链路从未启动 |
| AEE signal | ❌ `job_log_signal` AEE/VENDOR_AEE = 0 | 无历史可对账 |

**结论:需按 Step 1→5 从零启动整条链路。物理操作(改 Agent env/重启、触发 crash)在 host 7 执行。**

---

## 1. 开启 reconciler(Agent 主机 host 7)

编辑 host 7 上 `/opt/stability-test-agent/.env`,追加/确认:

```bash
# —— reconciler 双写最小集 ——
STP_WATCHER_AEE_RECONCILE_ENABLED=true      # 必须:默认 false
STP_WATCHER_AEE_RECONCILE_HOSTS=7           # 推荐:灰度只放行 host 7,限制影响面
STP_WATCHER_NFS_BASE_DIR=/mnt/storage/test-platform/sonic_tinno   # 必须:reconciler/puller 的 NFS 落点(按实际)
# STP_AEE_NFS_ROOT=...                       # 若已配则优先于上一行;二选一即可
STP_WATCHER_PLAN_DEFAULT=true               # 已默认 true:Plan Job 自动启 watcher

# —— 验证期可调小节奏,加速看到 emit(稳态再恢复 180/60)——
STP_WATCHER_AEE_RECONCILE_INTERVAL_SECONDS=60
STP_WATCHER_AEE_RECONCILE_BURST_INTERVAL_SECONDS=30
STP_WATCHER_AEE_RECONCILE_BURST_ROUNDS=5
```

生效(任选其一):
- `sudo systemctl restart stability-test-agent`
- 或前端「主机管理」→ host 7 →「热更新」按钮

核对启动日志(关键):
```bash
grep -E 'aee_reconciler_env|watcher_subsystem_disabled' /opt/stability-test-agent/logs/agent*.log | tail
# 期望看到:aee_reconciler_env enabled=true interval_seconds=60 ... hosts=7
```

> gate(全部满足才启动 reconciler):`RECONCILE_ENABLED=true` 且 host 命中 `_HOSTS` 白名单
> 且 Watcher 成功 start(capability ∈ {inotifyd_realtime, polling})且 `WatcherHandle.impl` 可用。
> `unavailable`/`skipped` 不启动(§11.2 偏差 1)。

---

## 2. 准备双写 Plan

DB 当前无 AEE Plan,需基于模板 `backend/schemas/pipeline_templates/monkey_aee_patrol.json` 创建一个 Plan
(前端 Plan 编辑页或 `POST /api/v1/plans`)。**双写关键约束**:

- patrol 阶段必须含 **`scan_aee` v1.0.0**(legacy 旧侧)+ `monkey_check`;`scan_aee` params 保持
  `export_bugreport=true`(与模板一致)。
- init 含 `monkey_launch` v5.0.0(真跑 monkey,长稳压测更易触发 AEE crash)。
- **先灰度 1 台**:首次只把该 Plan 调度到 host 7 的 1 台真机(如 `121512542H304510`),
  跑通再扩。

---

## 3. 触发 PlanRun + 产生 crash

1. 触发该 Plan 的 PlanRun(前端「执行」或 `POST /api/v1/plans/{id}/run`),记下 `plan_run_id`。
2. Job 进 RUNNING 后,确认 reconciler 已为该 Job 启动:
   ```bash
   grep "aee_reconciler_active" /opt/stability-test-agent/logs/agent*.log | tail
   ```
3. 等待/促使真机产生 AEE crash:
   - monkey 长跑自然触发(被动);或
   - 已知可复现的 crash 场景手动触发(主动);crash 会在设备 `/data/aee_exp/db_history` 新增行。
4. reconciler 每轮 `cat db_history` 比对 sha256,新行 → pull + emit;**有新行进 burst**(本配置 30s×5 轮)。
   对账日志:
   ```bash
   grep "aee_reconciler_round" /opt/stability-test-agent/logs/agent*.log | tail
   # serial=.. new=N ticks_total=.. new_entries_total=.. signals_emitted=.. signals_dropped=..
   ```

---

## 4. 三路对账

**A. signal 侧(后端 Windows,本仓库)**
```bash
python backend/scripts/aee_dual_write_recon.py --list                 # 找 plan_run_id
python backend/scripts/aee_dual_write_recon.py --plan-run <id>        # 对账报告
```
输出:by pull_source / by category / 每 serial 的 reconciler 去重 nfs_dir 数,并打印 NFS 侧 find 命令。

**B. API 侧(可选,需登录)**
```
GET /api/v1/plan-runs/<id>/aee-reconciliation   # reconciler_emitted / by_serial / missing_in_signal
GET /api/v1/plan-runs/<id>/watcher-summary      # aee_breakdown(crash/vendor_crash/anr)+ pull_sources facet
```

**C. NFS 物理侧(Agent host 7)** —— 跑 A 打印的命令,逐 serial 数实际 crash 目录:
```bash
find "$STP_AEE_NFS_ROOT"/*/<serial>/aee_exp "$STP_AEE_NFS_ROOT"/*/<serial>/vendor_aee_exp \
  -name '*.dbg' 2>/dev/null | sed 's#/[^/]*$##' | sort -u | wc -l
```

---

## 5. 判定标准(进 M2 的门槛)

- **漏报率** = (NFS_crash_dirs − reconciler_nfs_dirs) / NFS_crash_dirs **< 5%**(连续 1 个完整 PlanRun 周期)。
- **无重复**:同一 `nfs_path` 在 `job_log_signal` 不出现多条(DB `(job_id, seq_no)` 幂等键应已拦截)。
- **facet 正确**:watcher-summary `pull_sources` 含 `reconciler`;`aee_breakdown.crash_count` ≈ NFS 实际 crash 数。
- **bugreport**:NFS `correlated_bugreports/` 下有对应 zip(reconciler 经 `process_device_logs` 已导,§11.2 偏差 2 订正)。

---

## 6. 风险与回滚

| 风险 | 说明 | 缓解/回滚 |
|------|------|-----------|
| 改 env 影响面 | `_RECONCILE_HOSTS=7` 已把影响限定在 host 7 | 其他 host 不受影响 |
| Agent 重启 | 会中断 host 7 上在跑的 Job(当前 plan_run=0,安全) | 选无 Job 时段重启 |
| reconciler adb 占用 | 后台周期 `cat db_history`+`adb pull`,与 monkey 并发 | 用 interruptible adb,JobSession stop 时 join;先灰度 1 台观察 |
| 误报/重复 | 双写期 reconciler 与 patrol 共用 `scan_aee:{serial}:{aee_type}` 去重键,理论不重复 | 对账发现重复即排查 emit 幂等 |
| **回滚** | 退回 patrol-only | `STP_WATCHER_AEE_RECONCILE_ENABLED=0`(或清空 `_RECONCILE_HOSTS`)→ 重启 Agent;历史 signal 不动 |

> 不涉及:删除/迁移数据、改生产表结构。全程只新增 `job_log_signal` 行 + NFS 落盘。

---

*创建:2026-05-30 — 配套 `backend/scripts/aee_dual_write_recon.py` 与方案 §11.3 C 项*
