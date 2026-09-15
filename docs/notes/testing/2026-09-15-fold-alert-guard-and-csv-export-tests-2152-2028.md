# 折叠重复的告警契约守卫（#2152）+ 日志页 CSV 导出补测与截断可见化（#2028）

Status: implemented
Class: testing

## Decision

两件事同属一个面：**「同一事实的两处独立声明」与「Note 声称有测试其实没有」**——#2187
（dedup 契约对拍）的同一类问题，只是落在守卫层与前端层。

### #2152：折叠，不收缩（issue 选项 1）

删除 `tests/test_prometheus_alert_metric_names.py`（#2144 新增），把它唯一的新增点并进
`tests/test_prometheus_alerts_contract.py`：

- 新用例 `test_scenario_input_series_match_metric_registry`——promtool 场景文件的
  `input_series` 必须对上指标注册表，**既查名字也查标签形状**（#2144 那个文件不查标签）；
- 派生序列的**类型前提**不再需要手工判据：折叠后直接用 `metric_registry_index()`
  （prometheus_client 的实测索引），`stability_x_bucket` 只有在 `x` 仍是 Histogram/Summary
  时才存在于索引里。#2144 的 `_DERIVED_SUFFIXES` / `_DERIVED_PARENTS` 两套常量整体消失；
- `test_series_selector_parser_self_proof` 作解析器自证，配「形态外即 assert 报错」，
  避免解析退化把用例变成恒真（与 `#2141` 同一手法）。

**为什么场景文件值得单独查**：CI runner 没有 promtool，`test_alert_scenarios_fire_with_promtool`
恒 skip（#2151 记录的正是这件事），场景文件因此从不被真正执行——它的输入序列一旦指向不存在
的指标或标签，规则永远匹配不到样本，「这条告警可触发」的证据其实是空转。本机 promtool 可用，
变异自证时两层一起变红，说明折叠没有削弱任何一侧。

### #2028：`handleExportCsv` 三条静默行为收口 + 6 例

`frontend/src/pages/execution/PlanRunLogsPage.tsx`：

1. **去重**：按 `eventRowKey`（渲染进 CSV 的全部字段 + `ref` 的 `{type,id}`）过滤。后端事件是
   多源合成的**无 id 投影**，且 `ts DESC` + offset 分页在 run 在途时会因新事件插队而窗口位移，
   同一条在相邻两块里各出现一次；CSV 行尾如实写出「去重 N 行」。
2. **上限可见化**：`EXPORT_MAX_ROWS` 命中后此前完全静默。现在只有"读完"（`exhausted`）才算
   全量，否则 CSV 末尾追加一行说明——宁可文件里多一行提示，也不让使用者以为拿到的是全量。
3. **快照声明**：非终态 run 导出时追加「本文件是导出期间的快照」——不去禁用按钮（在途导出
   是合理需求，正确姿势是声明而不是禁止）。
4. **下载健壮性**：`<a>` 先 `appendChild` 入文档再 `click()`，`revokeObjectURL` 推到下一个
   定时任务（同一 tick 撤销会取消下载）。

同时回填 `1e339766` 那条 Note：它在没有测试的情况下写了「逻辑由单测覆盖」，这正是 #2028 的
立项理由——补测后以「回填」形式记录当时的不成立，不改写其余实测记录。

## Alternatives

- **#2152 选项 2（保留两个文件、把新文件砍到只剩 `series:`）**：否决。留两条标准就仍需两处
  同时维护，而「新文件是既有的子集」这个误导会一直在——issue 自己也推荐折叠。一个关注点
  （告警/场景 ↔ 注册表）回到一个文件。
- **复用 `_selectors()` 解析场景序列**：否决，**实测被绊**——该解析器的标签块用 `[^}]*`
  截断，而本仓 `endpoint` 标签值是**路由模板**（`endpoint="/api/v1/jobs/{id}/complete"`），
  值里的 `{id}` 会让 `}` 提前闭合，把 `complete`、`status_code` 误读成指标名（首跑即红两条
  UNKNOWN）。不顺手给 `_selectors` 打补丁：它同时服务 alerts 面，改标签块正则的爆炸半径
  大于本单，故为 `input_series` 这一形状单写 `_series_selector`。
- **去重键用 `(ts, category, title)`**（issue 的建议形态）：否决。三条不足以区分真实事件
  ——同一步骤对同一设备在同一秒重复上报是合法的，会被**吞掉真行**。全字段 + `ref` 的键即使
  撞上，那两行在 CSV 里也逐字节相同，去掉后一条不损失任何信息。
- **非终态 run 直接禁用导出按钮**：否决。在途导出当前快照是真实需求，缺的是「这是一份快照」
  的声明，不是禁止。
