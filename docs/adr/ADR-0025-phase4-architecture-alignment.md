# ADR-0025: Phase 4 架构对齐——单控制平面下 Agent 侧日志闭环与水平扩展推迟

- 状态：Accepted
- 优先级：P2
- 目标里程碑：M4 核心闭环（Sprint 1-3，共 12-19 天）；D6 运维收口为触发条件驱动，不计入 M4 排期
- 日期：2026-06-13（2026-06-14 按「首次落地」视角重排优先级）
- 决策者：平台研发组
- 标签：架构, Watcher, 无人值守闭环, 日志归档, 水平扩展, 部署策略
- 关联：ADR-0018 (Watcher), ADR-0011 (可观测)

## 背景

五维评估报告（`docs/architecture-five-dimensional-assessment-2026-06.md`）Phase 4 列出 7 项长期改动：SocketIO Redis adapter、APScheduler 外置、分布式限流、Loki 集中日志、Watcher CATCHUP、SAQ 多进程适配、Prometheus 多进程指标。评估假设"多后端实例 + 集中日志服务器"为终态架构。

经与项目实际部署需求对齐，发现原假设与项目背景存在根本偏差：

1. **部署形态**：同一局域网下一台控制平面（Windows 开发 / Linux 生产）+ 多 Agent 节点，只面向单 React Dashboard
2. **多实例需求**：平台需先在实际项目中跑顺，成熟后再考虑多控制平面或多 worker
3. **日志管理**：Loki 集中日志不契合——Agent 本地磁盘 1TB，NFS 14TB；日志在 Agent 侧存储，每日定时汇总去重后归档 NFS，需支撑 JIRA 提单；本地达阈值后溢出
4. **Watcher 定位**：应在 Agent 侧完成完整闭环（检测→拉取→分析→归档），控制平面只做定时拉取和聚合展示

### 排序依据（2026-06-14 补充）：平台尚未在实际项目中运用

本 ADR 初版（2026-06-13）以「成熟度加固」为标准，曾把运维收口（D6）提前为 Sprint 0。复盘发现一个更上位的事实：**平台尚未在任何真实项目中跑过**。在此前提下，评判「最明显增益」的标准不是成熟度，而是**能否让一个真实稳定性专项稳定地无人值守跑出价值、值得被采用**。

据此回归 `docs/project-vision.md` 的迭代落地原则：

1. **先确保「专项执行闭环」可稳定无人值守运行** ← 当前唯一应聚焦项
2. 再叠加「结果到报告/JIRA」的后处理自动化能力
3. 所有新专项复用统一编排框架

把愿景 9 步专项流程映射到现状，唯一拖累「无人值守」的承重项是 **Watcher 重启续航（D5）**：真实专项是数小时到数天的长跑，期间 Agent 必然重启（热更新本身是平台一等公民功能 + 崩溃 + 运维）。重启后崩溃检测能否恢复，是平台「无人值守抓崩溃」存在理由的承重项。

> 注：2026-06-14 逐行核实恢复链路后发现，watcher 实际**已能随 recovery_sync 的 RESUME 动作经 JobSession 自动重挂**（详见 D5），并非「完全不恢复」。但该链路无防回归测试、存在 RESUME 无 job_payload 的僵尸洞、且 enable.py 默认值与 main.py 不一致。D5 据此从「新建独立重挂」改为「验证并加固既有 RESUME 路径」。

因此重排优先级：**D5（Watcher 续航加固）为首次落地最高增益项，先做**；D4（LogArchiver）服务后处理链，列第二（对应原则 2）；D6（告警/备份运维收口）保护的是**尚不存在的生产负载，属过早投入，后移至首个真实专项稳定跑通后再启**。

## 决策

### D1: 水平扩展类改动 —— 推迟

涉及项：SocketIO Redis adapter / APScheduler 外置 / 分布式限流 / SAQ 多进程适配 / Prometheus 多进程指标

