# 稳定性测试平台 — 架构五维评估报告

> 生成日期：2026-06-12（初版）| 修订日期：2026-06-13
> 评估范围：`F:/stability-test-platform` 全仓库只读分析
> 评估维度：能力边界 / 可观测性 / 易用性 / 准确性 / 自稳定性
> 关联文档（已归档）：[生产就绪评估](./archive/assessments/production-readiness-assessment-2026-05-23.md)、[项目健康度与剩余工作](./archive/assessments/project-health-and-remaining-work-2026-05.md)、[主链路脆弱性分析](./archive/assessments/main-chain-fragility-analysis-2026-05-23.md)

### 修订说明（2026-06-13）

基于逐项代码核实，本次修订内容：

| 类型 | 条目 | 修订动作 |
|------|------|---------|
| 删除 | MapReducePage 占位 | 该页面不存在，为虚构项 |
| 删除 | R1 Nginx 无 `/socket.io/` 反代 | `deploy/control-plane/nginx/stability-platform.conf` + `deploy/nginx/frontend-docker.conf` 均已配置 |
| 删除 | R2 敏感读 API 匿名可访问 | 抽查业务读端点均挂 `get_current_active_user` / `_verify_agent` / `require_admin` |
| 删除 | `/health` 仅 DB ping，SAQ 未暴露 | `main.py:226-233` 已返回 `saq_ready` |
| 删除 | PlanRun 导出按钮 `toast.info('功能开发中')` 占位 | `PlanRunDetailPage.tsx:541-548` 已接通 `api.planRuns.exportReport` + blob 下载 |
| 删除 | SocketIO 断连无连接状态指示器 | `AppShell.tsx:138-145` 已有全局"实时连接/已断开" badge |
| 改写 | B3 Agent SQLite seq_no 描述 | 原描述"崩溃重启归零丢信号"错误，emitter 构造时显式恢复 MAX+1；残余风险收窄为 prune+重启极端场景 |
| 改写 | B5 ended_at NULL 描述 | 原位置标错（实为 `device_lease_reconciler.py` 非 `recycler.py`）；UNKNOWN 全部 4 个写入方均设 ended_at，缺 NULL guard 为防御性缺口，严重度降级 |
| 改写 | `/metrics` 无鉴权 | 机制已存在（`STP_METRICS_AUTH_REQUIRED`），但默认关闭；改述为"鉴权默认未启用" |
| 调整 | 综合/维度评分 | 准确性 7.5→7.8（B3/B5 降级后扣分减少），易用性 8.0→8.2（删除虚构劣势后扣分减少） |

---

## 总评

| 维度 | 评分 | 权重 | 加权分 |
|------|------|------|--------|
| 能力边界 | 7.5 / 10 | 20% | 1.50 |
| 可观测性 | 7.0 / 10 | 20% | 1.40 |
| 易用性 | 8.2 / 10 | 20% | 1.64 |
| 准确性 | 7.8 / 10 | 20% | 1.56 |
| 自稳定性 | 8.0 / 10 | 20% | 1.60 |
| **综合** | | | **7.7 / 10** |

**一句话结论**：平台核心编排链路与安全机制已达可运行水准，但在可观测闭环、状态准确性边界条件和水平扩展能力上仍有明确缺口。

---

## 1. 能力边界 — 7.5 / 10

> 衡量平台功能覆盖范围、已知边界和扩展能力

### 优势

- **编排完整度**：Plan→PlanStep→PlanRun→JobInstance 全链路闭环，支持 init/patrol/teardown 三阶段 + Plan 链串接 + CHAIN/SCHEDULE/MANUAL 三种触发模式
- **脚本扁平化落地**：`script:<name>` 统一 action 类型，目录契约清晰（`<name>/v<version>/<entry>`），版本即参数不可变性强制（已存在版本改 default_params 返回 422）
- **Agent 完整生命周期**：设备发现→任务认领→Pipeline 引擎执行→StepTrace/LogSignal/Artifact 三面输出→心跳/Lease 续期/Recovery Sync
- **安全机制 ADR-0024 完整落地**：HttpOnly Cookie + CSRF Origin/Referer + Refresh Token 黑名单 + 生产 guard（`validate_production_auth_cookie_settings` 启动硬校验）
- **设备租赁与防护**：Device Lease + fencing_token + 四路径 Reconciler + 两段式过期（RUNNING→UNKNOWN 300s grace→FAILED + release）

