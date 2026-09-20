# ADR-0039 §4.3 补「同步确权索引行」这第三项义务（#2546 残口）

Status: implemented
Class: architecture

## Decision

`57d637c1`（#2950）已把 ADR-0039 的「`AGENTS.md` 硬不变量」误标纠为**总原则**（D1 首句 + §4.3
第 2 条）并同步 ownership §8。本单**不重复**那部分，只补它没带的一项：

`### 4.3 落地顺序` 第 2 条是**将来执行 0039→Accepted 的人照做的清单**。它现在列了两项同步义务
（`AGENTS.md` 总原则该句 + `check-script-version-immutability.py` 判据），但**漏了第三项**：
`docs/design/2026-semantic-ownership.md` 中 `script-version-immutability` 行的 owner 必须同 PR
改指本 ADR D1。漏项的后果不是文档不好看，而是——

- 该行 S15② 的锚指向 `script-versioning.md ## 已发布版本不可变`，0039 生效后这处文本**仍命中 1 次**，
  门禁不会红；
- 于是索引会继续宣称"不可变契约范围"由旧口径承载，而 Accepted 的 0039 已把它收窄成两段式——
  正是 #2546 全程要治的**执行指令与真源不同步**，且落在 S11/S15 都够不到的语义面。

改动 = 在该条尾部补一句指向表行的同 PR 义务（措辞与 §8 已登记的触发器同口径），`1+/1-`。

## Alternatives

| 选项 | 否决理由 |
|---|---|
| 不加，认为 §8 与表行触发器已经写了 | 表行触发器只对**读索引的人**可见；照 ADR 落地清单执行的人不会先翻索引。义务要写在被执行的那份清单上 |
| 改 S15 去检测「Accepted ADR 与表行 owner 不一致」 | 属语义机读（§4.4 已定 Referential ≠ Semantic Integrity），不做 |
| 现在就把表行 owner 改指 0039 | 0039 仍是 Proposed，§5「Proposed 不强制入表」；提前改等于把未生效裁决写成权威 |
| 撤本单、等 0039 那一刀一并写 | 可行，但这一行正是"该改哪儿"的唯一落点，延后 = 再次依赖记性 |

## Verification

- `check_governance_surface.py --check` → `S1–S15、S5x 全绿`；`run_gates.py check:quick` → `12 gates OK`。
- 替换前对目标句做 `count == 1` 断言（不唯一即中止）。
- **撞车记录**（过程性事实）：#2951 于 `11:36:19Z` 提出，#2950 于 `11:35:52Z` 提出、`11:42:06Z` 合并
  → 本单原三处改动 ①②③ 全由 #2950 落地；`git reset --hard origin/main` 后只保留唯一残口重提，
  原 note（含已被 #2950 覆盖的误标纠偏叙述）不保留，避免双份勘误记录。
- 两条记录同 issue、同文件族：#2950 的 declare 未与本单互见——`ai_work` 的文件级 overlap 只是
  hint、从不禁止；本例说明**同文件小刀宜先 `status` 再动手**（本单在提 PR 前查过开放 PR 列表，
  但 #2950 当时尚在 27 秒的时间窗内）。

## Revisit

| 条件 | 动作 |
|---|---|
| 0039 转 Accepted | 按 §4.3 第 2 条的**三项**同步执行；做完把 §8 该行标「已随 Accepted 收口」 |
| 同类「清单漏项」再出现 | 统一判据：凡确权表行写了触发器，被指向 ADR 的落地清单须反向列出该义务（本次首例） |
