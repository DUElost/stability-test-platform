# ADR-0038 评审汇聚 synthesis（519e346）

Status: implemented
Class: process

## Decision

- 按 issue #1557「汇聚」节执行 Mode C 第 4 步：9 份独立评审（8 harness，全部已合入 main）+ 1 条非独立补充评论去重汇聚为 [`REVIEW_ADR0038_2026-09-13_519e346_synthesis.md`](../../reviews/REVIEW_ADR0038_2026-09-13_519e346_synthesis.md)，R1–R19 为唯一权威映射。总评谱系 9/9 Needs-revision；处置建议 = 修订 v0.2 后转 Accepted。
- 本 PR 只新增 synthesis 稿与本 note；不改 ADR-0038 正文、不做 Accepted 裁决、不开实现单——三者按 issue「目的」节属人工裁决后动作。稿件内嵌 D-1～D-6 二选一清单（各带推荐）供裁决。
- 采集/撰写阶段未读取其他 harness 的评审**结论**以外的任何新输入；汇聚本身即以 9 稿为输入，属 Mode C 汇聚轮的既定语义，非独立评审。
- Registry：`review-adr0038-synthesis-519e346`（role=review，scope docs/reviews，issue #1557，test_impact=none）；issue 级查重按 Mode C 以 `--force` 显式越过（在窗 `adr-0038-review-link` 亦引用 #1557）。

## Alternatives

- 只回 issue 评论不落盘：放弃——issue 明示 synthesis 统一落盘为 `docs/reviews/REVIEW_ADR0038_*_synthesis.md`，且 9 稿分散 file:line 需要权威映射。
- 汇聚时顺带修 ADR 至 v0.2：放弃——v0.2 含 6 个二选一裁决点（派发归位/在飞 Run/前置/去重载体/数据回收类/三列去留），先裁决后修订，避免返工。
- 以各稿原文行号直接引用：部分放弃——对承重阻断结论按 main `7fce1286` 抽样重验（synthesis §6.2），发现并合销 1 项（心跳超时双默认值已被单源化修复）；未复验条目仅采信 ≥3 稿一致者。

## Verification

- 汇聚核验 20 项承重结论全部在只读 shell 实证通过（清单见 synthesis §6.2），含 4 项单源发现（R15 retention 3 天 / R16 ADR-0025 死引用 / F-1 前端双错 / R4 死路径）逐条复核成立。
- 本地门禁（worktree 无 node_modules，diff 为 2 个 markdown）：ruff ✅、`check_governance_surface.py --check` ✅、`ai_work.py drift`（仅 overlap-hint）；eslint/tsc/knip/compileall **pending**（无 TS/JS/PY 改动，由 CI required checks 兜底）——非通过，仅标明。
- 未运行测试/迁移，未连库，未触碰生产；PR 仅 `Refs #1557`，不关闭收集 issue。

## Revisit

- D-1～D-6 裁决后：v0.2 修订 PR 应把 synthesis 链接并入 `docs/adr/README.md` 与 `docs/DOC-MAP.md` 的 ADR-0038 行（当前两者均未登记该 ADR，5ff80e-O9）。
- R19-⑧（CSRF `x-agent-secret` 豁免只判存在不校验值）为既有面，与本 ADR 无关，若确认风险可另立 issue。
- 若裁决对 R1 选择 retryable 方案，R17 验收判据与 §2 矩阵中派发面条目需同步改写。
