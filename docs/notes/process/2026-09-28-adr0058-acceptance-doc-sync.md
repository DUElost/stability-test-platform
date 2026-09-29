# ADR-0058 裁决与执行契约同步（2026-09-28）

Status: implemented
Class: process

## Decision

- ADR-0058 由 Proposed v0.3（PR #3489 已合入）转 **Accepted v1.0**。Owner 把 #3489 转 ready 并合入，即为裁决；
  §7 记录裁决，§5 记录本 PR 的同步内容。
- 按 ADR-0058 §4 同 PR 完成文档同步：
  - `execution-contract.md` → Living v1.14：§3.6 改写为「执行模型、协调域与字段封闭性」，退役 Mode A/B/C；
    §3.5 改为可见性双通道（协调域内 Registry + §3.4 查重，协调域外开放 PR 查重，同受「唯一权威 ADR」约束），
    事故实录迁附录 A.6；「适用」行收窄到协调域内的实施 Execution；
  - `harness-adapters.md`：原「文档 / 评审类会话同样 declare」改为只登记本地 Harness 中修改仓库文档并开 PR 的会话，
    纯 issue 评论 / PR 评审属复核职责、不登记；
  - ADR-0034 → v1.13 修订记录，§6 Competition mode 行加注；`adr/README.md`、`DOC-MAP.md` 同步；
  - `repository-workflow.md`：新增「批次交付流程（ADR-0058）」一节，「领单前分流」改为按 D7 分流，
    「并行 worktree」补协调域条款；
  - 新增 `docs/development/ai/batch-planning.md`、`batch-review.md`，由 AGENTS.md 按需入口索引；
    AGENTS.md「开始任务时」第 3 步注明只适用于本地 Harness 实施者，第 4 步（与 `repository-workflow.md` 同句）
    改为共享元文件「只由一个工作面修改」——协调域外规划者不是 Execution，旧措辞约束不到它；
  - 语义归属表登记 `batch-delivery` key，锚到 ADR-0058 D8；ADR-0058 头部归属域由 n/a 改为该 key；
    §0 Registry 行由「谁正在决定」改为「谁正在实施 / 哪些 Execution 在协调窗口」。
- 以上第二批修订（§3.5 双通道、harness-adapters 评审行、AGENTS 第 4 步、归属表 §0、本 note 的 Verification）
  来自复核者在 #3490 的审阅意见（需修改，5 处）。
- 执行契约正文已达 S6 预算上限（24500 / 24500 bytes）。按预算注释「先迁细则或去冗余，不许抬预算」，
  把头部 v1.9–v1.13 的变更明细迁入附录 A.4（与 v1.12 迁出 v1.1–v1.8 同一做法），正文降到 24034 bytes。
- 现行承载（Claude Code 云端 Web / ChatGPT Codex 云端 Web / Grok Bot）只写在 `repository-workflow.md`，
  不进 ADR，也不进执行契约：更换工具只改一张表。

## Alternatives

- **文档同步另开 PR**：ADR v0.3 原计划如此。但裁决已经落地，契约与 ADR 分两次合入会留下一段
  「ADR 已 Accepted、契约仍写 Mode B/C」的矛盾窗口，所以合为一个 PR。
- **抬高执行契约 S6 预算**：预算注释明确禁止；腾挪历史明细是既有做法。
- **复核指引做成 `.claude/skills`**：复核者在 Codex Web 上工作，读不到 Claude 专用 skill，所以写成普通文档。
- **归属域保持 n/a**：S15③ 要求版本号变化且带归属域的 ADR 同 PR 改归属表；ADR 原文也写明「转 Accepted 后如需锚点再登记」。
  `batch-delivery` 与 `execution-registry` 分工清楚（上游交付流程 vs. 实施 Execution 的协调），登记有实际价值。

## Verification

- `python tools/dev/check_governance_surface.py --check --base origin/main`：通过（S1–S15、S5x）。
- `python scripts/run_gates.py check:gov`：通过。
- 根目录 `tests/`：云端容器补装 pytest 后运行
  `python -m pytest tests/ -q -p no:cacheprovider --continue-on-collection-errors`，
  结果 1119 passed / 64 failed / 76 skipped / 25 errors；失败与收集错误均因容器缺后端依赖与数据库。
  在 `origin/main`（afd39339）的独立 worktree 上跑同一命令对照，失败 + 错误清单逐条一致（89 条），
  本变更未引入新失败；修订后在分支上复跑，清单仍一致。完整结果以 CI `pr-agent-tests` 为准。
- 预算：`execution-contract.md` 197 行 / 24053 bytes（上限 210 / 24500）；`harness-adapters.md` 100 行
  （上限 100）；`AGENTS.md` 79 行（上限 80）。
- 未完成：`check:quick`（云端容器无 `psycopg`，schema-at-head 起步即失败），交给 CI。

## Revisit

- ADR-0058 §6 的复议触发器。
- `AGENTS.md` 已到 79 / 80 行，下一次往入口表加行前需先去冗余。
- `harness-adapters.md` 已到 100 / 100 行，同上。
