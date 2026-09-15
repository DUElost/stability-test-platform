# ADR-0032 v0.8 修订提案：平台路由的完备性、聚合语义与未支持态

Status: proposed
Class: architecture

## Decision

提出 [ADR-0032](../../adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md) **v0.8 修订提案**（不新增 ADR 编号），不改既有方向：

| 编号 | 修订对象 | 性质 / 状态 |
|---|---|---|
| R1 | B1 完备性语义 | **已关闭**（2026-09-15 owner 裁决「改代码对齐 B1」，已实施）——见 [`2026-09-15-scan-completeness-per-host-platform.md`](../bug-fix/2026-09-15-scan-completeness-per-host-platform.md)。本提案不再就 R1 提出修订，只需在修订块中把 B1 的旧函数名同步为新实现。 |
| R2 | D1 `run_merge_all_platforms_sync` 的跨平台聚合语义 | **已关闭**（原前提不成立、已更正；真实缺口=逐平台结果不可观测，已改为纯增量记录并实施）——**无需裁决** |
| R3 | D3「控制面 merge 同一工具」+ B3 验收升格为门禁 | **已裁决**（2026-09-15：降级为待验证假设 + 条件裁决；B3 升为下一里程碑门禁） |
| R4 | D0/D6：`PlatformCollector.detect()` 死接口 + QCOM 未支持态可观测 | **已裁决并实施**（a1 删除 `detect()`；b1 控制面派生 + b3 Agent 留痕） |

触发来源：2026-09-15 设备日志流转只读评估（前端 / 控制面 / Agent / 中心存储四层）。该评估的结论是"决策在场、执行漂移"，因此按仓库纪律走**修订**而非新立 ADR——先例见 [`2026-09-11-three-question-confirmation-e16d6d.md`](../process/2026-09-11-three-question-confirmation-e16d6d.md)：「新建 ADR：放弃……新建会造成同一语义两份权威，应做修订」。

不改动的部分：D0 路由键、D2 两层职责、D4 展锐归档 Agent 化、D5 实施顺序、D6 与 #220 的关系（supersede 方向不变）、D7 归档汇总、D8 Watcher w1、B5 `job_log_signal` extra 契约。

### 裁决记录（2026-09-15，owner）

| 决策点 | 裁决 | 落地状态 |
|---|---|---|
| **D-1a** R3「同一 merge 工具」 | **② 降级为待验证假设 + 条件裁决** | 待 v0.8 修订写入 D3；B3 通过 → 维持「同一工具」，失败 → 引入 `STP_BACKEND_UNISOC_MERGE_PYTHON/_SCRIPT` |
| **D-1b** B3 五项验收定位 | **② 升格为下一里程碑门禁** | 完成前，任何依赖「双平台 merge 均可用」的容量/超时预算结论必须标注未验证 |
| **D-1c** B3 完成窗口 | **② 本里程碑内**（由 D-1b 推得；此项未预先给出推荐，按 D-1b 语义取一致默认） | 窗口内未完成 → R3 由「条件裁决」退回「未决」 |
| **D-2** R4-a | **a1 删除 `detect()`** | **已实施** |
| **D-3** R4-b | **b1 控制面派生（主）+ b3 Agent 留痕（辅）** | **已实施**；b2 留作升级路径 |
| **D-3 细节** b1 判定源 | **② 加显式 helper**（非复用 `dedup_platform_for_device_platform`） | **已实施**：`has_collection_impl`，并用测试钉住二者当前的等价性 |

**D-2 / D-3 的实施内容**（文件级）：

