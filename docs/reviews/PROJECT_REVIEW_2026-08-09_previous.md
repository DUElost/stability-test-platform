# Stability Test Platform 完整项目审查报告（修订版）

**审查日期**: 2026-08-09  
**审查范围**: 代码库、GitHub 仓库、文档、架构一致性、测试覆盖  
**当前分支**: fix/agent-shanghai-run-date-stamp  
**最新提交**: 8f88b18 fix(agent): use Asia/Shanghai for AEE run_date_stamp  
**修订说明**: 根据逐项核对结果修正事实错误，保留方向性判断

---

## 执行摘要

### 整体健康度：**良好 (B+)**

项目处于活跃开发状态，架构清晰，文档完善，CI/CD 健全。主要优势：
- ✅ 架构文档与代码高度一致（ADR-0018 至 ADR-0027 完整追溯）
- ✅ 状态机实现符合设计规范（VALID_TRANSITIONS 集中管理，含明确注释的有意绕过）
- ✅ CI 已全面清零 ruff/ESLint 警告，代码质量门禁生效
- ✅ 测试覆盖广泛（301 测试文件，~2780 测试用例）
- ✅ 方案 C 存储架构已落地（ADR-0025）

需要关注的领域：
- ⚠️ 7 项技术债务 Issues 待排期（表名复数、限流多副本等）
- ⚠️ 前端类型同步需定期人工校验（types.ts ↔ Pydantic schema）
- ℹ️ 环境变量文档已存在（docs/development/environment-variables.md，2026-07-15）

---

## 代码库统计

### 规模
- **Python 文件**: 618 个（git tracked）
- **TypeScript 文件**: 274 个（frontend/src/）
- **后端测试**: 132 个文件（backend/tests/）
- **Agent 测试**: 81 个文件（backend/agent/tests/）
- **前端测试**: 73 个文件
- **文档**: 193 个 Markdown 文件（git tracked）
- **代码库大小**: backend/ 251M，frontend/ 344M，docs/ 6.1M

### 测试用例统计（实测）
- **Agent 测试**: 963 个用例，~85s 运行时间
- **后端测试**: 1294 个测试函数
- **前端测试**: 463 个用例，~12s 运行时间
- **repo-level 测试**: 61 个用例
- **总计**: ~2780 个测试用例（含 repo-level）

### 数据库模型
- **表定义**: 26 个唯一表（按 `__tablename__` 赋值统计；`audit_logs` 在 docstring 中重复出现不计）
  - **单数表名**: 19 个（符合约定，含 `revoked_refresh_token`）
  - **复数表名**: 7 个（users, notification_channels, alert_rules, notification_logs, task_schedules, audit_logs, device_leases）
- **迁移版本**: Alembic head 为 o2p3q4r5s6t7_barrier_max_wait_seconds.py（2026-08-07）
  - 注：按字母排序 z3a4b5c6d7e8 看似最新，但实际是迁移链中段（2026-07-01）

### 近期活跃度（过去 3 个月）
- **主要贡献者**: DUElost (527 commits), Rin (255), xiaohong.li (32)
- **最近两周**: 165+ 非合并提交，55 个合并 PR，主要聚焦 dedup scan scoping、主机页 UI 改进、依赖安全更新

---

## GitHub 仓库状态

### PR 状态
- **Open PR**: 1 个（#204 - fix(agent): AEE 目录日期戳改用上海时区）
- **最近合入**: #203 - fix/plan-run-scoped-dedup-scan（2026-08-09）

### Issue 状态
- **Open Issues**: 20 个
- **标签分布**:
  - `tech-debt`: 7 个（技术债务积压）
  - `enhancement`: 6 个
  - `ops`: 5 个（运维部署相关）
  - `adr-0026`: 3 个（Plan 执行扩容追踪）
  - `observability`: 2 个
  - `agent`: 2 个
  - `watcher`: 1 个

