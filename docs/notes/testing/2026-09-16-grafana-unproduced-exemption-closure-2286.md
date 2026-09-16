# #1258 过期豁免收口：清单清空、断言改为派生、生产者判据补全（#2286）

Status: implemented
Class: testing

## Decision

三件事一次收口，因为它们的成立条件互为前提（只删清单会把断言留成恒真）。

**1. `record_api_request` 的 docstring 改成现状**（`backend/core/metrics.py`）

前提失效的时间线（逐条 git 可核，这才是本单真正的问题形态）：

| 时间 | 事件 | 对上游前提的影响 |
|---|---|---|
| 09-10 #1258 | 撤 API 面板 + 建 `UNPRODUCED_METRICS` 5 条豁免 + 写下「生产者未落地」docstring | 前提成立 |
| 09-13 #737 | 复核「仍属 deferred，未删」 | 仍成立 |
| 09-14 #743 (`2b89b7d4`) | 落地 `ApiRequestMetricsMiddleware` 与写入点 | **前提被推翻，但无人回写** |
| 09-16 #2237 | 一跳证据把 `stability_api_requests_total` 认出为有生产者 | 事实暴露，只记进 Revisit |
| 09-16 #2286 | 本单 | 收口 |

教训不是「某人写错了」，而是**前提的失效没有 owner**：落地中间件的那一单没有义务去改
#1258 留下的豁免与注释，靠下一次人工核对才偶然发现。所以本单的结构修法是第 2 项——
把豁免清单换成派生判据，让「生产者落地」这件事自己把豁免挤掉，而不是等人记得。

原文写「#1258 deferred：生产者（路径模板化的请求中间件）尚未落地……#737 复核确认仍属
deferred，未删」。事实是生产者已落地：中间件挂在 `backend/main.py:374`
（`add_middleware(ApiRequestMetricsMiddleware)`），写入点在
`backend/core/request_metrics.py:106,113`（5xx 分支也记）。改成「生产者已落地 + 面板缺口
指向本 Note」——错误陈述比没有陈述更贵：下一个来核的人会读它并据此保留豁免。

**2. `UNPRODUCED_METRICS` 清空，且断言从「手维护交集」改成「判据派生」**
（`tests/test_grafana_dashboard_contract.py`）

原断言是 `面板引用 ∩ UNPRODUCED_METRICS`。面板既然不引用这些指标，交集恒空——所以「把 5 条
删掉、留着断言」等于留着一条恒真断言，正是本单 #2286 验收里明确禁止的结果。改成
`test_dashboard_does_not_use_unproduced_metrics` 调
`test_alert_metric_producers.unproduced_definitions()` 派生「有定义、无生产者证据」集合，
再与面板引用（经 `_base_name` 归一）取交集；`UNPRODUCED_METRICS` 降级为**人工兜底**清单
（强判据漏认真实写入点、又来不及补锚点时才加条目，且必须写原因），并新增反向失效断言：
清单里的指标一旦被派生集合认出来就红（豁免不许过期）。

这样清空才成立：**清单为空 = 当前无债**，而不是「断言空转」。同一套判据同时服务告警面
（#2237）与仪表板面，`unproduced_definitions()` 是 #2287 扩到全指标面时的复用入口。

**3. 生产者判据补全**（`tests/test_alert_metric_producers.py`）

- **mutator 名单对齐全部已声明指标类型**：原名单只有 `inc/dec/observe/set/labels/remove/
  delete`，即 Counter/Gauge/Histogram/Summary 的方法，漏了 `Info` 的 `.info()`（以及
  `Enum.state()`、`Counter.exceptions()`、`Gauge.time()/set_to_current_time()`）。
  后果：`stability_build` 被判成死指标——真写入在 `backend/core/metrics.py:968` 的
  `init_build_info`（由 `backend/main.py:176` 调用），而 Build Version 面板其实有数据。
  漏一个方法 = 一类指标全体假红，且**假红会污染任何用它做存量判读的数字**（见 Revisit）。
- **`Info` 的样本名后缀**：代码声明 `stability_build`，导出/查询的是 `stability_build_info`
  （`prometheus_client` 的 `Info._child_samples` 产出 `_info` 样本）。`_base_name()` 增加
  `_INFO_SUFFIXES`，否则消费侧（面板/告警表达式）永远解析不到定义；`Enum` 是 stateset、
  用原名，不需要后缀。
