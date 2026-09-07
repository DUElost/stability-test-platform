# ADR 索引漂移五次复发收口：S12 索引一致性门禁

Status: implemented
Class: bug-fix

## Decision

ADR 版本 bump 漏同步派生索引面的问题由「PR 内记得同步」改为确定性门禁 S12
（`tools/dev/check_governance_surface.py`），并一次性修复门禁扫出的全部 9 处存量漂移：

- 规则面（最小可靠面，宁缺勿误报）：
  - status 词级一致：ADR 头部「状态」行 ↔ adr/README 主表行状态列（修 0002/0009/0011）；
  - 版本一致：仅当头部行携带**规范位版本**（`**Status（vX.Y）**` 或
    `**Status**（vX.Y：…）`）时，约束 adr/README 主表版本前缀、DOC-MAP 行版本
    token（末位）、M7 看板 `（**Status** vX.Y：` 条目（修 0032/0034）；
  - 文内一致：头部行版本 ↔ 「版本记录」块末项（修 0034 v1.3 vs v1.6）。
- 明确不约束：头部行无规范位版本的 ADR（0029/0030 注解散文里的 v2.1/v1.9 等
  token 不是头部版本；老 ADR 多数无版本），派生面版本对它们保持自由——
  强制统一会牵动大量历史格式且无一例现行事故支撑。
- 同步修复：ADR-0002/0009 主表状态改 Superseded、ADR-0011 改 Accepted（含 M2
  看板散文行）、ADR-0032 主表+M7 升 v0.7、ADR-0034 头部行/主表/DOC-MAP/M7
  升 v1.6（主表摘要补 v1.6 的 Antigravity 定性一句）。
- Living 文档 `2026-08-governance-surface-protection.md` L0 表补 S12 行 + 修订记录。

## Alternatives

- **索引面去版本化**（DOC-MAP/README 摘要列去掉版本串，版本只住正文）：更根修，
  但 adr/README 版本前缀是 0029 起的既成惯例且信息有用；改为 Revisit 项——若
  S12 后仍复发再切换。
- **强制所有 ADR 头部统一带规范位版本**：否决。老 ADR 无版本是历史格式而非漂移，
  无事故支撑，批量改写违反最小变更原则。
- **只查 ADR-0034**：否决。0032 的同类漂移（7d 审计已记）与 0002/0009/0011 的
  状态漂移同族，按号查会变成打地鼠。
- **纳入 M7 行以外的 DOC-MAP 散文提及**：否决。散文链接行的版本 token 语义不清，
  约束「指向 adr/ 的行」已覆盖现行全部事故面。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --self-test`
  ——13 条规则红绿双向全绿（S12 新增 12 个样例：解析器规范位/散文边界 4 +
  五面一致性红绿 8）；
- 修复前 `--check` 实跑：S12 恰好报出 9 项预期漂移、零误报（0029/0030/0033 等
  格式变体全部放行）；
- 修复后 `--check` 全绿（S1–S12、S5x）；
- `venv/bin/python -m ruff check tools/dev/check_governance_surface.py` 通过；
- `venv/bin/python -m py_compile tools/dev/check_governance_surface.py` 通过；
- `venv/bin/python scripts/run_gates.py check:quick` 全绿。

## Revisit

- 若 S12 之后仍出现索引漏同步（说明漂移面超出 S12 覆盖，如新增索引文件），
  切换到「索引面去版本化」根修方案；
- M7 看板行仅约束 `（**Status** vX.Y：` 形态条目；0029 式散文条目不在约束内，
  出现该形态漂移时再扩展解析；
- execution-contract.md（Living v1.1）与 ADR 版本的联动目前靠修订记录互引，
  出现漂移再考虑纳入 S12。