### 关键 Epic Issues
1. **#169**: 夜间真实设备 E2E（项目成熟后启动）
2. **#107**: 20 台 host 完整测试流程打通与持续化运行
3. **#105/106**: 容量 B1/B2（host 调度 + device 执行维度）
4. **#75**: 补足 host 库存至 ≥44（受采购阻塞）
5. **#46**: 生产 env 硬化（ADR-0024 guard + HTTPS + JWT 轮换）

### CI/CD 健康度
- **最近 10 次运行**: 8 success, 1 cancelled, 1 skipped
- **Workflows**: 6 个活跃（CI、Enable auto-merge、Main CI backstop、Dependabot Updates、Dependency Graph、CodeQL）
- **分层策略**: PR 跑轻量 job（lint/typecheck/compileall/agent-tests），全量 backend-test/frontend-check/docker-build 仅 main 或定时触发
- **Auto-merge**: 已启用，CodeRabbit 作为准入门禁

---

## 架构一致性审查

### ✅ 遵循良好

#### 1. 状态机实现
- `backend/services/state_machine.py` 集中定义 `VALID_TRANSITIONS`
- Job 状态转换: PENDING → RUNNING → COMPLETED/FAILED/ABORTED/UNKNOWN；另允许 PENDING → FAILED/ABORTED（超时/中止）
- PlanRun 状态转换: QUEUED → PRECHECK → RUNNING → SUCCESS/PARTIAL_SUCCESS/FAILED；另允许 FAILED → QUEUED（V2 人工重试）
- 终态时自动清理 `execution_state`（#116 修复）
- **实际引用**: `VALID_TRANSITIONS` 字面引用 6 处
- **有意绕过**（已注释）: `backend/scheduler/recycler.py` 两处直接 SQL 终态路径（:385 与 :579 附近）：
  - PENDING → FAILED（timeout，含 `execution_state=None` 终态清理）
  - RUNNING → UNKNOWN（CAS 更新，watchdog/reconciler 路径）
  - 一处是 PENDING→FAILED 的防御性直写，另一处是 RUNNING→UNKNOWN 的 CAS 竞态消除；均已明确注释说明

#### 2. 表名约定
- **实际表定义**: 26 个唯一表（基于 `__tablename__` 赋值统计）
- **单数表名**: 19 个（符合约定）
  - ✅ `script`, `plan`, `plan_run`, `plan_step`, `plan_run_host`, `plan_run_target_device`, `plan_run_artifact`, `plan_migration_audit`
  - ✅ `host`, `device`, `job_instance`, `job_artifact`, `job_log_signal`, `step_trace`
  - ✅ `action_template`, `jira_run`, `resource_pool`, `resource_allocation`, `revoked_refresh_token`
- **复数表名**: 7 个（已知例外）
  - `users`（标准用户表）
  - `notification_channels`, `alert_rules`, `notification_logs`（notification.py，历史模块）
  - `task_schedules`（调度表）
  - `audit_logs`（审计表，历史兼容，已注释说明）
  - `device_leases`（租约表）

#### 3. Pydantic v2 迁移
- ✅ **无遗留 v1 API**: 
  - `.dict()` 已全部替换为 `.model_dump()`
  - `parse_obj` / `from_orm` 未发现使用
  - `class Config` 已替换为 `ConfigDict(from_attributes=True)`

#### 4. 方案 C 存储路径（ADR-0025）
- ✅ **路径解析统一入口**: `backend/agent/aee/paths.py`
  - `get_aee_local_root()`: Agent HDD 第一落点
  - `get_aee_nfs_root()`: 15.4 CIFS 上送目标
  - `resolve_artifact_promote_dir()`: JobArtifact 文件路径
  - `resolve_upload_devices_dir()`: 事件目录上送路径
  - `resolve_spill_devices_dest()`: HDD 溢出目标
- ✅ **LogArchiver**: 仅 SSD prune，不再上送运行日志到 15.4
- ✅ **HddSpillMonitor**: HDD 超阈时溢出最旧事件到 CIFS
- ⚠️ **待加固**: `get_disk_usage()` 读盘失败应返回 `None`，勿返回 `0.0`