- `backend/agent/aee/collector.py`：协议删 `detect`，收窄为 `platform` + `parse_metadata`；三个 collector 同步删除。
- `backend/core/dedup_platform.py`：新增 `has_collection_impl`（模块定位更新为"控制面平台词表"）。
- `backend/api/schemas/plan_run.py` + `backend/api/routes/plan_runs.py`：`WatcherPlatformBucketOut.reconciler_supported`。
- `backend/agent/job_session.py`：`platform_reconciler_unsupported` 留痕（对照 UNISOC 的 degraded 日志）。
- `frontend/src/utils/api/types.ts` + `frontend/src/components/plan-run/AnomalyDashboard.tsx`：平台分桶显示「平台未支持」。
- `docs/design/2026-device-log-event-implementation-spec.md`：§5.1/§5.2/§5.4 同步。

**影响面**（若裁决采纳）：

- 控制面：`backend/services/dedup_scan.py`（merge 聚合、merge 工具解析）、`backend/tasks/saq_tasks.py`（`run_context.merge_platforms`）。R1 涉及的完备性函数与 `require_platforms` 传参已随 R1 关闭而移除。
- Agent：`backend/agent/aee/collector.py`（协议收口）、`backend/agent/job_session.py`（未支持态归因）。
- 前端：`frontend/src/components/plan-run/`（平台维度与失败态可见，见配套 issue 台账）。
- 文档索引：`docs/adr/README.md` 主表 ADR-0032 版本行、本文件入链（S12 门禁要求 ADR 头部版本 ↔ README 主表一致）。

**本提案不修改 `docs/adr/` 下任何文件**：改 Accepted 正文需 owner 裁决，且当前在窗 Execution #2133 持有 `docs/adr/README.md`。

---

## 拟写入 ADR-0032 的 v0.8 修订块（paste-ready）

> 以下为待裁决文本。裁决通过后追加到 `docs/adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md`，并在修订记录表增一行：

```text
| v0.8 | 2026-09-15 | 平台路由收口：R1 完备性按 host×platform 期望集 / R2 merge 聚合三态化 /
                  R3 UNISOC merge 工具降级为条件裁决 / R4 未支持态与死接口收口 |
```

### R1：完备性判定按「host × platform 期望集」（**已关闭——改代码对齐 B1**）

> **状态（2026-09-15）**：owner 裁决为**改代码对齐 B1**，已实施：`require_platforms`
> 退役，改为 `dedup_scan.scan_completeness(run_id, expected)` 的 (host, platform) 对判定，
> 期望集由 `plan_run_scan_scope.load_expected_scan_platforms` 派生。见
> [`2026-09-15-scan-completeness-per-host-platform.md`](../bug-fix/2026-09-15-scan-completeness-per-host-platform.md)。
> **以下保留为决策依据记录，不再是提案内容**；v0.8 修订块只需把 B1 实现约束行里的旧
> 函数名 `count_hosts_with_scan_artifacts` 同步为 `scan_completeness`（语义不变）。

**问题**：B1 原文为「`count_hosts_with_scan_artifacts`：按 **host 去重**，MTK/UNISOC **分区各自完备性判定** 后分别触发 merge」。实现采用 `require_platforms=DEDUP_PLATFORMS`，语义变成「host 必须同时具备 mtk 与 unisoc 产物才计数」（`backend/services/dedup_scan.py:186-206`，调用点 `backend/tasks/saq_tasks.py:450-454,482-486`）。

而 Agent 侧是**两个 runner 都跑、各自按 serial 过滤**（`backend/agent/scan_runner.py:246-277`）：纯 MTK host 的 UNISOC 工具扫不到 uniview 目录 → 无 fresh `*_org.xls` → 无 unisoc 产物；纯 UNISOC host 反之。于是**纯平台 host 永远不计入 `hosts_done`**，每轮 `scan_task` 烧满轮询预算（默认 300s + near-complete grace）后记 `saq_scan_partial_artifacts` WARNING。

**拟裁决**：完备性单位从「host」改为「(host, platform)」，期望集由 PlanRun 设备表派生——

