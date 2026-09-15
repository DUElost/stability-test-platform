# 归档流水线一处读完 + 手动扫描「最终轮」入口（#2185）

Status: implemented
Class: feature

## Decision

`DedupReportCard`（去重报告卡）两处改动，**范围刻意限定在 `frontend/src/components/plan-run/`**：

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

### 刻意没做的两件事（不是遗漏，是并发纪律 + 归属约束）

- **页面级收拢（退役 `ArchiveStatusCard` 的 `Scan:` 行）**：`ArchiveStatusCard` 实为
  「存储运维概览」（HDD/清理/溢出/signal 链接健康），其与流水线重叠的只有一行
  `Scan: {scanStatus}` 字符串。要退役它必须改 `frontend/src/pages/execution/…`，而该目录
  正被在窗 Execution `fix-2051-2054` 声明——**本 PR 不越界**。流水线建成后那行已成冗余，
  移除属后续小改。
- **流水线的平台维度**（原 issue 提的「阶段 × 平台」）：per-platform 结果在
  `run_context.merge_platforms`（#2174 后端已落），但前端**零引用**，要用它需二选一：
  ① 由页面透传新 prop（页面被上面那条占着）；② 在 `frontend/src/utils/api/types.ts` 登记类型
  （该目录在窗被两个 Execution 声明：`fix-2187-dedup-ok-normalize` 与 `fix-2051-2054`）。
  两条路本 PR 都避开。**故本 PR 只交付"阶段 × 完成度 + 原因码"，平台维度留在 issue #2185**。

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
- **顺手把 `merge_platforms` 读出来显示**：**否决**（本轮）。需越界改被声明目录（见上）。

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

## Revisit

- **页面/`utils/api` 的在窗声明释放后**：做两项收尾——① 退役 `ArchiveStatusCard` 的 `Scan:` 行
  （流水线已覆盖）；② 用 `run_context.merge_platforms` 补流水线的**平台维度**（同时把该键登记进
  `types.ts`，避免长期靠就地收窄读 `unknown`）。
- **若流水线条数继续增长**（例如加入 retention / delivery）：应改为服务端聚合的 facet 接口，
  而不是继续在卡片里拼前端判定——前端判定一旦超过 4 个阶段就会开始重复后端已有的口径。
- **`unknown` 的文案若被反馈"太长"**：可缩短为「—（无记录）」，但**不要**退回"隐藏"——
  隐藏与"没问题"不可区分，是本条要消除的歧义。
