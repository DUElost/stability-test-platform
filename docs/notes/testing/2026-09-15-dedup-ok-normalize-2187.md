# dedup.py 的 6 处 `ok({...})` 端点正规化，逐条进入响应形状对拍（#2187）

Status: implemented
Class: testing

## Decision

按 `dedup/status` 的样板（PR #2174），把 `backend/api/routes/dedup.py` 里 **6 处**
`response_model=ApiResponse[dict]` + `return ok({...})` 逐条正规化：加响应模型 →
`response_model` 改具体模型 → 前端匿名内联类型改 `types.ts` 具名类型 → 登记进
`_MODEL_PAIRS`。至此这 6 个端点从「任何轴线都覆盖不到」变成双向对拍。

| 端点 | 后端模型 | 前端具名类型 |
|---|---|---|
| `POST /jira/runs`（start_jira_run） | `JiraRunStartOut` | `JiraRunStartPayload` |
| `POST /jira/runs/{id}/cancel` | `JiraRunCancelOut` | `JiraRunCancelPayload` |
| `POST /plan-runs/{id}/dedup/scan` | `DedupScanTriggerOut` + 嵌套 `DedupSkippedHostOut` | `DedupScanTriggerPayload` + `DedupSkippedHost` |
| `POST /plan-runs/{id}/dedup/merge` | `DedupMergeTriggerOut` | `DedupMergeTriggerPayload` |
| `POST /plan-runs/{id}/dedup/extract` | `DedupExtractOut` | `DedupExtractPayload` |
| `POST /plan-runs/hosts/{host_id}/reload-config` | `DedupAgentConfigReloadOut` | `AgentConfigReloadPayload` |

轴线 C 登记由 8 对增至 **15 对**（6 个端点 + 1 个嵌套项）。嵌套项 `DedupSkippedHostOut`
一并登记：`skipped_offline` / `skipped_retired` 的元素形状来自
`plan_run_scan_scope.classify_recycle_targets`（文档承诺 `{"host_id", "status"}`），
不登记就正是「消费者看不见的键」。

顺手加两个守卫，把"正规化"从一次性劳动变成不可回退：

1. **`_MODEL_BLINDSPOT`（盲区台账，按文件 opt-in）**：仍走 `ApiResponse[dict]` 的函数必须
   逐条列名，且**实际 == 登记**——条目失效（已正规化/已删除）即红，防僵尸豁免。dedup.py
   只剩 `get_jira_run_status`（`ok(st)`）与 `get_jira_run_log`（`ok(console.read_log(...))`）
   两处运行期拼装，形状不在本模块，属 issue 明确排除的那一类。
2. **`_EXTRA_ALLOW_ALLOWED`（透传白名单）**：登记进轴线 C 的固定形状模型不得
   `extra="allow"`——那会把「后端多返回键」变成静默透传，正好抵消对拍。唯一豁免
   `DedupScanArchiveOut`（自由 JSONB）；白名单与实测**双向**比对，取消透传时同样报红。
   判据按 AST **取值**（`ConfigDict(extra="allow")` 的关键字常量）而不是比对
   `ast.unparse` 文本——unparse 会把引号规范成单引号，文本判据恒假，等于开了一个
   永远绿的守卫（本单实测踩过）。

前端侧的真实漂移（正规化才看得见，此前 `tsc` 全绿）：

- `planRuns.ts` 的 scan 内联类型只有 3 键，后端实际 6 键——缺 `enqueued` / `is_final` /
  `skipped_retired`：UI 无法如实展示「退役未获准」这一位（ADR-0038 D5 的口径）；
- merge 内联类型 2 键，后端 4 键——缺 `scan_round_id` / `round_started_at`；
- `dedup.ts` 里的 `JiraRunStart` 是**在 `types.ts` 之外**声明的 API 类型，4 键对后端 6 键，
  缺 `source` / `jira_project_key`。按 AGENTS.md 入口不变量移入 `types.ts` 为
  `JiraRunStartPayload`，原导出删除（全仓无其它引用，`tsc` 已验）。

`reload-config` 的配对**没有 SPA 消费方**（运维 runbook 直接 curl；AI 助手的同名动作走
`emit_agent_control`，不经这个 HTTP 端点）。仍登记的理由：未登记的 TS 接口才是幽灵声明，
**被双向对拍的** TS 接口不是——它给这个形状一个单一声明面。代价写进注释：该配对不绑定
调用点，端点退役时须同 PR 删模型 + 删 TS + 删登记项。

## Alternatives

- **A. 扩展判据去识别 `ok({...}` 里的字典字面量**（issue 已否决，本单复述其理由并补一条）：
  扩展 AST 仍有盲区（载荷由局部变量拼装时照样识别不到），且把「是否可对拍」变成概率问题；
  更根本的是——轴线 C 对拍的是**两处声明**，只解决后端一侧的键提取，仍然没有 TS 侧声明面。
  正规化让端点逐个退出盲区，收益是确定的。
- **B. `reload-config` 正规化但**不**登记**（本单一度采用）：否决。它会留下一个没被任何
  轴线检查的 TS 孤儿接口——那恰好是本门禁要抓的形状；要么就得连 TS 侧一起不写，
  等于 issue 验收的「6 处端点全部有登记项」少一处。取「登记 + 在注释里写明无调用点」。