**理由**：
- 当前单控制平面单实例，无多实例需求
- 多 worker 涉及 7 处全局状态冲突（APScheduler 重复调度 / SocketIO room 分裂 / 内存限流 N 倍放大 / `_host_to_sid` 跨进程失效 / SAQ `enqueue_sync` 绑定单 loop / Reconciler Lock 不跨进程 / Prometheus 指标各进程独立），改动量 10-17 天，当前 ROI 低
- 推迟不损失功能：单实例下现行代码完全可用

**重启条件**（立项依据）：
- 设备池 >80 台，单后端 CPU/连接数近极限
- 需要零停机滚动重启
- 需要多用户同时操作（多控制平面）

### D2: Loki 集中日志 —— 不引入

**理由**：
- Agent 日志归档是 Agent 侧职责，不是控制平面侧的集中式日志问题
- 引入 Promtail + Loki + Grafana 集成增加运维负担，且与"Agent 本地存储 + NFS 归档"模式冲突
- 当前 `log_writer.py`（后端写本地文件）+ Agent 写本地文件的模式对单控制平面已足够
- 日志下载端点已通过 NFS 路径读取 Agent 落盘文件（`runs.py` / `plan_runs.py` 的 `FileResponse`）

**替代方案**：Agent 侧实现日志归档调度器（LogArchiver，见 D4）

**重启条件**：
- 跨节点集中日志检索成为刚需且 NFS 模式不满足
- 需要与 Grafana 深度集成的日志探索体验

### D3: Watcher 定位对齐 —— Agent 侧完整闭环

**当前状态**：Watcher 做检测 + HTTP 上送信号元数据到后端 DB；crash 文件拉取到 NFS 但不做汇总/归档

**目标状态**：Agent 侧完成完整生命周期

| 阶段 | 当前 | 目标 | 缺口 |
|------|------|------|------|
| 检测 | ✅ inotifyd 实时监测 | 不变 | — |
| 拉取 | ✅ adb pull → NFS | 不变 | — |
| 上送 | ✅ HTTP POST 元数据 → 后端 DB | 不变 | — |
| 分析 | ❌ | Agent 侧汇总/去重/分类 | LogArchiver |
| 归档 | ❌ | 每日汇总归档 NFS + 本地溢出 | LogArchiver |
| CATCHUP | ❌ | Agent 重启后恢复活跃 Watcher | manager.py |
| 控制面拉取 | ⚠️ 仅 Agent 主动推送 | 后端可按需拉取归档状态 | 新端点 |

**控制平面角色**：聚合展示 + 按需拉取 Agent 归档状态，不再做日志存储

### D4: 新增 Agent 日志归档调度器（LogArchiver）

职责：
- 每日定时扫描 `BASE_DIR/logs/runs/` 下已完成 Job 的日志
- 对同 Job 的重复 ANR/AEE 条目做去重（基于 sha256 + first_lines 比对）
- 去重后的日志打包归档到 NFS（`{nfs_base_dir}/archives/{date}/{job_id}/`）
- 监控本地磁盘使用量，达阈值（如 80%）后主动溢出旧 Job 日志到 NFS
- 归档后的日志可被控制平面通过 NFS 路径访问，支撑 JIRA 提单

### D5: Watcher 无人值守续航 —— 加固既有 RESUME 重挂路径（非独立重挂）——【首次落地最高增益项，优先执行】

**为什么是最明显增益**：Watcher 续航是「专项执行闭环可稳定无人值守运行」（愿景原则 1）的承重项。真实专项数小时到数天长跑，期间 Agent 必然重启（热更新一等公民 + 崩溃 + 运维），重启后若崩溃检测不恢复，该设备剩余时间静默不再抓 AEE/ANR，直接击穿平台「放着跑、崩溃替你盯住」的核心承诺。

