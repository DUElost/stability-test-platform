# ADR-0023: 脚本溯源与观测链路收口

- 状态：Proposed
- 优先级：P0（D1 为隐患修复 / D2-D7 为观测打通）
- 目标里程碑：M3.2
- 日期：2026-05-10
- 决策者：平台研发组
- 标签：脚本溯源, 观测, dispatcher, plan_snapshot, PlanRun, ADR-0020 follow-up

## 背景

ADR-0020 完成 `Plan / PlanStep` 一次性切换，ADR-0021 / ADR-0022 在派发与运行时上加了门禁、abort、心跳聚合三层加固，整条「脚本目录扫描 → Plan 编排 → JobInstance 派发 → Agent 执行 → step_trace / log_signal 上报 → PlanRun 终态聚合」**正向链路**已稳定可跑。

随后于 2026-05-08 ~ 2026-05-09 完成的初步治理（`ddfc585` 脚本扫描按钮 / `e909c1e` `entry_point` 死字段移除 / `768b5ca` abort_reaper grace + active_jobs 富化 / `1b8c55b` retention 级联删除）解决了**入库门面**与**执行侧硬故障**，但「**反向溯源**」与「**幽灵参数回退**」两类缺口未被触及：

1. **反向溯源在前端断裂**。`PlanRun` 详情页的三个观测面（业务流时间线 / 设备矩阵 / 事件流）**全部不显示 `script_name` / `script_version`**：
   - 后端 `GET /plan-runs/{id}/timeline` 已在 `StageStepOut.script_name` 暴露，但 `BusinessFlowTimeline.tsx:221` 只渲染 `step_key`；
   - `GET /plan-runs/{id}/events` 的 `EventOut`（`plan_runs.py:931`）字段集**根本不含**脚本身份；
   - `GET /plan-runs/{id}/devices` 的 `DeviceMatrixItem`（`types.ts:741`）有 `current_step` 但无 `current_script_name`。
   - 后果：用户看到一个 step 失败，无法直接判断「是哪个脚本的哪个版本」失败，只能通过 `step_trace.ref` 跳转回查 plan_snapshot；运维和开发的诊断成本被外化到读取数据库。

2. **`plan_snapshot` 在前端无入口**。`plan_dispatcher_sync._build_plan_snapshot`（`plan_dispatcher_sync.py:147`）已写入完整脚本元数据快照（`script_name / script_version / param_schema / default_params / sort_order / enabled`），但 `PlanRunDetailPage` grep 无 `plan_snapshot` 引用，**事后审计「实际下发了什么」只能查数据库**。

3. **wifi 注入与 snapshot 不一致**。`_inject_wifi_params`（`plan_dispatcher_sync.py:86`）只改写每 Job 的 `pipeline_def`，**不回写到 `plan_run.run_context`**；事后无法回答「这次 PlanRun 给每台设备分配了哪个 SSID」。

4. **Dispatcher 缺失 default_params 时 silent 回退**——这是**最值得优先收口的隐患**：
   - `_fetch_script_metadata`（`plan_dispatcher_sync.py:115`）用 `Script.is_active.is_(True)` 过滤；
   - 若 Plan 引用的 `(name, version)` 在 dispatch 时已被 deactivate，metadata map 缺该键；
   - `_build_lifecycle_from_steps:46` 的 `script_defaults.get(..., {})` 静默回退到 `{}`，注入空 params；
   - **JobInstance 用空参运行**，仅 `logger.warning` 一行，无 422 阻断，无前端告警，无 audit；
   - 复现路径：Plan 创建后某天脚本被 deactivate（或扫描发现 sha 漂移走 conflicts 路径未新建版本），下次 cron / 手动触发 → 幽灵参数运行。

5. **既存 Plan 不知道引用脚本被失活**。`_validate_script_refs`（`plans.py:139`）只在 Plan 创建/更新时拦截，**Plan 列表与详情没有任何「引用了已 deactivate 的脚本」的视觉提示**。

