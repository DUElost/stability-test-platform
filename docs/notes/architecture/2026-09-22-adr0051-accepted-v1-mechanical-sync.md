# ADR-0051 v1.0 Accepted——§9 四组同 PR 机械改动（2026-09-22）

Status: implemented
Class: architecture

## Decision

owner 于 2026-09-22 对 ADR-0051 §10 五个裁决点**全采推荐项**（原话「按推荐建议进行下一步」）：
D1 不可变性从目录移到包、D3 采 C1 双列、D6 选 B、D2 例外声明 + 棘轮、Phase 3 只依赖 2a。
本 PR 把 ADR 转 **Accepted v1.0**，并按 §9 同 PR 完成四组机械改动：

1. **AGENTS.md 总原则条款改写 + S11 锚**：「已发布 `…/v<version>/` 不可原地修改或删除」改为
   「已发布的发布单元不可原地修改（ADR-0051）：包条目与 `packages/`；Phase 3 前版本目录仍是发布
   单元，同样不可原地修改或删除；删除按 ADR-0051 D5」。`HARD_INVARIANT_ANCHORS` 第 12 锚改为
   跨行正则同时绑「发布单元」句与「目录仍是发布单元」句（锚数仍 12），自测夹具同步。
2. **ADR-0039 / ADR-0046 转 Superseded**：头部状态行改写并注明继承/接管条款；正文保留为历史
   依据与事故记录。语义归属表 `script-version-immutability` 行 owner_anchor 从
   `script-versioning.md` 改指 ADR-0051 D1（S15 ⑤ 要求 Accepted，v1.0 满足），复议触发器改为
   Phase 2a / Phase 3 两个出口。
3. **索引同步**：adr/README 主表（0033/0039/0046/0051 四行）+ M7 看板（0051 入 Accepted，
   0039/0046 列 Superseded，Proposed 只剩 0047）+ 第 117 行注 + DOC-MAP 三行（0033/0046/0051）。
4. **ADR-0033 D3 一句措辞修订 → v1.13**：「`content_sha256 := tarball sha256`」改为
   「`package_sha256 := tarball sha256`；`content_sha256` 仍为入口 sha」，与 v1.12 C1 一致。

另补 D1 **过渡句**：Phase 3 前 `backend/agent/scripts/<name>/v<version>/` 目录仍是发布单元。
没有这句，AGENTS.md 条款一改就等于在 2a 前撤销目录保护，而 `check-script-version-immutability.py`
的判据会失去权威依据。

## Alternatives

- **把 ADR-0039 D7 的「门禁判据改为 refs>0」在本 PR 一并落地**：弃。ADR-0051 D5 继承 D7 的是
  「判据不连生产库」，而 D1 过渡句下目录在 Phase 3 前全部不可变（比 0039 D1 更严），门禁判据
  不动是正确的；随 Phase 3 与目录一起退役。
- **只改 40+12 个入向引用文件中的「Proposed / 待裁决」字样**：弃。S15 ⑤ 只约束语义归属表的
  ADR 型锚（实测只有 `script-version-immutability` 一行相关），S12 只管索引面；
  `docs/notes/` 与 `docs/reviews/` 是按日期留档的历史面，不改。活文档只改四处：
  `script-versioning.md`（ADR-0046 D2 → ADR-0051 D6；不可变节加 ADR-0051 指针）、
  ADR-0040 §1.3 括注、ADR-0033 §D3、语义归属表。
- **归属表 owner_anchor 仍指 `script-versioning.md`**：弃。该行原文自陈「Accepted ADR 面无承载体」
  才退而指 development 文档；现已有 Accepted ADR，且其复议触发器原文就要求转 Accepted 时改指 ADR。
- **S11 拆成两条锚**：弃。设计文档 §S11 写明「12 条锚串」，拆开需连带改设计文档；单条跨行正则
  同样能让「任一半句被删」过不了门禁。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py` 与 `--self-test`：结果见 PR 描述。
- `venv/bin/python -m pytest tests/test_adr_index_status_2989.py`：结果见 PR 描述。
- `venv/bin/python scripts/run_gates.py check:quick`：结果见 PR 描述。
- AGENTS.md 体量：77 行 / 5504 bytes（S6 预算 80 / 8000）。
- 语义归属表新锚 `### D1（核心）：发布单元 = 内容寻址包` 在 ADR-0051 内命中恰 1（grep -c = 1）。

## Revisit

- Phase 2a 合入：`script-versioning.md`「已发布版本不可变」节改写为包口径，归属表范围句同步。
- Phase 3 合入：AGENTS.md 条款删过渡句、S11 第 12 锚与自测夹具同步、
  `check-script-version-immutability.py` 退役、设计文档 §S11 锚计数复核。
- 若 owner 后续对 §10 任一点改判（如 D6 改 A），ADR-0051 出 v1.1，本 Note 归档。