### 劣势

- **Watcher CATCHUP 未实现**：Watcher 子系统主线完成但 `backend/agent/watcher/` 全目录无任何 CATCHUP 匹配，异常信号追赶能力缺失
- **水平扩展架构未就绪**：单 uvicorn + 进程内 APScheduler + 内存 RateLimit（`backend/core/limiter.py:15` 注释自证 "Simple in-memory rate limiter"），多实例部署会导致调度重复/限流失效/SocketIO room 分裂
- **SettingsPage 纯静态占位**：`frontend/src/pages/settings/SettingsPage.tsx` 全部硬编码展示（平台名称"稳定性测试平台"、时区"Asia/Shanghai"等），无写入能力，无后端配置接口
- **批量操作缺位**：无批量 Job 报告导出（zip/PDF）、无 Agent 批量升级 UI 集成（Ansible 回写未闭环）
- **精细 RBAC 未落地**：后端 `require_admin` 有，但前端无路由级门控（`router/index.tsx` 零角色守卫），read-only / operator 角色无法区分

### 关键指标

| 指标 | 值 |
|------|-----|
| API 端点数 | 40+（17 router 模块注册于 `main.py`） |
| 前端页面数 | 20+（6 个 Plan 体系新增页面） |
| Agent action 类型 | 1（`script:<name>`；已移除 tool/builtin/shell） |
| 仓库脚本数 | 14 个脚本 / 多版本 |
| 水平扩展就绪度 | 未就绪（需 SocketIO Redis adapter + 外置调度器 + 分布式限流） |

---

## 2. 可观测性 — 7.0 / 10

> 衡量系统内部状态对外可见程度、告警闭环和排障效率

### 优势

- **指标面丰富**：40 族 Prometheus 指标覆盖 9 大领域（Counter 24 + Gauge 8 + Histogram 7 + Info 1），包含 dispatch gate / precheck / patrol / CSRF / outbox / SAQ 等
- **CSRF 三档分类指标**：`stability_csrf_rejected_total{reason}` 区分 origin_not_allowed / referer_not_allowed / missing_origin_and_referer，可直接 split 攻击/误配置/探测
- **双层实时策略**：SocketIO 事件推送（job_status / plan_run_status / watcher_signal）+ React Query 分级 staleTime（ADMIN 5min / REFERENCE 1min / OPERATIONAL 15s / LIVE 0）
- **连接状态可见**：AppShell 顶部已有全局"实时连接/已断开" badge（`AppShell.tsx:138-145`），DeviceMonitorPanel 另有 Live/Offline badge（`DeviceMonitorPanel.tsx:258-262`）
- **APScheduler 全量埋点**：所有后台 job（recycler / watchdog / reconciler / cron / cleanup / reaper / token_cleanup / saq_poll）均接入 `stability_apscheduler_job_*`
- **Agent 边缘指标**：outbox 积压 Gauge 经心跳上报，step_trace_cache 防膨胀
- **健康端点完整**：`/health` 已返回 `saq_ready` 字段（`main.py:226-233`）

### 劣势

- **AlertManager 未部署**：ADR-0011 第二层（告警规则）仅草案文件 `deploy/prometheus/alerts-stability-platform.yml`，未验证路由和值班闭环
- **Grafana Dashboard 未导入**：模板已有 `docs/grafana/stability-platform-dashboard.json`，但未验证在生产 scrape
- **集中日志缺位**：`log_writer.py` 写本地文件，无 Loki/等价集中检索；跨 Job/PlanRun 日志关联查询需手动 SSH
- **关键故障无自动发现**：Reconciler 超时、dispatch 失败、SAQ 积压等仍靠人工刷库或刷 Prometheus
- **`/metrics` 鉴权默认未启用**：`STP_METRICS_AUTH_REQUIRED` 默认 `"0"`（`metrics.py:22-63` 支持 Bearer/Agent-Secret 鉴权但默认关闭），生产暴露内部运行指标

### 关键指标

| 指标 | 值 |
|------|-----|
| Prometheus 指标族数 | 40 |
| SocketIO 事件数 | 7（agent namespace 3 + dashboard namespace 4） |
| APScheduler 监控 job 数 | 8 |
| Grafana Dashboard | 已有模板，未部署 |
| AlertManager 规则 | 已有草案，未部署 |
| Loki / 集中日志 | 未部署 |

---

## 3. 易用性 — 8.2 / 10