6. **Script 没有反向引用查询**。脚本管理页只能「看自己」，无法回答「这个版本被哪些 Plan 引用、能否安全 deactivate」；运维只能 SQL 直查 `plan_step` 表。

## 决策

### D1 — Dispatcher 缺失元数据时 Fail-fast（两阶段校验）

**位置**：`backend/services/plan_dispatcher_sync.py` + `backend/services/plan_precheck.py`（precheck gate）

**现状与问题**：

当前 Dispatcher 脚本元数据校验只走 `plan_dispatcher_sync._fetch_script_metadata`，该函数用 `Script.is_active.is_(True)` 过滤。若 Plan 引用的 `(name, version)` 在 dispatch 时已被 deactivate，metadata map 缺该键，`_build_lifecycle_from_steps:46` 的 `script_defaults.get(..., {})` 静默回退到 `{}`，注入空 params，**JobInstance 用空参运行**。

手动派发（`POST /plans/{id}/run`）的实际调用链为：
1. `prepare_plan_run` 创建 PlanRun 行 → SAQ 入队 `precheck_and_dispatch_task`
2. `plan_precheck._drive_dispatch_gate` 异步执行 precheck gate（verify / sync / re-verify）
3. gate 通过后调用 `complete_plan_run_dispatch(plan_run_id, db)` 物化 JobInstance 行 —— **此处就是 D1 要守卫的元数据校验点**

**阶段 1（prepare 时，同步拒绝，不创建 PlanRun 行）**：

`prepare_plan_run` 在 `validate_pipeline_def` **之前**执行 `_fetch_script_metadata` keys 完整性校验：
- 若实际查到的 `(name, version)` 集合 ≠ 输入 PlanStep 的 keys 集合 → `raise PlanDispatchError` 并带 `missing` 列表
- `POST /plans/{id}/run` 端点捕获 `PlanDispatchError` → 返回 HTTP 400（**不创建 PlanRun 行**），与现有 `@router.post("/plans/{plan_id}/run")` 的错误处理一致（`plans.py:499`）
- `_build_lifecycle_from_steps` 把 `script_defaults.get(..., {})` 改为下标 `script_defaults[key]`——keys 完整性已由上游保证，缺失即 `KeyError`，符合 fail-fast

**阶段 2（dispatch 时，异步兜底，创建 FAILED PlanRun 供审计）**：

手动派发在 `prepare_plan_run` 与 `complete_plan_run_dispatch` 之间存在时间窗口（约 10-60s，取决于 precheck gate 的 verify/sync/re-verify 耗时）。在此期间脚本可能被 deactivate。为此在 `complete_plan_run_dispatch` 内部**再次执行** `_fetch_script_metadata` keys 完整性校验：
- 若此时校验失败 → **不回退 PlanRun 行**（行已存在，前端正在观测），而是：
  - 设置 `plan_run.status = 'FAILED'` + `ended_at`
  - 写入 `plan_run.result_summary = {"dispatch_failed": True, "missing_scripts": [...]}`
  - 写入 audit log
  - PlanRun 列表 / 详情页可见失败原因，便于运维追溯

两阶段均复用 `PlanDispatchError` 异常类，该异常已有 `missing_scripts` 属性承载缺失列表。

**不引入新枚举**：复用既有 `FAILED` 终态 + `result_summary` 子字段，与 ADR-0021 D2 一致。

### D2 — 观测端点统一暴露脚本身份

**位置**：`backend/api/routes/plan_runs.py`

**改动**：

