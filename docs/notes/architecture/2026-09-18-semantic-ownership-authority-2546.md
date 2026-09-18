# 语义归属索引草案落地（#2546）

Status: proposed
Class: architecture

## Decision

按 issue #2546 方案 A + 多稿 Accept-with-nits，落地 Ownership Authority **only** 索引，不写内容宪法：

- 新增 `docs/design/2026-semantic-ownership.md`：X1/X2/X3 单行口径、Tool/Script/Adapter、四类工具嵌入/外置、flash 补登记行、S15 表内可机读判据、预填 12 行（含 3 条关系边）。
- `docs/DOC-MAP.md`：头部指针 +「权威 vs 归档」登记 + 分层表一行；明确冲突仍以代码与测试为准，索引无「以本文为准」。
- `docs/adr/README.md` 模板增 `归属域`（触碰即补；里程碑枚举扩到 M7）。
- `ADR-0021` 关联区括注措辞降级（`script_meta` = 冻结副本，非独立权威）——nits 要求的勘误，不改 D4/0020 决策。

本 PR **不**实现 `check_governance_surface.py` S15 代码（判据已钉在设计 §4，follow-up）；**不**改 ADR-0033 D1 正文表（flash 由本表补登记）。

## Alternatives

- 方案 B（全量 Semantic Inventory / Core Semantics 层）：产出/成本比低，易成第 5 份平行权威 → 不采纳。
- 方案 C（仅 #530 式逐对）：X1/X3 已证明不够 → 保留为表外新双标的运行期路径，不替代 A。
- 照抄 storage-roles「冲突时以本文为准」：评审阻断；改为 Ownership Authority only。
- 本 PR 同步实现 S15：表形状尚未经 owner 合入确认，先合入索引再开门禁，避免假绿/假红锁死 draft。

## Verification

- 人工核对：设计文无「以本文为准」；X1/X2/X3 与评审推荐一致；表 12 行 key 唯一；边行含 `R-merge-locus` / `R-merge-consumes-log` / `R-tool-hosted-by-tier`。
- `python3 scripts/run_gates.py check:quick`（文档向变更；跑后记录结果）。
- ADR-0021 括注检索不再出现「script_meta 作为权威」独立权威读法。

## Revisit

- Owner 合入本草案后：实现 S15（表内①② + 字段驱动③）并加 `--self-test`。
- ADR-0033 下次 Accepted 修订时把 `flash-tool` 写入 D1 分类表。
- 台账/提案中 X3「0025/0033 域」措辞收窄为指向 0027 第 7 条（可另单，非本 PR 必做）。
- 若表行持续膨胀超过「约 2× 全仓权威声明行数」量级，按评审触发 S6/收益复核，允许降级为人工清单。