> 衡量操作流程效率、界面交互质量和学习曲线

### 优势

- **shadcn/ui 组件体系**：15 个原子组件 + Radix + CVA + tailwind-merge，设计一致性和无障碍基础好
- **PlanRunDetailPage 四卡布局**：Topbar（实时秒级 tick + 中止）+ ChainBreadcrumb + DispatchGateCard + BusinessFlowTimeline，信息密度和可操作性平衡
- **HostHotUpdateConfirmDialog 二次确认**：active_jobs 权威快照 + 勾选确认 + 409 fallback 自动重开，杜绝误操作
- **Plan 生命周期编辑器**：PlanLifecycleEditor 可视化 init/patrol/teardown 三阶段步骤编排
- **PlanRun 报告导出已实现**：PlanRunDetailPage 导出按钮已接通后端 API（`PlanRunDetailPage.tsx:541-548` 调用 `api.planRuns.exportReport` + blob 下载），支持 markdown/json 格式
- **API 响应格式统一**：全量 `ApiResponse[T]` 双字段格式（`{data, error}`），前端 `unwrapApiResponse` 统一解包
- **Agent 部署简便**：`install_agent.sh` 一键安装 + systemd 管理 + WSL 自检测；热更新支持前端一键/Ansible 批量

### 劣势

- **批量导出缺位**：测试经理大批量报告需求需绕行，无法 zip/PDF 打包
- **Host 页 lease 阻塞提示弱**：设备 BUSY 原因仅 Prometheus 有指标，UI 层需操作者自行排查
- **前端 API 客户端双风格**：16 个模块中 7 个（analytics/auth/devices/hosts/logs/management/pipeline）不走 `unwrapApiResponse`，其余走，调用方需记忆差异
- **SettingsPage 纯静态占位**：导航可见但不可配置，增加认知负担；WiFi 管理（WifiPage）独立存在但 Sidebar 和 SettingsPage 均不可达

### 关键指标

| 指标 | 值 |
|------|-----|
| 前端组件库 | shadcn/ui 15 原子组件 |
| Vitest 覆盖 | 109+ cases / 19 文件 |
| API 客户端模块数 | 16 |
| 浏览器兼容 | 现代浏览器（React 18 最低要求） |
| 移动端适配 | 无（桌面端优先） |

---

## 4. 准确性 — 7.8 / 10

> 衡量数据一致性、状态机正确性和边界条件处理

### 优势

- **Patrol CAS 操作**：`UPDATE WHERE status='RUNNING' AND last_patrol_heartbeat_at < cutoff RETURNING id`，避免并发误判
- **PlanRun 聚合 `FOR UPDATE`**：行锁序列化 + `_TERMINAL_PLAN_RUN_STATUSES` 守卫，pass_rate = completed/total 严格计算
- **JWT 类型严格分离**：`decode_token(token, expected_type="access")` 防止 refresh token 回放为 access
- **Device Lease fencing_token**：`lease_generation` 原子递增，格式 `{device_id}:{new_gen}`，防脑裂
- **PG 测试隔离**：`conftest.py` 从 `transaction.rollback` 改为 `TRUNCATE ... RESTART IDENTITY CASCADE`，修复了序列不重置等假阳/假阴
- **脚本版本即参数**：已存在版本改 `default_params` 返回 422，强制新建版本，保证幂等

### 劣势（关键 Bug）

1. **B1 `alembic/env.py` 缺少 `token_blacklist` 导入**（高危）：`get_metadata()` 导入清单（L25-41）不含 `backend.models.token_blacklist`，该模型文件存在且定义 `RevokedRefreshToken`（`__tablename__ = "revoked_refresh_token"`）。alembic autogenerate 将视该表为多余并生成 DROP TABLE 迁移。修法：添加 `import backend.models.token_blacklist`

2. **B2 `JobLogSignal.host_id` 缺 ForeignKey**（中危）：`backend/models/job.py:141` `host_id = Column(String(64), nullable=False)`，对比同文件 `JobInstance.host_id`（L24 有 `ForeignKey("host.id")`）。Host 删除后 log_signal 成为孤儿行。修法：添加 `ForeignKey("host.id")` + 迁移

