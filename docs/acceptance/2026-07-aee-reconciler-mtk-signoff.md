# #72 实机验收 runbook：AEE Reconciler MTK 端到端

- **跟踪**：[GitHub #72](https://github.com/DUElost/stability-test-platform/issues/72)
- **关联 ADR**：[ADR-0025](../adr/ADR-0025-phase4-architecture-alignment.md)（方案 C 存储）；平台门禁见 `backend/agent/aee/CLAUDE.md` §平台门禁（#73）
- **触发工具**：`backend/agent/scripts/aee_signal_trigger/v1.0.0/aee_signal_trigger.py`
- **文档状态**：模板（待实跑填入实测值并签字）

> **作用范围**：在一台 MTK 真机上跑完一个完整 PlanRun，证明 AEE Reconciler
> 主路径把设备崩溃采集到 `job_log_signal` 表并在前端 `AnomalyDashboard` /
> `WatcherSummaryCard` 显示。展锐/高通平台不在本 issue 范围（无 `/data/aee_exp`）。

---

## 1. 前置

### 1.1 机型

任一台 MTK 机型（`backend/agent/aee/CLAUDE.md` §平台门禁记录的生产实测机型）：

| 机型 | 平台 | RoM |
|------|------|-----|
| DAM-M500 / ELA-LX2 / ELA-LX3 / MLD-LX3 | `mt6768` | MTK ✅ |
| Z2581 / Z2582 | `ums9230` | UNISOC ❌（不在范围） |

从 `/home/debian13/hosts.ini` 的 `[android]` 段选一台 MTK host；`ssh android@<ip>` 免密已通。

### 1.2 Agent 环境确认（在目标 host 上）

```bash
# 平台应为 MTK（命中 STP_WATCHER_AEE_RECONCILE_PLATFORMS，默认 MTK）
adb -s <serial> shell getprop ro.soc.manufacturer   # 期望 MTK 相关
adb -s <serial> shell getprop ro.board.platform     # 期望 mt6768 前缀

# 存储根 + 监测目录
ssh android@<ip> 'echo $STP_AEE_LOCAL_ROOT'          # 期望 /home/android/aee-local（2026-07-25 修复后）
ssh android@<ip> 'echo $STP_AEE_NFS_ROOT'
adb -s <serial> shell ls /data/aee_exp/              # 期望含 db_history（或可被 AEE 创建）

# Watcher 门禁 + Reconciler 开关
ssh android@<ip> 'echo STP_WATCHER_ENABLED=$STP_WATCHER_ENABLED'        # 默认 true
ssh android@<ip> 'echo STP_WATCHER_AEE_RECONCILE_ENABLED=$STP_WATCHER_AEE_RECONCILE_ENABLED'  # 默认 true
```

### 1.3 关键前置：Agent 日志级别 ≥ DEBUG

**#72 issue 要求附 `aee_reconciler_emit` DEBUG 级行**，而 `backend/agent/main.py:76`
按 `LOG_LEVEL` env 设日志级别，**默认 INFO**——DEBUG 级的
`aee_reconciler_emit`（`backend/agent/aee/reconciler.py:777`）在默认级别下**不进 agent.log**。

故实跑前必须让目标 host Agent 以 DEBUG 级运行：

```bash
ssh android@<ip> 'grep ^LOG_LEVEL ~/.env 2>/dev/null || echo "未设（=INFO 默认）"'
# 临时改 DEBUG（不需改 .env，重启 Agent 时 export 即可；或 reload_config 不刷此项，须重启）
```

> 若生产策略不便全程 DEBUG，可只对本次验收的 Job 期临时 `LOG_LEVEL=DEBUG` 重启
> 该单机 Agent（与多实例、其他 host 无关）。

### 1.4 触发工具就位

`aee_signal_trigger/v1.0.0` 已随 Agent rsync 部署。首次需在控制面注册脚本版本
（ADR-0020：扫描只在磁盘发现文件，`default_params` 由 `POST /scripts/{name}/versions` 录入）：

```bash
# 控制面凭据边界见 docs/operations/production-diagnostics.md
curl -H "Authorization: Bearer <token>" \
  -F 'name=aee_signal_trigger' -F 'version=v1.0.0' \
  -F 'default_params={"package_name":"com.android.settings","poll_timeout_seconds":30,"poll_interval_seconds":1.0,"signal":11}' \
  -F 'param_schema={...}' \
  http://127.0.0.1:8000/api/v1/scripts/aee_signal_trigger/versions
```

> **触发方式说明**：#72 issue 原文 step 2 给的 `adb shell echo test > /data/aee_exp/db_history`
> 是简化示例——单纯追加行而**不落 db 目录**会让 Reconciler `adb pull` 失败、
> `_verify_pulled_aee_log_strict` 拒绝。本 runbook 用 `aee_signal_trigger` 脚本
> 对真实 App 发 SIGSEGV（`kill -11`），由 AEE 机制自己生成 `db.<id>/`（含 `.dbg` /
> `ZZ_INTERNAL` 真品）并追加 `db_history` 行——更贴合生产行为，且 S/A/B 评级
> 能在控制面正确聚合。手动 `echo` 仅作为 Reconciler hash 比对的最小烟测。

---

## 2. 验证步骤（对应 #72 issue 目标 1–6）

每步执行后填入实测值。PlanRun 用 1 台 MTK 设备 + 任一含 `aee_signal_trigger` 的
Plan 即可（最小：一个 PlanRun/单 PlanStep `script:aee_signal_trigger`；或挂在
现有稳定 PlanRun 的 teardown 后单独触发）。

| # | issue 目标 | 步骤 | 期望 | 实测 | 锚点 |
|---|-----------|------|------|------|------|
| S1 | `aee_reconciler_active` 日志出现 | Job 启动后在目标 host `agent.log` 检索 | `grep aee_reconciler_active agent.log` 命中 1 行（INFO） | ☐ | `backend/agent/job_session.py:358` |
| S2 | 设备产生 AEE 事件 | 在 PlanRun 中执行 `aee_signal_trigger`（参数见 §1.4） | stdout `success=true`，`raw_event_type`/`db_path`/`killed_pid` 非空 | ☐ | `aee_signal_trigger.py` |
| S3 | `job_log_signal` 出现对应行 | 控制面 SQL（`venv/bin/python` + `psycopg`） | 见 §4 schema 报告块，`extra->>'event_subtype'` 非空 | ☐ | `backend/api/routes/agent_api.py:2032` ingest / `agent_api.py:2116` 累加 |
| S4 | `JobInstance.log_signal_count` 自增 | `SELECT log_signal_count FROM job_instance WHERE id=<j>` | 触发后 > 触发前 | ☐ | `agent_api.py:2116` |
| S5 | `AnomalyDashboard` 双饼图 + 包名榜显示 | 前端 PlanRun 详情页目视 | 双饼图（按 category / subtype）非空 + 包名榜含被 kill 的包 | ☐ | `frontend/src/.../AnomalyDashboard.tsx` |
| S6 | `WatcherSummaryCard` S/A/B 评级正确 | 同页目视进度条 | 至少 1 次 S 或 A 级（见 §3 验收标准 R4） | ☐ | `backend/services/report_service.py:65` `_RISK_RATING_RULES` |

> **S2 替代**：若不便跑触发脚本，可改用长稳自然崩溃——同一台 MTK 设备跑 monkey
> 数小时待其自然 ANR/JE/NE，再用本 runbook 的 S3/S4/S5/S6 确认。任一方式满足即可。

---

## 3. 验收标准（对应 #72 issue 验收清单 4 项）

| ID | 标准 | 通过判据 | 实测 |
|----|------|----------|------|
| R1 | schema 校验报告 | §4 块填齐：≥5 行 `job_log_signal` + 截图 AnomalyDashboard / WatcherSummaryCard | ☐ |
| R2 | agent.log DEBUG 行 | grep `aee_reconciler_emit` 命中，含 `pkg=` / `subtype=`（需 LOG_LEVEL=DEBUG，见 §1.3） | ☐ |
| R3 | Reconciler 与 patrol 不抢 adb | 同 Job 期 `agent.log` 无 `adb: device offline` / pull 超时交叠；reconciler 60s 周期与 patrol `adb shell` 在时间线上不并发冲突 | ☐ |
| R4 | 风险评级触发 ≥1 次 S 或 A | S2 选 SWT/HWT/HANG/KE/HW Reboot/Fatal NE/Fatal JE（任 1 次 = S），或 ANR≥10 / JE≥3 / NE≥2 / Java≥3（A）。默认 SIGSEGV 多发即 Native NE，2 次→A | ☐ |

> **R3 的观察方法**：`grep -E "aee_reconciler_|patrol|adb shell" agent.log | head -50`
> 看时间戳——reconciler `_db_history_changed` 与 patrol step_trace 的 adb 命令不应
> 在同一秒并发；`reconciler.py:555` 的 hash 比对走 `cat db_history`（短命令），冲突
> 概率低但需目视确认无 `device offline` / `AdbCommandRejectedException` 交叠。

---

## 4. 实测填空

### 4.1 环境快照

| 项 | 值 |
|----|-----|
| 验收 host IP | ☐ |
| Agent 机型 serial | ☐ |
| `ro.soc.manufacturer` | ☐ |
| `ro.board.platform` | ☐ |
| Agent `LOG_LEVEL` | ☐ （须 DEBUG） |
| PlanRun # | ☐ |
| 触发方式 | ☐ SIGSEGV 脚本 / 长稳自然崩溃 |
| 验收日期 | ☐ |

### 4.2 schema 校验报告（R1）

```sql
-- 用 venv/bin/python + psycopg（见 docs/operations/production-diagnostics.md）
SELECT id, job_id, category, source,
       extra->>'event_subtype'  AS event_subtype,
       extra->>'package_name'   AS package_name,
       extra->>'aee_ts'         AS aee_ts,
       extra->>'pull_source'    AS pull_source,
       detected_at
FROM job_log_signal
WHERE plan_run_id = <N>
ORDER BY id
LIMIT 5;
```

粘贴结果：

```
☐
```

### 4.3 agent.log DEBUG 行块（R2）

```
$ grep "aee_reconciler_emit" agent.log | head -5
☐  期望形如：
   aee_reconciler_emit serial=<serial> job=<j> cat=AEE pkg=<pkg> subtype=<SWT|NE|JE|...>
```

### 4.4 Reconciler/patrol adb 时间线（R3）

```
$ grep -E "aee_reconciler_(active|emit)|patrol" agent.log | head -30
☐
```

### 4.5 S/A 触发记录（R4）

| 触发轮次 | raw_event_type | event_subtype | 评级 | 备注 |
|----------|----------------|--------------|------|------|
| ☐ | ☐ | ☐ | ☐ | ☐ |

### 4.6 前端截图

- AnomalyDashboard 双饼图 + 包名榜：☐ 附图
- WatcherSummaryCard 异常率进度条：☐ 附图

---

## 5. 签字

| 项 | 值 |
|----|-----|
| 验证总结 | ☑ 全部 PASS  ☐ 有 FAIL 项 |
| 操作人 | ☐ |
| 签字日期 | ☐ |
| 备注 | 关 [GitHub #72](https://github.com/DUElost/stability-test-platform/issues/72)；ADR-0026 修订记录追加「单实例代码层承载量已证明」行依赖本签字 |

---

## 6. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-28 | 初版：诱发式 SIGSEGV 触发脚本 + 实机端到端验收模板（含 LOG_LEVEL=DEBUG 前置）|

