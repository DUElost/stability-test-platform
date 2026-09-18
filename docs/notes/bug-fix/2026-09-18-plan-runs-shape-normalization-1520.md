# plan_runs 响应形状正规化：summary/artifacts 升模型 + opt-in I-9 台账（#1520）

Status: implemented
Class: bug-fix

## Decision

**`GET /plan-runs/{id}/summary` 与 `.../jobs/{id}/artifacts` 从
`ApiResponse[dict]` / `ApiResponse[list]` 提升为具名模型**
（`PlanRunJobsSummaryOut` / `PlanRunJobArtifactOut`，落 `schemas/plan_run.py`；
服务函数返回类型随迁）。命名刻意避开 `routes/plans.py` 已占用的
`PlanRunSummaryOut`——#82 的教训写在该 schema 文件头：同名模型会把 OpenAPI
component key 逼回模块路径消歧。

**`plan_runs.py` 正式 opt-in 形状契约（`_MODEL_BLINDSPOT` 文件集）**，这是
watcher-slice note 里预告的「#2187 正规化候选 + 顺势进 opt-in」的落地：

- 新升的两个模型**登记进 `_MODEL_PAIRS` 做双向对拍**（TS 侧 `PlanRunSummary` /
  `JobArtifactEntry` 已存在且字段逐名一致——契约当场验证，无需前端改动）；
- 三个写侧摘要（abort/archive/retry 的 `ApiResponse[dict]`，服务层多分支运行期
  拼装——#2089 `released_leases` 同族形态）**认领进盲区台账**，逐分支正规化留
  后续批；
- 9 个遗留具名模型（`PlanRunDetailOut`/`JobInstanceOut`/`PlanChainOut` 等）
  按 `_MODEL_UNREGISTERED` 显式豁免并写明理由——「不登记=继续盲区，但有人认领」，
  失效即红的机制保证它们不会被遗忘。

**顺带修掉一个 opt-in 扩面即爆的判据缺陷**：`test_typed_endpoints_are_registered_or_reasoned`
的 stale 检查原先在 **per-file 循环内对全局豁免清单**判失效——第一个文件
（dedup.py）时 `JiraRunOut` 合法，第二个文件 opt-in 的瞬间 `JiraRunOut ∉
plan_runs 的 used` 必误报。改为对**并集**判失效（保证仍成立：任何豁免模型若不再
被任何 opt-in 文件的端点引用即红，防僵尸豁免的语义不放宽）。单文件时代该缺陷
不可见，属「门禁随扩面进化」的常规代价。

**新模型不配前端改动**是有意核查过的：`types.ts::PlanRunSummary` 与
`JobArtifactEntry` 的字段集与模型逐名一致（双向对拍在契约用例里当场断言），
SOP 的步骤 5/6 以「核对无漂移、零改动」结案——漏同步才是事故，硬造 diff 不是。

## Alternatives

- **本批把 9 个遗留模型全部逐字段对拍**：弃——每个模型都可能引出真漂移
  （#2089/#2285 的历史证明漂移是常态），发现即须修，会把「扩面认领」的刀变成
  不可 review 的大杂烩；I-9 台账逐模型推进是本仓既定的增量路径；
- **只升模型不 opt-in**：弃——契约文档明说「正规化到 ApiResponse[X] 却忘了登记
  比不正规化更危险，因为 diff 读起来像已收口」；
- **abort/archive/retry 三个 dict 一并升模型**：弃——多分支运行期 dict 的
  字段集要逐分支枚举（正是 #2089 咬过的地方），值得独立批，不夹带。

## Verification

- `tests/test_api_response_shape_contract.py` → **15 passed**（含两个新配对
  的双向对拍 + stale 并集修复在双文件 opt-in 下绿）；
- 新增运行时用例 `backend/tests/api/test_plan_run_shape_1520.py` → 3 passed：
  信封形状、**键集合逐键相等**（response_model 漏声明字段的静默裁剪是 dict→模型
  迁移唯一新引入的失败形态，用实弹钉住）、`filename` 尾段派生、归属 404；
- 受影响回归批（plan_runs_api / read_api_auth / aggregation / export / runs /
  archive + 契约）→ **188 passed**；
- `run_gates.py check:quick` → 见 PR；
- （对拍批 1）契约 15 passed；`tsc --noEmit` 通过。

## 追加：对拍批 1（同日第二 commit）——9 条豁免全部转正

预照契约测试自己的解析器跑了一遍 9 豁免模型 ↔ types.ts 候选 interface 的字段
对账：**8 个零漂移**（`PlanChainOut↔PlanChain`、`PlanRunDevicesOut↔
PlanRunDevicesPayload`、`PlanRunEventsOut↔PlanRunEventsPayload`、
`PlanRunListPageOut↔PlanRunListPage`、`PlanRunTimelineOut↔PlanRunTimeline`、
`JobInstanceOut↔PlanJobInstance`、`JobManualActionOut↔JobManualActionResult`、
`TestCaseResultsPayload↔TestCaseResultsPayload`）；**1 个真缺口**：
`PlanRunDetailOut↔PlanRun` 的 `jobs`——wire 上一直存在（detail 端点带 Job 明细、
list items 恒序列化空数组），TS 侧漏声明。修法 = `PlanRun` 补
`jobs?: PlanJobInstance[]`（可选：plans.py 的 run 摘要行不返回该键，消费页也
从不读它——只钉 wire 事实，不造新依赖）。

9 条 `_MODEL_UNREGISTERED` 全部移除、11 对 `_MODEL_PAIRS` 在册；豁免清单只剩
`JiraRunOut`（解析器跨文件基类的真实限制，非拖延）。`check:quick` 的 knip/eslint
对纯注释+可选字段零触发；`tsc` 通过。

## Revisit

- abort/archive/retry 的 dict→模型（写侧摘要）：做时把 `#2089` 的逐分支判据写进
  测试（只看并集拦不住多分支形态）——这是形状系列剩下的最后一块 plan_runs 面；
- cursor 在窗「删 re-export 测改 service 导入」与本刀在 `plan_runs.py` 导入区
  可能擦碰——本刀不消费路由 re-export（模型直接来自 schemas），冲突仅文本面。
