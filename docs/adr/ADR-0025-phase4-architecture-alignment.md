# ADR-0025: Phase 4 架构对齐——单控制平面下 Agent 侧日志闭环与水平扩展推迟

- 状态：Accepted
- 优先级：P2
- 目标里程碑：M4（Sprint 1-3，共 12-19 天）
- 日期：2026-06-13
- 决策者：平台研发组
- 标签：架构, Watcher, 日志归档, 水平扩展, 部署策略
- 关联：ADR-0018 (Watcher), ADR-0011 (可观测)

## 背景

五维评估报告（`docs/architecture-five-dimensional-assessment-2026-06.md`）Phase 4 列出 7 项长期改动：SocketIO Redis adapter、APScheduler 外置、分布式限流、Loki 集中日志、Watcher CATCHUP、SAQ 多进程适配、Prometheus 多进程指标。评估假设"多后端实例 + 集中日志服务器"为终态架构。

经与项目实际部署需求对齐，发现原假设与项目背景存在根本偏差：

1. **部署形态**：同一局域网下一台控制平面（Windows 开发 / Linux 生产）+ 多 Agent 节点，只面向单 React Dashboard
2. **多实例需求**：平台需先在实际项目中跑顺，成熟后再考虑多控制平面或多 worker
3. **日志管理**：Loki 集中日志不契合——Agent 本地磁盘 1TB，NFS 14TB；日志在 Agent 侧存储，每日定时汇总去重后归档 NFS，需支撑 JIRA 提单；本地达阈值后溢出
4. **Watcher 定位**：应在 Agent 侧完成完整闭环（检测→拉取→分析→归档），控制平面只做定时拉取和聚合展示

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

### D5: Watcher CATCHUP + enable.py 修复

- enable.py `STP_WATCHER_ENABLED` 默认值 `false` → `true`（与 main.py 一致）
- manager.py 实现 `catchup_on_startup(active_jobs)`——Agent 重启后恢复活跃 Job 的 DeviceLogWatcher
- 依赖 recovery_sync 返回的 active_jobs 列表
- 信号幂等由后端 `(job_id, seq_no)` UNIQUE + `ON CONFLICT DO NOTHING` 兜底

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
| CATCHUP | manager.py + main.py + job_session.py 联动 |
| enable.py 修复 | 1 行改动，无破坏性 |
| 归档状态端点 | 后端新增只读端点，不影响现有 API |

## 实施计划

### Sprint 1（5-8 天）：Watcher CATCHUP + enable.py 修复

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `backend/agent/watcher/enable.py:11` | `STP_WATCHER_ENABLED` 默认值 `false` → `true` |
| 2 | `backend/agent/watcher/manager.py` | `catchup_on_startup(active_jobs)` 实现 |
| 3 | `backend/agent/main.py` | 传入 active_jobs 参数 |
| 4 | `backend/agent/job_session.py` | 退出协议与 catchup 协调 |
| 5 | `backend/agent/registry/local_db.py` | watcher_state 清理/更新 |
| 6 | 测试 | catchup + enable.py + 重启恢复 |

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

## 验证

1. **Sprint 1**：Agent 重启后 Watcher 自动恢复 + 信号不丢失 + enable.py 默认值一致
2. **Sprint 2**：归档后本地日志缩量 + NFS 归档目录有文件 + 磁盘达阈值自动溢出
3. **Sprint 3**：前端显示归档状态 + 下载端点可访问 NFS 归档文件
4. **全局回归**：`python -m pytest backend/tests/` + `npx vitest run` 全过

## 索引

- 评估报告：`docs/architecture-five-dimensional-assessment-2026-06.md` Phase 4
- Watcher 主线：ADR-0018
- 可观测路线：ADR-0011
- 派发门禁：ADR-0021
- 设备租赁：ADR-0019
