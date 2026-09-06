# ADR-0034 P0b——supersede 标注与薄入口接线

Status: implemented
Class: process

## Decision

执行 ADR-0034（Accepted v1.0）§2.7 P0 的 **P0b**（P0a 已于同日交付：`execution-contract.md` 建立与 ADR v1.1 细则迁出）。本变更完成 supersede 语义的仓库级接线：

1. **supersede 标注**：`2026-09-04-multi-agent-parallel-convention.md` 头部加取代注记——并行执行语义由 ADR-0034 取代、细则以 `execution-contract.md` 为唯一权威源；其**元文件串行化与派生视图实践经契约 §9 过渡条款保留**（Registry P1 启动判据未触发期间的现行操作规范），其余成为被取代的历史记录，原文留档。
2. **薄入口接线**：`AGENTS.md`「开始任务时」第 3 条加 execution-contract.md 指针、按需入口表加一行（63→65 行，80 行预算内）；`CLAUDE.md` 按需读取加一行（26→28 行）；`harness-adapters.md` 修改顺序加契约步骤、「直至新 ADR 取代」句更新为「ADR-0034 已取代」；`repository-workflow.md` 并行 worktree 节改为 ADR-0034+契约权威、过渡条款显式化、「Registry 语义待后续 ADR」句删除（已发生）。
3. **治理门禁扩展**：`execution-contract.md` 入 `check_governance_surface.py` 的 S2 `link_files`（断链防护）与 S6 `RESIDENT_BUDGETS`（200 行/20KB 预算，现值 163 行/13.4KB）；设计文档 §3 S6 行同步。
4. **刻意不做**：不改 `docs/DOC-MAP.md` / `docs/README.md`（并行会话有未提交改动，避免撞文件——留给该会话或后续补一行）；不动 AGENTS.md 的派生视图现行用法（过渡条款生效中）。

## Alternatives

- **P0b 顺带更新 DOC-MAP**——放弃：主工作树存在并行会话对 DOC-MAP/README 的未提交改动，同文件改动会造成 auto-merge 冲突；DOC-MAP 已有 ADR-0034 行（#862），contract 行可随后补。
- **AGENTS.md「开始任务时」直接改为 Registry 读法**——放弃：ai_work.py 是 P1 交付且启动判据未触发，把不存在工具写进启动指令会制造「契约要求用不存在的工具」的矛盾；过渡条款才是现行规范。
- **09-04 note 整体标 Status: superseded**——放弃：note 的 Status 枚举（proposed/implemented/rejected）无 superseded 值（S10 校验会红）；按 RESIDENT_CONTEXT_AUDIT 先例用头部取代注记 + 原文留档。
- **S6 给 contract 定 163 行现值预算**——放弃：预算是 guardrail 不是快照，给演进留余量（200/20KB）同时保持阻塞语义。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --check`：S1–S11+S5x 全绿（含 S2 对 contract 新链接的校验、S6 对 AGENTS 65 行/CLAUDE 28 行/contract 163 行的预算校验）；
- `--self-test`：12 规则红绿双向通过；
- supersede 语义抽查：09-04 note 头部注记、repository-workflow 过渡条款、harness-adapters 权威源句三处交叉一致；
- 未运行（pending）：PR CI 六项 required checks 以实际运行为准；纯文档+checker 表项变更，无行为语义改动。

## Revisit

- P1 启动判据触发时：实现 `ai_work.py`，AGENTS.md「开始任务时」第 3 条随之改为 Registry 读法（过渡条款退场）；
- DOC-MAP/README 的 contract 行由并行会话或后续 PR 补；
- G2 试点（`backend/agent/` → `backend/agent/aee/` 真身+symlink 薄壳）在 P0b 合入后启动。