- **把 run 窗口上界下发成服务端过滤条件**（issue 的另一建议）：否决并记入 Revisit。要改
  `backend/api/routes/plan_runs.py`（并行 Execution 正在改的热点文件），而窗口位移在
  `ts DESC` + offset 下**只会造成重复、不会丢尾部**——重复已被去重消除，服务端改造的收益
  只剩省一次请求。
- **上限提示放按钮 `title`**：否决（issue 允许二选一）。放 `title` 要改
  `PlanRunEventStream.tsx`（另一组件），而截断是**文件属性**，写在文件里才能被拿到 CSV 的
  人看见——看提示的人未必是点按钮的人。

## Verification

`python -m pytest tests/ -q` → 最终 HEAD 上 **1053 passed**（再并入 22 个 main 提交后；
上一 HEAD 为 1040）。这一项要连着读：本单**删 4 例**（#2144 那个文件）+ **加 2 例**（场景
序列守卫 + 解析器自证），即净 **-2**（1042 → 1040，main 新增的另算）；用例数变少是折叠的
**预期结果**，判据强度由下面的红自证保证——**总数不是这里的信号**。

- `python -m pytest tests/test_prometheus_alerts_contract.py -q` → **7 passed**（原 5 例 + 2）；
- `npx vitest run src/pages/execution/PlanRunLogsPage.test.tsx` → **10 passed**（原 4 例 + 6 新）；
- `npx vitest run`（前端全量）→ **106 files / 825 passed**；
- `npm run type-check`（`tsc` 双项目）、`npm run lint`、`ruff check` 通过；
  `scripts/run_gates.py check:quick` → **[OK] (10 gates)**（补齐本 Note 后复跑）。

**红绿自证**（逐条注入后还原；#2152 5 例、#2028 5 例全红，未注入全绿）：

| 注入 | 报红的用例 |
|---|---|
| 场景 `series:` 指标名改名 | `test_scenario_input_series_match_metric_registry`（+ 本机 promtool 真跑亦红） |
| 场景 `series:` 用了不存在的标签 | 同上（#2144 原文件**看不见**这一条） |
| 场景 `series:` 用 Histogram 裸基础名 | 同上 |
| 场景 `series:` 改写成多序列块标量 | `_series_selector` 显式报错，不静默放过 |
| alerts `expr` 引用未声明指标 | `test_alert_selectors_match_metric_registry`（回归确认既有面未削弱） |
| 组件去掉去重 | `在途 run 的窗口位移…`（逐条核到过用例名，非语法噪声） |
| 组件去掉截断尾注 | `命中 20 000 行上限…` |
| `revokeObjectURL` 回到同 tick | `下载：先入文档再 click…` |
| `<a>` 不入文档直接 click | 同上（`isConnected` 断言） |
| 去掉 BOM | 任一读 CSV 的用例（BOM 按**字节**断言：`Blob.text()` 走 UTF-8 decode 会剥掉前导 BOM，用它断言 `startsWith('\ufeff')` 恒假） |

顺带记两条测试自身的坑（都已修，写下来防复发）：`renderPage()` 的页面查询与导出共用同一个
`getEvents` mock，必须按 `limit` 分流、只数导出调用，否则次数断言全错；上一用例「推迟到下一
tick」的 revoke 会记到下一用例的 mock 上（跨用例污染），`afterEach` 需先还原 spy 再排空一个
宏任务。

## Revisit

- **promtool 场景层在 CI 恒 skip**：由 #2151 承载，本单不修。折叠后的场景名字/标签守卫正是
  它缺的那一半兜底——#2151 落地（CI 装 promtool）后本守卫仍然有用，两者不重复。
- **导出失败没有 `catch`**：`handleExportCsv` 只有 try/finally，失败会成一次 unhandled
  rejection 且界面毫无提示。补齐要先定「前端如何报告动作失败」（本仓没有统一 toast 约定），
  不混在 #2028 里做——已在测试文件里留同名注释。
- **`EXPORT_CHUNK` / `EXPORT_MAX_ROWS` 在测试里重复声明**：组件常量没有导出面。当前由两条
  断言双向钉住（恰好 40 次翻页 + 尾注文案含 `20000`），改上限会红；真要根治是把常量提到模块
  导出，属可选清理。
- **`annotations` 里的散文不查指标名**：沿用 #2144 的取舍（迁移期允许散文保留历史名）。
  代价不变：值班指引文字可能指向已改名的指标而不报红。要做需要一份「允许保留历史名」白名单。
- **「两套标准」的守卫**本次只在告警面做了普查，全仓没扫。若要扫，可考虑在
  `tests/` 里做一次同名主题的用例文件清点（独立议题）。