- **C. `JiraRunStart` 原地补 2 个键**（不搬进 `types.ts`）：否决。AGENTS.md 硬不变量规定
  前端 API 类型以 `types.ts` 为入口；登记项虽可指向任意 `.ts`，把消费面留在域文件里会让
  下一次漂移重新变得不可见。
- **D. 盲区台账做成全仓扫描**（凡 `ApiResponse[dict]` 的函数都必须登记）：否决。
  `backend/api/routes` 另有 23 处分布在 8 个文件（`agent_api.py` 10、`plan_runs.py` 4……），
  多个正被并行 Execution 改动；全仓强制会让别人的正常改动撞红、逼出的只是豁免，
  与本仓「强制力优先，但不拿别人的在途文件当筹码」的口径冲突。改为**按文件 opt-in**，
  未纳入者记在台账 I-9。
- **E. 前端只做 `unknown` → 具体类型的最小改动**：否决。issue 的步骤 3 要求具名类型，
  且匿名内联类型正是本次实测漂移（scan 少 3 键、merge 少 2 键）的成因。

## Verification

数字全部取自最终 HEAD——本单两次 `merge origin/main`（第一次带进 #2191，第二次带进
#2195/#2196 等；#2195 同样改了 `planRuns.ts`，无冲突），合并后在最终 HEAD 上复跑
（testcontainers PG 可用）：

- `python -m pytest tests/test_api_response_shape_contract.py -q` → **15 passed**
  （轴线 C 登记 8 对 → 15 对；用例数 14 → 15）；
- `python -m pytest tests/ -q` → **1025 passed**（合入前 995；差值为 main 新增用例，
  本单自身净增 1 个用例）；
- `python -m pytest backend/tests/api -q` → **1161 passed**——`response_model` 由 `dict`
  改具体模型**会裁键**，这一项是"没裁任何端"的主证据；
- `python -m pytest backend/agent/tests -q` → **2055 passed**（本单不触碰 agent 面，作基线）；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**，含 `ruff`、
  `eslint`、`tsc`、`knip`、`compileall`、`orphan-models`、`gov-surface`（S1–S14）、`ai-work`。

**红绿双向自证**（逐条注入后还原，8 例全红；未注入时全绿）：

| 注入 | 报红的用例 |
|---|---|
| TS 删 `DedupScanTriggerPayload.skipped_retired` | `test_model_fields_are_declared_in_ts` |
| 模型删 `JiraRunStartOut.source` | `test_ts_fields_are_all_declared_in_model` |
| 模型加未声明键 `DedupExtractOut.unexpected_key` | `test_model_fields_are_declared_in_ts` |
| TS 加幽灵字段 `DedupExtractPayload.phantom_key` | `test_ts_fields_are_all_declared_in_model` |
| 盲区台账少登记一个函数 | `test_dict_response_blindspot_is_listed_and_not_stale` |
| `extra="allow"` 白名单与实际不符 | `test_registered_models_do_not_open_extra_allow` |
| 正规化 `extract` 但删登记项 | `test_typed_endpoints_are_registered_or_reasoned` |
| `_MODEL_UNREGISTERED` 留僵尸项 | `test_typed_endpoints_are_registered_or_reasoned` |

其中第 1/2 例正是 issue 验收口径「删掉任一侧字段 → 对拍变红」。第 7 例是写本单过程中
**真实踩到**的缺口：`reload-config` 一度正规化完成却没登记，全绿通过——该缺口已由
`test_typed_endpoints_are_registered_or_reasoned` 堵上。

判据取值的坑记一笔：`extra="allow"` 判据最初写成比对 `ast.unparse` 文本，而 unparse 把
引号规范为单引号 → 判据恒假 → 守卫**永远绿**。已改为按 AST 关键字常量取值，并用第 6 例
红自证它真的会红。

## Revisit

- **推广到其余 8 个文件（23 处 `ApiResponse[dict]`）**：`agent_api.py` 10、`plan_runs.py` 4、
  `plans.py` / `scripts.py` / `suites.py` 各 2、`mtbf.py` / `projects.py` / `ai_assistant.py`
  各 1。建议**按文件逐条另立单**（`agent_api.py` 优先：面最大，且 `#787` / `#2089` 两次漂移
  都出自它）。台账 I-9 已记为「仍未覆盖」首条；本单不越界改他人在途文件。
- **`JiraRunOut` 要能登记**：先得让 `_pydantic_model_fields` 解析跨文件基类
  （`ORMBaseModel`）。这项能力一旦打开，可登记的面会远超本单范围，需单独评估误报风险。
- **dedup.py 剩下的 2 处**（`get_jira_run_status` / `get_jira_run_log`）：形状归属
  RunConsole 侧，要覆盖得先给 console 的 status/log 定模型——是 console 域的议题，不是本单。
- **`reload-config` 无仓内消费者**：只有运维 runbook 直接 curl。它该退役还是该补 UI，属独立
  判断；本单只把形状收到单一声明面。若退役：同 PR 删模型 + 删 TS + 删登记项 + 删台账。
- **`Field(alias=…)` / `serialization_alias` 仍未处理**（登记前提是"无别名"，本单 7 对均无）。
- **两个台账都是按文件 opt-in**：真要全仓强制，得先与并行 Execution 协调改动窗口，
  否则只会逼出豁免（Alternatives D）。