1. 期望集 = `{(host_id, platform) | 该 host 在本 PlanRun 中持有 ≥1 台 platform 设备}`；`Device.platform` 已是既有列（参照 `backend/api/routes/plan_runs.py:2023` 的平台分桶聚合）。
2. 判定：每个 (host, platform) 在本轮 `since` 水位线后有 ≥1 条该平台 scan 产物即算满足；`hosts_done` = 满足的 (host, platform) 对数，`n_triggered` 同口径。
3. merge 触发沿用 B1「分区各自」语义：按平台分别判断输入集是否就绪，不做跨平台合并。

**不变量（不变）**：部分 host / 部分平台未齐时**仍链后继**——「部分报表优于零报表」是既有有意设计，`saq_tasks.py:500-507` 的注释与本提案一并保留。

**备选（未采纳）**：维持「每 host 双平台齐」并新增 host 级平台能力声明。放弃理由：能力声明是从"事后按目录推断"改为"事前声明"的更彻底修复，但它改变了 Agent→控制面的上报契约（新字段 + 迁移），代价高于本修订要解决的问题；若后续要做，应作为独立 ADR 提案。

### R2：多平台 merge 结果可观测（**原提案前提不成立，已改写并实施**）

> **2026-09-15 更正**：本提案初稿称「`any_ok` 会吞掉单平台真失败」。**该前提经代码核实不成立**——
> `run_merge_sync` 在工具/校验/发布真失败时全部 `raise`
> （`backend/services/dedup_scan.py:402,405,419,425,431,455`），而
> `run_merge_all_platforms_sync` 不捕获异常、直接向上传播，`merge_task` 与手动 merge
> 端点据此失败收敛。空串只有两个来源：**工具未配置**（`:353-356`）与**该平台本轮无 org
> 文件**（`:364-367`）。因此不存在"平台互相掩盖"，`#1527` 的收敛也未被绕过。
> 原稿引用的 `:463-467` 实际只说明 `saw_hard_fail` 变量名与 docstring 措辞失准
> （"含工具失败空串"），**不是缺陷**。

**真实缺口（可观测，非缺陷）**：没有任何地方记录**逐平台** merge 结果。ADR-0032 B1 说
"分区各自产出"，但"UNISOC 这一轮出了报表 / 本来就没有输入 / 被 skip"三者只能靠查中心
目录与 artifact 反推。

**已实施（纯增量，无需裁决）**：`run_merge_all_platforms_sync` 逐平台记录
`ok` / `skipped_failed` / `no_input` 到 `run_context.merge_platforms`，并打
`merge_platforms plan_run=… mtk=ok unisoc=no_input` 日志；**返回字符串与控制流完全不变**，
记录失败不影响 merge 结论（与 `record_scan_archive_state` 同款取向）。测试见
`backend/tests/services/test_dedup_scan_merge.py::test_run_merge_all_platforms_records_*`。

**v0.8 修订块对本项的处理**：不动 D1/B1 语义；可选地在 B1 实现约束中补一句"逐平台结果落
`run_context.merge_platforms`"。原拟裁决项**全部撤回**：

| 原拟裁决 | 撤回理由 |
|---|---|
| `run_merge_sync` 返回三态、把 `no_input` 与 `failed` 拆开 | `failed` 已由 raise 表达；拆空串只改措辞，收益不足抵一次接口变更 |
| 聚合结果结构化 + `merge_task` 平台级失败上报 | 不存在"被吞掉的失败"需要上报 |
| 全平台 `failed` 才 raise | 现状即如此（`merge_task` 对 `""` raise） |

### R3：D3「控制面 merge 同一工具」降级为条件裁决，B3 升格为门禁（**已裁决——照此执行**）

