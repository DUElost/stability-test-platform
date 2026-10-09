# 人工 UI 可理解性审查：API 可达 vs 人可达

> 日期：2026-10-09（Asia/Shanghai） · 代码基线：本地 `main@89b9dc85`（未 fetch）· 审查方：Claude Code（Opus 5.5）
>
> 问题陈述（来自 owner）：用 agent 调 API 使用平台很顺手，但人工走 UI 时理解问题突出——计划的前置运行内容、
> 日志产物、整体运行情况，都无法只靠 UI 获得。
>
> 方法：静态审查。后端路由 AST 抽取 × 前端调用点与响应字段消费比对；三条人工旅程逐页走查；git 历史追溯。
> **未做**浏览器目视，**未读**生产库、生产接口或凭据（原因见 §7）。所有结论均给出 `file:line`，可按基线复核。
>
> 状态：审查留档，2026-10-10 入库，未登记 DOC-MAP。正文与 §8 是 2026-10-09 当日快照；
> 文中 PR 与 ADR 的后续状态以 GitHub 和 `docs/adr/README.md` 现查为准。

## 0. 结论

判断成立。根因不是笼统的「UI 做得不够」，而是以下六点：

1. **运行日志对人完全不可达。** 后端给出的三条官方取日志路径（实时控制台 / `GET /logs/query` /
   `POST /agent/logs`）在 UI 里一条都走不通。第一条已在 #2400 删除；第二条前端客户端已封装，但没有任何页面调用；
   第三条只认 Agent 机器密钥，浏览器会话根本调不到。
2. **前置步骤（init）在每台设备上的执行情况，数据已到浏览器，却没有渲染。**
   这包括哪步成功、哪步失败、耗时、退出码、报错与输出。
   代码注释把 `GET /plan-runs/{id}` 内嵌的 `jobs[].step_traces` 标为「仓内零消费方」，随后改成不取；
   `timeline` 已返回每个步骤的成功、失败、进行中设备数，页面只画了脚本名。
3. **「这个 Plan 会用什么参数跑」有 4 个视图、4 种口径，没有一个等于实际生效值。** 编辑器的口径对；
   执行页、快照抽屉的口径错；派发预览已算出生效的 lifecycle，结果被前端丢弃。
4. **决定前置等待和日志采集的 Plan 旋钮只能用 API 设，UI 看不到也改不了。**
   包括 barrier、watcher_policy、auto_archive、停滞钟。
   另外，「全局超时」实际是**跑测时长**（到点算成功），UI 却解释成「超时后中止」。
5. **全局视图回答不了三个问题：「现在在跑什么、跑到哪了、哪里要我处理」。**
   「运行 / Run」在不同页面分别指 PlanRun、Job、设备。风险分布统计的是全量历史，却放在「系统运行状态总览」。
6. **这是一条退化链，不是一次性遗漏。** 2026-02 曾有逐步骤树加 xterm 实时日志页。
   06-08 该页作为「孤儿兼容路由」退役，新的 PlanRun 详情页没有补上；08-21 相关组件以「无生产引用」删除；
   09-16 后端推送以「前端无订阅方」删除。每一步单看都合理，叠在一起，人类的观测能力归零，API 却完整保留。
   这就是「agent 顺手、人看不懂」的机制（§3）。

## 1. 方法与覆盖

| 维度 | 做法 | 结果 |
|---|---|---|
| 端点面 | AST 抽取 `backend/api/routes/*.py` + `backend/main.py` 全部路由；与 `frontend/src` 字面量及 `utils/api` 方法的页面调用做比对 | 非 Agent 路由 200 条；**26 条没有任何页面调用**（另含根路径 `/`）。其中 5 条属机器专用，**21 条是人类相关能力**（附录 A） |
| 字段面 | 对「已请求但未渲染」的响应字段逐个 grep 前端消费点 | 7 组关键字段已返回却未渲染（附录 A 末） |
| 旅程 | A：Plan 前置内容 → B：日志 / 产物 → C：整体概况，逐页读组件 | 见 §2 |
| 历史 | `git log -S` 追溯被删除的人类观测面 | 见 §3.1 |

严重度口径：**高**＝人无法通过 UI 拿到完成工作所需的事实，或会被误导做出错误判断；
**中**＝能拿到，但需要跨页拼接或容易误读；**低**＝局部瑕疵。

## 2. 发现

### A. 计划的前置运行内容（这个 Plan 跑前做什么，每台设备做成没有）

**A1【高】逐设备的步骤执行证据在 UI 上完全空白，而数据已到浏览器门口**

- 现象：设备抽屉只有「当前步骤 / 脚本」两行（`frontend/src/components/plan-run/DeviceDetailDrawer.tsx:190-201`）。
  某台设备 init 里 `check_device → ensure_root → flash_firmware → …` 各自的成败、耗时、退出码、报错、输出，全部看不到。
