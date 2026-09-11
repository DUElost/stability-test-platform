# 退役审查稿转正与丢失稿恢复

Status: implemented
Class: process

## Decision

- 按用户裁决（2026-09-11「可以转正 / 恢复丢失稿」）执行两件事：
  1. **退役稿转正（入库）**：
     - `docs/reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_85d793.md`（Codex，独立审查）——
       含 #901/#1123 已关闭修复的隔离反例、#942 契约漂移、通知幂等边界，以及 10 个候选治理主题与 ADR 判定；
     - 配套 Note `docs/notes/process/2026-09-11-review-coverage-fix-effectiveness-85d793.md`；
     - 两份审计证据笔记（原名 `audit-*.md`，规范化命名后入库）：
       `docs/notes/process/2026-09-11-failure-mode-resilience.md`、
       `docs/notes/process/2026-09-11-doc-architecture-integrity.md`。
  2. **丢失稿恢复（入库）**：`PROJECT_REVIEW_R01_R15_SYNTHESIS_2026-09-11.md` 于并行清理中从工作树移除、
     未入主干；按 zcode 会话 `7a55c418` 的 **03:20:08 完整读取快照**（`sizeBytes=31886`，402 行）恢复为
     `docs/reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_705379-synthesis.md`
     （写入/主要编辑会话 `aaa7eec3` → 705379；同一会话另有 `..._705379.md` 稿未入库）。
- 恢复稿仅加「恢复说明」块并修正相对链接（原 Note 退役、audit 笔记路径），**不改结论内容**；
  DOC-MAP 登记转正与恢复的两份 Living 审查稿。
- **其余退役稿只登记去向、不入库**：`5e3831`、`705379`、`f9a21b0f`×2、`f61411` 及其配套笔记存于
  `/tmp/stp-inflight-20260911/backup/`（哈希清单 `sha256-before.txt`，评审类文件 20/20 一致）。

## Alternatives

- **恢复稿按原名 `PROJECT_REVIEW_R01_R15_SYNTHESIS_*.md` 入库**：不采纳——与总纲 §4.1
  「禁止无会话后缀笼统名」冲突。
- **Cursor 家族五稿全部入库**：不采纳——同模板近重复、已被汇聚稿取代；只做去向登记。
- **逐字节重建「最终版」**：不采纳——该文件曾被多会话并行编辑，最终态不可唯一确定；
  采用可验证的完整快照为恢复基准，并在恢复说明中标注时点与来源。
- **把 85d793 的反例直接开残余单**：不采纳（本轮）；属于修复动作，按用户既有流程另行领单。

## Verification

- 恢复稿与入库笔记相对链接检查通过（快照原有 2 处断链已按新名闭合）；
- `check:quick` 7 gates、`ip-leak`、`git diff --check` 结果见 PR；
- 未改业务代码；备份目录保持原样（未删除）。
- 精确恢复方法（只读）：以 Python `sqlite3` 只读打开 `~/.zcode/cli/db/db.sqlite`，读取 part
  `part_mtvwwdxu_80cc6d11-9c5a-422d-b446-60f652180ff5`（snake_case 行号前缀清理后即得快照；文中哈希与行数可复核）。

## Revisit

- 备份目录位于 `/tmp`（易失）：如需长期留档，应将 `5e3831`/`705379`/`f9a21b0f`×2/`f61411` 迁出或申报弃用；
- 85d793 的 F01（#901 原子消费）与 F02（#1123 取消路径互斥）反例应转仓库回归测试
  或重开/关联残余单——另行领单；
- 恢复稿为历史草案：综合轮汇聚时应以汇聚稿为准，不引用本稿计数。
