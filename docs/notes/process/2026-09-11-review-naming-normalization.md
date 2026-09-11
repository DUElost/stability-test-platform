# 同题审查稿命名统一（会话后六位规范）

Status: implemented
Class: process

## Decision

- 按用户裁决（2026-09-11：统一为 COVERAGE 系列、主干+在途全部执行、会话编号后六位不变），
  将同题审查结论稿统一为
  `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_<YYYY-MM-DD>_<会话后六位>[-<角色>].md`：

  | 旧名 | 新名 | 会话归属 |
  |---|---|---|
  | `..._fb87d5f1.md` | `..._fdca41.md` | CodeBuddy `01a08caa-…-48fdca41` |
  | `REVIEW_INDEPENDENT_VERIFICATION_..._4a955874.md` | `..._b77c27-verification.md` | zcode `7a55c418-…-70b77c27` |
  | `REVIEW_THREE_QUESTION_CONFIRMATION_..._d00273d0.md` | `..._b77c27-confirmation.md` | zcode（同会话两稿，角色后缀区分） |
  | `..._4e188e.md` / `..._e16d6d.md` | 前缀并入，后缀不变 | Cursor |
  | `REVIEW_THREE_QUESTION_CONVERGENCE_2026-09-11.md` | `..._pf8rII-convergence.md` | opencode `ses_f7f07f53bffevfu53As5pf8rII`（据本机会话库推定） |

> **更正（2026-09-11，后单）**：上表最后一行「opencode（据本机会话库推定）」系**推定错误**——
> 经 dsh 会话写入载荷核验（`write` tool-call 的 `file_path` 指向该稿路径），汇聚稿实为
> **dsh 会话 `f4f2a372-…-7d4f85`** 所写（与 `_7d4f85` 确认稿同源）；已改名
> `..._7d4f85-convergence.md`。详见[归属更正记录](2026-09-11-convergence-attribution-correction.md)。

- 主干 3 份经本 PR 改名并同步全部引用（DOC-MAP、两份流程 Note、稿件互引）；
  在途 3 份在本地重命名并同步引用（**未提交**，待其入库时按新名提交）。
- 命名纪律补入总纲 §4.1（会话后六位 + 同会话角色后缀 + 新稿不覆盖 + 禁止无后缀笼统名）。
- 只改文件名与引用，不改稿件结论内容；历史基线 sha（`fb87d5f1` / `4a955874` / `d00273d0` 等）
  作为事实保留在正文中。

## Alternatives

- **保留各自主题词、只统一结构**：不采纳。同题稿将长期分裂为多套前缀，检索与汇聚成本持续存在。
- **只改主干、不改在途**：不采纳。在途 3 份正是下一步汇入主干的输入（汇聚稿为当前裁决层）。
- **顺手修复稿件内容缺陷（CF/IV 引用的 4 份不存在文件等）**：不采纳。属引用完整性/契约漂移
  治理面（R15-F07 #1299），不在命名单内夹带。

## Verification

- 通过：重命名后全仓 grep 旧文件名零残留（`docs/` 范围，含 DOC-MAP、Note、稿件互引）。
- 通过：`git diff --check` 无空白错误；`git status` 审阅仅含 3 个 R 重命名 + 5 处引用文件 + 本 Note。
- 通过：`.venv/bin/python scripts/run_gates.py check:quick` → `[OK] (7 gates)`。
- 口径声明：未运行 pytest/Vitest/迁移/部署；未改结论内容与业务代码。
  在途 3 份未提交，其改名与引用更新仅在本地工作树生效（复核命令：`ls docs/reviews/REVIEW_COVERAGE*`）。

## Revisit

- 若 zcode 两稿实为不同会话、或 opencode 汇聚稿作者会话号有误 → 按规范更正后缀（改名成本低）。
- 在途 3 份若被清理或需入库 → 按新名提交；建议综合轮统一处理，避免再次出现无后缀稿。
- 总纲 §5 进度登记仍过时（CA-D01）；命名纪律落地**不代表**总纲整体修订完成。
