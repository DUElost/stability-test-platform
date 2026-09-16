# Job 报告页状态徽标恒「未知」：改接对外词表 + 三面同源门禁（#2418）

Status: implemented
Class: bug-fix

## Decision

**报告页拿错了词表；修消费方，并把「后端词表 → 前端徽标键集」变成机器判据。**

后端把库内 `JobStatus` 映射成对外词表（`PENDING→QUEUED` / `COMPLETED→FINISHED` /
`ABORTED→CANCELED`），前端 `StatusBadge` 按 `kind` 查表、**缺键落到 FALLBACK「未知」**。
`RunReportPage.tsx:155` 用的是 `kind="job"`（库内词表那张 `JOB` 表），而它拿到的
`report.run.status` 是**映射后**的对外值 ⇒ 除 `FAILED` 外全部缺键：

- `COMPLETED` 的 job 报告页显示「未知」（issue 在 dev `e6ff7e1f` 实测）；
- `ABORTED` 显示「未知」（issue 在生产只读 GET 实测）——与真正的未知态**不可区分**；
- 结果页正确，因为它用的是 `kind="job-result"`（`JOB_RESULT` 表键集 = 后端映射值域全集）。

报告页是对外交付口径（可导出 Markdown/JSON 给人看），把「成功完成」显示成「未知」＝误读。
同形状先例 #786（`status-badge.tsx` 注释里就写着「缺键落到 FALLBACK 与『已断开』混淆」），
这次换了消费方而没人发现——**因为没有任何判据把两张表和后端词表绑在一起**。

落法三件：

1. **消费方改对表**：`RunReportPage` 状态徽标改 `kind="job-result"` 并开 `fallbackToRaw`
   （未识别值回显原文而非「未知」，与 #786 指引一致）。
2. **三面同源门禁** `tests/test_status_vocabulary_drift.py`（纯文本、离线、PR 路径）：
   - 后端**两处** `_JOB_STATUS_TO_RUN_STATUS` 的值域 ⊆ 前端 `JOB_RESULT` 键集；
   - `JobStatus` 枚举全集 ⊆ 前端 `JOB` 键集（库内词表那张表也不许漂）；
   - 两处后端映射键集必须相同、值除豁免外必须一致。
   全部带「解析器自守」断言（哨兵项缺失即红），沿用 `tests/test_frontend_api_types_sync.py`
   范式——否则解析失效会让三条恒真，成为一条假绿门禁。
3. **页面级判据** `frontend/src/pages/runs/RunReportPage.test.tsx`：直接断言「任务信息 →
   状态」这一行渲染出 完成/已中止/排队中 与原文回显。**光有 status-badge 单测拦不住把
   kind 挑错**——那张表本身一直是对的，错的是消费方。查询用「状态」标签所在行限定，
   避免把同页风险徽标的「未知」误算进来。

另外给 `JOB` 表补了词表归属注释（只有直接读库内字段的消费方用它；API 出参走
`JOB_RESULT`），因为报告页改完之后 `kind="job"` 在前端已**零非测试消费方**——这张表
正是下一次挑错的候选。

## Alternatives

- **往 `JOB` 表补 `FINISHED/CANCELED/QUEUED` 三个键**：被否。那是把两套词表混进一张表，
  正是 #2419 的成因形状；而且混进去之后，「这个字段到底是库内状态还是对外状态」在代码里
  就再也问不出来了。
- **`StatusBadge` 自动探测词表**（两个表都查一遍）：被否。`kind` 参数随之失去意义，且隐式
  猜测会把下一次漂移藏得更深——问题恰恰需要**显式选表**。
- **顺手删掉 `kind="job"`**：被否（延后）。改完本单它确实零消费方，但删表要动
  `StatusBadgeKind` 联合类型与既有单测，属前端瘦身面，与「修徽标显示错」不是同一件事。
  已登记在 Revisit。
- **后端不再映射、直接回库内词表**：被否。`results` 列表页已按对外词表消费，改后端等于
  改已发布 API 契约，收益却只是把错处从前端挪到后端。