**问题**：D3 记载「控制面 merge：MTK `STP_BACKEND_DEDUP_SCAN_*`；UNISOC **同一 merge 工具**，输入按 B1 子目录」。实现确实对所有平台只读同一个控制面工具（`backend/services/dedup_scan.py:325`）。但 B3 的验收项「分平台 merge 试跑（`mtk`/`unisoc` 子目录）」与「双 merge 产物均发布至 `merge/mtk/`、`merge/unisoc/`」**至今为 `[ ]`**（`docs/adr/ADR-0032-...md:140-146`）。即：该断言**从未被验证**，而 UNISOC org xls 是 15 列 `aeeexp` 格式（`ADR-0032:24`），与 MTK 报表不同构。

**裁决（2026-09-15）**：

- 「同一 merge 工具」由**断言**降级为**待验证假设**，绑定条件裁决：
  - B3 五项验收在约定窗口内完成；
  - **通过** → D3 维持「同一工具」，B3 勾选项作为验收证据入库；
  - **失败**（工具无法合并 15 列 aeeexp）→ 引入 `STP_BACKEND_UNISOC_MERGE_PYTHON` / `_SCRIPT`，`run_merge_sync` 按平台解析工具，D3 相应改写；hot-update 侧补 `STP_AGENT_UNISOC_MERGE_*` 映射（对齐 `backend/services/agent_env_sync.py:83-101` 的既有命名模式）。
- B3 从"P2 spike（可延后）"**升为进入下一里程碑的门禁**：在该项完成前，任何基于「双平台 merge 均可用」的容量/超时预算假设（如 `backend/tasks/saq_tasks.py:37-42` 的 `_MERGE_TASK_SAQ_TIMEOUT`）都必须标注为未验证。
- **不变量（不变）**：合并输入严格按 `dedup/{run}/{platform}/` 分区，禁止跨平台混入同一输入集（D1）。

### R4：未支持态与死接口收口（**已裁决：a1 + b1 + b3，均已于 2026-09-15 实施**）

#### R4-a `PlatformCollector.detect()`：删除还是接线？

**事实**：协议定义了 `detect()`（`backend/agent/aee/collector.py:36`），MTK / UNISOC / QCOM 三个实现都写了，**全仓零调用点**；平台判定实际由 `detect_device_platform` 唯一承担（`backend/agent/device_platform.py:98`，调用点 `backend/agent/job_session.py:339`）。

| 选项 | 做法 | 代价 | 评估 |
|---|---|---|---|
| **a1 删除（推荐）** | 协议收窄为 `platform` + `parse_metadata`；三个实现的 `detect` 一并删除 | 将来若真要"按设备探测选 collector"需重新加回 | 平台判定权威唯一；消除"定义了却从不调用"的第三种状态 |
| a2 工厂化接线 | `get_collector_for_platform` 升级为 `get_collector_for_device(shell_fn, serial)`，内部调 `detect()`，并在 `job_session` **替代** `detect_device_platform` | 每个 Job 多一次 adb 往返；出现**两套平台判定** | 与"同一决策两处描述"的第一性原理冲突 |
| a3 保留现状 + 注释 | 只加"未接线"说明 | — | 保留死接口，等于承认第三态 |

**裁决点 Q1：a1 还是 a2？**（推荐 a1；若选 a2，须同时移除 `detect_device_platform` 的对应职责，不得两套并存）

#### R4-b QCOM「平台未支持」如何可见？

**事实**：`_resolve_reconciler_class` 对 QCOM 返回 `None`（`backend/agent/job_session.py:311-313`），调用方**静默 `return`**（`:341-342`）；对照 UNISOC 在降级启动时有 `platform_reconciler_start_degraded` 日志（`:348-354`）。结果：UI 表现为"这台设备没有异常"，而非"平台未支持"。

