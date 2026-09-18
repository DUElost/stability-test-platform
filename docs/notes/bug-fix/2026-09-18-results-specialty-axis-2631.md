# /results「按测试类型统计」改按 specialty 聚合：轴不得用 Plan.name（#2631）

Status: implemented
Class: bug-fix

## Decision

`GET /api/v1/results/summary` 的 `test_type_stats` 原本按 **`Plan.name`** 分组，而图标题写
「按测试类型统计通过/失败」。本仓对「测试类型」有权威定义且**不是** Plan 名：

- `docs/operations/new-specialty-onboarding-runbook.md:33`：specialty（专项）＝**测试类型维度标签**，
  Plan 的下拉字典（ADR-0029 D6）；
- 字典侧是**有界**的（dev 内 6 值），Plan 名是用户自由输入且**只增不减**——这张图每次新建
  Plan 就恶化一分，同一个专项有多个 Plan 时还会被拆成 N 条，看起来像 N 种测试类型。

裁决：**改聚合维度，不改文案**（文案本来就是对的，错的是实现）。三处具体选择：

1. `type_query` 走 `Plan LEFT JOIN specialty`，按 `(Specialty.id, display_name, sort_order,
   JobInstance.status)` 分组——`LEFT JOIN` 是必须的：未设专项的 Plan **也要出现在图上**；
2. 未设专项归入模块常量 `UNSPECIFIED_SPECIALTY_LABEL = "未设专项"`，**不回落 Plan 名**。
   静默回落正是本单的成因：回落一次，双标就重新长回来，而且这次连注释都不再撒谎；
3. 聚合字典的键取**标签**而不是 `Specialty.id`：`display_name` 无唯一约束，按 id 聚合会给
   同名专项画出两条一模一样的图例（读数被摊薄且不可分辨）。排序取字典表 `sort_order`
   （与 `/orchestration/plans` 那排专项 chip 同序），未设专项用哨兵值恒排最后。

**快照 vs 当前归属（issue 第 3 点要求显式决策）**：本次取**当前归属**——`specialty_id` 挂在
`Plan` 上，某 run 之后其 Plan 换专项，历史 run 的读数会跟着挪动。理由有三：(a) 旧口径
（`Plan.name`）本来就是当前值，本单没有引入新的时间语义，只是换分组键；(b) `JobInstance` /
`PlanRun` 都没有 specialty 快照列，改成快照要迁移 + 回填历史；(c) 与同文件的 project 过滤
不对称是**有意的**——`plan_run.project_id` 是 ADR-0029 D5/M-b 已冻结的快照语义（归属会变、
且已落库），专项是类型标签、变更频率与语义分量都低一档。代价写进 Revisit。

`frontend/src/utils/api/types.ts:319` 的 `TestTypeStat` **shape 不变**（`type: string`），只补
一行口径注释——按 AGENTS.md，前端 API 类型是同步入口，「type 到底是什么口径」必须在这一眼
能看到的地方写清。前端图表与文案零改动：轴换了以后标题即自洽。

## Alternatives

- **改文案为「按 Plan 统计」**：否决。issue 第 4 点已把它列为另一条合法路径，但前提是**同时**
  修订权威定义（runbook:33 + ADR-0029 修订）；用文案去追认一个基数无界的聚合键，等于把
  「测试类型」这个词永久放弃，且现存的 6 值字典继续闲置。
- **只在前端把 Plan 名映射成专项**：否决。前端拿不到 `specialty_id`（响应里没有），要么改
  DTO 要么多发一次字典请求，两套口径的**产生点**从后端挪到了渲染层，漂移面更大。
- **给 `plan_run` 加 `specialty_key` 快照列**：本单不做（见 Revisit 的触发条件）。它改的是
  数据模型和派发路径，属 ADR-0029 D5 快照语义的扩展，需独立裁决。
- **保留 `Plan.name` 兜底 + 未设专项时显示「(未分类)」**：与选定方案等价，但兜底名一旦写成
  `plan.name or UNSPECIFIED` 就会被下一个人「顺手」改回回落——用**显式常量 + 一条钉它的用例**
  比用文案更耐久。

## Verification

环境：本机隔离库（`env -u DATABASE_URL`，testcontainers PG；`PYTHONDONTWRITEBYTECODE=1`），
未触碰生产控制面与生产库（`ip-leak` 门禁也要求仓库里不出现该地址字面量）。

- 基线：`backend/tests/api/test_results.py` → **14 passed**（原 8 条 → 14 条：新增 5 条轴判据 +
  1 条同名合并）。
- **既有用例本身是共犯**：`test_summary_aggregates_from_job_instance_chain` 原先断言
  `type_stats[smoke_type]`（Plan 名），把错误口径钉成了契约——已改为断言专项 display_name，
  并**双向**钉住「Plan 代号不得出现在轴上」。
- 变异自证 5 条，全部 on-target（每条跑完还原、结尾复跑基线）：
  - `M1` 退回按 `Plan.name` 聚合 → **7 红**（含上面那条既有用例，正是它证明了旧断言站错边）；
  - `M2` 未设专项被丢掉（`outerjoin`→`join`） → **2 红**（固定桶 + 顺序）；
  - `M3` project 过滤的 `PlanRun` join 失效 → **1 红**（换分组键时最容易丢的就是它）；
  - `M4` 出图顺序不按 `sort_order` → **1 红**；
  - `M5` 聚合键改回 `Specialty.id` → **1 红**（同名专项画出两条）。
- 一条判据是**值域级**的（`test_axis_values_are_resolvable_in_the_specialty_dict`：轴上每个值
  都必须在 specialty 字典里或等于固定桶），所以将来任何人把聚合键换成别的自由文本字段也会红，
  而不是只有换回 `Plan.name` 才红。用例内还断言了「Plan 名确实不在字典里」，防这条判据恒真。
- 门禁：见本 PR 后续评论 / CI 结果。

## Revisit

- **若出现「历史 run 的测试类型必须冻结」的真实需求**（例如按专项出周报、事后重算不能变），
  当前口径会给出错的数：那时需要 `plan_run`（或 `job_instance`）级 specialty 快照列 + 迁移回填，
  并走 ADR-0029 D5 的快照语义扩展，而不是在查询里猜。触发信号：有人开始按 `project_key` 之外
  的维度做「不可重算」的历史报表。
- **`specialty` 字典与产品现状漂移**：如果「未设专项」桶在生产上占比很高，问题不在这张图，
  而在 Plan 建档时专项没被要求（`plan.specialty_id` 可空）。本单把它显式化就是为了暴露这条；
  读数应在 #2546（跨 ADR 语义所有权评审）或下一次 dev 栈普查时看一次。
- 本单是 **#2546** 登记的一个双标实例（同族见 #2494/#2418/#2365）。若 #2546 的评审给出
  「一个概念唯一 owner」的统一机制（如词表注册表 + 展示面强制引用），这里的
  `UNSPECIFIED_SPECIALTY_LABEL` 与字典读取应收编到那个单源，而不是继续各自实现。
