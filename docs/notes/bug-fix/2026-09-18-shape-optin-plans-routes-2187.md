# #2187 扩面：plans.py opt-in 形状契约，当场钉出一个幽灵返回类型

Status: implemented
Class: bug-fix

## Decision

把 `backend/api/routes/plans.py` 纳入响应形状契约的 opt-in 文件集
（`test_api_response_shape_contract` 的 I-9 台账机制——#2187 主诉「对拍覆盖不到
`ok({...})` 端点」收口后，剩余面按文件逐批推进）：

- 两个 typed 模型登记轴线 C 对拍：`PlanOut ↔ Plan`（18↔18 零漂移）、
  `PlanRunSummaryOut ↔ PlanRunTriggerResult`（新 TS interface，15↔15）；
- `delete_plan` / `preview_plan_run` 两处运行期 dict 按台账认领（不冒充已收口）。

**登记当场逮到的真东西**：`api.plans.run()` 的返回类型一直标 `PlanRun`——
那是 plan-runs 列表/详情投影，比触发端点实际返回的 `PlanRunSummaryOut`
**多 9 个键**（capabilities/jobs/device_count/enqueued_at/queue_reason/
priority/plan_name/project_key/next_admission_at）。按 #787 的口径这就是
幽灵声明：消费方若读 `run.capabilities.abort` 会拿到 undefined 且类型系统
不会拦。全仓唯一调用点（PlanExecutePage，确认后仅读 `run.id`）碰巧没踩——
**「没出事」不是「类型对」**，这正是契约门禁存在的理由。修法：新增
`PlanRunTriggerResult`（逐字段复刻后端模型）并收紧 client 返回类型；
wire 形状零改动，纯类型事实纠正。命名注释同时钉住它与
`GET /plan-runs/{id}/summary` 的 `PlanRunSummary`（名字近、形状远）防混用。

## Alternatives

- **`run()` 保留 `PlanRun` 标注 + 加注释**：弃——幽灵键声明留着，下一个读
  `.capabilities` 的人仍静默中招；
- **把 `PlanRun` 的 9 个多出的键改成可选**：弃——那会连带放松 plan-runs
  面（detail 端点真返回它们）的类型，为一个端点污染另一个面的声明。

## 追加：第 2 批 projects.py + TS 解析器补 `extends` 链

`projects.py` 预照时 6 对里 2 对「漂移」全在带继承的界面上（`ProjectSummary
extends Project`、`ProjectDetail extends ProjectSummary`）——先判定为**解析器
假阳性而不是真漂移**（真去改 TS 就是迁就误报），给 `_ts_interface_fields` 补
extends 链展开：同文件可解析则合并、解析不到**报错**、带环检测——与 Python 侧
跨文件基类判据同形。补完全集 **6/6 MATCH、TS/后端零改动**登记：
`ProjectSummaryOut/ProjectDetailOut/InventorySummaryOut/InventoryModelOut/
ProjectModelCoverageOut/ProjectMapPreviewOut` ↔ 同名族 interface。
盲区认领 `remove_project_rule`（ok 壳）；`GET /customers` 的 `list[dict]`
内层无具名模型、现判据扫不到——挂 Revisit 不隐身。

**判据顺序的注脚**：扩面工具本身错了两种（Python 跨文件基类=上批修的、
TS extends=这批修的），两种都选择「报错优先于静默少收」——静默少收的
门禁会制造假绿，比没有门禁更坏。

## 追加：第 3 批 scripts.py + 判据第三处修正（标量内层不是模型）

`scripts.py` opt-in：`ScriptOut ↔ ScriptEntry`、`ScriptUsageOut ↔ ScriptUsage`
两对 **MATCH 直接登记**（TS 用消费侧原名，键集逐名一致，前端零改动）；
`scan_scripts` / `deactivate_script` 两处运行期 dict 认领进台账（scan 聚合含
conflicts 数组，正规化需连 #2386 的守卫字段一起设计，独立小批）。