- 证据：
  - `GET /runs/{job_id}/steps`（`backend/api/routes/runs.py:276`）返回 StepTrace 的 status / exit_code / error_message / 起止时间，**前端零调用**；
  - `GET /plan-runs/{id}` 内嵌的 `jobs[].step_traces`（`backend/services/plan_run_catalog.py:160-172`）
    字段含 `output / error_message / exit_code / metadata`（`backend/api/schemas/plan_run.py:31-45`）。
    前端注释自述「仓内零消费方」（`frontend/src/utils/api/planRuns.ts:105`），详情页用 `includeJobs:false` 不取
    （`frontend/src/hooks/plan-run/usePlanRunDetailData.ts:57`）；
  - 事件流只收录**失败**的 step（`backend/services/plan_run_event_feed.py` 文件头）。成功步骤在人面前只剩一条合成的「INIT 完成」。
- 影响：刷机、装包、推资源这类长前置步骤，人判断不了「卡在哪一步、哪台失败、为什么」，只能找人查库或调 API。
- 最小修法：在抽屉里加「执行步骤」分区，直接消费 `/runs/{job_id}/steps`；失败步骤展开 error_message。纯前端改动。

**A2【高】业务流进展没画已返回的步骤漏斗，等待态不可见，而且放在页面最底部**

- BusinessFlowStepper 每个阶段只显示「N 活跃 · N 完成」和脚本名（`frontend/src/components/plan-run/BusinessFlowStepper.tsx:59-97`）。
- timeline 已返回以下数据（`backend/api/schemas/plan_run.py:238-266`，`frontend/src/utils/api/types.ts:1809-1838`），
  前端 plan-run 目录对 `device_running` / `duration_seconds` / `aborted_job_count` 零引用：
  - 每步的 `device_succeeded / failed / skipped / running`；
  - 每阶段的 `duration_seconds / device_failed / started_at / ended_at`；
  - `aborted_job_count`。
- ADR-0026 为 permit/barrier 可观测性设计了 Job 子状态 `execution_state`，
  取值 `WAITING_BARRIER / WAITING_EXECUTION_SLOT / EXECUTING_STEP / PATROL_SLEEP`（`types.ts:1606-1610`），前端没有渲染。
  设备矩阵只有 `running`，于是「已做完 init、在等慢同伴（比如还在刷机）」和「正在执行步骤」在 UI 上看起来一样。
- 详情页区块顺序（`frontend/src/pages/execution/PlanRunDetailPage.tsx:329-399`）：设备矩阵 → 异常仪表盘 → 存储运维概览 →
  DLE → 用例结果 → 去重 → JIRA → **业务流进展** → **派发门禁**。人的心智顺序是「门禁 → init → patrol → teardown → 后处理」，
  页面正好反过来，前置内容沉在最底。
- 最小修法：
  - Stepper 每个步骤加一行「✓23 ✗2 ⟳0」，每个阶段加耗时（纯前端）；
  - 设备行显示「等待同伴 / 等待执行槽位」。`execution_state` 目前只在 `/plan-runs/{id}/jobs` 里，
    `/devices` 矩阵没有，所以要么给矩阵加这一列（小后端改动），要么前端合并 `/jobs`；
  - 把 Stepper 和门禁摘要上移到设备矩阵之前（纯前端）。

**A3【高】「会跑什么参数」：4 个视图，4 种口径**

| 位置 | 显示什么 | 与实际执行的差异 | 证据 |
|---|---|---|---|
| Plan 编辑器检查器 | step.params ＞ default_params ＞ schema.default | 与派发一致 ✓，但不含派发时的注入 | `components/pipeline/PlanStepInspector.tsx:269-276` |
| 执行 Plan 页步骤列表 | 只有脚本 `default_params`，标「只读」 | 忽略步骤级覆盖（#508）和 schema.default | `components/execution/plan-execute/PlanStepList.tsx:49-53,69`；`pages/execution/PlanExecutePage.tsx:291-297` |
| 派发驾驶舱（确认发起前） | 只用了 preview 里的设备数 | 后端已算出合并参数后的生效 lifecycle，前端丢弃 | `components/execution/plan-execute/DispatchCockpit.tsx:137-141`；`utils/api/types.ts:1595-1603` |
| PlanRun「查看快照」 | default_params 与 param_schema 两段原始 JSON | 快照里存了 step 的 `params`，却不显示；派发注入不可见 | `components/plan-run/PlanSnapshotDrawer.tsx:111-122`；`backend/services/plan_dispatcher_core.py:566` |