| 选项 | 做法 | 成本 | 覆盖 |
|---|---|---|---|
| **b1 控制面派生（推荐）** | 平台分桶已按 `Device.platform` 聚合（`backend/api/routes/plan_runs.py:2012-2119`），为**无采集实现**的平台在 bucket 上标注（如 `reconciler_supported: false`），前端分桶处显示"平台未支持" | 控制面 + 前端各一处；**零 Agent 改动、零新列** | 用户可见；判定源 = 设备登记平台（心跳探测结果） |
| b2 Agent 上报新能力档 | Agent 在无可选 reconciler 时回填 `watcher_capability="unsupported_platform"`；复用**既有通道**（`complete_job` 的 `watcher_summary` → `backend/api/routes/agent_api.py:2927-2929`；巡逻心跳 `:2026-2028`），并在 `_CAPABILITY_SEVERITY` 增一档（`plan_runs.py:2414-2422`）+ 前端文案 | Agent + 控制面 + 前端三处 | 用户可见；判定源 = Agent 实测（更权威，但与 b1 的知识重复一份） |
| b3 仅 Agent 日志 | 在 `:341-342` 静默 `return` 前打一条 `platform_reconciler_unsupported` | 一行 | 控制面/UI 不可见，仅现场排障 |

**裁决点 Q2：b1 / b2 / b3？**（推荐 **b1 + b3**：b1 给用户可见性、b3 给现场排障；b2 作为"将来要按 Agent 实测而非设备登记判定"时的升级路径）

**b1 的判定源建议**：复用 `core.dedup_platform.dedup_platform_for_device_platform`（返回 `None` 即无采集实现），避免新增第三个"支持平台清单"常量。**注意其耦合**：该函数今天同时表达"有无归档分区"，而 b1 需要"有无采集实现"——二者当前等价（QCOM 两者皆无），但语义不同；若评审不接受这层隐含耦合，应在同模块加显式 helper（如 `has_collection_impl`）并注明等价前提。

#### R4-c（已核非缺陷，仅登记）

**UNKNOWN 的平台处理**：路由侧维持"UNKNOWN 保守放行到 MTK"（`backend/agent/aee/collector.py:56`、`backend/agent/job_session.py:308`，理由见 `device_platform.py:11-13`：adb 抖动不应让 MTK 机型漏采）；**事实记录侧已正确**——DLE 的 `platform` 写的是探测真值（`backend/agent/aee/device_log_event_client.py:104,266`；`backend/api/routes/agent_api.py:2365,2507,2640`），UNKNOWN 不被静默改写为 MTK。因此本项不需要代码变更，只需在 ADR 中把"路由放行"与"事实记录真值"两件事显式区分，避免后来者把二者混淆后误改。

### 需同步修订的既有条目

| 既有条目 | 处理 |
|---|---|
| B1（混平台 PlanRun / merge） | **只同步实现约束行的旧函数名**为 `scan_completeness`（语义已由代码对齐，R1 已关闭）；「路径分区 + 每平台一次 merge」不变量保留 |
| D3（env 键与 fleet 键） | 按 R3 条件化；增加 unisoc merge 键的**条件性**条目 |
| B3（P2 spike 验收） | 按 R3 升格为门禁，保留原五项勾选项 |
| B5（UI 按 platform 分桶） | 不变；但 DLE 终态视图尚未落平台列 → 归 issue，不在 ADR 内 |

### 验收判据（若裁决采纳）