#### 5. 脚本目录契约（ADR-0020）
- ✅ 布局符合 `<STP_SCRIPT_ROOT>/<name>/v<version>/<entry>.{py,sh}`
- ✅ CI 门禁 `check-script-version-immutability.py` 已生效
- ✅ `ruff.toml` 已将脚本目录加入 `extend-exclude`
- ✅ 扫描语义：created / skipped(sha一致) / conflicts(sha不一致不落库) / deactivated

---

### ⚠️ 需要关注的领域

#### 1. 环境变量文档（已存在）
**位置**: `docs/development/environment-variables.md`（2026-07-15 创建）  
**状态**: ✅ 文档已存在且被多处引用（README.md、docs/DOC-MAP.md、docs/development/README.md、docs/development/local-development.md）

**内容覆盖**:
- 控制平面变量（DATABASE_URL, REDIS_URL, JWT_SECRET_KEY, AGENT_SECRET）
- SocketIO Redis adapter 配置（`STP_SOCKETIO_REDIS_ADAPTER`, ADR-0027）
- Agent 侧变量（心跳、超时、ADB、Watcher、scan/upload）
- 方案 C 存储路径（`STP_AEE_LOCAL_ROOT`, `STP_AEE_NFS_ROOT`, `STP_AEE_CIFS_ROOT`）
- 开发陷阱与生产约束

**无需行动**: 该文档已完整，不在待办事项中

#### 2. 前端类型同步
**当前**: `frontend/src/utils/api/types.ts` 需人工对齐后端 Pydantic schema  
**风险**: 字段新增/删除/重命名时容易漂移

