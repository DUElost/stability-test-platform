# ADR-0045：风险分级的对外词表——统一到 S/A/B，翻译只留在前端

- 状态：**Accepted** v1.0（2026-09-17 owner 裁决：「列表侧与报告侧统一到 S/A/B」；由
  [#2494](https://github.com/DUElost/stability-test-platform/issues/2494) 触发，是
  [#2419](https://github.com/DUElost/stability-test-platform/issues/2419) 与
  [#2365](https://github.com/DUElost/stability-test-platform/issues/2365) 收口之后的残余口径）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-17
- 决策者：平台研发组（owner 裁决 2026-09-17）
- 标签：风险分级, 词表契约, 展示层翻译, #2365, #2419, #2494
- 关联：[ADR-0025](./ADR-0025-run-console-and-command-execution.md)（v2.5 D13 风险趋势桶首次引入 S/A/B/NONE）、
  [#2418](https://github.com/DUElost/stability-test-platform/issues/2418)（同族词表漂移缺陷，
  判据与测试风格沿用 `tests/test_status_vocabulary_drift.py`）

## 1. 背景

风险分级的**判定链已经唯一**：`backend/services/log_observation.py` 用活链
（`device_log_event` 台账为权威 + 未链接信号）经 `_subtype_levels()` → `_worst_level()` 产出级别，
值域 `S/A/B`；无判定依据的 job 不进入结果（`aggregate_risk_levels_by_job` 的 docstring 明写
「`None` 表示**没有可判定的事件**（风险未知），不是「低风险」」）。这一步由 #2365 的 PR 落地，
#2419 的「列表侧读 `log_summary` 文本」旧链已删除。

但**判定链收口做在服务层，展示层的翻译表留在了后端**，于是同一个「job 风险级别」在契约面上
仍有 4 种形状（读 `origin/main` tip `dc0254c1` 得到的事实，非推测）：

| 面 | 对外值域 | 位置 |
|---|---|---|
| 报告 DTO 风险级别 | `S/A/B` | `risk_summary.risk_level`（前端类型 `frontend/src/utils/api/types.ts:190` 已声明 `'S' \| 'A' \| 'B'`） |
| 结果页最近运行 | `HIGH/MEDIUM/LOW/UNKNOWN` | `backend/api/routes/results.py:124` `_RISK_LABEL_BY_LEVEL = {"S":"HIGH","A":"MEDIUM","B":"LOW"}` → `:128-133` `_risk_level_label()` |
| 首页风险分布 | 字段名 `high/medium/low/unknown` | `results.py:47-51` `RiskDistribution` + `:125` `_RISK_BUCKET_BY_LEVEL` |
| 风险趋势 | `S/A/B/NONE` | `results.py:74-77` `RiskTrendBucket`（第四种词 `NONE`） |

直接后果是**读数不可信这件事还留在原处**，而且这次在报告页自己身上：

```text
frontend/src/pages/runs/RunReportPage.tsx:105   const riskLevel = risk.risk_level || 'UNKNOWN'   // 实际值 S/A/B
                                          :204   <StatusBadge kind="risk" status={riskLevel} />
frontend/src/components/ui/status-badge.tsx     RISK 表只有 HIGH/MEDIUM/LOW/UNKNOWN，未传 fallbackToRaw → 回落「未知」
同卡 :219-221                                    直接渲染 counts.by_severity.S / A / B 三个数字
```

即同一张卡片上徽标写「未知」、下方分布写「S:0 A:0 B:1」。这与 #2418（Job 报告状态徽标恒「未知」）
是**同一类缺陷的第二个实例**：数据没坏，是徽标的词表键没对齐；#2418 的修法是换对词表 +
`fallbackToRaw`，本 ADR 把同一判据推广为**契约规则**，避免第三次复发（下一个新风险面又自带一套词）。

## 2. 决策

- **D1（唯一判定源）**：job 风险级别只能由 `log_observation` 的活链判据产出。任何新页面、新指标、
  新导出**不得**自行解析日志文本、不得另建级别来源。此为对 #2365 现状的追认。
- **D2（对外词表 = `S|A|B|UNKNOWN`）**：级别出 API **不再翻译**。具体到契约面：
  - `RecentRun.risk_level` 值域改为 `S|A|B|UNKNOWN`；
  - `RiskDistribution` 字段改为 `s/a/b/unknown`；
  - `RiskTrendBucket` 的 `NONE` 并入 `UNKNOWN`（语义相同：无判定依据）；`B` 的含义不变
    （有事件但非 S/A），**零事件不得算进 `B`**；
  - 后端删除 `_RISK_LABEL_BY_LEVEL` / `_RISK_BUCKET_BY_LEVEL` 这类**服务端翻译表**。
- **D3（翻译只发生在前端一处）**：「高/中/低」是**文案**不是词表，只存在于
  `frontend/src/components/ui/status-badge.tsx` 的 `RISK` 表（补 `S/A/B` 三键：
  S→高 destructive、A→中 warning、B→低 success，与既有 `RISK_RATING_TEXT` 对齐）与饼图/趋势的桶文案。
  任何组件不得再自带一份 S→高 的映射。
- **D4（`UNKNOWN` 不可折叠）**：保留 #2365 的覆盖率语义——「没有采到异常」≠「查过且没风险」。
  因此 `UNKNOWN` 既不许压成 `B`/低，也不许在饼图里被省略；覆盖率仍由 #2365 单独跟踪。
- **D5（告警严重度是另一条轴，本 ADR 不动其值域）**：`backend/api/schemas/run.py:132`
  `severity: Literal["HIGH","MEDIUM","LOW"]` 描述的是**单条告警**（含阈值类告警 `ANR_DETECTED`、
  `CRASH_DETECTED`），与「job 最高风险级别」不是同一对象。现状 `report_service.py:333-352` 由
  `risk_level` 派生时把 S 与 A 都落成 `HIGH`（靠 `code` 区分）——是否算信息丢失由后续单独立裁决，
  **不在本 ADR 范围**。同一徽标 `kind="risk"` 同时服务两条轴（`RunReportPage.tsx:204` 与 `:253`）
  正是本 ADR 要消除的根因之一，D3 落地时两条轴需各自明确。
- **D6（直接改，不留双写窗口）**：本仓前端与后端同仓同发布，且硬不变量要求
  `frontend/src/utils/api/types.ts` 与后端 schema 同步，因此**不做别名、不做双字段过渡**。
  唯一前置条件是清点**仓外消费者**：`RiskDistribution` 字段名与趋势桶词一旦出现在 Prometheus
  规则/面板或运维脚本中，需与 #2485（风险 gauge 归零与作用域）同批或排在其后。

## 3. 备选方案与权衡

| 方案 | 内容 | 结论 |
|---|---|---|
| **A（采纳）** | 对外统一 `S/A/B/UNKNOWN`，翻译只在前端 | 与判定链的原生词表一致；新增面零成本复用；改的是「翻译的位置」而不是「判定的位置」，不动已收口的服务层 |
| B | 反向统一为 `HIGH/MEDIUM/LOW/UNKNOWN`，把 S/A/B 只当内部实现 | 否决：`B` 与「无判定」在外部词表里都可能被读成 LOW，正是 #2365/#2419 花力气消掉的歧义；且报告 DTO 与趋势已是 S/A/B，改名面更大 |
| C | 保留双词表，前端做映射兼容 | 否决：「两处翻译」本身就是根因，兼容层会让第四个面继续自带一套词 |
| D | 把告警 severity 一起并入 S/A/B | 推迟（见 D5）：阈值类告警没有「job 级别」语义，强并会伪造映射，且连带 JIRA 草稿链 |

## 4. 影响

- **API 破坏性变更**（三处值域/字段名）：`/api/v1/results/summary` 的 `recent_runs[].risk_level`、
  `risk_distribution`，`/api/v1/results/risk-trend` 的桶词。前端同 PR 改完。
- **零数据迁移**：风险级别是**派生值**，不落库（无 `risk_level` 列、无快照表携带旧词），
  因此改词表不需要任何 backfill，也不与「已发布版本不可变」类不变量冲突。
  这一点是本裁决可以「直接改、不留双写」的根本原因。
- **可观测性**：Prometheus 侧标签值若沿用后端词表，需按 D6 清点后随批调整（与 #2485 对齐）。
- **历史 UI 读数**：修复前所有报告页风险徽标恒「未知」，修复后会一次性变成 S/A/B——
  运维侧会看到「读数突然变多」，这是修复而非新增风险，需在收口记录里说明。

## 5. 遗留与终态出口

- 本 ADR 即终态口径，不留过渡态：实现 PR 完成后 D2/D3 同时成立，没有「临时兼容层」需要退役。
- 遗留 1：告警 severity 轴是否并入（D5）→ 若 owner 决定并，出 v2.0 修订，另单执行。
- 遗留 2：`UNKNOWN` 的占比（覆盖率）不在本 ADR 目标内 → 继续由 #2365 跟踪。
- 遗留 3：#2419 已关，其评论中「报告页风险徽标恒未知」的建议修法未落成单 → 由 #2494 承接本 ADR 的可执行清单。

## 6. 实施与验证（本 ADR 的钉子）

落地位置（实现 PR 必须逐条命中）：

1. `backend/api/routes/results.py`：删 `:124-125` 两张翻译表与 `:128-133` `_risk_level_label()`，
   `RecentRun.risk_level` 直出级别（无判定 → `UNKNOWN`）；`RiskDistribution` 字段改 `s/a/b/unknown`；
   `RiskTrendBucket` 的 `NONE` → `UNKNOWN`。
2. `frontend/src/components/ui/status-badge.tsx`：`RISK` 表补 `S/A/B` 三键；
   `frontend/src/utils/api/types.ts` 的 `risk_level` / `RiskDistribution` / `RiskTrendBucket` 同步（硬不变量）。
3. `frontend/src/pages/Dashboard.tsx` 与结果页：饼图桶名与列文案改由徽标表出，不再自带映射。

判据（缺一不算完成）：

- **契约对拍测试**（新增，风格沿用 `tests/test_status_vocabulary_drift.py`）：同一 job 集合下，
  `recent_runs[].risk_level`、`risk_distribution` 计数、`risk-trend` 桶、报告 DTO
  `risk_summary.risk_level` 必须与 `aggregate_risk_levels_by_job` / `aggregate_risk_summary` 逐 job 一致；
  并断言后端源码不再出现 S→HIGH 类映射（静态判据，防复发）。
- **页级用例**：`RunReportPage.test.tsx` 断言 `risk_level='S'` 渲染为「高」，且与同屏 S/A/B 分布不矛盾；
  同时删掉现有那条「避免把风险徽标的『未知』算进来」的注释前提（它把这个缺陷固定成了测试期望）。
- **静态清点**：`rg "HIGH|MEDIUM|LOW" frontend/src` 在风险**级别**语境下 0 命中（告警 severity 除外，见 D5）。
- 门禁位：`pr-typecheck`（前端类型同步）+ `pr-agent-tests`（契约测试）。