3. **B4 UNKNOWN→COMPLETED 不可达**（高危）：`agent_api.py:51-56` `_TERMINAL` 集合包含 `UNKNOWN.value`；L825 `already_terminal = job.status in _TERMINAL` 导致 Agent 对 UNKNOWN Job 上报完成时被静默跳过（L833 `if not already_terminal` 不进入），最终 reconciler 将 UNKNOWN 强转 FAILED。状态机本身允许 UNKNOWN→COMPLETED（`state_machine.py:11`），但该路径无人调用。**附带发现**：L883 对 `already_terminal` 分支仍无条件刷新 `ended_at`，会重置 UNKNOWN 的 grace 窗口

4. **B3 Agent SQLite seq_no prune 后重启丢信号**（低危）：原描述"崩溃重启归零丢信号"不准确——`emitter.py:67-68` 构造时显式调用 `local_db.next_log_signal_seq_no()`（`local_db.py:522`，取 `MAX(seq_no)+1`）恢复单调递增。残余风险收窄为：`prune_acked_log_signals(keep_recent=1000)` 全局清理 acked 行后，若 Agent 重启且该 Job 的 outbox 行已被全部 prune，`MAX(seq_no)` 可能回退到低于后端已记录的最大 seq_no，后续 `INSERT OR IGNORE` 静默丢弃——需单 Job 海量信号 + prune + 重启三者叠加才触发

5. **B5 `ended_at` NULL 比较缺防御性 guard**（低危）：原描述"导致 UNKNOWN 粘滞"不成立——UNKNOWN 的全部 4 个写入方（`recycler.py:349/416`、`device_lease_reconciler.py:127`、`session_watchdog.py:62`）均设置 `ended_at`，现实中不存在 `ended_at=NULL` 的 UNKNOWN 行。但 `device_lease_reconciler.py:144,185` 和 `session_watchdog.py:82` 的 SQL/Python 比较缺少 `ended_at IS NOT NULL` 防御，如未来有写入方遗漏设 `ended_at`，PG `NULL < timestamp` 求值为 NULL 将导致查询漏行。**位置修正**：实为 `device_lease_reconciler.py` 非 `recycler.py`

### 劣势（设计缺陷）

- **密码策略缺失**：后端 `UserCreate.password: str`（`auth.py:58-60`）无 Pydantic `min_length`/`max_length` 约束；`register`（`auth.py:194-217`）和 `change_password`（`users.py:197-213`）无密码长度/强度检查；前端 `RegisterPage.tsx` 无 `minLength`，`ChangePasswordPage.tsx` 有 `minLength={6}` 但二者不一致；仅 IP 级 300 req/60s 限流，无登录失败锁定
- **Recovery Sync 边界模糊**：`JOB_NOT_RUNNING` 触发 recovery 后，Agent 本地状态与后端判定可能背离
- **Patrol 退避状态机**：BACKOFF / RISK / manual_retry / manual_exit 多入口，缺少全状态可达性验证

### 关键指标

| 指标 | 值 |
|------|-----|
| Backend pytest | ~718 passed（PG testcontainers） |
| Agent pytest | ~371 collected |
| 已知数据正确性 Bug | 5（B1/B4 高危，B2 中危，B3/B5 低危） |
| 状态机转换覆盖 | JobStatus 6 态 / PlanRunStatus 5 态 |
| 并发安全机制 | CAS / FOR UPDATE / fencing_token / lease_generation |

---

## 5. 自稳定性 — 8.0 / 10

> 衡量系统自愈能力、故障隔离和降级策略

### 优势

- **四路径 Reconciler**：abort_running → expired_leases → stale_unknown → terminal_residual，15s 扫描周期，覆盖完整异常谱
- **两段式 Lease 过期**：RUNNING→UNKNOWN（lease 仍阻塞）→ 300s grace → FAILED + release，避免瞬间误判
- **SAQ 全链路硬失败**：`enqueue_sync(..., required=True)` → 503；lifespan Redis PING + SAQ 启动失败即退出
- **Dispatch Sync Gate**：CHAIN/SCHEDULE 统一 `dispatch_plan_sync` inline gate + `DISPATCH_SYNC_MAX_ATTEMPTS` 可配置重试
- **Subprocess 进程组隔离**：Agent 脚本执行进程组 kill，超时/崩溃不感染主进程
- **Patrol Recovery**：`JOB_NOT_RUNNING` → recovery/sync 路径自动恢复脱离态 Job
- **StepTrace Cache 防膨胀**：Agent 本地 SQLite step_trace_cache 限制最大行数
- **Outbox 积压自动上报**：log_signal / terminal outbox 积压经心跳 Gauge 上送后端
- **SocketIO 断连降级有指示**：AppShell 全局 badge 告知实时连接/已断开状态，降级到 React Query 分级轮询（dispatch 3s / running 10s / watcher 30s）时用户可感知