预照当场把 `/scripts/categories` 的 `ApiResponse[List[str]]` 误报成「未登记
具名模型 str」——判据第三次被扩面暴露（前两次：Python 跨文件基类、TS extends）：
`List[str]` 内层是标量，既非 Pydantic 模型也不该登记/伪豁免。修法是 skip 集
补标量类型（str/int/float/bool），语义与既有的 `dict`/容器豁免同源。三处修正
共同口径：**判据缺陷修判据，不拿被登记者迁就判据**。

## 追加：第 4 批 devices.py（Union 判据 + 批 1 积累直接回本）

`devices.py` 无 `ApiResponse[dict]` 端点（盲区登记为空集，typed 记账照常生效）。
`DeviceOut ↔ Device` MATCH 登记——它是 `ORMBaseModel` 系，**正是第 1 批解析器
扩展的适用面**（跨文件基类若没修，这里又要挂一条豁免）。`Union[List[DeviceOut],
PaginatedResponse]` 暴露判据第四处：typing 联合被当具名模型——`Union` 入 skip
（成员各自入账、联合本身不是模型）；`PaginatedResponse` 是通用分页壳
（items: List[Any]），按 `_MODEL_UNREGISTERED` 具名认领「内层形状由成员配对
承担」——不为壳建 TS 幽灵配对。devices 多数端点仍是裸 `DeviceOut`/无信封
（#2129 前遗产），信封化是另一条面，不在台账内顺手扩张。

## 追加：第 5+6 批「纯记账面」——七个 routes 文件一次入账，15 对零漂移

`heartbeat/logs/notifications/audit/stats/results/hosts` 预照结果：**15/15 MATCH，
无一处需要改 TS 或后端**——这批的产出就是把既有的双端声明正式纳入对拍：以后这些
文件新增 `ApiResponse[dict]` 端点会被空集台账当场逼出，新增具名模型必须登记或
具名豁免。`response_model=Any`（logs/query 的 FastAPI「放弃声明」写法）与
`Union` 同族入 skip——typing 构造不是模型，判据第五处、也是最后一处小修。

`HostOut ↔ Host`（32↔32）能直接入账，吃的是批 4 的 Union 修正 + 批 1 的
跨文件基类（`HostOut` 亦 `ORMBaseModel` 系）——**前面批次的判据修复在这里连本
带利回收**：若那些没修，这 15 对里至少 3 对要挂伪豁免。

累计：opt-in 文件 12 个（plan_runs/dedup/plans/projects/scripts/devices/
heartbeat/logs/notifications/audit/stats/results/hosts + schema 面配对），
在册对拍 27 对，判据修正 5 处（Python 跨文件基类 / TS extends / 标量内层 /
typing 联合 / `Any`），未决豁免 2 条（archive 前端无消费者、分页壳——均写明
失效条件）。

## Verification

- 契约 15 passed（plans 2 对、projects 6 对、scripts 2 对全部当场通过）；
- `test_scripts.py + test_scripts_default_params.py` → **37 passed**（第 3 批后）；
- 第 4 批：契约 15、devices 路由组 106 passed；
- 第 5+6 批：契约 15（15 对在册全解析通过）、七文件路由回归（`-k "result or
  stats or notification or audit or agent_log or heartbeat or hosts"`）与
  `check:quick` 12 门禁 → 见 PR；
- `test_project_routes.py` → **75 passed**；`check:quick` 12 门禁（第 2 批后）；
- `test_plans_api.py + test_read_api_auth.py` → **145 passed**；
- vitest `PlanExecutePage.test.tsx` → **58 passed**（唯一消费点行为回归）；
- `check:quick` 12 门禁绿（局部 import 棘轮 610 未涨）。

## Revisit

- `agent_api.py`（10 dict 端点、#1520 切片在飞）是 opt-in 余量最大的一块，等切片退场后单独批做；
  `agent_api.py`（16、10 dict）等 cursor 的 #1520 切片退场后再动，别撞在飞的
  搬迁面；
- `/specialties` 返回 `ApiResponse[List[dict]]`：内层无具名模型、现判据扫不到——
  要么升 `SpecialtyOut` 要么给解析器补「list[dict] 内层认领」记法，挂此台账不隐身。
