# Note 引用漂移审计：909 篇全量扫描 + 两处笔误修正

Status: implemented
Class: process

## Decision

**背景**：`docs/notes/**` 里的路径引用没有门禁（`memory_lint` 只管 harness 记忆库），
被引用的文件改名/删除后，note 会静默指向不存在的东西，误导后来者。

**做法**：把 `memory_lint` 的候选口径搬过来做一次性审计（只读）——扫 909 篇 note 的
反引号 token，取仓库前缀形态、剥离 pytest node（`::x`）与行号后缀（`:12`/`:12-14`/`:12,14`/`#L12`），
逐个做存在性判定。结果 **48 个不同路径** 不在树上，但**绝大多数不是笔误**：

| 类别 | 例 | 处置 |
|---|---|---|
| **历史引用**（当时真实存在，后来改名/删除） | `tests/test_prometheus_alert_metric_names.py` → 现 `test_prometheus_alerts_contract.py`；`tools/dev/run_gov_evals.py`；`backend/services/deployment_digest.py` → 现 `artifact_digest.py` | **不回溯改**。note 是当时的记录，改它等于篡改历史 |
| **扫描器假阳性** | 符号形式 `backend/__init__.py:__version__`、`file.py:function`；版本区间 `gpu_setup/v1.0.5-1.0.7/gpu_setup.py`；**故意不存在**的探针（`tests/test_zzz_probe_newfile.py`、`backend/models/_ghost_probe_tmp.py`）；**被否决的方案**里出现的路径（`tools/dev/sync_prometheus_rules.py`，“新写一个…：否决”）；未来计划（`backend/core/timefmt.py`，“若出现第三份，应抽…”） | 不动 |
| **真笔误** | 见下 | **改** |

**本次修正（两处，均经 `git log --all` 核实「该名字从未存在过」——不是改名造成的）**：

1. `docs/notes/architecture/2026-09-14-shared-row-lock-table.md`（2 处）：
   `backend/tests/scheduler/test_retention_lock_order_2010.py` →
   `…_2022.py`（`_2022.py` 由 `f710a147 fix(#2022)` 引入，`_2010.py` 全史无此文件）；
2. `docs/notes/bug-fix/2026-09-17-risk-gauge-zeroing-and-scope-2365.md`（1 处）：
   `tests/test_prometheus_alert_metric_names.py` → `tests/test_prometheus_alerts_contract.py`
   （前者 2 次提交后改名；今天写的 note 引用今天不存在的名字，属笔误）。

**判据（写给下一个人）**：note 里引用的路径**在当前树上找不到时，先 `git log --all -- <path>`**——
有历史 ⇒ 历史引用，不动；无历史 ⇒ 笔误，改。

## Alternatives

- **A. 全量回溯改所有历史引用**：否决。notes 是时间点的记录（例如 #1258 那篇写的就是
  当时的测试名），改掉会让「当时到底跑了什么」不可考。
- **B. 把这条扫描做成门禁**：本单否决。假阳性面太大（探针文件、被否决方案、未来计划、
  符号形式、版本区间都长得像路径），做门禁会逼着写 note 的人伺候工具；作为**定期审计
  配方**（本 note 的判据 + 扫描口径）比门禁合适。若将来要门禁，先只对**指定目录**（例如
  `docs/notes/process/` 的当期台账）启用。
- **C. 只改最近 24h 合入的**：本单实际覆盖到全量 + 按类别分流——成本相同，结论更完整
  （顺带证明了 909 篇里真笔误只有 2 处，说明这个面总体健康）。

## Verification

- 扫描口径与结果：909 篇 note → 48 个缺失路径 → 逐类分流（历史 3 类高频项各经
  `git log --all` 核实存在过；假阳性按形态归 5 类；真笔误 2 处）。
- 修正后复跑：两处目标文件均存在（`ls` 验证），且这两处引用在全文替换后与真实文件名一致。
- 本单**只改文档**：无代码、无测试改动；`check:quick` 与 PR 路径门禁照常（docs-only）。

## Revisit

- **审计配方**：本 note 的判据（有史⇒历史，无史⇒笔误）+ 扫描口径可直接复用于下一轮；
  若做成脚本，建议放 `tools/dev/` 并默认 **report-only**。
- **48 条里未处置的部分**：除两处笔误外都按上表归类保留了。若某天 `run_gov_evals.py` 与
  `gov_evals_cases.yaml` 这一对（3 篇 note 引用）确实永久退役，那些 note 应按「历史引用」
  保留——但若其中还有**当期台账**在指向它们，那些台账该改。
- **门禁边界**：真正值得门禁的是「当期台账/索引类文档」的引用（`docs/notes/process/`、
  `docs/DOC-MAP.md` 这类），而不是全部历史 note —— 本单不做，留作独立判断。