- **容器间接写入登记为 `_CONTAINER_WIRED_METRICS`**：`stability_host_online` /
  `stability_device_online` 由 `backend/api/routes/metrics.py:43` 的 `_FLEET_GAUGES`
  持有，`_refresh_fleet_gauges` 里 `for model, gauge, ... in _FLEET_GAUGES:
  gauge.labels(...).set(...)` 写入——AST 上标识符与写入调用不在同一条链，强判据看不见。
  #2237 的 Note 已把「扩展锚点形态」列为这类指标转正的唯一出口，本单落地：锚点要求
  ①模块级容器赋值里出现该标识符 ②存在遍历该容器的 `for` ③循环体内对解包出的循环变量有
  mutator 调用，三段同时成立才算生产者；任一段被改写就退回「无生产者」。登记表本身由
  `test_wiring_registries_stay_wired` 钉在场（含「强判据已能认出 → 锚点多余，请删」的
  反向失效断言）。两张表判据不同是有意的：`_MANUAL_WIRED_METRICS` 的
  `stability_api_requests_total` 现在也被强判据认出来了，但它必须留着——一跳证据只能证明
  `record_api_request` 有调用点，「中间件是否真挂载」只有该锚点能证明。
- 函数改名 `test_framework_wired_alert_metrics_stay_wired` →
  `test_wiring_registries_stay_wired`：合并后注册表叫 `_MANUAL_WIRED_METRICS`，且本单起
  它同时覆盖两张表，旧名会误导。

**面板：有意不恢复（本单的「不要留成沉默」出口）**

仪表板当前 24 panels、13 个 `stability_*` 引用、`templating.list` 为空，**没有** API
请求量/延迟面板；`stability_api_requests_total` 是「有告警（`StabilityGhostJobComplete
Endpoint`）无面板」。不在本单恢复的理由：

1. 恢复面板不是收口而是**新设计**：要先定错误率口径（4xx vs 5xx vs 仅 404）、延迟要不要
   `histogram_quantile`（一旦引入就等于替 `_bucket` 桶边界背书），以及是否引入 `$endpoint`
   变量——现有面板零变量，加变量改变整张表的交互模型；
2. 该指标的可辨识信号已在告警面闭环（幽灵端点靠 `endpoint=~".*/complete"` 的 404 速率抓），
   面板此时只提供趋势可见性，属于「有则更好」，不补不会静默失效；
3. 更关键的是延迟侧：`stability_api_request_duration_seconds`（含 `_bucket/_count/_sum`）
   现在**有生产者、零消费方**（既无告警也无面板）。先建面板等于用一个看板把这个「没人裁决过
   用途」的指标固化成资产；它应与 #2287 的逐条裁决一起做（已在 #2287 正文补记）。

再评估条件（任一成立即恢复面板，并同时裁决该直方图的去留）：#2287 裁决保留
`stability_api_request_duration_seconds`；或出现需要按 endpoint 看趋势的真实排障/容量场景
（带 issue 编号）；或 API 类告警阈值需要面板校准。

## Alternatives

- **只清空清单、保留原交集断言**：否决——正是 #2286 验收禁止的「断言改成恒真」：新面板引用
  真无生产者的指标时没人会记得往清单加条目，断言永远绿，且「清空」这个动作本身不留证据。
- **删掉 `test_dashboard_does_not_use_unproduced_metrics`**（既然清单为空）：否决——本单
  明令不做；而且删掉后仪表板面就只剩 ghost-series 一层，回到 #1258 之前的状态。
- **把 `record_api_request` 与 `api_request*` 定义一起删**：否决——生产者已落地、
  `stability_api_requests_total` 被告警引用，删定义会让告警变成查询不存在序列（比静默失效
  更响，但属于制造故障）。
- **放宽判据：容器赋值即算生产者 / 跨文件出现标识符即算生产者**：否决——前者会放行
  「塞进元组但循环被改写」，后者放行「import 了从不写」；改成三段同时成立的锚点，成本是
  多写 40 行 AST，换来的是「接线断了会红」。
- **把 `_FLEET_GAUGES` 改写成强判据能直接看见的形态**（例如显式 `host_online.labels(...).set(...)`）：
  更干净但属行为面重构（两组 gauge、标签枚举遍历、异常兜底都要重写），且会绕开本单真正要补的
  判据能力；改判据留下机制，`#2287` 之后若要收敛写法再单独评估。
- **本单顺手把 10 条真无生产者的定义删掉**：超范围——那是 #2287 的逐条裁决（含当初为什么
  存在、是否有外部消费者），且删除要过 `env-inventory` 等门禁。

## Verification

- `pytest tests/test_alert_metric_producers.py tests/test_grafana_dashboard_contract.py -q`
  → **8 passed**（告警面 6 条 + 仪表板面 2 条）；
- 判据复算（同一入口，修完 mutator/后缀/容器锚点之后）：全仓 `backend/` 非测试代码
  **73 个指标定义**，61 个拿到直写/一跳证据，2 个经 `_CONTAINER_WIRED_METRICS`，
  `unproduced_definitions()` = **10 条**，与 #2287 修正后的清单逐字一致；
  `stability_build` 现判为有生产者：`backend/main.py:176 init_build_info() -> init_build_info`；
