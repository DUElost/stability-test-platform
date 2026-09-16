# 归档流水线一处读完 + 手动扫描「最终轮」入口（#2185）

Status: implemented
Class: feature

## Decision

`DedupReportCard`（去重报告卡）两处改动，首段**范围刻意限定在 `frontend/src/components/plan-run/`**；
同日第二段（见下「收尾」）因受阻声明转为 `liveness=STALE` 而扩到 `frontend/src/pages/execution/`：

1. **归档流水线（四阶段一处读完）**：新增 `stage` 条（scan → upload → merge → extract），
   把原先散在三处的"走到哪一步 / 卡在哪"收拢：

   | 阶段 | 数据来源 | 判定 |
   |---|---|---|
   | 扫描 | `dedup/status.archive`（host 级完成度 / 未回执 / `scan_failed`） | `fail`（零报表）> `ok`（done≥triggered）> `warn` |
   | 上送 | `run_context.upload_summary` | `failed>0` → fail；`ready===false` → warn；否则 ok |
   | 合并 | `dedup/status.artifacts` 的 `merge_result_xls` 计数 | 有产物 ok；有 scan 产物但无 merge 产物 → warn（"未合并"） |
   | 提取 | `run_context.extract` | `missing>0` → fail；`copied≥targets` → ok；否则 warn |

   **关键语义：无记录时明说无记录（`unknown` + 原因），不显示成 0，也不静默隐藏。**
   "0 条"与"不知道"是两件事——前者会让人以为该阶段已空跑完。存量行为是
   `archive` 缺失时整块完成度**静默消失**，现在改为「未开始（无本轮 host 计数）」。
   同理上送/提取缺失时说「run_context.x 缺失」。

2. **手动扫描「最终轮」入口**：扫描按钮此前固定 `scanMut.mutate(false)`，而
   `POST /plan-runs/{id}/dedup/scan` 本就支持 `is_final`（自动链在终态也走最终轮），
   于是操作者想让本轮"收口"时**没有入口**。现加「最终轮」勾选，按钮按勾选值传参，
   `title` 随状态变化（非最终轮 / 作为最终轮）。

**涉及**：`frontend/src/components/plan-run/DedupReportCard.tsx`（+ 用例）。**无后端改动、无契约改动**
（`triggerScan(runId, isFinal)` 早已支持该参数）。

### 收尾（同日第二段）：退役冗余 `Scan:` 行 + 补平台维度

首段刻意避开了两项（因 `frontend/src/pages/execution` 与 `frontend/src/utils/api` 被在窗
Execution `fix-2051-2054` 声明）。第二段落地它们——前置判断变了：该 Execution 已转
`liveness=STALE`、无 PR，且本仓 scope 重叠是 **advisory**（registry 原文：「hint，从不禁止修改」）。
仍保留一条纪律：**`utils/api/types.ts` 依旧不碰**（同类声明在窗，能就地收窄就不越界）。

1. **退役 `ArchiveStatusCard` 的 `Scan: {scanStatus}` 行**——先取证再删：`scan_status` 由
   `PlanRunArtifact` 计数派生（`plan_runs.py`：有 merge 产物→`merged`、只有 scan 产物→`scanned`、
   都没有→`pending`），正是本流水线「扫描 / 合并」两阶段已表达的三种状态 ⇒ **纯显示冗余**。
   **但页面自身的判断不动**：`finalArchiveReady`（#780 归档提示）仍读
   `archive.scan_status === 'merged'`——删的是卡片里那一行，不是这个字段的用法。
2. **流水线补平台维度**（原 issue 提的「阶段 × 平台」）：读 `run_context.merge_platforms`
   （#2174 后端已落，前端此前零引用），作为「合并」阶段的附加段逐平台渲染：
   - **逐平台独立着色**（`StagePart.tone`）：`ok`→success、`no_input`→muted、
     `skipped_failed`→warn。**`no_input` 明确不是失败**（该平台本轮没有输入），悬停写明；
   - **任一平台被 skip → 「合并」整行降为 warn**：否则"有产物"会把平台级 skip 盖住；
   - **未知结果码原样露出**（`qcom=brand_new_outcome`），不静默吞掉后端新增的结果类型；
   - 结果码不翻译成中文（与 `upload_summary` 的原因码不同：这三档是**机器口径**，
     翻译反而模糊），解释走 `title`。

**涉及（两段合计）**：`frontend/src/components/plan-run/{DedupReportCard,ArchiveStatusCard}.tsx`
（+ 各自用例）、`frontend/src/pages/execution/PlanRunDetailPage.tsx`（两行：停止传 `scanStatus`、
传 `mergePlatforms`）。**无后端改动、无契约改动**。

## Alternatives

- **只在 `ArchiveStatusCard` 里加行、不动 `DedupReportCard`**：**否决**。流水线四阶段的数据
  里三个（archive / 产物 / 上送 / 提取）都在 `DedupReportCard` 已有输入里，改那边等于先跨组件
  搬数据再显示；而 `ArchiveStatusCard` 的输入（host ops 指标）与流水线无交集。
