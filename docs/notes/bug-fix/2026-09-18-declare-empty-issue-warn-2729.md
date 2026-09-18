# declare 的空 issue 集提示升级为独立 [WARN]（#2729，前提经更正）

Status: implemented
Class: bug-fix

- 日期：2026-09-18
- 关联：`#2729`（本单）、`#2706`（撞车实录）、`#978`（declare 在窗查重与其行尾 hint 的出处）、
  契约 [`execution-contract.md`](../../development/ai/execution-contract.md) §3.4 / 附录 A.5

## Decision

`tools/dev/ai_work.py` 的 `declare`：issue 集为空时输出**独立一行 `[WARN]`**（风险措辞 + 两种补齐写法），
并**撤掉**原先挂在 `[OK]` 行尾的括号 hint——同一事实只留一处，且从「命名提示」改成「**你的记录对
§3.4 查重不生效**」这个风险陈述。**advisory、不阻断**（纯重构/探索是合法场景）。契约 §3.4 增一条
语义（v1.13），细节落附录 A.5。

### 前提更正（本单立案时我写错了，必须记账）

issue 初稿说「declare 成功、**无任何提示** ⇒ 静默盲区」。**这是错的**：那条提示自 `9c5c4446`
（2026-09-07，引入查重的同一次改动）起就存在——

```
（hint：requirement/branch 未含 issue 号且未带 --issue——在窗查重无输入，建议 --issue N）
```

我误判的原因：只读了代码里的分支判断，**没有端到端跑一次空集 declare**。在临时仓里跑出来才看见。
更正后的可考事实（写进附录 A.5）：**#2706 撞车发生在 hint 在场时**（对方记录 `issues=[]`、其 PR
#2708 确实落地）⇒ 真正成立的残留问题是**显著度与措辞**，不是「工具沉默」。issue 正文与标题已按
此改写（REST PATCH），初稿结论作废。

### 为什么不只是「加个 WARN」而值得立项

- **判据面本身有边界**：§3.4 的 issue 集来自「显式 `--issue` ∪ 名字启发式」。名字里没有带标记的
  数字时集合为空 ⇒ 该记录对查重**不可见**——工具无法从 `MS-04`/`install.s3.migrate` 推断出 2706。
- **人都需要被告知这件事**：撞车那次的双方都不知道「自己的记录在查重里是隐形的」。把这条边界说在
  declare 当场（而不是等人去读契约 §3.4），是成本最低的一步。

改动面：`tools/dev/ai_work.py`（纯函数 `issue_binding_notice` + declare 接线 + 撤 hint）+ `--self-test`
用例 + 契约 §3.4/头部/附录 A.5/DOC-MAP 版本同步。

## Alternatives

- **只改措辞、保留行尾 hint（不新增 `[WARN]`）**：hint 已在场且被忽略——继续留在成功行的括号里，
  等于把「没起作用的那一版」再发一次。否。
- **两条提示都留**（独立 WARN + 行尾 hint）：同一事实两处说法，读者还得自己判哪个算数。否（并成一条）。
- **改成硬拒绝**（issue 集为空即拒 declare）：纯重构/探索是合法场景（§3.4 明说「工具只保证可见与
  默认拒绝，冲突由人裁决」），硬拒会制造新的绕行（随便编一个号）。否。
- **对名字里的 `#N`（母题号）也做提取**：对方名字里的 `#2404` 是**另一个 issue**（母题关联），
  提取它会把「新记录与 #2404 冲突」这种假阳性推给人。否（可作独立提案，需先定义母题语义）。
- **把细节写进契约正文**：S6 预算实测**恰好压线**（24500/24500 bytes）——正文只留一条语义，
  实录进附录 A.5 ✓（这正是 v1.12 分层规则的用法）。

## Verification

- `python tools/dev/ai_work.py --self-test` → **通过**（新增 4 条断言：空集产出提示、提示含 §3.4、
  非空不产出、决策类不产出——决策类由 §3.5 的拒绝/警告文案承担）；
- **端到端**（临时 git 仓，registry 落在 `/tmp/…/.git/ai-work/`——**不污染本仓**）：
  - `fix-ms04 MS-04 的 S3 证据补候选集（#2404 同类）` → 独立 `[WARN]` 一行；`[OK]` 行**不再**带 hint；
  - `fix-2706 handover MS-04` → 无 `[WARN]`，`issues=['2706']` ✓；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**；`check:gov` 的 `gov-surface` →
  **S1–S14、S5x 全绿**（含 S6 预算与 S13 正文/附录/DOC-MAP 版本三名一致）；
  `gov-skills` 另有一条红——它读 `~/.claude` 会话转录（两条 HOLLOW 技能），**与本改动无关**且不在
  required checks 内（本地环境面，非仓库状态）。
- **一次操作事故（已处置，记账）**：我第一次做端到端验证时以为 `--worktree` 会选 registry——
  **不会**：registry root = **当前 cwd 的 git common dir**，`--worktree` 只影响 branch 推导。于是两条
  测试声明写进了**共享 registry**。处置：对两条测试记录 `finish --abandon`（正规出窗出口；
  ABANDONED 不占窗口、不挡查重），真实验录未受影响；重做的端到端验证改为 `cd` 进临时仓。

## Revisit

- **`--worktree` 与 registry 的关系值得在契约里点一句**：§2.1 写了 registry root 的发现方式
  （`git rev-parse --path-format=absolute --git-common-dir`），但没写「它跟 cwd、不跟 `--worktree`」——
  这正是我踩的点；若再有人用它做隔离实验，应加一行提示（或让 declare 在 cwd 与 `--worktree` 跨仓时
  显式拒绝）。
- **hint 撤除后的观测面**：旧 hint 出现在 `[OK]` 行，属机器可解析形态；新 `[WARN]` 是独立行——
  若将来有工具 grep `[OK]` 行取 declare 结果，需按新形态调整（当前仓库内无此消费者，已 grep 确认）。
- **母题号（`#2404 同类`）的可提取性**：见 Alternatives 最后一条；若同类撞车再现且名字里都只写母题号，
  应把「母题语义」正式化（例如 requirement 名允许 `parent:#N`）而不是放宽正则。