### 劣势

- **单点 backend**：uvicorn 单进程，无 HA，进程崩溃 = 全服务不可用
- **无自动备份**：PG 仅文档提及 pg_dump，无 cron / 恢复演练
- **Watcher 灰度无回退 runbook**：`STP_WATCHER_ENABLED` 切换后对 CPU/IO/NFS 影响未评估；`enable.py:11` 默认 `"false"` 与 `main.py:69` 默认 `"true"` 不一致

### 关键指标

| 指标 | 值 |
|------|-----|
| Reconciler 路径数 | 4 |
| Reconciler 扫描周期 | 15s |
| Lease grace 时长 | 300s |
| 后端单点容灾 | 无（单 uvicorn） |
| 自动备份 | 无 |
| 断连降级 | React Query 分级轮询 + 全局连接 badge |

---

## 6. 关键 Bug 与风险清单

### Bug（需代码修复）

| # | 问题 | 位置 | 严重度 | 修复复杂度 |
|---|------|------|--------|-----------|
| B1 | `alembic/env.py` 缺 `token_blacklist` 导入，autogenerate 将删表 | `backend/alembic/env.py:25-41` | 高 | S |
| B2 | `JobLogSignal.host_id` 无 ForeignKey | `backend/models/job.py:141` | 中 | S + 迁移 |
| B4 | UNKNOWN→COMPLETED 不可达（`_TERMINAL` 含 UNKNOWN 导致 Agent 完成上报被吞）；附带：`already_terminal` 分支无条件刷新 `ended_at` 重置 grace 窗口 | `backend/api/routes/agent_api.py:51-56,825-833,883` | 高 | M |
| B3 | Agent SQLite seq_no prune+重启极端场景丢信号 | `backend/agent/registry/local_db.py:522-532,549` | 低 | M |
| B5 | `ended_at` NULL 比较缺防御性 guard（UNKNOWN 4 个写入方均设 ended_at，现实不触发；但未来写入方遗漏时将漏行） | `backend/scheduler/device_lease_reconciler.py:144,185` + `backend/scheduler/session_watchdog.py:82` | 低 | S |

### 风险（需运维/架构决策）

| # | 风险 | 严重度 | 缓解方案 |
|---|------|--------|---------|
| R3 | 无告警闭环，故障靠人工 | 中 | P1：部署 AlertManager |
| R4 | 单点 backend 无 HA | 中 | 接受 MVP；文档化 |
| R5 | 无自动备份 | 中 | P1：pg_dump cron + 演练 |
| R6 | Watcher 默认值不一致（`enable.py` vs `main.py`）+ CATCHUP 未实现 | 低 | 统一默认值 + 评估生产开启条件 |
| R7 | 密码策略缺失（无长度/复杂度/锁定） | 中 | 功能增强 |
| R8 | 2026-06-21 refresh jti grace 收口 | 低 | 日历提醒 |
| R9 | `/metrics` 鉴权默认未启用 | 低-中 | 生产设 `STP_METRICS_AUTH_REQUIRED=1` 或 Nginx ACL |

---

## 7. 维度交叉分析

### 能力边界 × 自稳定性

Plan 链串接（`next_plan_id`）增加了编排能力，但链式 dispatch 失败仅回滚 `next_plan_triggered` 标记，不自动中止上游 PlanRun——这是能力延伸与自稳定性之间的设计取舍。建议增加链式中止策略选项。

### 可观测性 × 准确性

指标面丰富但 B4 会导致 UNKNOWN Job 的终态被 reconciler 强转 FAILED 而非 Agent 上报的真实结果，使 `stability_plan_run_terminal_total` 终态分布在 FAILED 方向偏移。**修复 B4 是可观测数据可信的前提之一**。

### 易用性 × 准确性

`HostHotUpdateConfirmDialog` 二次确认避免了误操作，但 409 fallback 自动重开 dialog 的路径未经 E2E 验证——如果 API 返回 409 但 payload 格式变化，用户可能卡在无法关闭的弹窗中。

### 自稳定性 × 准确性