- **用 TypeScript 字面量类型把词表编码进类型系统**：被否（超范围）。跨语言没有共享类型源，
  要做得引入 codegen 或运行时 meta 接口。本单用门禁达到同等拦截效果；真要升级见 Revisit。

## Verification

只列实跑项。

- `pytest tests/test_status_vocabulary_drift.py` → **4 passed**（新增，PR 路径）
- `npx vitest run src/pages/runs/RunReportPage.test.tsx` → **4 passed**（新增）
- **红绿双向（5 个破坏性对照，全部 on-target）**
  - R1 报告页退回 `kind="job"` → 页面测试 3 红（`SOMETHING_NEW` 那条仍过，因为
    `fallbackToRaw` 与 kind 无关——判据分工清楚，不是漏红）
  - R2 关掉 `fallbackToRaw` → 精确红在第 4 条「回显原文」用例
  - R3 前端 `JOB_RESULT` 删 `FINISHED` 键 → python 门禁两条 param 同时红
  - R4 后端新增 `PAUSED→ON_HOLD` 且前端无键 → 值域判据 + 两表一致性判据同时红
  - R5 `results.py` 侧多加一个键（两表分叉） → 键集一致性判据红
  变异后文件均从 `/tmp` 备份还原，`git diff --stat` 回到 2 files / +12 / -1。
- `npx vitest run`（全量前端）：**920 passed / 2 failed**。那 2 条红在
  `src/pages/execution/PlanRunDetailPage.test.tsx`，与 `origin/main` 干净 worktree 上实跑的
  失败名单**逐字相同**（用例名一致），非本单引入——本单未触碰该文件。
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (10 gates)`（含 type-check /
  knip / compileall / orphan-models / gov-surface / ai-work self-test）
- `env -u DATABASE_URL pytest tests/ --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py`（CI 同款 PR 子集）→ **1237 passed**（含本单新增 4 例）
### 判据落在哪条 CI 路径（重要）

`frontend-check`（唯一跑 vitest 的 job）条件是 `github.event_name != 'pull_request'`——
**PR 路径不跑前端单测**。所以本单的页面级用例是「夜间 + 本地」判据；**PR 必拦的那道**是
`tests/test_status_vocabulary_drift.py`（随 `pr-agent-tests` 跑）。两者分工明确：前者守
「消费方挑对表」，后者守「词表不许漂」。只留后者会漏掉 R1/R2 这类选错 kind 的回归，只留
前者则 PR 侧无防线——这也是 #2418 能存活至今的机制原因。

- 本机为生产控制面候选：本单**未做任何生产侧探测**，现象证据全部引自 issue 的只读实测；
  本地验证只跑离线单测与门禁。

## Revisit

- **`UNKNOWN` 的值分歧仍未裁决**：`report_service` 把 `UNKNOWN` 映射成终态 `FAILED`，
  `results` 映射成非终态 `RUNNING`——同一个 job 在列表与报告页可以显示两种状态。本单
  **不裁决**，只把它放进豁免表 `_KNOWN_VALUE_DIVERGENCE`，并加了反向判据：实际分歧集
  必须**等于**豁免集，所以 #2419 真正收敛词表时会强制回来删掉这条豁免（豁免不回收就是
  下一个 #2419）。
- **`kind="job"` 现在零非测试消费方**：确认没有新消费方后，删 `JOB` 表与 `"job"` 联合成员
  比留着更防复发。前端瘦身另案。
- **报告页其余契约项**：#2420 列的「`runId` 实为 `jobId` 且无归属校验」「live/cached 信封
  不一」等在同文件相邻位置；动那里时先跑本单的 `RunReportPage.test.tsx`。
- **文本解析的失效边界**：若后端映射哪天不再是字面量 dict（动态生成），解析器自守断言会红。
  那时的正解是**导出一个运行时真值源**（如 `GET /api/v1/meta/statuses`）让前端与门禁都消费
  它，而不是放宽解析规则。