**关键勘察修正（2026-06-14）**：初版 D5 设想「`manager.catchup_on_startup(active_jobs)` 独立重挂 DeviceLogWatcher」。逐行核实恢复链路后**否定该设计**——watcher 实际上已经能随 Job 恢复自动重挂：

```
Agent 重启
 → reconcile_on_startup()         清理上次残留 watcher_state→stopped（manager.py:439）
 → run_recovery_sync_if_needed()  上报 active_jobs（main.py:754）
 → 后端 recovery_sync 返回 RESUME + job_payload（agent_api.py:1668-1678 / 1709-1721）
 → execute_recovery_actions_impl → resume_job(payload)（main.py:267）
 → executor.submit(run_task_wrapper, payload)（main.py:739，与正常 claim 同一入口）
 → job_runner.run_task → JobSession.__enter__()（job_runner.py:188-197）
 → manager.start() → 重新挂载 DeviceLogWatcher  ← 崩溃检测已恢复
```

独立重挂是**冗余且有害**的：watcher 不变量要求「绑定 JobSession + stop(drain) 在释放设备锁前由 JobSession 调用」（manager.py:11-12），独立重挂会造出无 pipeline 执行、无锁释放协调的**孤儿 watcher**；且 `manager.start` 的 `already_running`（按 serial）守卫会让后续 RESUME 驱动的 JobSession.start 抛 `WatcherStartError`，**反而打断恢复**。

**因此 D5 改为「验证并加固既有 RESUME 路径」**，工作项：
- **enable.py 默认值统一**：`enable.py:11` `STP_WATCHER_ENABLED` 默认 `false` → `true`，与 `main.py:69` 一致（消除表述歧义；当前因 `plan_default` 默认 true 仍启用，属混乱非损坏）
- **端到端续航测试**（核心交付）：构造「活跃 patrol Job → Agent 重启 → recovery RESUME → watcher 重挂 → 信号续流」用例，把当前隐式可用的链路固化为防回归保证
- **堵 RESUME 无 job_payload 僵尸洞**：`agent_api.py:1710` job 行缺失时 RESUME 不带 payload → `resume_job` 不触发（main.py:256 守卫）→ job 登记 active 但 pipeline/watcher 永不恢复。改为缺 payload 时降级为 `CLEANUP`/`ABORT_LOCAL` 或显式告警
- **catchup 可观测**：watcher 重挂时区分「resume 重挂 vs 全新 claim」打点/日志，让运维能看到续航确实发生
- **数据来源契约（已澄清）**：catchup 不需要 recovery_sync 额外回吐 active_jobs——它搭载在已携带 `job_payload` 的 RESUME action 上。初版 D5 的「(a) 反推 / (b) 新增字段」二选一作废
- **续航前提依赖**：信号幂等由后端 `(job_id, seq_no)` UNIQUE + `ON CONFLICT DO NOTHING` 兜底（已落地：`models/job.py:158` + 迁移 `k9f0a1b2c3d4` + `agent_api.py:1293`），RESUME 重复上送天然安全

**超出 D5 范围、需单独立项**：RESUME 从 `run_task_wrapper` 顶部重跑 → patrol 中途的 Job 会重做 init（check_device/ensure_root/monkey_setup/资源推送/launch）。这是 Job 恢复语义问题（ADR-0019/0022 范畴），比 watcher 续航更大，本 ADR 仅标注不解决。

### D6: 单节点运维成熟度收口 ——【后移，触发条件：首个真实专项稳定无人值守跑通后】

**背景**：2026-06-13 代码核实发现，五维评估报告标记「Phase 1-3 已完成」的若干运维加固项，实际仅到「配置/脚本就位」，运维链路未闭合。这些项与被推迟的水平扩展（D1）正交，是单节点部署下的成熟度。

**排期决策（2026-06-14 修正）**：初版曾把本项提前为 Sprint 0。但平台尚未真实落地，告警与备份保护的是**尚不存在的生产负载**——在没有真实专项运行时，没有故障可告警、没有业务数据可丢失。故本项**后移**，不计入 M4 核心排期，触发条件为「首个真实专项已能稳定无人值守跑通（D5/D4 落地并验证）」。届时这三项合计 < 2 天即可收口。