- **缺失时继续隐藏该行**：**否决**。那是原行为，也正是 issue 要治的"状态分散且会静默消失"。
  隐藏的代价是"看不到"被读成"没问题"。
- **缺失时显示 0**：**否决**。`0/0` 与"没有本轮计数"在语义上相反（前者像"已完成空任务"）。
- **把 retention 也做成流水线阶段**：**否决**（本轮）。retention 没有 per-run 状态，只有 host 级
  ops 指标；硬塞进流水线会造一个永远 `unknown` 的行。
- **`is_final` 做成两个按钮（扫描 / 最终轮扫描）**：**否决**。同一动作两个按钮会让误触概率上升；
  勾选 + 动态 title 更省空间，也把"当前会用哪个值"显示在 title 里。
- **顺手把 `merge_platforms` 读出来显示**：**否决**（首段，理由：需越界改被声明目录）。
  **第二段已落地**——改的是前置（受阻声明转 STALE）而非结论；`utils/api/types.ts` 仍不碰，
  故仍以就地收窄读 `unknown`。
- **把 `no_input` 也标成失败色**：**否决**。它表示"该平台本轮没有输入"（无工具/无 org 文件），
  是**正常结果**；标红会让纯 MTK fleet 每轮都显示"平台异常"——正是 #2183 那类假警报的形态。
- **保留 `Scan:` 行并让它与流水线并存**：**否决**。同一屏两处表达同一件事（`merged`/`scanned`/`pending`
  ⟺ 合并/扫描两阶段），且一处在标题栏、一处在正文，读者要先判断"这两个说的是不是一回事"。
- **删 `Scan:` 行时连页面 `finalArchiveReady` 的字段一起换掉**：**否决**（本轮）。那是 #780 的
  行为逻辑，改它属功能变更、需独立论证与回归；本单只退役显示冗余，不碰行为。
- **平台结果码也翻译成中文**：**否决**。这三档（`ok`/`no_input`/`skipped_failed`）是机器口径，
  翻译会模糊"后端到底返回了什么"；与 `upload_summary.incomplete_reason` 的处境相反
  （那里是给操作者看的原因句，翻译有增益）。

## Verification

- `CI=1 npx vitest run src/components/plan-run/DedupReportCard.test.tsx` → **7 passed**
  （改写 2 条 + 新增 4 条）：扫描阶段三段文本与 `destructive` 着色；**缺失时明说**
  （`archive` 缺失 → 「未开始（无本轮 host 计数）」、upload/extract 缺失各有措辞）；
  勾选「最终轮」后 `triggerScan(1, true)`（未勾选为 `false`）；合并阶段"未合并（本轮 scan 产物 1 份）"
  与提取阶段 `缺失 1` 的 `destructive`。
- 前端全量 → **819 passed（106 files）**；`tsc --noEmit` 通过；
  `run_gates check:pr` → **[OK] 18 gates**。
- **一次真实红灯（记录）**：首版流水线扫描行与下方错误块都含「去重状态加载失败」，导致
  既有用例 `findByText(/去重状态加载失败/)` 命中两个元素而失败。**没有改成 `findAllByText`
  绕过**，而是把流水线行改为「未知（状态查询失败）」——同一屏对同一件事说两遍本身就是冗余。

### 收尾段的验证

- `CI=1 npx vitest run src/components/plan-run/{DedupReportCard,ArchiveStatusCard}.test.tsx`
  → **15 passed**（2 files）；新增 3 条：逐平台结果可见且 `no_input` 为 muted 且 title 写"不是失败"、
  被 skip 时「合并」行降 warn（行内圆点变 `bg-warning`）、未知结果码原样露出。
- 前端全量 → **838 passed（107 files）**，其中 **1 个与本改无关的顺序类偶发**：
  `src/pages/assistant/AssistantPage.test.tsx`（助手页，与本改零交集）——**单跑通过**（4 passed）。
- `tsc --noEmit` 通过；`ruff check` All checks passed；`run_gates check:pr` → **[OK] 18 gates**
  （在 `.wt/stp-2185-pipeline` 工作区实跑）。

## Revisit

- **`utils/api/types.ts` 的声明释放后**：把 `merge_platforms`（本单）与 `extract.missing_items`
  （#2186）正式登记进 `RunContext` 类型，并删掉两处就地收窄——**收窄是并发期的权宜，
  长期留着会把"未登记字段"变成常态**。
- **`scan_status` 的彻底退役**：本次只退役显示。若将来把 #780 的 `finalArchiveReady` 也改为
  读流水线口径（例如以 `dedup/status` 的产物计数判定"可提取"），则 `WatcherArchiveOut.scan_status`
  可整体退休——那是一次行为变更，需独立 PR 与回归。
- **若流水线条数继续增长**（例如加入 retention / delivery）：应改为服务端聚合的 facet 接口，
  而不是继续在卡片里拼前端判定——前端判定一旦超过 4 个阶段就会开始重复后端已有的口径。
- **`unknown` 的文案若被反馈"太长"**：可缩短为「—（无记录）」，但**不要**退回"隐藏"——
  隐藏与"没问题"不可区分，是本条要消除的歧义。
