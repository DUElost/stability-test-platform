# ADR-0028 阶段 3 验收记录（MTK DeviceLogEvent + EventUploader）

- **跟踪**：[GitHub #219](https://github.com/DUElost/stability-test-platform/issues/219)
- **ADR**：[ADR-0028](../adr/ADR-0028-device-log-event-and-continuous-upload.md)
- **规格**：[2026-device-log-event-implementation-spec.md](../design/2026-device-log-event-implementation-spec.md)
- **文档状态**：已签字（生产交付；跳过原定 1–2 天观察期）
- **签字日**：2026-08-12

> **范围**：MTK 过滤模型（方案 A，`STP_EVENT_UPLOADER_CONTINUOUS=0`）：`LOCAL → UPLOAD_PENDING → REMOTE → ARCHIVED/PRUNED`。  
> UNISOC/QCOM Collector 仅留入口、不扫描（阶段 4 / #220 / #73）。  
> 旧 PlanRun 触发上送双轨已删除（#213 Track A CLOSED；Track B 验收见 §4 已知缺口与 #309）。

---

## 1. 交付清单

| 项 | 结果 |
|----|------|
| 表 / migration | `device_log_event`；生产 alembic head `q4r5s6t7u8v9`（含 `signal_seq_no`，#214 / PR #221） |
| 控制面 flag | `.env.backend`：`STP_EVENT_UPLOADER_ENABLED=1`、`STP_DEVICE_LOG_EVENT_ENABLED=1` |
| Agent flag 同步 | #218 / PR #224：两 key 进 `_FLEET_ENV_KEYS`；hot-update 先合并 `.env` 再 restart |
| 灰度 → 全舰队 | 阶段 3 灰度：16 台 MTK host + pilot `192-0-2-87`；#218 后 20 台均双开（含原无 flag 的 `8.116` / `9.126` / `9.127`） |
| Agent revision | `be4f31a`（#218）含 #215 `resolve_device_log_event_type` |
| 观察脚本 | `tools/dev/monitor-device-log-events.py`（只读） |

---

## 2. 生产 E2E 证据

### 2.1 阶段 3 灰度上送（历史回放 / HDD AEE）

| PlanRun | 结果（摘要） |
|---------|--------------|
| #180 / #183 / #184 / #185 / #194 / #195 | 共 **28** 条 DLE 至 `ARCHIVED`；当时 `event_type=UNKNOWN`（#215 前）、`device_log_event_id` 未关联（#214 前） |

Hosts 有 DLE 落库：`8.103` / `8.143` / `8.192` / `8.195` / `9.124` / `9.6`（及灰度波次中的其它 MTK host）。

### 2.2 P1 闭环真机（#216，同趟验 #214 / #215）

| 项 | 值 |
|----|-----|
| Host / device | `192-0-2-143` / `0000NX2622000488`（ELA-LX2 `mt6768`） |
| Plan / PlanRun / Job | Plan `dle-e2e-216-aee-trigger` (#7) / **#199 SUCCESS** / **#2729 COMPLETED** |
| Trigger | `aee_signal_trigger` → `success=true`，`db_path=/data/aee_exp/db.02.NE` |
| DLE | **6** 条 `ARCHIVED`；`event_type` = ANR×3 / JE×1 / NE×2 |
| Signal | **6/6** `job_log_signal.device_log_event_id` 非空；DLE `signal_seq_no` 1–6 |
| NFS | `/mnt/stp-aee/devices/199/` 有对应目录；无 `event_uploader_failed` |

说明：Reconciler 在岗时 inotifyd 不单独写 DLE（有意去重）；本趟 DLE 来自 Reconciler。  
历史 28 条 ARCHIVED **不回填**类型 / 关联。

**#213 Track A（2026-08-12）**：`upload_task` / `upload_events` / `upload_event_dirs` 已删除；`scan_task` 只 enqueue `merge_task`；事件上送仅 EventUploader + DLE。

> **同日 方案 A 修订（bce5177）已恢复 `upload_task`**：`scan_task` → `upload_task`（按 scan xls 标记 `UPLOAD_PENDING`）→ `merge_task`；EventUploader 保留 copytree 作为 Agent 侧唯一执行者（`STP_EVENT_UPLOADER_CONTINUOUS=0` 默认过滤模型）。部分 host 覆盖仍可交付：记 `saq_scan_partial_artifacts` WARNING + `PlanRun.run_context.archive`，继续 enqueue upload/merge（成功 host 的报表保留）。上一行 Track A 的「已删除 / 只 enqueue merge」是当日早晨的中间态，勿当现状。
- **#199**：`devices/199/` 于 **11:22–11:26**（Job 中 EventUploader），早于 **11:33** scan/merge；`jira/199/` extract 完整。  
- **#200**（2026-08-12 复验，`note=213-cutover-reconfirm`）：**SUCCESS**；DLE **7** 条 typed（ANR×3/JE×1/NE×3）+ signal 7/7 → `REMOTE` 后 `ARCHIVED`；`devices/200/` + `jira/200/` 齐全。  
功能上新链路已可替代旧 PlanRun 上送；#213 Track A 收口见 #311（已完成：生产代码无
`upload_events` / `upload_event_dirs`，相关文档加历史 banner 或改现状；弃用 env 别名
`STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR` 保留至 fleet 迁移完成，#172）。

### 2.3 签字时库存（2026-08-12）

```text
alembic q4r5s6t7u8v9
device_log_event: 34 ARCHIVED（28 灰度 + 6 × #199）
event_type typed (≠ UNKNOWN): 6（均为 #199）
with signal_seq_no / linked signals: 6
```

复检：

```bash
./venv/bin/python tools/dev/monitor-device-log-events.py
./venv/bin/python tools/dev/monitor-device-log-events.py --plan-run-id 199
```

---

## 3. 已关闭相关 issue

| Issue | 结论 |
|-------|------|
| #214 | signal 关联：#221 + Agent 热更新后 #199 验证 6/6 |
| #215 | `event_type`：#223 + #199 为 JE/ANR/NE |
| #216 | MTK 真机 E2E：#199 |
| #218 | fleet flag 同步：#224；`8.116`/`9.126`/`9.127` 热更新验收 |

---

## 4. 已知缺口（不假装已验）

| Issue | 状态 |
|-------|------|
| #213 | Track A 完成（#228）；Track B：extract 仅 DLE + unassigned 事后关联（进行中） |
| #217 | `STP_EVENT_UPLOADER_PRUNE_LOCAL` / HddSpill — 见 [`../operations/adr-0028-prune-local-and-spill-gray.md`](../operations/adr-0028-prune-local-and-spill-gray.md)；**勿** fleet 开 prune |

| #220 / #73 | 阶段 4：UNISOC/QCOM 仅入口；非 MTK 跳过扫描 |
| inotifyd 独占写 DLE | Reconciler 在岗时 inotifyd 路径被抑制；未单独做「关 Reconciler 只走 inotifyd」E2E |

---

## 5. 运维备忘

- 新 host / 重装：控制面 `.env.backend` 双 flag 非空 → hot-update 即可，无需逐台 SSH。  
- 勿在 hot-update 未返回前抢 `reload_config`（#218 SOP）。  
- `STP_EVENT_UPLOADER_PRUNE_LOCAL` **不**进 fleet（#217）。