| 项 | 当前状态（代码佐证） | 收口动作 |
|----|---------------------|---------|
| AlertManager 触达 | `deploy/prometheus/alertmanager.yml` 有 route + receiver 骨架，但 webhook 为占位 `127.0.0.1:5001/alerts`，钉钉/邮件配置全注释 | 接入真实值班通道（钉钉/飞书/邮件）+ 触发一次端到端验证（造一条 critical 告警确认触达） |
| PG 备份调度 | `scripts/pg_backup.sh`（含 `-mtime` 轮转）+ `scripts/pg_restore_test.sh` + `docs/preprod-drill-runbook.md` 齐备；脚本头第 10 行 cron 仅注释示例 | 安装 systemd timer 或 cron 真正调度每日备份；按 runbook 跑一次恢复演练并记录 RTO |
| Grafana 导入 | `docs/grafana/stability-platform-dashboard.json` 仅模板，无 provisioning | 配置 provisioning 自动导入 + 验证生产 scrape 到本平台指标 |
| RBAC 分级（可选） | `AdminRoute`（`router/index.tsx:99`）单档 admin/非 admin，覆盖 users/notifications/settings/audit | 评估是否需 read-only/operator 第三档；若否，记录「单档已满足当前用量」并关闭该项 |

**理由（后移后仍成立的价值，但非首次落地前置）**：
- 告警未触达 = R3「故障靠人工」实质未解——但首个专项跑通前无真实故障流，价值滞后
- 备份脚本就位但无调度 = R5「无自动备份」实质未解——但首个专项跑通前无真实业务数据，风险滞后
- 三项改动量 < 2 天、不碰业务代码，跑通后顺手收口即可，无需阻塞核心闭环

**不纳入本 ADR 的成熟度项**（记录但不在 M4 排期，避免范围蔓延）：
- SettingsPage 静态占位（`SettingsPage.tsx` 全硬编码，无后端配置端点）——需求待确认，单独立项
- 批量 Job 报告导出 zip/PDF（当前仅单 PlanRun JSON/MD）——测试经理需求驱动，单独立项
- 前端 API 客户端双风格统一（~5 模块走 unwrap / 7 模块裸响应，`management.ts` 24 调用全裸）——技术债，渐进收敛
- Plan 链失败中止策略（`plan_chain_trigger.py` 仅回滚标记不中止上游）——能力增强，单独立项

## 影响

### 推迟项的影响（可接受）

| 推迟项 | 当前约束 | 接受理由 |
|--------|---------|---------|
| SocketIO Redis adapter | 单后端崩溃 = 全平台不可用 | 设备池 <80，非工作时间崩溃概率低 |
| APScheduler 外置 | 重启后定时任务重新计时 | 重启频率低（月度升级），interval 任务重新计时影响有限 |
| 分布式限流 | 多 worker 限流 N 倍放大 | 当前单 worker，不影响 |
| Loki | 跨节点日志需 SSH | NFS 归档模式替代，LogArchiver 补齐 |

### 新增项的影响

| 新增项 | 影响范围 |
|--------|---------|
| LogArchiver | Agent 侧新模块（~300 行），不影响现有数据流 |
| Watcher 续航加固 | enable.py 1 行 + agent_api RESUME 降级 + main.py 打点 + 测试；不新增 manager 重挂逻辑（避免孤儿/重挂冲突） |
| enable.py 修复 | 1 行改动，无破坏性 |
| 归档状态端点 | 后端新增只读端点，不影响现有 API |

## 实施计划

> 执行顺序：Sprint 1 → 2 → 3 为 M4 核心闭环（首次落地路径）；运维收口阶段（D6）为触发条件驱动，跑通后再启。

### Sprint 1（3-5 天）：Watcher 无人值守续航——加固 RESUME 重挂路径 ——【最高增益，先做】

