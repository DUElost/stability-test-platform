# 对外风险词表统一到 S/A/B：后端不再翻译，前端一处着色（#2494 / ADR-0045）

Status: implemented
Class: bug-fix

关联：[ADR-0045](../../adr/ADR-0045-risk-level-vocabulary.md)（D1–D6 由 owner 于 2026-09-17 裁决）、
[#2419 的 note](./2026-09-17-risk-vocab-and-dead-summary-panel-2419.md)（本单是它留下的词表残余）、
[#2365 gauge 的 note](./2026-09-17-risk-gauge-zeroing-and-scope-2365.md)（同一指标的口径）、
[#2418 的范式](./2026-09-16-job-report-status-badge-2418.md)（同型缺陷：数据没错，键没对齐）。

## Decision

### 1. 四面统一成"级别本身"，服务端翻译表整体删除（D2）

`backend/api/routes/results.py` 的 `_RISK_LABEL_BY_LEVEL`（S→HIGH）与
`_RISK_BUCKET_BY_LEVEL`（S→high）两张表**删除而不是改名保留**：留着它们，下一个风险面
（趋势、导出、告警卡片）就会各自去查其中一张，于是又长出第二、第三套对外词表。
取代它的是 `_risk_level_or_unknown()`——只做一件事：`非 S/A/B` → `UNKNOWN`。
这不是展示层翻译（它不产生新词），而是值域归一，所以留在后端。

契约面同时改：`RiskDistribution` 字段 `high/medium/low` → `s/a/b/unknown`；
`RecentRun.risk_level` 直出 `S/A/B/UNKNOWN`。

### 2. `NONE → UNKNOWN` 只允许一个方向（D2 + D4）

趋势第四态 `NONE` 并入 `UNKNOWN`（语义相同：无可判定事件）。**方向是单向的**：
`B` 仍然是"有事件但非 S/A"，零事件不得算进 `B`——这是 #2365 覆盖率观测的立足点
（"没采到异常" ≠ "查过且没风险"）。因此本单同时钉两条：门禁钉 `NONE` 不再出现，
行为用例钉 `UNKNOWN` 与 `b` 是两个桶、饼图不许省略 `UNKNOWN` 档。

### 3. 翻译只留两处，且第二处不再自带词表（D3）

- `frontend/src/components/ui/status-badge.tsx` 的 `RISK` 表补 `S/A/B` 三键（级别轴）。
  **同时保留 `HIGH/MEDIUM/LOW`**：`kind="risk"` 目前服务两条轴——报告页的风险徽标
  （级别）与告警列表的 severity（`backend/api/schemas/run.py` 的
  `severity: Literal["HIGH","MEDIUM","LOW"]`）。D5 明确 severity 值域不在本 ADR 范围，
  删任一组会让另一组恒显"未知"。这是**已知共表**，等 D5 裁决再分表。
- `frontend/src/components/charts/RiskDistributionChart.tsx` 原先自带一份 `s→高` 的
  `LABELS`，那是 D3 禁止的"第二处翻译"。改为新增
  `frontend/src/components/charts/riskBuckets.ts`：序列身份是 `S/A/B/UNKNOWN`，
  **图例文案查徽标表**（`resolveStatusEntry('risk', level).label`），只有
  变体名→色板（destructive→error 等）留在图表侧——那是渲染关注点，不是词表。

### 4. 映射为什么单独成文件（而不是继续留在组件里）

两个都是硬约束，不是偏好：

1. **可测性**：recharts 的图例在 jsdom 里根本不渲染（零尺寸 + `StableResponsiveContainer`
   的尺寸门禁），仓库既有 chart 测试（`PlanSuccessRateChart` / `HostFailureRateChart`）
   因此只测 loading/empty。若映射埋在 `useMemo` 里，"桶名/文案漂移"这条真判据在 DOM 上
   测不到——写出来的会是一条恒绿断言，比没有门禁更糟。抽成纯函数后判据能真的跑。
2. `react-refresh/only-export-components`（`--max-warnings 0`）不允许组件文件导出非组件。

同仓已有同形状的做法（`components/plan-run/planRunStatus.ts`、`stepTiming.ts`）。

### 5. 离线门禁用 AST，不用子串/正则

`tests/test_risk_vocabulary_drift.py` 的结构判据全部走 AST
（`_module_names` / `_class_fields` / `_code_string_constants`，后者排除 docstring）。
原因很实际：解释性注释必然要写"以前这里有 `_RISK_LABEL_BY_LEVEL`"，子串判据会把**说明本身**
判成违规——同一个坑在 #2432/#2402 的守卫上已踩过，本会话又踩了一次（正则刮到同名前缀的
`RiskTrendOut` 类，假红）。同理，门禁自带**解析器自守**断言（解析结果为空即红），
否则退化的解析器会让判据恒真。

### 6. `UNKNOWN` 在 DOM 上分不出"显式键"与"兜底"——所以不设 DOM 判据

徽标 FALLBACK 与 `UNKNOWN` 键的 label/variant 完全同形（都是「未知」+ secondary），
在页面上断言"`UNKNOWN` 不是兜底"只能靠比对实现细节。故这条不写进 vitest，改由离线门禁
`test_every_outward_risk_level_has_a_badge_key` 直接解析 `RISK` 表钉**键存在性**；
vitest 只钉 S/A/B 三档"不得出现「未知」"+ 着色轴按级别区分。

### 7. 词表真漂移时回显原文（`fallbackToRaw`）

报告页与结果页的风险徽标都加 `fallbackToRaw`：将来若后端多出一档（例如新的 `C`），
界面显示 `C` 而不是静默的"未知"。#2418 的判据推广到风险轴。

### 8. 指标标签值随词表一起改，前置清点已完成（D6）

`stability_risk_jobs_by_level` 的 `level` 标签值由 `high/medium/low/unknown` 改为
`s/a/b/unknown`。D6 的唯一前置是"清点仓外消费者"，本仓内的清点结果：
`deploy/prometheus/*.yml`（规则与告警）与 `docs/grafana/stability-platform-dashboard.json`
**没有一处引用该指标**（`grep -rn "risk" deploy/prometheus docs/grafana` 空命中），
且 `tests/test_prometheus_alerts_contract.py` + `tests/test_grafana_dashboard_contract.py`
33 passed。因此本单不需要排在 #2485 之后，也不动指标名与标签集。

### 9. 与 #2506 的重叠：本单 rebase 在后，路由形状跟随它

`RunReportPage.tsx/.test.tsx`、`types.ts`、`ResultsPage.tsx` 同时被 PR #2506
（#2420 第 2 项：报告路由改以 job 为权威 `/jobs/:jobId/report`，旧 `/runs/:runId/report`
只重定向）改动。本单**不动路由形状**，因此重叠只是文本冲突：#2506 先合，本单 rebase 后把
新增的页级用例路径对齐到 `/jobs/:jobId/report`（唯一冲突文件是 `RunReportPage.test.tsx`，
其余四个文件自动合并）。留给后面的同类单一条经验：**同文件的两单在队列里必须显式写清
先后**，否则后合的一方会以为自己在改测试，实际是在解 rebase。

## Alternatives

- **保留旧字段做别名双写（`high` 与 `s` 同时出）**：否决。D6 已裁决不留双写窗口，
  理由可执行：风险级别是**派生值**，不落库、无快照表携带旧词，因此没有 backfill 与
  回滚成本（这也是本裁决能做成的根本原因）。
- **反向统一到 `HIGH/MEDIUM/LOW`，把 S/A/B 当内部实现**：否决（ADR 方案 B）。`B` 与
  "无判定" 在外部词表里都会被读成 LOW，正是 #2365/#2419 花力气消掉的歧义；且报告 DTO
  与趋势已是 S/A/B，改名面更大。
- **饼图继续自带 `LABELS`，只在门禁里比对两边一致**：否决——"两处必须一致"本身就是
  根因形状，一致性问题应由单点消除，而不是由门禁长期背着。
- **门禁用子串搜索（`"_RISK_LABEL_BY_LEVEL" not in text`）**：否决，见 Decision 5。
- **把告警 severity 一起并入 S/A/B**：推迟（D5）。阈值类告警（`ANR_DETECTED` 等）没有
  "job 级别"语义，强并等于伪造映射，且连带 JIRA 草稿链。
- **顺手把 `counts.by_severity`（报告 DTO 里那个键名）改成 `by_level`**：否决。它不属于
  本 ADR 的四个面，改它是又一次契约变更；记入 Revisit。

## Verification

基线：rebase 到 `origin/main` = `56ed1970`（#2506/#2508/#2509/#2510/#2511 已在内）之后重跑全部
检查；与 #2510/#2511 的文件交集为 0（两侧 diff 文件表 `comm -12` 为空），故第二次 rebase 后
只重跑门禁与前端全量。

**实跑命令与结果**（工作树 `.wt/stp-2494`）：

- `python -m pytest tests/test_risk_vocabulary_drift.py tests/test_status_vocabulary_drift.py backend/tests/api/test_results.py -q`
  → **17 passed**。本单新增：门禁 5 条 + `TestRiskVocabularyParity::test_same_level_across_summary_trend_and_report`。
  Parity 那条跑在 testcontainers 的 PG 上（**不碰本机生产库**）：造**两个 run 各一个 job**——
  一个经活链判成 S（ANR+swt 信号），一个零信号。断言 `recent_runs[].risk_level` 分别为
  `S` / `UNKNOWN`、整列值域集合 = `{S, UNKNOWN}`（⊆ 对外词表）、`risk_distribution` 键集 =
  `{s,a,b,unknown}` 且 `s>=1 / unknown>=1 / b==0`、`risk-trend` 当日桶无 `NONE` 且
  `S>=1 / UNKNOWN>=1 / B==0`、报告 DTO `risk_summary.risk_level` 与列表**同值**。
  两个 job 是必需的：趋势按 **run** 汇总，单 run 时"零事件那一档"会被同 run 的 S 吃掉；
  且值域集合断言在只有一行时会被更早的逐行断言吃掉（先试过，红点落在别处=假绿风险）。
- `vitest run`（全量）→ **124 files / 999 tests passed**；其中本单相关：
  `RiskDistributionChart.test.tsx` 13 passed、`RunReportPage.test.tsx` 11 passed（9 条来自 #2506 + 本单 2 条）。
- `tsc --noEmit`、`tsc --noEmit -p tsconfig.node.json`、`eslint src --max-warnings 0`：通过（0 warning）。
- `python scripts/run_gates.py check:quick` → **10 gates OK**；其中 `check_alembic_at_head`
  输出 **WARN：DATABASE_URL 未配置，跳过**——本地未配 DB，该项只在 CI `pr-migrate-empty-db`
  有真值，**不记为通过**。
- `pytest tests/test_prometheus_alerts_contract.py tests/test_grafana_dashboard_contract.py -q`
  → **33 passed**（D6 的仓外消费者清点，与下面的 grep 一起构成"可直接改"的依据；
  这两个测试文件与本单零交集）。

**回退自证（红→绿，回退 = `git checkout HEAD~1 -- <file>` 只回退实现、保留用例）**：

| 回退项 | 实测红点 | 恢复后 |
|---|---|---|
| `backend/api/routes/results.py` | 门禁 **4 failed / 1 passed**（字段漂移为 `['high','low','medium','unknown']`、检出 `{'S':'HIGH',…}` 字面量映射、`NONE` 复现、映射表复现）；API 面 **3 failed / 5 passed**（`KeyError: 's'`、桶键集不等、gauge 桶名） | **13 passed** |
| `frontend/src/components/ui/status-badge.tsx` | 门禁 **1 failed / 4 passed**：`前端 RISK 表缺键 ['A','B','S']`；`vitest`（徽标 + 报告页 + charts）**5 failed / 70 passed (75)** | **5 passed** + **75 全绿** |

徽标回退连带红掉分布图那条「四档文案与着色」——这不是噪声，正是 D3 的**结构后果**：
图例文案查的就是这张表，表里没键 → 饼图也一起说"未知"。一处翻译点的意义在此可见。

**后端定向变异**（`_risk_level_or_unknown` 的兜底分支，两处各一次，均红、恢复后 8 passed）：

- `else "UNKNOWN"` → `else "MEDIUM"`（旧词表值域漏出）→ parity 用例 **1 failed**；
- `else "UNKNOWN"` → `else "B"`（D4 反例：零事件压成低风险）→ **1 failed**，
  失败信息即 `零事件的 job 必须是 UNKNOWN，不得压成 B/LOW：B`。

**变异自证（分布图）**：回退组件文件测不到映射（已移出到 `riskBuckets.ts`），故改用三处
定向变异，每条都必须让对应用例红：

- `UNKNOWN: 'unknown'` → `'b'`（把零事件压进低风险，D4 反例）→ **4 failed**；
- `RISK_LEVELS` 去掉 `'UNKNOWN'`（饼图省略未知档，D4 反例）→ **5 failed**；
- `S: 's'` → `'high'`（桶名退回旧词，D2 反例）→ **4 failed**；
- 恢复原实现 → **13 passed**。

**未覆盖 / 未做**（不是待验证，是本单刻意不做）：

- 结果页 `ResultsPage.tsx` 的风险徽标加了 `fallbackToRaw`，但**没有**页级用例（本仓无
  `ResultsPage.test.tsx`）。该行为由 `status-badge.test.tsx` 的 `fallbackToRaw` 用例与
  报告页同形状用例覆盖；为它另建一套页面 fixture 判为超出本单最小范围。
- 告警 severity 与级别共用 `kind="risk"` 一张表这件事**没解决**，只是写明（D5 未裁决）。
- 报告 DTO 的 `risk_summary.counts.by_severity` 键名**没改**（见 Revisit）。
- 生产读数变化**未做线上验证**：修复前所有报告页风险徽标恒「未知」，合入后会一次性变成
  S/A/B。按 ADR §4 这是修复而非新增风险，但"上线后读数突然变多"必须在收口评论里向运维说明。

## Revisit

- **D5 裁决后**：`kind="risk"` 应拆成两张表（级别轴 / severity 轴），届时
  `RISK` 表里的 `HIGH/MEDIUM/LOW` 移走，本 note 与 `status-badge.tsx` 的注释前提同时失效。
- **色阶三处仍不一致**：徽标 `B` = success、饼图 `b` = success，而报告页数字用的
  `RISK_RATING_TEXT.B` = `text-warning/80`。ADR-0045 D3 说"与既有 `RISK_RATING_TEXT` 对齐"，
  但它给不出 B 的 success/warning 二选一。要统一应连同 D5 一起判，别在词表单里顺手改。
- **同一 DTO 族里大小写并存**：分布桶用 `s/a/b/unknown`（summary），趋势桶用
  `S/A/B/UNKNOWN`（trend），报告风险用 `counts.by_severity.{S,A,B}`。这是三个不同响应体，
  本单按 ADR 只统一"级别值域"，不统一"字段风格"。若将来合并报告/汇总 DTO，
  `by_severity` 这个名字（它装的其实是 by_level）应一并正名。
- **gauge 的"拉取式端点喂推取式 gauge"语义问题仍在**（#2365 那轮已记），本单只改标签值。
- **`UNKNOWN` 占比（覆盖率）不在本单目标内**，继续由 #2365 跟踪。