- 实际执行值 = 快照合并（`plan_dispatcher_core.py:314-318`，`merge_effective_params`）加上派发注入
  `inject_wifi_params` / `inject_suite_params`（同文件 `:417-495`）。
- 影响：「执行前确认」和「事后复核」这两个关键时刻，人看到的参数都可能是错的。凡用了步骤级覆盖的 Plan 都受影响，
  例如 install_apk 的 apk_path。
- 最小修法：
  - 抽一个 `effectiveParams(step)` 纯函数，三处共用；
  - 驾驶舱把 `preview.lifecycle` 渲染为「将执行清单」；
  - 快照抽屉显示 `params` 与合并结果，并标注「派发时注入：WiFi / 套件参数」。

**A4【高】影响前置等待和日志采集的 Plan 旋钮只能经 API 设置，UI 看不到**

| 字段 | 作用 | UI 现状 |
|---|---|---|
| `barrier_timeout_seconds` / `barrier_max_wait_seconds` | INIT→PATROL 栅栏的等待预算。模型注释写明：含长耗时前置步骤（自动刷机等）的 Plan **必须显式抬高**，否则先做完的设备会被慢同伴连坐（`backend/models/plan.py:38-47`；#872 / #174 / #2948 有误杀前科） | 编辑器没有字段；快照抽屉不显示 |
| `watcher_policy` | 采集哪些设备日志目录（AEE / VENDOR_AEE / MOBILELOG），能力不可用时走 fail / degraded / skip（`backend/agent/watcher/policy.py:1-60`） | 编辑器没有；快照抽屉只给原始 JSON |
| `auto_archive_interval_seconds` | 自动周期执行 scan→upload→merge（`backend/scheduler/cron_scheduler.py:1013-1030`），决定产物会不会自己出现 | 编辑器没有；快照抽屉不显示 |
| step `stall_seconds` | 步骤停滞钟 | 编辑器自己写着「需经 API 配置」（`PlanStepInspector.tsx:733-735`） |

- API 侧：`PlanCreate` / `PlanUpdate` 都接受这些字段（`backend/api/routes/plans.py:97-105,123-131`）。前端保存时不发送它们
  （`pages/orchestration/usePlanEditForm.ts:275-283`），所以不会被清掉，但人也看不到。
- 影响：同一个 Plan，用 API 建和用 UI 建，行为不同，UI 用户无从得知。刷机类 Plan 如果靠 UI 建，正好踩中栅栏连坐。
- 最小修法：编辑器加「高级（执行与采集）」折叠区。先做只读展示加白话解释（零风险），再按需开放编辑；快照抽屉同步展示。

**A5【中】「全局超时」其实是跑测时长**

- 证据：`backend/agent/pipeline_engine.py:2332`：自该设备 init 完成起，计满 `timeout_seconds` 后以 `timeout` 结束 patrol；
  `:2154` 把 `timeout` 当作成功（COMPLETED）。
- UI 的说法：
  - 编辑器：「全局超时 · 不限」（`components/pipeline/PlanCanvas.tsx:393-408`）；
  - 驾驶舱：「超时：整个 PlanRun 超时后中止；已完成步骤的结果会保留」（`DispatchCockpit.tsx:251`）。
- 影响：
  - 专项最核心的参数「跑多久」被包装成故障兜底；
  - 「从各设备 init 完成起算」这一点完全不可见（前置越慢，结束越晚）；
  - 详情页没有「计划时长 / 剩余时长」。
- 修法：改名为「巡检时长（自 init 完成起计，到点正常结束）」，详情页按设备显示进度。

**A6【低】看不懂每个步骤是干什么的**

- `script` 表有 `description` 列（`backend/models/script.py:33`；前端类型 `types.ts:1017`），但没有任何页面渲染。
  `tool_manifest.json` 的 40 个条目里 0 个带描述，描述没有事实源。
- 内置 8 个专项模板（带说明，位于 `backend/schemas/pipeline_templates/*.json`）只能经 `GET /pipeline/templates` 获取，
  前端客户端已封装但零调用。「新建 Plan」从只有 check_device 的空模板开始（`pages/orchestration/planEditUtils.ts:9-29`）。
- 修法：新建 Plan 时提供模板选择（附说明）；描述先从脚本 docstring 提取，写入 manifest。

### B. 日志与产物

**B1【高】运行日志：官方三条路径在 UI 里全断**

后端对「运行日志在哪」的官方回答写在 `run_log_bundle` 的 409 文案里（`backend/services/job_artifact_download.py:78-85`）：
*Use live console / GET /api/v1/logs/query during execution, or POST /api/v1/agent/logs (SSH) for post-mortem files on the agent host.*