- 破坏性对照（改完即从 `/tmp` 备份还原，`git status` 已确认干净）：
  - 从 `_MUTATORS` 删掉 `"info"` → 新回归用例
    `test_info_metric_written_by_same_file_helper_is_produced` **红**（合成树复刻
    `init_build_info` 形态：写入在定义文件 helper 里、用的既不是 inc 也不是 set），
    还原即绿；告警面 5 条仍全绿——说明这个假阴性**不会**被 #2237 的告警轴暴露（
    `stability_build` 不在任何告警里），只有把它用到告警面之外才会浮现，这正是本单的经过；
  - 临时把某面板的 expr 换成 `sum(rate(stability_task_dispatch_total[5m]))`（真·零写入定义）
    → 派生断言**红**并给出 `backend/core/metrics.py:74 有定义、无任何写入点也无跨文件引用`；
    还原即绿。**这条就是「断言未恒真」的直接证据**；
  - 清空 `_CONTAINER_WIRED_METRICS` → 仪表板断言**红**，报错带弱引用线索
    （`backend/api/routes/metrics.py:21,45` + 「确认接线后进登记表，别放宽本判据」）；
    还原即绿。证明容器锚点是承重结构，不是装饰；
  - `test_container_wiring_anchor_is_discriminative` 自带三组负向（循环体不再写循环变量 /
    指标被移出容器 / 只剩赋值无人遍历）都必须判「无生产者」；
- 最终 head：`pytest tests/ -q` → **1131 passed**；
  `python scripts/run_gates.py check:quick` → **10 gates 绿**（base `origin/main@8124f9c2`）；
- 判据在合并 `origin/main` 后复算仍是 73 定义 / 10 无生产者——本单没有把数字写死在
  一次性手算上：`unproduced_definitions()` 就是那条断言的来源，改一个定义就会反映到面板面
  与告警面两处。

同批文档一致性（不动历史正文，只按各 Note 自己写的出口处理）：

- `docs/notes/testing/2026-09-16-alert-metric-producer-guard-2237.md`：Revisit 的存量数字
  13/11 → 修正为 10，并点名 `stability_build` 是**被自家判据漏看**的那条（不是真死指标）；
- `docs/notes/bug-fix/2026-09-10-grafana-dashboard-metric-producers-1258.md`：其 Revisit 首条
  自带指令「API 中间件落地时更新清单并从本 Note 移除对应待办」——本单就是那个触发点，按
  #2089 的先例用删除线标为已收口；
- `docs/notes/bug-fix/2026-09-13-ghost-configs-dead-metrics-737.md`：同上处理其 Revisit 的
  「中间件落地后恢复面板」条目；其正文表格里「中间件未落地」是**当时**的判断，不回改
  （改了会让 #737 那轮的删除理由失去上下文），失效信息由 Revisit 指路。

## Revisit

- **`init_build_info(version="2.0.0", commit="unknown")` 是硬编码**（`backend/main.py:176`）：
  Build Version 面板因此永远显示 2.0.0，与本仓实际发布版本无关——「有面板、数据是假的」是
  #1958 那一族缺陷的镜像形态。修法要先定版本真值来源（`pyproject.toml` / 构建元数据 /
  CI 注入），属行为面，未在本单动；需要时另立单。
- **10 条「有定义、无生产者」的存量**（`stability_task_dispatch_total`、
  `stability_task_dispatch_errors_total`、`stability_task_run_duration_seconds`、
  `stability_device_lease_duration_seconds`、`stability_device_lease_conflicts_total`、
  `stability_device_monitoring_updates_total`、`stability_host_heartbeat_latency_seconds`、
  `stability_plan_run_active`、`stability_expired_active_leases`、`stability_unknown_jobs`）
  逐条补埋点或删除定义 → #2287；本单已把 #2287 的前置 1（容器锚点）与「面板不得引用无生产者
  指标」的派生化做完，#2287 只剩存量裁决 + 扩面。
- **`stability_api_request_duration_seconds` 有生产者、零消费方**：生产者轴抓不到它（它确实
  有写入点），需要的是**反向轴**「已埋点的指标必须被告警或面板引用」。本单不建该轴（判据要不要
  默认「零消费方即债」、以及 `backend/tests/` 里的断言算不算消费方，都还没裁决），已记入 #2287。
- **API RED 面板**：见 Decision 的三条再评估条件；恢复时 `test_dashboard_has_no_ghost_series`
  与派生断言会一起把关，不需要新增机制。
- `_CONTAINER_WIRED_METRICS` 只认「模块级容器 + 同文件遍历写入」。跨文件持有容器、或容器是
  dict/list 且写入点在其他函数里的形态暂不覆盖——真出现时扩锚点，不放宽强判据。