B4 的 `already_terminal` 分支无条件刷新 `ended_at` 会重置 UNKNOWN 的 grace 窗口，与两段式 Leasing 的自愈设计矛盾——reconciler 计算的 `grace_deadline` 依据 `ended_at`，每次 Agent 心跳刷新都会推迟 grace 到期，UNKNOWN Job 可能永远不被回收。

---

## 8. 优先修复路径建议

```
Phase 1 (1 周内)  →  B1 alembic/env.py 导入 + B4 UNKNOWN 移出 _TERMINAL + ended_at 刷新 guard
Phase 2 (2-3 周)  →  B2 ForeignKey + B5 NULL guard + 密码策略 + /metrics 鉴权默认启用
Phase 3 (4-6 周)  →  前端 admin 路由门控 + AlertManager 部署 + PG 备份自动化 + B3 seq_no 改进
```

以上 Phase 1-3 已全部完成。原 Phase 4 经 ADR-0025 对齐后决策如下：

| 原 Phase 4 项 | 决策 | 理由 |
|--------------|------|------|
| SocketIO Redis adapter | **推迟** | 单控制平面部署下无多实例需求，推迟不损失功能 |
| APScheduler 外置 | **推迟** | 同上，多 worker 7 处全局状态冲突，当前 ROI 低 |
| 分布式限流 | **推迟** | 单 worker 下内存限流有效 |
| Loki 集中日志 | **不引入** | Agent 本地存储为主，每日汇总归档 NFS，Loki 不契合 |
| SAQ 多进程适配 | **推迟** | 同水平扩展推迟 |
| Prometheus 多进程指标 | **推迟** | 同水平扩展推迟 |
| Watcher CATCHUP | **保留** | Agent 重启后恢复活跃 Watcher，核心闭环 |
| Watcher enable.py 修复 | **保留** | 1 行改动统一默认值 |
| 日志归档调度器 | **新增** | Agent 侧每日汇总去重 + 归档 NFS + 磁盘溢出 |

**修订后 Phase 4 路径**（ADR-0025）：

```
Phase 4a (Sprint 1, 5-8 天) →  Watcher CATCHUP + enable.py 默认值统一
Phase 4b (Sprint 2, 5-8 天) →  Agent 日志归档调度器（LogArchiver）+ 磁盘监控
Phase 4c (Sprint 3, 2-3 天) →  控制平面拉取优化 + 前端归档状态展示
```

---

## 9. 与既有评估文档的关系

| 文档 | 本报告定位 |
|------|-----------|
| [生产就绪评估](./archive/assessments/production-readiness-assessment-2026-05-23.md) | 本报告 §4 准确性中 B1/B4 为新发现；R1/R2 在该文档中标为 P0，但代码已实现（Nginx 反代 + 读 API 鉴权），该文档该两条已过时 |
| [项目健康度与剩余工作](./archive/assessments/project-health-and-remaining-work-2026-05.md) | 本报告 §5 自稳定性与该文档 §2 加固摘要对齐，§6 风险表与该文档 §4 待办清单互补 |
| [主链路脆弱性分析](./archive/assessments/main-chain-fragility-analysis-2026-05-23.md) | 本报告 §7 交叉分析为该文档脆弱点提供维度化归因 |
| [主链路剩余工作实施计划](./main-chain-remaining-work-implementation-plan-2026-05-25.md) | 本报告 §8 修复路径与该文档 Phase A/B/C 排期对齐 |

---

## 附录：核实中删除的条目及理由

| 原条目 | 删除理由 | 反证 |
|--------|---------|------|
| MapReducePage 占位页 | 虚构 | `frontend/src` 零匹配，该页面不存在 |
| R1 Nginx 无 `/socket.io/` 反代 | Nginx 配置已存在 | `stability-platform.conf:21-30` + `frontend-docker.conf:18-19` |
| R2 敏感读 API 匿名可访问 | 业务读端点均挂鉴权依赖 | 抽查 plans/plan_runs/hosts/devices/logs/runs 全部端点确认 |
| `/health` 仅 DB ping | 已返回 `saq_ready` | `main.py:226-233` |
| PlanRun 导出按钮 `toast.info` 占位 | 已接通后端 API | `PlanRunDetailPage.tsx:541-548` |
| SocketIO 断连无连接状态指示器 | 全局 badge 已有 | `AppShell.tsx:138-145` |

---

*维护：重大功能交付或架构变更后同步更新评分与 Bug 清单；Bug 修复后在该行标注 `✅ fixed` 而非删除。*
