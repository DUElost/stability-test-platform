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
    AGENTS.md「开始任务时」第 3 步注明只适用于协调域内的实施者（现阶段即本地 Harness），第 4 步（与 `repository-workflow.md` 同句）
    改为共享元文件「只由一个工作面修改」——协调域外规划者不是 Execution，旧措辞约束不到它；
  - 语义归属表登记 `batch-delivery` key，锚到 ADR-0058 D8；ADR-0058 头部归属域由 n/a 改为该 key；
    §0 Registry 行由「谁正在决定」改为「谁正在实施 / 哪些 Execution 在协调窗口」。
- 以上第二批修订（§3.5 双通道、harness-adapters 评审行、AGENTS 第 4 步、归属表 §0、本 note 的 Verification）
  来自复核者在 #3490 的审阅意见（需修改，5 处）。
- 执行契约正文已达 S6 预算上限（24500 / 24500 bytes）。按预算注释「先迁细则或去冗余，不许抬预算」，
  把头部 v1.9–v1.13 的变更明细迁入附录 A.4（与 v1.12 迁出 v1.1–v1.8 同一做法），正文降到 24034 bytes。
- 现行承载（Claude Code 云端 Web / ChatGPT Codex 云端 Web / Grok Bot）只写在 `repository-workflow.md`，
  不进 ADR，也不进执行契约：更换工具只改一张表。
- **2026-09-29 勘误（ADR-0058 v1.1，Owner 审阅发现）**：
  - ADR-0034 §2.6 第 4 条仍写「评审 / scratch 会话按 #919 指引同样 declare」，是 v1.13 现行正文，#3490 漏改，
    两轮复核也都没有发现；已收窄，并在 v1.13 修订记录中注明补漏。全仓扫描（含 `ai_work.py` 提示文字与各 Harness rules）
    没有其他需要改的现行残留；ADR-0034 附录 A 的 Zcode 探针行记录的是当时「P2 动作表已补 #919 指引」这一动作、
    ADR-0058 §1.3 引用的是旧规则原文，二者均已看过、按历史保留；
  - ADR-0058 头部、§4、§5、§7 的 PR 归属改为准确表述（裁决 = #3489 合入；Accepted 状态与同步 = #3490）；
    §5 按实际落地重写（§3.5 为可见性双通道）；D10-2、D10-4 加落地注，不改决策原文；
  - Owner 对 #3492 的二次审阅：AGENTS.md 第 3 步断句修正；「本地 Harness 实施者」统一为「协调域内的实施者
    （现阶段即本地 Harness）」；D10-2 落地注补明「比原文更窄」并交 Owner 确认；§5 写上勘误 PR 编号 #3492；
  - 教训：同步清单按「文件」核对不够，被收窄的规则要按「语义」全仓搜索现行正文里的每一处表述
    （本例的 #919 规则在 ADR-0034 与 harness-adapters 两处出现，只改了一处）；读扫描结果时不能看截断的输出下结论
    （ADR-0034 附录 A 那一处其实命中了，因输出截断被误判）。

## Alternatives

- **Accepted 状态翻转与文档同步分两个 PR**：会留下一段「ADR 已 Accepted、契约仍写 Mode B/C」的矛盾窗口，
  所以都放在 #3490。这符合 v0.3 §4「转 Accepted 后另起 PR、不在 ADR PR（#3489）内改」，不是对 §4 的偏离
  （v1.0 初稿此处误写成「偏离另开 PR」，2026-09-29 勘误）。
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

- 下次改变执行语义时，同步前先按被收窄规则的关键词全仓搜索现行正文（ADR、契约、Harness 适配、rules、工具提示），
  把命中清单写进 PR 正文，交复核者对照。

- ADR-0058 §6 的复议触发器。
- `AGENTS.md` 已到 79 / 80 行，下一次往入口表加行前需先去冗余。
- `harness-adapters.md` 已到 100 / 100 行，同上。
