# 上帝文件行数封顶棘轮门禁（#736 的门禁半件）

Status: implemented
Class: process

## Decision

#736 的**重构半件**（`plan_runs.py` / `agent_api.py` / `agent/main.py` 下沉领域逻辑）
正由 #1520 的一系列切片推进；本单落地 issue 评论里点名的**门禁半件**——
`tools/dev/check_god_files_ceiling.py` + 本地 `check:quick`/`check:pr` gate +
CI `lint` job step + `GATE_TO_CI_ANCHOR` 登记（S5x 强制）。

**棘轮语义**（不是统一上限）：`CEILINGS` 给每个文件一个封顶值，越过即红；
把逻辑下沉的同一个 PR 里应把对应值**调小**，上调必须在 PR 描述里写明理由。

| 文件 | `origin/main` 实测（1f22c951） | 封顶值（+5% 缓冲） |
|---|---|---|
| `backend/api/routes/plan_runs.py` | 2303 | 2419 |
| `backend/api/routes/agent_api.py` | 957 | 1005 |
| `backend/agent/main.py` | 1622 | 1704 |

- **为什么棘轮**：三个文件体量差异大（957 / 1622 / 2303），一刀切的上限要么形同
  虚设（按大的定）、要么逼出「先改名再堆」的应付式改动（按小的定）。
- **为什么留 5% 缓冲**：#1520 的在飞切片正在搬代码，搬出一半时文件可能短暂变长；
  缓冲只影响「红的时机」，不改变棘轮方向。
- **过期条目即红**：文件被拆分/改名后未同步维护封顶表 → 报红（同
  `_LEGACY_ALLOWLIST` 的过期判定思路），防止封顶表慢慢变成摆设。

**落点**：`scripts/run_gates.py` 的 `god-files` gate（`--self-test` + 主检查同 step，
毫秒级纯读）进 `check:quick` 与 `check:pr`；CI 侧在 `lint` job（PR 可达）加同名 step；
锚点按 S5x 要求登记——新增门禁时漏登记会被治理面门禁当场抓红（本次实测经历）。

**未做的部分（issue 里的第二条建议）**：「路由私有辅助函数数 ≤5」硬门禁。它是风格
偏好而非可判定缺陷，硬门禁会逼出「把函数改名/挪走但不减复杂度」的应付式改动——
收益低、噪声大，故不做；逻辑复杂度仍由行数棘轮与评审承接。

## Alternatives

- **统一上限（如三文件一律 ≤2000）**：否决。对 `agent_api.py`（957）形同虚设，对
  `plan_runs.py`（2303）一上线即红，没人会去修。
- **按 `git diff` 判「本次不许净增」而不是绝对封顶**：否决。净增判定需要 base ref
  与两个版本的取样，且对「先删后加」不敏感；绝对封顶更简单、可在本地秒级跑。
  与之最接近的形态是 `check_invariant_diff.py`（差异面不变量），若将来要收「净增」
  可复用它的 base-ref 机制。
- **封顶值取「服务层文件数」或复杂度指标（圈复杂度）**：否决。行数是**可见、无歧义、
  零依赖**的代理指标；圈复杂度需要引入分析依赖且对「拆函数不改行为」的重构不敏感。
- **把 `--update` 之类的自动改写封顶值做进工具**：否决。自动上调等于取消棘轮；
  下调也应发生在「把逻辑搬走」的同一个 PR 里、由人来写。

## Verification

- **反例构造（先证伪再采信）**：
  - 把 `plan_runs.py` 封顶值改到当前行数以下（1000）→ `test_repo_files_are_under_their_ceilings`
    **FAILED**，工具主检查 **exit 1** 且报出 `2303 > 1000（超出 1303）`；
  - 删掉封顶表里 `plan_runs.py` 一条（绕门禁）→ `test_ceiling_table_covers_the_named_god_files`
    **FAILED**。恢复后 7 passed。
- 实测命令与结果：
  - `python tools/dev/check_god_files_ceiling.py --self-test && …` → 三态可判 + 三文件
    均 `[OK]`；
  - `TESTING=1 python -m pytest tests/test_god_files_ceiling.py -q` → **7 passed**；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (11 gates)**（10 → 11，
    新 gate 在列）；
  - `python tools/dev/check_governance_surface.py --check` → S1–S14、S5x 全绿（含本次
    新登记的 CI 锚点）；
  - `python -m ruff check`（改动文件）→ All checks passed。

## Revisit

- **封顶值随 #1520 收尾下调**：切片把 `agent_api.py` 搬到 565 行（在飞分支实测）后，
  封顶值应从 1005 调到接近届时实测值；本期不预调，避免与在飞切片互相打架。
- **缓冲是 5%**：若实测发现它反而让「偷偷加 3% 代码」长期不被拦，把缓冲收到 1–2%
  （或改成「不得净增」的差异面判据，见 Alternatives）。
- **覆盖范围**：当前只覆盖 issue 点名的三文件。若 `backend/services/` 里出现新的巨型
  文件（下沉的副作用），是否纳入同一张表、按什么阈值，需要实测数据再定——
  不预先扩表。
- **CI 锚点与 `fix-735-monitoring-drift` 的在飞改动同文件**（ci.yml）：本 PR 的 step 是
  新增块，若对方先合入则 rebase 即可；两侧改的是不同的 step。
