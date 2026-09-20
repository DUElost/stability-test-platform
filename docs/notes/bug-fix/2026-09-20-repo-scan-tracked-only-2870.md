# 守卫的扫描面收成「仓库跟踪内容」：`.wt/*` 副本不再让门禁在本机恒红（#2870）

Status: implemented
Class: bug-fix

## Decision

**本质不是「少排除一个目录」，而是两条守卫把「文件系统」当成了「仓库内容」。**
`tests/test_ci_test_db_guard_wiring.py` 从仓库根 `rglob("*.py")`，只排 `backend/tests/`、
`tests/`、`/.git/`；`tests/test_removed_env_keys.py` 同理按目录 `rglob`。于是：

- `.wt/*`（并行 Execution 的专属 worktree，`.gitignore` 里就有 `.wt/`）里是**整仓副本**
  ——本机 10 个 worktree = 14,645 个 `.py`，判定集被副本撑到 61 项（期望 1 项）；
- 本地未跟踪的 `backend/.env` 里留着已移除的键，把「不得把已删配置当生效开关」这条守卫
  在本机点红。

净效果是**本机恒红、CI 恒绿**：红灯失去信息量，下一次真回归混在里面分不出来。
这套 worktree 隔离本来是 AGENTS.md 推荐的并行做法，门禁没跟上自己的协作方式。

修法取**已有正解**、不再发明：`tools/dev/check-internal-ip-leak.py` 的
`default_targets()` 早已是「已跟踪 ∪ 未跟踪且未被 .gitignore 命中」，且它的 docstring
把两条边界都写清了（含未跟踪是为了 #2402：只取 `git ls-files` 会让 `git add` 之前——
**最该拦的时点**——完全不在门禁眼里；不绕过 `.gitignore` 正是为了 `.wt/`、`.venv`、
`node_modules`）。新增 `tests/repo_scan.py` 把这份口径收成一处，两条守卫改用 `iter_scanned()`
/ `tracked_and_new_files()`；各自的排除面（测试面、`docs/notes` 历史面）仍留在调用方——
**收成一处的是扫描集，不是各守卫的判据**。

再加一条 **ratchet**：`tests/*.py` 不得再从「绑定到仓库根的标识符」做文件系统扫描
（`.rglob(` / `os.walk(`）。判据按**绑定关系**认，不按变量名字面量——第一版只认
`REPO/REPO_ROOT/ROOT` 三个名字，`R = Path(__file__).resolve().parents[1]; R.rglob(...)`
直接绕过（实测绕过过一次，注释里留着）。豁免表两项各带理由，并有「失效条目即红」的
反向检查（豁免面悄悄扩大是这仓反复踩过的那类）。

## Alternatives

- **只加一条 `startswith(".wt/")`**：否决。那是把「文件系统 ≠ 仓库内容」散回每个守卫自己记，
  下一个副本目录（别的 agent 缓存树、构建产物）再犯一次；而且修不了本地 `.env` 那一半。
- **只取 `git ls-files`（不并 untracked）**：否决，理由照抄 ip-leak 的 #2402 教训：提交前
  正是门禁最该有效的时候，默认集为空等于没有门禁。
- **改用 `.gitignore` 加更多 negation**：否决。`.gitignore` 是开发者环境策略，不该承担
  门禁语义；且它无法表达「本地有、仓库没有」与「仓库有、本地未跟踪」的区别。
- **顺手把 `test_env_example_parity.py` 也迁到统一口径**：不在本单。它有 #1978 的
  `_rel_parts()` 机制（只跳仓库**内部**的 `.wt`，避免绝对路径含 `.wt` 时把整个 worktree
  跳过、语料变空），本机与 CI 都绿；改一条绿的守卫属另单，本单把它记进豁免表并写明理由。

## Verification

- 本机复现（就是 #2870 报的形态）：`pytest tests/test_ci_test_db_guard_wiring.py -q` →
  1 failed；`pytest tests/test_removed_env_keys.py::test_removed_keys_are_never_referenced_bare` → 1 failed
- 修后：`pytest tests/test_repo_scan_tracked_only_2870.py tests/test_ci_test_db_guard_wiring.py
  tests/test_removed_env_keys.py tests/test_internal_ip_leak_allowlist.py -q` → **全绿**
  （本单新增判据 7 条：真实仓不变量 2 + 合成 git 仓库 4（含「git 不可用必须抛错，
  不得返回空集」）+ ratchet 与其红侧自证 1 组）；
  `pytest tests/ -q` → **1629 passed**（此前本机是 2 failed / 1620 passed）
- 扫描集实测：**3,546 项**（`.wt/` 命中 **0**；`backend/.env` 不在集内；
  `backend/.env.example` **在**集内——示例文件已跟踪，`tracked` 优先于 ignore，
  这条是 #2661 的漂移面，专门有断言钉住）
- **合成 git 仓库**证明的是口径不是运气（fixture 用 tmp_path + `git init`，不碰真仓）：
  已跟踪 ✓、未跟踪新文件 ✓（#2402 形态）、被忽略的 `secret.env` ✗、`.wt/sibling/copy.py` ✗
- **变异自证**（逐条红，还原后全绿）：① 新加一个 `R = Path(__file__).resolve().parents[1]`
  + `R.rglob(...)` 的守卫文件 → ratchet 红（第一版判据曾放它过去，改动后能抓）；
  ② git 不可用的目录 → `tracked_and_new_files` 抛错而非返回空集；
  ③ 后缀写成 `"py"`（缺点）→ 入口 `AssertionError` 直接红，而不是扫出空集静默全绿
- `ruff check tests/` 通过；治理面检查全绿（`check_governance_surface --check`）
- 未做（标 pending）：没在 CI 侧验证「干净检出恒绿」这件事本来就成立（CI 无 `.wt/`），
  本单改变的是**本机有效性**；`test_env_example_parity.py` 的迁移仍待另单

## Revisit

- 如果将来还要挡「别的 agent 的缓存树 / 新 kinds of worktree 目录」，正确动作是加进
  `.gitignore`（它们本就不该是仓库内容），而不是往守卫里再加前缀——扫描集定义只有一处。
- `git ls-files` 在巨型仓上是 O(文件数) 但一次调用；若将来某条守卫又开始逐文件
  `subprocess`，那才是需要重新设计的点（现在是批量取一次再过滤）。
- #2870 也提醒了一类**协作面失效**：门禁的可信度取决于它对**当前工作方式**的建模
  （worktree 并行、多 harness 同仓）。同类风险还有 `.codex/`、`~/.claude` 转录面
  （`gov-skills` 已知依赖本地转录并被 `FULL_EXCLUDE` 排除，见 `scripts/run_gates.py:315`）。