1. `EventOut` 新增 `script_name: Optional[str]` / `script_version: Optional[str]`。`category='step'` 类事件从 `plan_snapshot.steps` 字典查表填充（用 `(stage, step_key)` 索引，不需 join）；其他类事件留 `None`。
2. `DeviceMatrixItem` 新增 `current_script_name: Optional[str]` / `current_script_version: Optional[str]`，从 `plan_snapshot.steps` 按 `current_step`（= step_key）查表派生，不需要新查询。
3. `StageStepOut` 已有 `script_name`，本切片**追加 `script_version`**，与 EventOut / DeviceMatrixItem 字段命名对齐。
4. 兜底：当 `plan_snapshot` 不可解析时（早期遗留 PlanRun）字段返回 `None`，前端显示 `—`，不报错。

**契约不变量**：所有新字段 `Optional`，前端可向前兼容；现有契约字段不动。

### D3 — 前端观测组件消费脚本身份

**位置**：`frontend/src/components/plan-run/`

**改动**：

1. `BusinessFlowTimeline.tsx:202-232` 的 step row：在 `step_key` 旁追加灰色 chip `script_name@version`；标签放 step row 末尾，避免压缩 succeeded/total 计数。
2. `DeviceMatrixCard.tsx`（表格视图）：`current_step` 列展示从 `step_key` 改为 `step_key · script_name@version`（缩略图视图保留 `step_key` 紧凑显示）。
3. `DeviceDetailDrawer.tsx:111-125` `KvList`：「当前步骤」行扩展为「`step_key`（`script_name` v`script_version`）」并做 monospace。
4. `PipelineStepInspector.tsx`（编辑态）的「在脚本管理中编辑参数」链接做 deep-link：`/scripts?name=X&version=v`。

**实施前提（必须在 C5 之前完成）**：

- **snapshot 扩展 `nfs_path`**：当前 `_fetch_script_metadata`（`plan_dispatcher_sync.py:123`）只取 `default_params` / `param_schema`，`_build_plan_snapshot`（`plan_dispatcher_sync.py:147`）也不写 `nfs_path`。要在 DeviceDetailDrawer 展示脚本路径，需**先修改 `_fetch_script_metadata` 和 `_build_plan_snapshot`**，为每个 step 写入 `nfs_path` 字段。此改动属于 C1（D1 dispatcher 改动）的一部分，不拆分新切片。

- **ScriptManagementPage 需支持 URL 查询参数**：当前 `ScriptManagementPage`（`frontend/src/pages/scripts/ScriptManagementPage.tsx:11`）不读取 `?name=X&version=Y` 查询参数，也不支持根据 query 自动筛选/定位/展开版本。在 D3 的 deep-link 完全生效之前，需先给 ScriptManagementPage 增加 URL 参数解析逻辑（作为 C7 D8 前端改动的一部分）。在未实现之前，deep-link 仍跳转到脚本管理页，但不会自动定位到指定版本。**此 gap 不影响 C5 合入，但需在 C7 处收口**。

**类型同步**：`frontend/src/utils/api/types.ts` `PlanRunEvent` / `DeviceMatrixItem` / `StageStep` 三处补可选字段，命名与后端一致。

### D4 — PlanRun 详情页 `plan_snapshot` 浏览面

**位置**：`frontend/src/pages/execution/PlanRunDetailPage.tsx` + 新建组件 `PlanSnapshotDrawer.tsx`

**改动**：

1. `PlanRunTopbar` 右侧加按钮「查看快照」（图标 + 文字），打开 right Drawer。
2. Drawer 内容：
   - 顶部：`Plan 名称` / `failure_threshold` / `patrol_interval_seconds` / `watcher_policy`；
   - 步骤列表按 `(stage, sort_order)` 排序，每行折叠卡片：`step_key` / `script_name@version` / `timeout_seconds` / `retry` / `enabled` / `default_params`（展开） / `param_schema`（展开）；
   - 底部 system note：「快照由 `prepare_plan_run` 写入，与当时 Script 表元数据一致；后续 Script 升级不影响此快照。」