| 路径 | 后端 | 前端 |
|---|---|---|
| 实时控制台 | job / run 房间推送已删（`backend/realtime/socketio_server.py:235-245`，#2400，理由「前端没有任何订阅方」）。注释称行级实时面「本就由落盘路径承担：log_writer → /logs/query」 | 无 |
| `GET /logs/query` | 读控制面 `{LOG_BASE_DIR}/jobs/{job_id}/console.log`，支持按 job、step、级别、关键字、时间、游标查询（`backend/api/routes/logs.py:114-127`；`backend/realtime/log_writer.py:4`） | `logs.queryRuntime` 已封装，**零页面调用** |
| `POST /agent/logs` | SSH 尾读 Agent 主机上的文件（`logs.py:284-285`） | 鉴权是 `verify_agent_secret`（`backend/api/routes/auth.py:50-69`），**浏览器会话不可能调到**；`logs.queryAgent` 同样零调用 |

- PlanRun 的「日志」页签实际是多源事件流（`pages/execution/PlanRunLogsPage.tsx:64`；`components/plan-run/PlanRunTabs.tsx:24-30`），不是日志文本。
- 影响：在 UI 里**读不到任何一行脚本输出**。目前能读日志的，只有拿到 API（以及 Agent 密钥）的自动化 agent。
- 最小修法：
  - 在设备抽屉或步骤行加「查看日志」，用现成的 `components/log/XTerminal.tsx` 渲染 `/logs/query?job_id=&step_id=`，非终态时轮询；
  - 如果要让人能用 `/agent/logs`，需要另做一个 session + admin 鉴权的薄壳端点。这牵涉安全边界（ADR-0024 / ADR-0038 D5 的审计语义），
    需单独评审。

**B2【中】产物可达性零散**

- 设备抽屉的产物区只列 `aee_crash / vendor_aee_crash / bugreport` 三类（`DeviceDetailDrawer.tsx:408-424`），
  与 Agent 上送白名单一致（`backend/services/agent_artifacts.py:33`）。也就是说，按设备能下载的只有崩溃文件。
  运行日志包 `run_log_bundle` 在方案 C 下已不再上送，历史条目下载返回 409。设备维度没有任何「本次运行日志」的入口（见 B1）。
- DLE 归档表只在终态渲染（`components/plan-run/LogEventsCard.tsx:83`；`PlanRunDetailPage.tsx:360`），多天长跑期间看不到。
- DLE 只有 REMOTE / ARCHIVED 状态可下载（`LogEventsCard.tsx:224`）。LOCAL 状态显示的是 Agent 主机本地路径，人拿到也用不上。
- DLE 的 9 个状态里只有 2 个有说明（`LogEventsCard.tsx:52-69`），UPLOAD_FAILED / PULL_FAILED 没有「怎么办」。
- 手动归档 `POST /plan-runs/{id}/archive` 没有 UI 入口。
- 修法：
  - 抽屉在崩溃文件旁加「运行日志」入口（同 B1）；
  - DLE 表在运行中也开放（附「运行中，状态会变」提示）；
  - 状态加中文名和处置建议。

**B3【中】Watcher 降级徽章在重构中丢失：「0 异常」分不清是没崩溃还是没采集**

- `types.ts:2095-2098` 的注释写明：`watcher_capability='unavailable'` 时，PlanRun 详情顶栏显示降级徽章。
- 这个徽章原本在 `WatcherSummaryCard` 里。08-21 该组件随「无生产引用的组件」一起删除（`a58e45e3`），
  替代它的 AnomalyDashboard 没有迁移徽章，当前前端零引用。
- watcher 的默认策略是 DEGRADED：能力不可用时，Job 照常继续（`backend/agent/watcher/policy.py:53-56`）。
- 影响：采集失效的长跑会显示「无异常」，人会得出错误的稳定性结论。
- 修法：数据已经在 `watcherQ` 里，补回徽章即可（纯前端改动，工作量小）。

**B4【中】日志到问题单的链路有断点**

- 实际流程是：去重卡（扫描 / 合并 / 提取）→ 下载 merge xls → 线下复核 → 到「问题追踪」页上传提单。UI 没有把这几步串起来：
  - 详情页没有到 `/issue-tracker` 的链接；
  - 本 Run 的提单历史为空时，没有指引（`components/issues/JiraRunHistory.tsx:95-99`）。
- 失败或中止的 Run 需要人工确认后才去重（`docs/design/01-execution-pipeline.md` §6），但没有任何列表告诉人「哪些 Run 在等你确认」。
- 修法：去重卡加「下一步」指引和跳转；另见 C3。

### C. 整体运行概况

**C1【高】仪表盘答不出「现在在跑什么、跑到哪了、哪里要我处理」**

- 仪表盘的数据源只有 hosts / devices / alerts / host_resources（`backend/api/routes/stats.py:227-231`），没有任何 PlanRun 维度。
  在跑的 Run、排队的 Run、各 Run 的阶段 / 进度 / 异常数，都不在首页。KPI「测试中」统计的是设备数。