> 详细实现计划见 `docs/adr-0025-watcher-catchup-implementation-plan-2026-06-14.md`。

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backend/agent/watcher/enable.py:11` | `STP_WATCHER_ENABLED` 默认值 `false` → `true`，与 main.py 一致 |
| 2 | `backend/api/routes/agent_api.py:1709-1721` | RESUME 缺 `job_payload`（job 行缺失）时降级为 CLEANUP/ABORT_LOCAL，堵僵尸洞 |
| 3 | `backend/agent/main.py:243-275` | RESUME 重挂打点：区分 resume 重挂 vs 全新 claim（可观测） |
| 4 | `backend/agent/tests/` | 端到端续航测试：活跃 Job → 重启 → RESUME → watcher 重挂 → 信号续流 |
| 5 | 回归 | 既有 recovery + watcher 用例全过，确认未引入孤儿/重挂冲突 |

### Sprint 2（5-8 天）：Agent 日志归档调度器

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backend/agent/log_archiver.py`（新建） | 每日扫描 + 去重 + 归档 NFS + 溢出逻辑 |
| 2 | `backend/agent/local_disk_monitor.py`（新建） | 磁盘阈值监控 + 触发 LogArchiver |
| 3 | `backend/agent/main.py` | 集成归档调度器 |
| 4 | `backend/api/routes/agent_api.py` | `GET /agent/{host_id}/archive-status` |
| 5 | `backend/agent/registry/local_db.py` | 归档标记字段 |
| 6 | 测试 | 归档流程 + 溢出 + 端点 |

### Sprint 3（2-3 天）：控制平面拉取优化

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backend/api/routes/plan_runs.py` | watcher-summary 支持拉取模式 |
| 2 | `frontend/src/components/plan-run/WatcherSummaryCard.tsx` | 展示归档状态 |
| 3 | `backend/api/routes/runs.py` | 日志下载支持 NFS 归档路径 |
| 4 | 测试 | 端到端验证 |

### 运维收口阶段（1-2 天，D6）——触发条件：首个真实专项稳定无人值守跑通后

> 不计入 M4 核心排期。Sprint 1-3 落地、且有真实专项稳定跑通后再启动。

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `deploy/prometheus/alertmanager.yml` | 接入真实值班 webhook（钉钉/飞书/邮件），替换占位 URL |
| 2 | 端到端 | 造一条 critical 告警验证触达值班通道 |
| 3 | `deploy/control-plane/systemd/`（新建 timer）或 crontab | 调度 `scripts/pg_backup.sh` 每日运行 |
| 4 | 运维 | 按 `docs/preprod-drill-runbook.md` 跑一次 `pg_restore_test.sh` 恢复演练，记录 RTO |
| 5 | Grafana provisioning | 自动导入 `docs/grafana/stability-platform-dashboard.json` + 验证 scrape |
| 6 | 决策 | RBAC 第三档评估：需要则立项，不需要则在 ADR 标注关闭 |

## 验证

1. **Sprint 1**：Agent 重启后 Watcher 自动恢复 + 信号不丢失 + enable.py 默认值一致
2. **Sprint 2**：归档后本地日志缩量 + NFS 归档目录有文件 + 磁盘达阈值自动溢出
3. **Sprint 3**：前端显示归档状态 + 下载端点可访问 NFS 归档文件
4. **全局回归**：`python -m pytest backend/tests/` + `npx vitest run` 全过
5. **运维收口阶段（触发后）**：critical 告警可触达值班通道 + pg_backup 每日产物落地 + 恢复演练 RTO 记录 + Grafana 可见本平台指标

## 索引

- 评估报告：`docs/architecture-five-dimensional-assessment-2026-06.md` Phase 4
- Watcher 主线：ADR-0018
- 可观测路线：ADR-0011
- 派发门禁：ADR-0021
- 设备租赁：ADR-0019