3. **不**为 snapshot 增加新端点——`GET /plan-runs/{id}` 已返回 `plan_snapshot` 字段，前端从 `useQuery(['plan-run', id])` 直接读。
4. `wifi_assignments`（D5 写入）若存在则在 Drawer 末尾新增一节展示 `device_id → ssid / pool_name` 表格。

### D5 — wifi 注入回写 `run_context.wifi_assignments`

**位置**：`backend/services/plan_dispatcher_sync.py` + `backend/services/plan_dispatcher.py`

**改动**：

1. `complete_plan_run_dispatch` 在创建完 JobInstance 行后，若 `wifi_allocations` 非空，把每 device 的 `{ssid, pool_name, pool_id}` 写入 `pr.run_context["wifi_assignments"][str(device_id)]`。`password` **不入** `run_context`（避免审计面泄露）；snapshot 看不到密码，但 `JobInstance.pipeline_def` 仍持有运行所需的实际值（仅 Agent 可读）。
2. SQLAlchemy `JSON` 列变更 in-place 不会触发 dirty，需 `flag_modified(pr, "run_context")` 显式标脏。
3. `_build_plan_snapshot` 不再修改——snapshot 是「派发前的设计」，wifi 是「派发时的分配」，语义独立。

### D6 — PlanList 健康度

**位置**：`backend/api/routes/plans.py` + `frontend/src/pages/orchestration/PlanListPage.tsx`

**改动**：

1. `GET /plans` 列表查询补一次性 LEFT JOIN：以 `plan / plan_step / script` 三表 join，按 plan.id 分组聚合 `COUNT(*) FILTER (WHERE script.id IS NULL OR script.is_active = false)`。`PlanOut` 新增 `inactive_script_refs: int = 0`。
2. `GET /plans/{id}` 详情补 `inactive_script_refs: int` + `inactive_steps: list[{step_key, script_name, script_version}]`，让 PlanEditPage 能精准标红问题步骤。
3. 前端 `PlanListPage` 在卡片右上角：`inactive_script_refs > 0` 时渲染红色 badge「N 个引用失活」，hover 显示 tooltip 列表。`PlanEditPage` 顶部 banner：amber 提示「该 Plan 引用了 N 个已停用的脚本版本，无法直接发起测试」+ 在 `PlanCanvas` 把对应步骤卡片描红边。
4. `_validate_script_refs`（创建/更新）保持 422 拦截不变。

### D7 — Script 反向引用查询端点

**位置**：`backend/api/routes/scripts.py` + 新增端点

**改动**：

1. 新增 `GET /api/v1/scripts/{name}/versions/{version}/usage`，返回 `{script: {...}, plan_count: N, plans: [{id, name, step_keys: [...]}]}`。实现：`SELECT plan.id, plan.name, array_agg(plan_step.step_key)` 走 `plan_step JOIN plan` 按 `(name, version)` 过滤分组。
2. `ScriptManagementPage` 卡片展开行追加「被 N 个 Plan 引用」链接，点击触发查询 + 弹小窗显示 plan 列表（每行可点击跳到 `/orchestration/plans/{id}`）。
3. `ScriptVersionDialog` 失活按钮（D8 顺带做）调用此端点：若 `plan_count > 0` 弹二次确认 + 列出受影响 Plan，要求勾选「我已知悉这些 Plan 将无法直接派发」。

### D8 — ScriptManagementPage 失活/复活按钮（顺带）

`DELETE /api/v1/scripts/{id}` 已存在但前端 0 调用点。本切片暴露：

1. 卡片操作区追加红色「停用」按钮（active 时）/ emerald「复活」按钮（inactive 时——`api.scripts.list` 加 `?is_active=false` 才能看到失活版本，需要顶部新增「显示已停用」toggle）。
2. 失活前调 D7 的 usage 端点二次确认。
3. 复活直接走 `PUT /scripts/{id}` `is_active=true`（已在 ScriptUpdate schema 中允许）。

## 实施切片