**现状检查**:
- ✅ 前端类型检查通过（`tsc --noEmit` 无错误）
- ⚠️ 但无自动化校验 types.ts ↔ backend/api/schemas/*.py 一致性

**推荐**:
1. 短期：在 PR 模板中增加"如修改 Pydantic schema，需同步 types.ts"检查项
2. 长期：考虑引入 openapi-typescript 从 OpenAPI spec 自动生成类型

#### 3. 敏感信息日志
**检查结果**: ✅ 未发现明显的密码/token 明文日志  
**发现**:
- `backend/core/security.py`: 仅输出生成命令提示，不打印实际密钥
- `backend/api/routes/auth.py`: 仅记录 `sub`（用户标识），不记录 token 内容
- `backend/agent/lease_renewer.py`: debug 日志不含敏感字段

#### 4. SQL 注入风险
**检查结果**: ✅ 未发现原始 SQL 拼接  
**所有数据库操作均通过**:
- SQLAlchemy ORM（参数化查询）
- Alembic 迁移脚本（DDL，非用户输入）
- 例外: `backend/core/migration_guard.py:37` 用 `text(f"...")` 拼接表名，但表名来自调用方硬编码常量，不涉及用户输入

---

## 测试覆盖分析

### 测试套件分布

| 套件 | 文件数 | 实测用例数 | 运行时间 | 依赖 |
|------|--------|------------|----------|------|
| **backend/tests/** | 132 | 1294 个测试函数 | 需 PG | PostgreSQL (testcontainers) |
| **backend/agent/tests/** | 81 | 963 | ~85s | 无外部依赖（自包含） |
| **frontend/** | 73 | 463 | ~12s | vitest + jsdom |
| **tests/** (repo-level) | 15 | 61 | 快速 | 仓库布局检查 |

### 覆盖关键路径

✅ **已覆盖**:
- 状态机转换（`test_phase0_closure.py`, `test_clean_terminal_execution_state.py`）
- 设备租约（`backend/tests/services/` 与 `backend/tests/scheduler/` - 注：无 `api/test_device_leases.py`）
- Agent fencing contract（`backend/agent/tests/test_agent_api_fencing_contract.py`）
- 集成主链（`test_main_chain_happy_path.py`, `test_plan_chain_e2e.py`）
- AEE 路径解析（`backend/agent/tests/test_aee_paths.py`）

⚠️ **需要补充**（从 open issues 推断）:
- 多副本限流器测试（#91 - 进程内状态在多 worker 下的行为）
- 大规模分页测试（#83 - 1000 设备下 `/plan-runs/{id}/devices` 性能）
- Watcher 展锐平台采集（#73 - UNISOC 异常采集缺失）

### CI 测试策略

```yaml
# PR（轻量）: 
  - lint (ruff + ESLint --max-warnings 0)
  - pr-typecheck (tsc)
  - pr-compileall (python -m compileall)
  - pr-agent-tests (backend/agent/tests/)
  - 空行污染检查
  - 脚本版本不可变检查

# main / 定时（全量）:
  - backend-test (pytest backend/tests/ + integration)
  - frontend-check (vitest + tsc + build)
  - docker-build
```

✅ **优势**: PR 快速反馈，main 全量兜底  
**调度策略**（2026-08-08 更新）:
- auto-merge 的 merge commit 不触发 on: push（GITHUB_TOKEN 级联限制）
- `main-ci-backstop.yml` **每天一次**（cron `0 18 * * *` UTC = 本地 02:00）检查 main 尖端，无全量 CI 则 dispatch
- 全量 CI 成功后自动清理已合并分支（GitHub "Automatically delete head branches" 对 auto-merge 不生效，需兜底）

---

## 文档完整性

### 文档结构健康度：**优秀 (A)**

✅ **分层清晰**:
- `docs/DOC-MAP.md`: 文档地图（阅读顺序 + 权威来源）
- `docs/README.md`: 文档中心
- `CLAUDE.md`: 架构不变量 + 关键约定（AI 共享上下文）
- `AGENTS.md`: 开发命令 + AI 约定

✅ **ADR 完整**:
- ADR-0018 至 ADR-0027（10 个决策记录）
- 最新 ADR-0027: 控制面水平扩容
- 追溯完整，无断层

✅ **设计文档对齐**:
- `docs/design/00-system-overview.md`: 部署拓扑
- `docs/design/01-execution-pipeline.md`: Plan→PlanRun→Job 主链路
- `docs/design/07-execution-protocol.md`: 状态机、abort、claim
- `docs/design/2026-plan-c-storage-and-access.md`: 方案 C 存储（与代码一致）

✅ **已验证存在**:
1. `docs/development/environment-variables.md`（2026-07-15 起存在）
2. `docs/design/mockups/plan-execute-v2/`（2026-07-20 引入，含 5 个 HTML + README.md + styles.css）
3. `CLAUDE.md` 的 `@文件名.md` 引用格式（第 5/9/13/51 行）均已独占一行，无问题

### 文档引用检查

✅ **已验证存在**:
- `docs/DOC-MAP.md` 引用的所有文档均存在
- `CLAUDE.md` / `AGENTS.md` 引用的核心文档存在
- ADR 编号连续（0018-0027，无跳号）

---

## 安全审查

### ✅ 已实施的安全措施

1. **认证与授权**:
   - HttpOnly Cookie + CSRF token（ADR-0024）
   - Refresh token 黑名单（`revoked_refresh_token` 表）
   - Production guard: `ENV=production` 强制 `AUTH_COOKIE_SECURE=1`

2. **SQL 注入防护**:
   - 全部通过 SQLAlchemy ORM 参数化查询
   - 无原始 SQL 字符串拼接

3. **敏感信息保护**:
   - 日志不打印密码/token 明文
   - `JWT_SECRET_KEY` 等通过环境变量注入
   - `.gitignore` 覆盖 `.env*`、`opencode.json`、`hosts.ini`

4. **依赖安全**:
   - Dependabot 已启用（最近合入 #199/200 - nanoid/brace-expansion 安全更新）
   - CodeQL 扫描已启用

### ⚠️ 待加固

1. **JWT 轮换机制**（#46 - Epic: 生产 env 硬化）:
   - 当前 `JWT_SECRET_KEY` 为静态值
   - 建议：实施定期轮换 + 多版本支持

2. **HTTPS 强制**（#46）:
   - Production guard 已检查 `AUTH_COOKIE_SECURE`
   - 但实际 Nginx HTTPS 配置需运维验证

3. **Agent 认证**:
   - 当前通过 `AGENT_SECRET` header 认证
   - 建议：考虑 mTLS 或 JWT per-host

---

## 架构债务与技术债

### 高优先级（P0）

无立即阻塞的 P0 债务。

### 中优先级（P1）

1. **#91 - 限流器多副本问题**:
   - **现状**: 限流器为进程内状态，多 worker/副本下实际限额 = 配置值 × 副本数
   - **影响**: 生产多副本部署时速率限制失效
   - **建议**: 迁移到 Redis 计数器 或 引入 kong/nginx rate-limit

2. **#83 - 大列表分页缺失**:
   - **现状**: `GET /plan-runs/{id}/devices` 无分页，1000 设备下响应过大
   - **影响**: 页面加载慢，浏览器卡顿
   - **建议**: 引入 offset/limit 分页 + cursor-based pagination

3. **#60 - agent_api.py 模块拆分**:
   - **现状**: 单文件承载 claims/runtime/ingest/control 四类路由
   - **影响**: 可维护性差
   - **建议**: 按职责拆分为 4 个子模块

### 低优先级（P2）

4. **#84 - 内联 schema 移入 schemas 包**:
   - **现状**: `agent_api.py` 有 29 个内联 Pydantic schema
   - **影响**: 代码重复，无法复用
   - **建议**: 移入 `backend/api/schemas/agent.py`

5. **#85 - pyupgrade 风格债务**:
   - **现状**: ~2378 处 ruff UP 族告警（`Optional[X]`→`X|None`、`Dict`→`dict`，2026-08-09 实测）
   - **影响**: 仅风格，无功能问题
   - **建议**: 分批改写，避免淹没真实信号

6. **#73 - 展锐异常采集**:
   - **现状**: AEE Reconciler 仅支持 MTK，UNISOC 平台缺失
   - **影响**: Z2581/Z2582 等展锐机型无崩溃信号
   - **建议**: 研究展锐平台日志路径，扩展 Reconciler

---

## 容量与扩展性

### 当前目标（ADR-0026）

- **当前规模（生产实测 2026-08-09）**: 20 台 ONLINE host，319 台 ONLINE device（总注册 398）
  - 注：ADR-0026 未明确定义"Phase 1 20/400"口径，此处为生产实际数据
- **目标规模（Phase 2 规划）**: 60 host, 1000 device
  - 阻塞因素：#75 - host 库存采购
  - 准备工作：#105 - host 调度维度承载力，#106 - device 执行维度承载力

### 已实施的扩容措施

✅ **准入队列**（ADR-0026）:
- PlanRun 状态新增 QUEUED / PRECHECK
- Feature flag 控制，schema 就绪

✅ **批量续租**:
- 减少 device_leases 表压力
- O(1) 聚合（避免 N+1 查询）

✅ **OperationScheduler**:
- Host 级并发上限（`STP_MAX_CONCURRENT_OPERATIONS`，默认 5）
- 防止 ADB 风暴

### 待优化

⚠️ **#76 - OperationScheduler 仿真**:
- 需要在 100/200 device 唤醒风暴下独立仿真 permit cap=5 行为

⚠️ **#77 - Terminalization 漂移监控**:
- 计数器漂移率需接入 Prometheus + SLO 告警

---

## 推荐行动项

### 立即执行（本周）

~~1. 创建环境变量文档~~ ✅ **已完成**（2026-07-15 已存在）

~~2. 验证文档引用完整性~~ ✅ **已完成**（mockups 目录、@ 引用均正常）

1. **PR 模板增加类型同步检查项**
   ```markdown
   - [ ] 如修改 Pydantic schema，已同步更新 `frontend/src/utils/api/types.ts`
   ```

2. **修复或移除过时工具**
   - `tools/ci_check_migrations.py` 不支持合并迁移的 tuple down_revision
   - 要求已删除的 tool/workflow 模型
   - 虽然当前不在 CI 中，但建议修复或删除避免混淆

### 近期排期（本月）

4. **技术债务 Sprint**（选择 2-3 项）:
   - #91 - 限流器迁移 Redis（P1，影响多副本部署）
   - #83 - 大列表分页（P1，影响 1000 设备目标）
   - #60 - agent_api.py 拆分（P1，提升可维护性）

5. **容量准备**:
   - #76 - OperationScheduler 仿真
   - #105 - Host 调度承载力评估
   - #75 - 协调 host 采购（阻塞因素）

### 长期规划（本季度）

6. **可观测性部署**（Epic #3）:
   - #77 - Prometheus + terminalization 漂移 SLO
   - 接入 Grafana dashboard

7. **生产加固**（Epic #1, #46）:
   - JWT 轮换机制
   - HTTPS 强制 + Nginx 配置验证
   - Agent mTLS 认证

8. **测试与 CI 加固**（Epic #2）:
   - #169 - 夜间真实设备 E2E（项目成熟后启动）
   - 多副本限流器集成测试
   - 前端类型自动生成（openapi-typescript）

---

## 结论

**总体评价**: 项目架构扎实，文档完善，开发流程规范，处于健康的演进状态。

**核心优势**:
- 架构设计清晰，ADR 追溯完整
- 代码质量高，CI 门禁严格
- 测试覆盖广，状态机实现正确
- 方案 C 存储已落地，路径管理统一

**主要风险**:
- 技术债务积压（7 项），需排期清理
- 容量扩展受 host 采购阻塞
- 多副本部署存在限流器失效风险

**下一步**:
1. 为 PR 增加类型同步检查项，并修复/移除过时的 `tools/ci_check_migrations.py`
2. 启动技术债务 Sprint（限流器 + 分页 + 模块拆分）
3. 推进容量评估与采购协调
4. 规划生产加固与可观测性部署

---

## 核对勘误记录

### 修正的主要错误
1. **环境变量文档**: 报告称"不存在"，实际 2026-07-15 已创建且被多处引用
2. **mockups 路径**: 报告称"未验证"，实际 2026-07-20 已存在含 7 个文件
3. **CLAUDE.md 引用**: 报告称"需独占一行"，实际已全部独占一行
4. **迁移 head**: 报告称 z3a4b5c6d7e8 为最新，实际 head 是 o2p3q4r5s6t7（2026-08-07）
5. **提交数统计**: 报告称"最近两周 20 commits"，实际 165+ 非合并提交
6. **VALID_TRANSITIONS 引用**: 报告称"19 处"，实际字面引用仅 6 处（含明确注释的 2 处有意绕过）
7. **表名统计**: 报告称"18/20 表"，实际 26 个唯一表定义（19 单数 / 7 复数；初版误将单数的 revoked_refresh_token 列入复数例外）
8. **文档数量**: 报告称"993 个 Markdown"，实际 git tracked 193 个（993 是 find 连 node_modules 统计）
9. **测试用例数**: 报告估算"~600/~400+/~200+"，实际实测 963/1294/463/61（总计 ~2780）
10. **backstop 频率**: 报告称"每 15 分钟"，实际 2026-08-08 改为每天一次（cron 0 18 * * *）
11. **容量规模**: 报告称"Phase 1 已完成 20/400"，实际生产 20 host / 319 online device，且 ADR 无此口径
12. **租约测试路径**: 报告称 `api/test_device_leases.py`，实际在 `services/` 与 `scheduler/` 目录

### 保留的准确判断
- GitHub 状态（PR/Issue/CI）、贡献者统计、安全审查、架构一致性方向全部准确
- 整体健康度 B+ 评级、技术债务清单、优先级排序合理

---

**审查人**: Claude (Opus 4.8)  
**审查方式**: 静态代码分析 + 文档审查 + GitHub API 查询 + 架构一致性验证  
**审查耗时**: 初审 ~10 分钟（并行任务 + 主进程），修订 ~15 分钟（根据逐项核对反馈）  
**修订依据**: 上一轮逐项核对反馈
