# R01–R15 独立核验报告：覆盖、修复有效性与闭环可持续性

Status: implemented
Class: process

## Decision

新增只读审计报告
[`docs/reviews/REVIEW_INDEPENDENT_VERIFICATION_2026-09-11_4a955874.md`](../../reviews/REVIEW_INDEPENDENT_VERIFICATION_2026-09-11_4a955874.md)，
作为"R01–R15 之后"元审查的**独立第二意见**，与已合入的
[`REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fb87d5f1.md`](../../reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fb87d5f1.md)
（`CA-*` 编号）同题独立完成，供综合轮按 Mode C「先独立、后汇聚」并表。本文使用 `IV-*` 前缀避免编号冲突。

三项独立结论（不是复述 `CA-*`）：

1. **覆盖（IV-C03）**：R 区之外存在未消化的存量缺陷池——2026-09-03 四域审查批次
   （label `code-review-2026-09`，台账 #827）仍有 **44 条 open**，其中 14 条被 R 台账以"既有"引用但一条未关，
   **30 条与 R 区零关联**（抽查 #789 无修复 PR 交叉引用）。因此 CA 稿"不存在整块被遗忘的业务域"的表述
   在业务域层面成立，但会系统性高估"已覆盖且已消化"的比例。
2. **修复可持续性（IV-Q04，与 CA-Q04 分歧）**：唯一承载后端全量验证的自动门禁 `main-ci-backstop`
   在 2026-09-08、09-09 连续 failure，期间 09-09/09-10 共 72 个 PR 照常合入，失败**无阻断力**；
   观测时点存在一次人工 `workflow_dispatch` 全量 CI（run 34517671739）。即"可持续=条件成立"的那一项
   条件在当前**实际不成立**，而非仅设计上的已知取舍。
3. **闭环模式（IV-W02）**：本交付物自身即有 **4 条在窗 Execution 同题并行**
   （codebuddy 已合入 PR #1307、cursor 写 synthesis、codex 占 docs/reviews、本文）。
   overlap 检测按契约设计只给 hint、从不禁止——这是机制按设计工作的结果，同时构成双源记账风险的活样本。

另附对平行稿的更正（IV-X01/X02）：cursor synthesis §7 台账映射与 §3.1 计数失实；
`CA-*` 稿三处需补正（覆盖结论、CA-Q04、§5 平行审计清点）。

## Alternatives

- **不另写文档，改为在 PR #1307 的 `CA-*` 稿上追加评论**——放弃：该稿已合入 main，
  正文修订需新 PR；且综合轮需要一份可引用的独立载体，评论不可作为汇聚输入。
- **与前稿合并为单一文档**——放弃：Mode C 的价值在于两份独立结论的差异本身；
  合并会掩盖 IV-Q04 与 CA-Q04 的判定分歧，也无从检验"独立复现"是否成立。
- **同时改 `PROJECT_REVIEW_PLAN.md` §5 以消除总纲漂移**——放弃（本文仅记录该漂移）：
  §5 与 DOC-MAP 正被 cursor 在窗 Execution 占用（`docs-r01-r15-synthesis-2026-09-11`），
  共享元文件同一时间只由一个 Execution 修改；漂移的收口属综合轮裁决项 IV-D01/CA-D01。
- **为本次审计开 issue**——放弃：与前稿一致，审计结论由综合轮按 IV-D*/CA-D* 逐项裁决后落地载体。

## Verification

- 未运行 pytest / Vitest / 门禁 / 迁移 / 部署；未做目视与运行时验证；未触发 CI；未新建或关闭 issue。
- 全部计数为 2026-09-11 观测值，口径与复现命令写在报告 §7：
  `gh issue list --state all --limit 1500`（R 发现计数）、
  `gh api .../issues/<N>/timeline`（关单/PR 关联/reopen）、
  `gh pr list --state merged --limit 500 --json mergedAt,mergedBy`（合入身份按日）、
  `gh run list --workflow main-ci-backstop.yml`（夜间兜底近况）、
  `gh issue list --state open --label code-review-2026-09`（存量缺陷池）。
- 关键交叉事实均双源核对：关单秒级时间戳（合入 API vs issue timeline）、
  09-09 起合入身份 100%（`gh pr list` 全量 vs 逐日分组）。
- 本 PR 门禁：`python scripts/run_gates.py check:quick`（含 gov-surface 与 ai-work gate，见 PR 描述实测输出）。

## Revisit

- 综合轮对 `IV-D08`（44 条存量的归口）与 `IV-D09`（验证网阻断力）作出裁决后，
  本文件 §6 相应条目标注裁决结果与落地载体；若裁决为"维持现状"，
  应把维持理由写入本 §6，而不是留空——否则下一轮元审查会以新编号重复命中同一结论。
- `main-ci-backstop` 恢复绿灯后，IV-Q04 的判定需按新数据更新（本文只断言"观测时为红且不阻断"，
  不断言该网永久失效）。
- cursor synthesis 合入后，IV-X01 两处更正若已被其自行修正，本节与报告 §4 应标注"已消解"。