| Commit | 范围 | 单测 |
|---|---|---|
| **C1** | **D1 风险点收口**：`_fetch_script_metadata` keys 完整性校验 + `_build_lifecycle_from_steps` 改下标 + dispatcher 三处入口失败回写 + `prepare_plan_run` 早期校验。`plan_dispatcher.py`（async）同步对偶。 | pytest 5 cases：① 全部脚本存在 active → 派发成功（回归）② 引用脚本被 deactivate → `PlanDispatchError`，PlanRun 状态 FAILED + result_summary.missing_scripts 列出键 ③ 引用脚本完全不存在 → 同 ② ④ 部分缺失部分存在 → 失败列表精确 ⑤ async 对偶单测 |
| **C2** | **D2 后端观测端点字段扩展**：`EventOut.{script_name,script_version}` + `DeviceMatrixItem.current_script_{name,version}` + `StageStepOut.script_version`。从 `plan_snapshot.steps` 查表填充，category != 'step' 时 None。 | pytest 6 cases：① events 端点 step 类事件含脚本身份 ② events 端点 log_signal/audit/trigger 类事件字段为 None ③ devices 端点 current_step 命中 snapshot → 含脚本身份 ④ devices 端点 current_step 不在 snapshot（边界）→ None ⑤ timeline 端点 StageStepOut 含 version ⑥ plan_snapshot 缺失（早期遗留 PlanRun）→ 字段全 None，不抛错 |
| **C3** | **D5 wifi 回写**：`complete_plan_run_dispatch` 写入 `run_context.wifi_assignments`（不含 password）+ `flag_modified` + audit。 | pytest 3 cases：① 含 connect_wifi 的 Plan → run_context.wifi_assignments 按 device_id 索引 ② 不含 connect_wifi → 字段不写入 ③ 资源池满 fallback → wifi_assignments 不写 + warning |
| **C4** | **D6 + D7 + D8 后端**：PlanOut.inactive_script_refs（list 端点 1 次 join 聚合）+ Plan 详情 inactive_steps 列表 + `GET /scripts/{name}/versions/{version}/usage` 新端点 + `PUT /scripts/{id}` is_active 切换无新逻辑（已支持）。 | pytest 6 cases：① 全 active → inactive_script_refs=0 ② 引用 deactivate 版本 → 计数正确 ③ Plan 详情 inactive_steps 列表正确 ④ usage 端点空引用 ⑤ usage 端点多 Plan 多 step_key ⑥ usage 端点不存在脚本 → 404 |
| **C5** | **D3 前端观测组件**：`BusinessFlowTimeline` step row 加 `script_name@version` chip + `DeviceMatrixCard` current_step 列扩展 + `DeviceDetailDrawer` KV 行 monospace + `types.ts` 三处类型补字段。 | Vitest 8 cases：① BusinessFlowTimeline 渲染 chip ② step_key 缺失/snapshot 缺失时 chip 不渲染 ③ DeviceMatrixCard 表格视图含脚本 ④ 缩略图视图保持紧凑 ⑤ DeviceDetailDrawer KV 行扩展 ⑥ 字段为 null 时显示 `—` ⑦ Inspector deep-link 含 version query ⑧ 类型回归 |
| **C6** | **D4 plan_snapshot 浏览面**：`PlanSnapshotDrawer` 新组件 + `PlanRunTopbar` 加按钮 + 步骤折叠卡片 + wifi_assignments 节。 | Vitest 5 cases：① 按钮打开 drawer ② 步骤排序 + 字段渲染 ③ default_params/param_schema 展开 ④ wifi_assignments 节存在 ⑤ snapshot 缺失 fallback 显示空态 |
| **C7** | **D6 前端 + D7 前端 + D8 前端**：`PlanListPage` badge + tooltip + `PlanEditPage` banner + 描红 step + ScriptManagementPage 卡片「被 N 个 Plan 引用」链接 + 弹窗 + 失活/复活按钮 + 「显示已停用」toggle。 | Vitest 9 cases：① PlanList badge 数量 ② 0 时不渲染 ③ tooltip 列表 ④ PlanEdit banner ⑤ 描红 step ⑥ Script 引用计数链接 ⑦ 引用列表弹窗 ⑧ 失活二次确认（含 plan_count > 0 的强制勾选）⑨ inactive toggle 切换 |
| **C8** | **集成测试 + 文档**：端到端 `tests/test_script_to_plan_to_observation.py`：扫描脚本 → 创建 Plan → 触发 PlanRun（mock dispatch）→ 拉 timeline + events + devices 三端点，断言能从 step_trace 反向溯源到 `script_name@version`。CLAUDE.md changelog 追加 ADR-0023 段。 | 1 集成 case + 文档 |