| 编号 | 判据 | 验证手段 |
|---|---|---|
| AC-R1-1 | ~~纯 MTK / 纯 UNISOC / 混平台三型 host：一轮 scan 一次轮询即达 complete~~ | **已关闭**：由 [bug-fix note](../bug-fix/2026-09-15-scan-completeness-per-host-platform.md) 的三型用例覆盖（单元/集成级；真实 fleet 观测仍缺，该 note 已声明） |
| AC-R1-2 | ~~纯平台 run 不产生 `saq_scan_partial_artifacts`~~ | **已关闭**，同上 |
| AC-R2-1 | ~~构造 mtk `ok` + unisoc `failed`：整体非 `ok`~~ | **已关闭**：`failed` 由 raise 表达，无需聚合断言；改由 `test_run_merge_all_platforms_records_per_platform_outcomes` 断言逐平台落 `run_context.merge_platforms`（`mtk=ok` / `unisoc=no_input`） |
| AC-R2-2 | ~~单平台 `no_input` 不报错、不写 failed~~ | **已关闭**：`test_run_merge_all_platforms_records_skipped_failed` |
| AC-R3-1 | B3 五项全绿，含 UNISOC org xls 进 merge 且 `merge/unisoc/` 有产物 | spike + 证据 |
| AC-R3-2 | spike 失败路径下 `STP_BACKEND_UNISOC_MERGE_*` 生效、按平台选工具 | 单测 + 配置回归 |
| AC-R4-1 | `collector.py` 无 `detect` 符号残留；`grep -rn "\.detect(" backend/agent/` 无 collector 命中 | 静态 |
| AC-R4-2 | QCOM 设备进 PlanRun → `watcher_platform_unsupported` 出现且前端可见 | 单测 + 前端用例 |

---

## Alternatives

- **新立「设备日志流转统一治理 ADR」**——放弃。ADR-0025 / 0028 / 0032 已覆盖该域全部方向级决策；第 4 份会造成同一语义多份权威，复利为负。仓库先例已把这条路判为「放弃」（`2026-09-11-three-question-confirmation-e16d6d.md:38-43`）。
- **直接改写 `docs/adr/ADR-0032-...md` 为 v0.8**——放弃（本 PR）。改 Accepted 正文需 owner 裁决与 S12 索引同步，且在窗 #2133 持有 `docs/adr/README.md`，并发改写共享索引违反仓库串行纪律。
- **R1 采用「host 平台能力声明」**——放弃（本提案）。它改的是 Agent→控制面上报契约，属独立方向，代价高于本修订目标；已登记为备选并注明复议入口。
- **R2 原方案（把 `no_input` 与 `failed` 拆成返回值三态）**——撤回。经核实 `failed` 已由 `raise` 表达，`any_ok` 不会吞掉真失败；拆空串只改措辞。改为在聚合层记录逐平台结果（已实施）。**另一条同样撤回**：「任一平台失败即整体失败」——纯 MTK fleet 会因 UNISOC 无输入每轮报错，且现状本就不是这样。
- **R3 直接改用「每平台独立 merge 工具 env」**——放弃（暂）。在无 B3 证据前引入新键，会把一个未验证假设变成两个未验证假设（键 + 工具）。
- **把全部待改善项（含前端展示、文档漂移）写进 ADR**——放弃。任务形内容进 ADR 必然过期，且属负复利；归 issue 台账（见 [`2026-09-15-device-log-flow-issue-backlog.md`](../process/2026-09-15-device-log-flow-issue-backlog.md)）。

## Verification

- **R1 已实施并验证**（2026-09-15）：`TESTING=1 JWT_SECRET_KEY=test-secret .venv/bin/python
  -m pytest backend/tests/services/test_dedup_scan_merge.py
  backend/tests/services/test_plan_run_scan_scope.py
  backend/agent/tests/test_saq_scan_pipeline.py -q` → 80 passed；扩大范围（dedup 链 +
  scan runner + abort race）→ 233 passed。代码改动与用例清单见
  [`2026-09-15-scan-completeness-per-host-platform.md`](../bug-fix/2026-09-15-scan-completeness-per-host-platform.md)。
- **R2 已改写并实施**（2026-09-15）：原前提（`any_ok` 吞掉单平台真失败）经代码核实不成立，
  已更正为"逐平台结果可观测"的纯增量记录；`backend/tests/services/test_dedup_scan_merge.py
  backend/tests/tasks/test_saq_tasks.py backend/tests/api/test_dedup_scan_endpoints.py -q`
  → 98 passed。
- **R3 已裁决**（2026-09-15）：条件裁决 + B3 升格门禁。本次**无代码改动**；B3 执行待排期
  （需要真实 toolkit 与 SPRD `_org.xls` 样本，本次环境不具备）。
