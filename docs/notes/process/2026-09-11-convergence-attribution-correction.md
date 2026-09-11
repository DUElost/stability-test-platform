# 汇聚稿归属更正与 7d4f85 两稿入库

Status: implemented
Class: process

## Decision

- **归属更正**：`..._pf8rII-convergence.md` 的会话归属由「opencode（据本机会话库推定）」更正为
  **dsh 会话 `f4f2a372-fc73-4756-821d-bfe5ac7d4f85`**（末六位 `7d4f85`）。依据 = 该会话存储中的
  **写入载荷**（`write` tool-call 携带 `file_path` = 本仓库该稿路径，写于 2026-09-11 13:12:14）；
  opencode 会话 `ses_f7f07f53…` 仅在其库中出现**引用**、无写入载荷。
- **文件改名**：`..._pf8rII-convergence.md` → `..._7d4f85-convergence.md`（与同会话确认稿以角色后缀区分）。
- **入库**：从 dsh 会话写入载荷恢复的 `..._7d4f85-confirmation.md`（373 行）+ 配套 Note
  `docs/notes/process/2026-09-11-three-question-confirmation-7d4f85.md`（仅同步引用名，未改结论内容）。
- **引用同步**：DOC-MAP（改名行 + 新增登记行 + 审计行措辞）、审计稿 §0/§6/§7、meta-audit Note、
  收敛 Note、`705379-synthesis`、命名 Note（附更正批注）。
- **方法固化**：归属的终局判据 = 在作者 harness 的会话存储中定位**携带 `file_path` 的写入载荷**；
  他库命中与转录中的标题出现只是引用痕迹。

## Alternatives

- **保留 `pf8rII` 名不改**：不采纳——与写入证据冲突，且会让后续引用沿用错误归属。
- **为同一文件保留两个归属名/别名**：不采纳——决策实体唯一性（ADR-0034 §3.5 同精神）。
- **不恢复确认稿（按收敛冻结仅增补）**：用户已裁决恢复入库；按裁决执行，裁决基准仍为汇聚稿。

## Verification

- 联合链接检查：本单涉及的 8 份文档零断链；`check:quick` 与 `git diff --check` 见 PR。
- 写入证据复核（只读）：
  `zstd -dc ~/.dsh/sessions/--home-debian13-stability-test-platform--/session-f4f2a372-…/session.v3.jsonl.zstd | grep -o '"file_path":"[^"]*"'`
  → 可见四条：汇聚稿 / 其 Note / 确认稿 / 其 Note。
- 未改业务代码；两稿结论内容原样（仅引用名同步）。

## Revisit

- dsh 会话记录不可用时，归属证据退化为本单记录与 git 改名历史。
- `7d4f85` 两稿均无 declare 记录（声明缺口）；如需补登记，以新 Execution 重新 declare。
- 汇聚稿仍为**唯一裁决基准**；后续新稿只做增补（收敛冻结）。
