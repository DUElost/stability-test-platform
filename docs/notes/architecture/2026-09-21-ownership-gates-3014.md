# S15 补 ④⑤⑦ 三条判据（#3014 案 1A / 2A）

Status: implemented
Class: architecture

## Decision

owner 按 #3014 推荐项裁决：案 1 = **1A**、案 2 = **2A**（不做行数硬卡）、案 3 = 3A（另单，见下）。
本单落 1A + 2A，全在治理面 `tools/dev/check_governance_surface.py` + 其规格文档。

1. **S15④｜每行 `复议触发器` 非空**。为此把 `parse_ownership_table()` 的返回从
   `(key, anchor_cell)` 扩为 `(key, anchor_cell, trigger_cell)`（两处调用面同步）。
2. **S15⑤｜锚目标若为 `docs/adr/ADR-*`，头部状态必须 `Accepted`**。复用 S12 已有的
   `parse_adr_status_line`，不新造解析器；**非 ADR 目标**（design / development / 两份契约）
   仍只由 ② 管存在性与命中数，不重复判状态。
3. **S15⑦｜新建 ADR 必须写 `归属域：` 字段**（`n/a（理由）` 为合法逃生值），按**头部日期
   ≥ `2026-09-22`** 判、**不追溯存量**。cutoff 写法照 S10 `NOTE_HEADER_CUTOFF` 先例——
   这样"是否新建"不需要 diff 面，`--check` 在无 git 基数的 CI 步里也能确定性执行。
4. 规格同步：索引 §4.2（三条 → 六条 + 「为什么是 ⑤ 而不是行数卡」注）、§4.4（写明 ⑤ 不越界）、
   §6.2（把"行数自卡未落地"改判为"改上 ④⑤"）、§7（定强制面与 `n/a` 逃生）、§10；
   `adr/README.md` 模板行与该表的可选性说明。

**为什么 ⑤ 比行数卡值钱**（本案核心判断）：② 守得住「锚还在」，守不住「锚还指向有效权威」。
`ADR-0048 v1.0 → v1.1`（#2982 owner 重议）改了 `### D1` 标题，索引旧锚当场命中 0 —— 那次是
② 报警 + 作者主动同步才没漂；反过来，若某行 owner 漂到 `Superseded` 或回退 `Proposed`，
② 完全静默。行数不是维护性风险的真指标，且硬卡会造出本仓 S14 文档注释里明确排除的
「红灯但不可修」死结。

## Alternatives

| 选项 | 否决理由 |
|---|---|
| 2B：行数上界降为 WARN（≤80） | 与 ④⑤ 不同源的信息，靠人看 WARN 不会行动；留着只会稀释红灯信号 |
| 1C：凡被 bump 的 Accepted ADR 强制补字段 | 判据依赖 diff 面（`--check` 在 CI 里无 git 基数），且把小 PR 变成治理 PR |
| 1B：承认字段永久休眠并写清 | 成本更低，但放弃反向导航价值；owner 选 1A 后作废，`n/a` 逃生值即为其兜底 |
| 让 ⑤ 也覆盖 design/development 型锚 | 那些文档无 `- 状态：Accepted` 语义（Living 等），强判会造成假红 |

## Verification

- `python tools/dev/check_governance_surface.py --self-test` → `[OK]`；**新增 6 条红/绿样例**：
  ⑤绿（锚指 ADR-0001 Accepted）/ ⑤红（锚指 ADR-0039 Proposed）/ ④红（触发器空）/
  ⑦绿（写了 `n/a`）/ ⑦红（新建缺字段）/ ⑦绿（cutoff 前存量不追溯）。
- `python tools/dev/check_governance_surface.py --check` → `S1–S15、S5x 全绿`；
  三条新判据在**当前真实表（34 行）上零 retroactive 红灯**（裁决前已实测：触发器 34/34 齐、
  21 个被指向 ADR 全 Accepted、无 ADR 头部日期 ≥ 2026-09-22）。
- `python scripts/run_gates.py check:quick` → `[OK] (12 gates)`；
  `python -m pytest tests/test_skill_type_registry.py -q` → `4 passed`（该测试 import 本模块）。
- CI 侧强制点：`.github/workflows/ci.yml:291-292` 的 `--self-test` + `--check`（属 required `lint`）。
- Registry：`feat-3014-ownership-gates`（codex，`--test-impact direct`，专属 worktree
  `.wt/stp-3014-gates`，issue #3014——本次 declare **未需要** `--force`）。

## Revisit

| 条件 | 动作 |
|---|---|
| 案 3A（D0 触发指标从「族数」改为「带外部资产的族数」） | 属 `ADR-0033` 正文口径修订（v1.7 + S12 索引面同步），**另单另 PR**，不混进治理面 |
| ⑦ 被绕过（新 ADR 无 `- 日期` 行） | 现判据遇无日期行即跳过 → 若出现该形态，补一条「新建 ADR 必须有头部日期」（S12 同处）|
| ⑤ 误伤（某行有意指向 Superseded 作历史锚） | 走 §4.3「行级豁免（理由必填）」机制；当前无此需求 |
| 索引继续膨胀到难以一眼读通 | 先按 §5.1 域矩阵**拆表**（同一文档内分节），而不是加行数红灯 |