- **R4 已实施**（2026-09-15）：后端
  `backend/agent/tests/test_platform_collector.py backend/agent/tests/test_job_session.py
  backend/tests/core/test_dedup_platform.py backend/tests/api/test_plan_run_aggregation_endpoints.py
  backend/tests/services/test_dedup_scan_merge.py backend/tests/services/test_plan_run_scan_scope.py
  backend/tests/services/test_dedup_extract.py backend/tests/tasks/test_saq_tasks.py
  backend/tests/api/test_dedup_scan_endpoints.py backend/tests/api/test_dedup_jira_endpoints.py
  backend/tests/test_measure_center_storage.py -q` → **311 passed**；
  前端 `npm run lint` / `npm run type-check` 通过，`CI=true npx vitest run
  src/components/plan-run/AnomalyDashboard.test.tsx` → **11 passed**（含新增 R4-b b1 用例）。
- 仍未修改 `docs/adr/` 下任何文件：v0.8 修订正文需与 `docs/adr/README.md` 的版本行同步落
  （S12 门禁要求头部版本 ↔ README 主表一致）。
- 正文引用逐条对到代码事实（本次会话亲读）：`backend/services/dedup_scan.py:186-206,325,337-340,387-398,446-467`；`backend/tasks/saq_tasks.py:37-42,450-454,482-486,500-507,824-835`；`backend/agent/scan_runner.py:246-277`；`backend/agent/aee/collector.py:36,41-61`；`backend/agent/device_platform.py:11-13,98`；`backend/agent/job_session.py:308,311-313,339,341-342,348-354`；`backend/agent/aee/device_log_event_client.py:104,266`；`backend/api/routes/agent_api.py:2365,2507,2640`；`backend/api/routes/plan_runs.py:2023`；`backend/services/agent_env_sync.py:83-101`；`docs/adr/ADR-0032-...md:24,53-70,113,140-146`。
- 已核实非缺陷项：`docs/design/2026-08-governance-surface-protection.md:136` 确记 `types.ts` ↔ 后端 schema 为 **residual**，故 [issue 台账](../process/2026-09-15-device-log-flow-issue-backlog.md) 的对应条目按"门禁缺失"而非"缺陷"登记。
- 待裁决后需补的验证：本块 §验收判据 AC-R1-1 ～ AC-R4-2；以及 `python scripts/run_gates.py check:quick` 与 S12 索引同步门禁（`docs/adr/README.md` 版本行）。
- **本 PR 级复验（2026-09-15，改动集全量重跑）**：`run_gates.py check:pr` → **[OK] 18 gates**；
  `check:quick` → **[OK] 10 gates**；根 `tests/` → **925 passed**；
  `backend/tests/{api,services,tasks,core}` → **2209 passed**；前端 `CI=1 npx vitest run` →
  **813 passed（106 files）**、`tsc --noEmit` 通过、`ruff check` All checks passed。
  即 R1–R4 的全部改动（含 R4-b b1 的控制面派生与前端「平台未支持」展示）在上述范围内均已覆盖。

## Revisit

- **B3 窗口内未执行**：R3 从"条件裁决"退回"未决"，并在 ADR 中显式标注"同一 merge 工具未被验证"——禁止任何依赖双平台 merge 可用性的预算/容量结论在此之前成立。
- **owner 认为「每 host 双平台齐」是刻意收紧而非漂移**：R1 改走备选（host 平台能力声明），并需补充该收紧的理由与代价评估；此时 AC-R1-* 判据失效，需重写。
- **QCOM 进入正式支持范围**（#73 落地）：R4-b 的"未支持态"应改为正式 collector + reconciler 条目，并新增 QCOM 的 B5 分桶与验收。
- **中心存储结构重排或 merge 执行位置变更立项**：属 ADR-0025 域，与本修订正交；若同时推进，需按"决策实体唯一性"先做主题查重，避免两份权威。
