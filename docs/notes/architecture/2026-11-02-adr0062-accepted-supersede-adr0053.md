# ADR-0062 转 Accepted，ADR-0053 转 Superseded

Status: implemented
Class: architecture

## Decision

PR #3833 第 6 轮独立复核（评论 6568551177）结论为「可转 Accepted」。Owner 已于
2026-11-02 裁定 §10.3 的全部三项（ADR-0062 §10.6）。本次提交只改状态，并同步权威文档：

- ADR-0062 从 Proposed v0.10 转为 Accepted v1.0，政策不变。
- ADR-0053 从 Accepted v0.3 转为 Superseded by ADR-0062（v0.4）。Owner 裁定为「甲：目标承接，手段替代」：
  - ADR-0053 的目标（中心字节不随 run 数与转储体量成比例增长）由 ADR-0062 承接，做法改为上送前按问题选代表。
  - 内容寻址、manifest、持久引用与 Phase A–D 被替代，它们都没有实施过。
  - D7 规则 1、2、4、5 保留，规则 3 按 ADR-0062 D6 第 4 条修订。
  - D4、D5 的原则句按 ADR-0062 §10.3 逐句去向表承接。
- `center-storage-model` 的语义归属锚点从 ADR-0053 §2 改到 ADR-0062 §5。治理检查 S15 要求锚点指向 Accepted ADR，否则失败。
- 过渡项 `center-storage-interim-dedup` 的出口从 `adr:ADR-0053#Phase B` 改为 `adr:ADR-0062#9. 迁移与激活`（阶段三：退出新旧并存）。止血范围、到期日与做法都不变。手册与脚本 docstring 里的出口指针同步修改。
- ADR-0025 D4、ADR-0028 与四份设计文档补上取代指针。旧段落保留为历史，不改写。

## Alternatives

- **ADR-0053 只部分 Superseded，D4、D5 保持 Accepted**：Owner 未采纳。两条中的 manifest 与持久引用已没有载体，实施者无法执行。
- **过渡项保留旧出口**：放弃。出口会指向一个已被取代 ADR 的阶段，台账语义悬空。
- **本次顺手改写设计文档中按 ADR-0053 写的段落**：放弃。B8 尚未实施，现态契约要等阶段二切换上传后再同步。本次只加指针。

## Verification

- `python3 tools/dev/check_governance_surface.py --base origin/main`：通过（S1–S15、S5x）
- `python3 tools/dev/check_transitions.py` 与 `--self-test`：通过
- 本次只改文档、台账和一处 docstring，不涉及运行代码，未运行 pytest。

## Revisit

- B8 阶段二切换上传后：按 ADR-0062 同步 `2026-scan-upload-merge-contract.md`、`2026-storage-roles-and-aliases.md` 的现态契约。
- B8 阶段三退出新旧并存后：`center-storage-interim-dedup` 转 done，并附 evidence；同时删除脚本、手册和告警指引。