- 修法：首页加两块。
  - 「正在运行」表：项目 / Plan / 阶段 / 设备完成比 / 异常数 / 已跑时长对比计划时长；
  - 「排队中」条：附排队原因。

  可复用 `/plan-runs?status=RUNNING|QUEUED` 与 timeline。若需要批量拉取，再加一个后端聚合接口。

**C2【中】同一个词在不同页面指不同的东西**

| 页面 | 文案 | 实际计数单位 | 证据 |
|---|---|---|---|
| 执行记录 | 总数 / 运行中 / 失败 | PlanRun | `pages/execution/PlanRunListPage.tsx:156-181` |
| 测试结果 | 运行总数 / 已完成 / 失败 / 运行中 | JobInstance（设备 × 运行） | `backend/api/routes/results.py:225-240` |
| 测试结果 · 最近运行 | 「Run #N」 | Job id（点击进 `/jobs/N/report`） | `pages/results/ResultsPage.tsx`（最近运行表） |
| 仪表盘 | 测试中 | 设备 | `pages/Dashboard.tsx:194-203` |
| 仪表盘 | 风险分布 | **全量历史**的所有 Job（代码注释却写「近期」） | `results.py:341-358`；`Dashboard.tsx:85` |
| 仪表盘 | 告警 N | 低电量 + 高温 + 错误的设备数；点进去却是通知记录页 | `backend/services/dashboard_summary.py:208`；`Dashboard.tsx:204-228` |
| 仪表盘 | 任务活动趋势（24h） | 24h 内**启动**的 Job 数。只要 24h 内没有新 Job 启动，即使数百台在长跑，图上也是 0 | `stats.py:124-170` |
| PlanRun | 「日志」页签 | 事件流 | `PlanRunLogsPage.tsx:64` |

修法：
- 统一命名：执行（PlanRun）/ 设备任务（Job）/ 设备；
- KPI 标明单位和时间窗；
- 风险分布改为近 N 天；
- 告警卡改为下钻到设备页，并带上筛选条件。

**C3【中】没有「待我处理」的入口**

- 待办散落在各页：失败 / 中止待确认去重、合并完成待提取、上传失败、卡死的 Job、门禁失败、主机降级。没有一个聚合入口。
- 项目卡上的「N 在跑」不能点进对应的 Run 列表。
- 执行记录页的项目筛选不写进 URL，没法分享或深链（`PlanRunListPage` 不读 searchParams）。

**C4【中】机器词汇直接给人看**

- 抽样 10 个后端原因码，前端**0 处翻译**：`admission_requeue_exhausted / device_conflict_at_materialization / unknown_grace_timeout /
  terminal_job_active_lease / devices_unavailable_at_dispatch / wifi_allocation_failed / no_healthy_devices / script_sync_failed /
  host_unreachable / precheck_stale`。这些码一旦落进 PlanRun 的 `result_summary.reason` 或 Job 的 `status_reason`，
  就原样显示，DispatchGateCard 和设备抽屉都没有翻译层。例：
  - `devices_unavailable_at_dispatch`、`wifi_allocation_failed` → PlanRun FAILED reason（`backend/services/plan_dispatcher_sync.py:943,1017`）；
  - `unknown_grace_timeout` → Job FAILED reason（`backend/scheduler/device_lease_reconciler.py:229`）。
- 内部字段名和实现细节写进了文案：
  - `components/pipeline/stepTiming.ts:35`：STP_STEP_WALL_CLOCK_SECONDS；
  - `components/plan-run/DispatchGateCard.tsx:247-249`：SAQ Worker / Redis / precheck reaper；
  - `components/plan-run/DedupReportCard.tsx:202,247`：run_context.upload_summary / extract 缺失；
  - `DispatchCockpit.tsx:220`：effective_slots；
  - `LogEventsCard.tsx:183`：device_log_event；
  - `PlanStepList.tsx:69`：default_params；
  - 快照抽屉直接显示原始键名（`PlanSnapshotDrawer.tsx:185-192`）。
- 主工作页里混入了运维指标：「存储运维概览」的 HDD 使用率、SSD 已清理、溢出次数、Signal 链接健康、不可链、待修复
  （`components/plan-run/ArchiveStatusCard.tsx:95-131`），对测试工程师没有可执行的含义。
- 修法：
  - 前端集中建一张「码 → 中文 / 含义 / 建议动作」映射表，未知码回显原文；
  - 运维指标折叠到「高级 / 运维」，或移到主机页、存储页。

**C5【低】死链与过期指引**