依赖关系：C1-C4 后端串行 → C5/C6/C7 前端可并行（依赖 C2/C4 后端字段就绪）→ C8 收尾。

## 后果

### 收益

- **幽灵参数 bug 根治**：deactivate 脚本后再触发的 Plan 立即在 prepare 阶段 422，不会进入运行态，失败语义诚实。
- **观测面闭环**：从 PlanRun 详情页任一观测视图（时间线 / 矩阵 / 事件 / 抽屉）都能直接读出「哪个脚本的哪个版本」，诊断不再需要数据库直查。
- **审计完整**：plan_snapshot 浏览面 + wifi_assignments 让「这次跑了什么」可追溯到脚本目录与资源池层级。
- **运维双向可见**：从 Plan 看引用脚本健康度（D6），从 Script 看引用 Plan（D7），失活操作有受影响范围预览。
- **零新枚举 / 零新表 / 零迁移**：所有改动复用既有数据结构，前端新增可选字段向前兼容。

### 代价

- **PlanRun 列表查询多一次 LEFT JOIN**：D6 列表端点性能影响在 plan/plan_step/script 三表 < 数千行规模可忽略；真有压力时可缓存 `inactive_script_refs` 到 plan 行（本切片不做）。
- **plan_snapshot 体积**：D4 暴露 plan_snapshot 后前端 query payload 变大（每个步骤约 500 字节）。方案已定：**不 lazy fetch，直接用 `GET /plan-runs/{id}` 返回的 `plan_snapshot` 字段**——D4 已明确"不增新端点"，PlanRunDetailPage 的 `useQuery(['plan-run', id])` 刷新时自然会重建 cache，无需单独 GET。若未来图体积成为问题再评估 lazy fetch。
- **D1 fail-fast 是行为变更**：现网若有「引用 deactivate 脚本但仍想用空参跑」的 Plan（理论上不该有，实际不排除），C1 上线后会立即失败。**上线前必须先跑 SQL 审计**：`SELECT plan_id FROM plan_step ps LEFT JOIN script s ON ps.script_name=s.name AND ps.script_version=s.version WHERE s.id IS NULL OR s.is_active=false` —— 列出所有问题 Plan，联系负责人确认后再合 C1。

### 备选方案被排除

| 方案 | 排除理由 |
|---|---|
| Dispatcher 缺失元数据时降级到 default_params={} + 加 audit | 仍是 silent 行为，运维难发现，且违背「派发即对齐」（ADR-0021 D1）的精神 |
| 在 plan_snapshot 里每步骤冗余存 `current_script_meta` | 与 plan_snapshot.steps 重复，徒增体积；snapshot 已是事实源 |
| 后端在 timeline / events / devices 端点 join `script` 表填脚本身份 | 增加 N 次查询；plan_snapshot 已经是冻结快照，从中查表更准确（升级版本不污染历史 PlanRun） |
| 给 Plan 增加 `frozen_script_refs` 字段拒绝 deactivate | 反向锁会导致脚本生命周期管理被 Plan 绑死；ADR-0020 已约定脚本与 Plan 解耦，靠 plan_snapshot 切断时间线依赖 |
| 给 PlanRunStatus 加 `DISPATCH_FAILED` 终态 | 与 ADR-0021 D2 一致：状态机膨胀蔓延到 aggregator/recycler/前端，复用 `FAILED` + `result_summary.dispatch_failed` 即可 |