- 「在脚本库中编辑参数」指向 `/scripts?name=`（`PlanStepInspector.tsx:110`），但路由只有 `/script-management`（`frontend/src/router/index.tsx:156`），
  点了就是 404。测试还把这个错误 URL 写死了（`PlanStepInspector.test.tsx:605-611`）。自 `2a86d339`（三栏重构）起一直如此。
  文案本身也不对：已发布版本的 default_params 不可改，参数应该在当前面板里改。
- 设备抽屉写着「明细见上方'业务流时间线'事件流」（`DeviceDetailDrawer.tsx:254`），但本页没有这个区块，事件流在「日志」页签。
- 没有 WiFi 池时，页面提示「先在『资源池』页添加」（`DispatchCockpit.tsx:320`），但 `/wifi` 是 admin-only，
  导航对非 admin 也是隐藏的（`router/index.tsx:183-185`，`layouts/navItems.ts:66-68`）。

## 3. 根因：为什么「agent 顺手、人看不懂」

### 3.1 退化链（git 历史为证）

| 日期 | 提交 | 事件 |
|---|---|---|
| 2026-02-23 | `80c36c66` | 新增 Pipeline 可视化编辑器与 xterm.js 实时终端。TaskDetails 页包含 PipelineStepTree（读 `/runs/{id}/steps`）、XTerminal（逐步实时日志）、LogViewer |
| 2026-05-06 | `6b8cddf1` | ADR-0020：Plan UI 替换 Workflow 页面。新 PlanRun 详情没有承接逐步骤与日志视图 |
| 2026-06-08 | `c7699586` | TaskDetails 作为「孤儿兼容路由」退役。**从此人看不到逐步骤和日志** |
| 2026-08-21 | `a58e45e3` | PipelineStepTree / PipelineExecutionTimeline / WatcherSummaryCard（含降级徽章）以「无生产引用」删除 |
| 2026-09-16 | `1fa1f7d7`（#2400） | 后端 job / run 日志推送以「前端无订阅方」删除 |
| 2026-09 | #2623 | 详情接口内嵌的 jobs（含 step_traces）以「前端零消费方」改为不取 |

每一步的判据都是「机器侧没有消费者」，没有人问过「这项人类能力在别处还有吗」。API 层始终完整，所以 agent 用起来毫无察觉。

### 3.2 其他结构性原因

- **页面按数据源组织，不按人的问题组织。** 详情页 9 个区块各对应一个端点，没有一块回答「这台设备在前置阶段发生了什么」。
- **「生效配置」没有单一投影。** 参数和旋钮的真值分散在 `script` 表、`plan_step`、`plan` 的列、派发时注入这四处，前端各页各取一部分。
- **平台内的 AI 助手也补不上。** 它有 26 个工具，没有步骤轨迹工具，也没有运行日志工具（`backend/services/ai_assistant/tools.py:598-808`）。
  所以「用 agent 很方便」指的是能直连 API 或数据库的外部 agent，不是平台内的助手。

## 4. 建议路线（最小方案优先，P0 不需要改后端）

**P0：纯前端，数据和端点都已现成**

1. 设备抽屉加「执行步骤 + 日志」：`/runs/{job}/steps` + `/logs/query` + XTerminal（A1、B1）。
2. 业务流进展渲染步骤漏斗和阶段耗时，并上移到设备矩阵之前（A2；等待态见 P1 第 6 条）。
3. 生效参数用一个函数算，三处共用；驾驶舱展示 `preview.lifecycle`；快照抽屉显示 `params`（A3）。
4. 补回 watcher 降级徽章（B3）。
5. 修死链和过期指引；「全局超时」改名并加说明（C5、A5）。

**P1：需要一点后端改动，或涉及跨页**

6. 编辑器加「高级（执行与采集）」区，先只读展示 barrier / watcher_policy / auto_archive / stall，快照抽屉同步（A4）；
   设备矩阵加 `execution_state`，显示「等待同伴 / 等待执行槽位」（A2）。
7. 首页加「正在运行 / 排队 / 待处理」三块；统一 KPI 的口径和单位；风险分布加时间窗；告警卡下钻到设备页（C1-C3）。
8. 原因码与 DLE 状态做中文映射，附处置建议；运维指标降级展示（C4、B2）。

**P2**

9. 新建 Plan 时提供模板选择；建立脚本描述的事实源（manifest / docstring）（A6）。
10. 给 `/agent/logs` 做一个人能用的形态（session + admin 鉴权薄壳），需安全评审（B1）。

**治理：防止再次退化**

11. 建一个「人类可达性」棘轮：
    - 后端路由与关键响应字段都要登记，标注为 `ui`（写明消费页面）、`admin-ops` 或 `machine-only`；
    - CI 校验登记与前端实际消费一致；
    - 清理「无消费方」代码前必须先查登记，标为 `ui` 的能力被删，视为回归。

    本次审查用的静态抽取思路（路由 AST × 前端调用点）可以直接复用。

## 5. 做得好的部分（避免误伤）

- 派发驾驶舱：就绪检查、容量估算、重复发起检测、近期同 Plan 运行。
- PlanRun 详情：
  - 排队原因与阻塞设备；
  - 门禁里逐主机的脚本一致性；
  - 设备矩阵的当前步骤 / 连击 / 下次重试；
  - 异常仪表盘与 crash 下钻；
  - 去重四阶段状态与缺口清单。
- 编辑器的参数表单严格遵循 `step.params > default_params > schema.default`。
- 多处刻意区分「未知」和「0」（DedupReportCard、LogEventsCard 的空态）。

这些说明团队对「数据正确性」很严谨。缺的是「把人要回答的问题放在一处」，以及「删东西前确认人还能看到」。

## 6. 附录 A：API-only 能力清单（基线 `89b9dc85`）

### 6.1 端点（人类相关 21 条）

| 端点 | 能力 | 建议归类 |
|---|---|---|
| `GET /api/v1/logs/query` | 运行日志（按 job、step、级别、关键字查询） | **应有 UI** |
| `POST /api/v1/agent/logs` | Agent 主机日志尾读（仅限机器密钥） | 应有人类形态（需安全评审） |
| `GET /api/v1/runs/{id}/steps`、`…/steps/{step_id}` | 单设备步骤轨迹 | **应有 UI** |
| `GET /api/v1/pipeline/templates`、`…/{name}` | 内置专项模板与说明 | 应有 UI |
| `POST /api/v1/plan-runs/{id}/archive` | 手动归档 + scan（admin 可触达退役机） | 应有 UI（admin） |
| `GET /api/v1/runs/{id}/report` | 实时重算报告（UI 只用 cached） | 视需要 |
| `GET /api/v1/runs/{id}/artifacts/{aid}/download` | job 域下载（UI 走 plan-run 域） | 机器 / 脚本入口 |
| `GET /api/v1/hosts/{id}/log-signal-dead-letters`、`POST …/{row_id}/replay` | 日志信号死信的查看与重放 | admin 运维面 |
| `GET /api/v1/log-signals/orphans` | 孤儿日志信号 | admin 运维面 |
| `POST /api/v1/plan-runs/hosts/{id}/reload-config` | 让 Agent 重载配置 | admin 运维面 |
| `POST /api/v1/script-presence/refresh-all` | 全量刷新脚本在位 | admin 运维面 |
| `GET /api/v1/notifications/logs/{id}/deliveries` | 通知投递明细 | admin 运维面 |
| `POST` / `DELETE /api/v1/hosts/{id}/device-intent` | 设备面意图（ADR-0038 v0.3 D9，实施单 #3159） | 设计中 |
| `POST /api/v1/mtbf/runtask/validate` | runtask 校验 | 视需要 |
| `GET /api/v1/jira/runs/{id}`、`…/record` | 提单运行状态与记录 | 视需要 |
| `GET /api/v1/scripts/categories` | 脚本分类 | 低 |

机器专用 5 条：`/health`、`/health/live`、`/metrics`、`/metrics/health`、`POST /api/v1/notifications/webhook`。

### 6.2 已返回但未渲染的字段

- `GET /plan-runs/{id}`：`jobs[].step_traces`（含 output / error / exit_code），已改为不取；
- `GET /plan-runs/{id}/timeline`：步骤漏斗、阶段耗时、`aborted_job_count`；
- `GET /plan-runs/{id}/jobs`：`execution_state`（栅栏 / 槽位等待）；
- `GET /plan-runs/{id}/watcher-summary`：`watcher_capability`；
- `POST /plans/{id}/run/preview`：`lifecycle`；
- `plan_snapshot.steps[].params`；
- `plan_snapshot.plan` 中的 barrier 与 auto_archive 字段。

### 6.3 只能用 API 设置的 Plan 字段

`barrier_timeout_seconds`、`barrier_max_wait_seconds`、`auto_archive_interval_seconds`、`watcher_policy`，以及 step 的 `stall_seconds`。

## 7. 局限与后续

- **没有做浏览器目视。** 本机没有带真实运行数据的隔离栈：stp-dev 已不存在；正在运行的 `stp-b3-client-test-*` 属于其他会话，
  未触碰。生产 UI 和接口需要管理员凭据，未获授权不使用。上述结论均可由源码确定。建议 P0 落地时做一次真实浏览器验收，
  因为 jsdom 测不了布局与下载。
- **没有量化生产影响面。** 按 `prod-db-readonly-diagnose` SOP，需要操作者提供 `stp_ro` 口令，本次未执行。
  以下可选 SQL 按 ORM 定义草拟（`backend/models/*.py`），执行前须先核对 `information_schema`：