## 上线 / 回滚

- **上线顺序**：C1 单独发版（含 SQL 审计与公告）→ C2/C3/C4 后端串行 → C5/C6/C7 前端并行 → C8 收尾；C1 与其他切片之间至少留 24h 观察 Prometheus `stability_plan_run_terminal_total{status="FAILED"}` 走势。
- **回滚**：C1 回滚 = revert dispatcher 改动；行为退化到 silent fallback，安全但不期望。其他切片均为新增字段 / 新组件 / 新端点，回滚即 revert，无数据迁移。
- **Feature flag**：不设。所有改动均为契约扩展或行为修复，无渐进式开关价值。

## 实施注意事项（review findings）

以下问题在本文档定稿时已识别并决定处理策略：

1. **D1 两阶段校验**（§D1）：已明确"阶段 1 在 prepare 时 400 拒绝（不创建 PlanRun 行）/ 阶段 2 在 `complete_plan_run_dispatch` 时 FAILED 审计（PlanRun 行已存在，供前端观测）"，消除了原文档"回写 result_summary 但又要避免先建 PlanRun 行"的矛盾。

2. **plan_snapshot 缺少 `nfs_path`**（§D3 实施前提）：当前 `_fetch_script_metadata`（`plan_dispatcher_sync.py:123`）只取 `default_params` / `param_schema`，`_build_plan_snapshot`（`plan_dispatcher_sync.py:147`）也不写 `nfs_path`。D3 要在 DeviceDetailDrawer 展示脚本路径，须在 C1 中修改 `_fetch_script_metadata` 和 `_build_plan_snapshot` 写入 `nfs_path` 字段。

3. **D3 deep-link 需要 ScriptManagementPage 支持 URL 参数**（§D3 实施前提）：当前 `ScriptManagementPage`（`ScriptManagementPage.tsx:11`）不读取 `?name=X&version=Y` 查询参数。deep-link 跳转在 C5 可先不做定位，C7（ScriptManagementPage 改造）时收口。

4. **D4 不 lazy fetch**（§D4 + §代价）：已统一为"直接复用 `GET /plan-runs/{id}` 返回的 `plan_snapshot`，不另增端点，不 lazy fetch"。若未来 payload 体积成为问题再评估。

5. **precheck gate 中的 `complete_plan_run_dispatch`**：D1 阶段 2 的校验需覆盖 `plan_precheck._drive_dispatch_gate`（`plan_precheck.py:374`）中调用 `complete_plan_run_dispatch` 的路径。当前若该路径抛异常，异常会被 `_drive_dispatch_gate` 的 except 块吞为 `precheck_failed/unexpected_exception`（`plan_precheck.py:379`），不会产生 `dispatch_failed + missing_scripts`。须在 `complete_plan_run_dispatch` 内部（而非外层）捕获 `PlanDispatchError` 并写入 `result_summary`。

## 引用 / 关联

- ADR-0020：Plan / PlanStep one-shot migration（建立 plan_snapshot 事实源）
- ADR-0021：脚本内容对齐门禁（建立 PlanRun.run_context.precheck，本 ADR 复用同样的「用 result_summary 子字段表达细分失败原因」模式）
- ADR-0022：Patrol heartbeat 聚合（建立 manual_action 字段，本 ADR 不动）
- 初步治理 commit：`ddfc585` / `e909c1e` / `768b5ca` / `1b8c55b`（构成本 ADR 的前置基线）
- 评估纪要：会话「2026-05-09 链路评估」（本 ADR 是该评估的实施回应）