```sql
-- A3：使用了步骤级参数覆盖的步骤数（执行页 / 快照抽屉会显示错参数）
SELECT count(*) FILTER (WHERE params IS NOT NULL AND params <> '{}'::jsonb) AS overridden_steps,
       count(*) AS all_steps
FROM plan_step;

-- A4：只能经 API 设置的 Plan 旋钮实际使用情况
SELECT count(*) FILTER (WHERE barrier_timeout_seconds IS NOT NULL)       AS barrier,
       count(*) FILTER (WHERE barrier_max_wait_seconds IS NOT NULL)      AS barrier_max,
       count(*) FILTER (WHERE auto_archive_interval_seconds IS NOT NULL) AS auto_archive,
       count(*) FILTER (WHERE watcher_policy IS NOT NULL
                          AND watcher_policy <> '{}'::jsonb)             AS watcher_policy,
       count(*)                                                          AS plans
FROM plan;
SELECT count(*) FILTER (WHERE stall_seconds IS NOT NULL) AS stall_steps FROM plan_step;

-- B3：近 30 天 watcher 能力分布（"0 异常"可能其实是没采集）
SELECT watcher_capability, count(*)
FROM job_instance
WHERE created_at > now() - interval '30 days'
GROUP BY 1;

-- A6：活跃脚本描述填充率
SELECT count(*) FILTER (WHERE coalesce(description, '') <> '') AS described, count(*) AS active
FROM script
WHERE is_active;
```

- **本次没有审查的范围**：AI 助手页，用户 / 审计 / 设置 / WiFi 等管理页的可理解性，窄屏布局。

## 8. Owner 反馈与修订（2026-10-09 同日）

**优先级调整**：owner 裁定，UI 侧聚焦两件事：「便于确认与修改执行步骤中的参数与设置」和
「执行测试前给出测试计划的各类参数及其含义」。日志查看器（B1 的 UI 修法）不做。

**关于「当初移除是为了防日志累积」的核对**（只陈述事实，不改变上面的裁定）：

- 中心存储侧确实做过存储取舍：方案 C 起运行日志包 `run_log_bundle` 不再上送 NFS，
  历史条目下载返回 409（`backend/services/job_artifact_download.py:76-85`）。
- UI 的移除是另外三件事，提交记录给出的理由都与存储无关：
  - 孤儿路由退役（`c7699586`）；
  - 死组件清理（`a58e45e3`）；
  - 无订阅方的推送删除（`1fa1f7d7` / #2400，理由是空扇出的序列化开销）。
- 控制面仍在逐行落盘：Agent 照常发 `step_log`（`backend/agent/agent_application.py:134`），
  控制面写入 `{LOG_BASE_DIR}/jobs/<job_id>/console.log`（`backend/realtime/socketio_server.py:270-272` → `log_writer`）。
  这些文件只在保留清理删除 PlanRun 时才删（`backend/scheduler/cron_scheduler.py:961-967`）。
  按 2026-09-25 的既有记录，生产保留设置为「保留全部历史」。
- 结论：去掉界面没有省下存储。若担心膨胀，抓手在 `log_writer` 与保留策略，不在 UI。本次未实测磁盘体量。

**新增发现 A7【中】已有的参数说明被藏起来了**：

- 参数表单只把 `param_schema.description` 当作字符串输入框的 placeholder（`components/pipeline/PlanStepInspector.tsx:612`），
  字段一旦有值（通常来自 default_params），说明就看不到；数字、枚举、布尔字段根本不显示说明。
- 种子迁移里约一半带 param_schema 的文件写了 label / description，集中在
  flash_firmware、oobe_skip、flash_preflight、fill_storage、connect_wifi。
  monkey / mtbf / gpu / sleep / powercycle 等族基本没有。
- 已发布版本的 `param_schema` / `default_params` 不可原地改：种子迁移对被 `plan_step` 引用的版本
  直接失败（`docs/development/script-versioning.md`「种子迁移治理」）。
  所以靠「补进 param_schema」来写说明，就意味着发新版本、再重指 Plan。

**处置**：

- C5 死链已修：PR #3630（`fix/plan-step-inspector-script-link`，已合入）。
- Owner 同意后续行动后（同日）：
  - A7 参数说明常显：PR #3634；
  - A5「全局超时」改名巡检时长：PR #3635；
  - B3 采集降级提示：PR #3636；
  - 参数含义事实源：ADR-0060 草稿 PR #3638（draft，待 Owner 审定），载体 issue #3637。
- 参数可理解性的后续规划见会话答复：先定参数含义的事实源（建议与不可变发布单元解耦），
  再按 ADR-0058 走批次。
