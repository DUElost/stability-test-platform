# env 门禁在 `.wt/` 专属 worktree 下恒红：跳过规则按绝对路径匹配 `.wt`（#1978）

Status: implemented
Class: bug-fix

## Decision

把两处「路径组件跳过规则」的判据从**绝对路径组件**改为**相对仓库根（`ROOT`）的组件**，
并各补回归：

- `tools/dev/env_inventory.py`：新增 `_rel_parts()`；`_iter_py_files`（`:152`）与
  `example_keys`（`:309`）改用它。
- `tests/test_env_example_parity.py`：同样新增本地 `_rel_parts()`；
  `_example_files`（`:37`）与 `_repo_tokens`（`:56`）改用它。
- `docs/development/local-development.md`：新增 §6「专属 worktree 里跑门禁」，
  给出复用主检出依赖的做法与本次事故的一句话背景。

### 为什么会假红

本仓推荐的并行执行方式是专属 worktree，约定位置就是 `<repo>/.wt/<name>`
（`.gitignore` 已收录 `.wt/`）。而两处跳过规则写的是
`any(part in SKIP_PARTS for part in path.parts)`——用**绝对**路径组件与 `.wt` 比较，
于是当 `ROOT` 自己就在 `.wt/` 下时，**根下每个文件**的绝对组件都含 `.wt`，全被跳过：

- `env_inventory.scan_reads()` 实测返回 **0 条读取** → `audit()` 认定
  `_INTERNAL_ONLY` 每条都「已陈旧」→ **19 条假红**，且报错文本正好是
  「删除声明」，会诱导人删掉**真实读点**；
- `tests/test_env_example_parity.py` 的示例语料变成空集 → 主用例直接
  `assert examples` 红。

跳过规则的**本意**是排除仓库**内部**的 `.wt/`（嵌套 worktree），不是排除
「worktree 根自己」；按相对组件比较同时满足两种意图（另见回归
`test_nested_dot_wt_inside_repo_is_still_skipped`）。

### 影响（为什么值得单独修）

`AGENTS.md` 要求每个 Execution 提交前跑 `python scripts/run_gates.py check:quick`，
而 `check:quick` **首项**就是 `env-inventory` 且**失败即停**——在推荐的 worktree 布局下
这条命令永远走不到后面的 ruff / compileall / gov-surface。实际后果已经出现过：
2026-09-14 讨论 #1969 时把它当成了「main 上的既存红灯」，并据此差点去做一件
（不存在的）清理工作。

### 顺带查实的第二层阻塞（已写入文档，未改代码）

修好跳过规则后 `check:quick` 会继续跑到前端门禁并停在
`eslint: command not found`——**新建 worktree 没有 `frontend/node_modules`**。
这是依赖供给问题而不是门禁语义问题，故不改 `run_gates.py` 的自动兜底（兜底会掩盖
「依赖没装」的真实状态），而是在 `local-development.md` 给出确定做法：把主检出的
`frontend/node_modules`（以及 `.venv`）以相对符号链接暴露进来。两者都在 `.gitignore`
覆盖范围内，不污染 `git status`。

## Alternatives

- **把 `.wt` 从 `SKIP_PARTS` / `_SKIP_DIRS` 里删掉**：放弃。会让仓库内部嵌套的
  worktree 被扫进语料（本意丢失）；`.git` / `node_modules` / `.venv` 同理不能删。
- **只修 `env_inventory.py`**：放弃。同一 bug 在 `test_env_example_parity.py` 有两处，
  只修一处会让该文件继续假红。
- **改约定：worktree 不放在 `.wt/` 下**：放弃。`.wt/` 是仓库既有约定（已被
  `.gitignore` 收录、多会话在用），且问题本质是**判据用错**，换目录只是绕开。
- **让 `run_gates.py` 在缺依赖时自动去主检出找 `node_modules`**：本次不做。属环境
  供给而非门禁语义；静默兜底会掩盖依赖未装。若后续发现「新会话反复踩」，
  再按仓库既有做法（同类问题 ≥2 次再前移/机制化）评估。
- **改 `docs/development/environment-variables.md`**：放弃。本次修复不改变清单内容
  （正常检出下 reads 与生成块都不变），且该文件当前由在窗 Execution（#737 P1 试点）
  声明；不动它可避免与其开放 PR 冲突。

## Verification

本机实跑，`origin/main` = `f8fed0bf`：

| 场景 | 命令 | 修前 | 修后 |
|---|---|---|---|
| `.wt/` 工作树（复现布局） | `pytest tests/test_env_inventory.py tests/test_env_example_parity.py -q` | **2 failed / 6 passed** | **13 passed** |
| 主检出（普通布局） | 同上 | 8 passed | 行为不变（判据按相对组件，普通布局下相对组件不含跳过名） |
| 直接观测 | `scan_reads()` 在 `.wt/` 根下 | **reads=0** | 正常返回读点 |
| 仓库级契约 | `.wt/` 工作树内 `pytest tests/ -q` | 327 passed / **2 failed** | **337 passed** |
| 门禁整链 | `.wt/` 工作树内 `run_gates.py check:quick` | 首项即 FAIL（env-inventory） | `[OK] check:quick (10 gates)`，exit 0 |

关键断言（新增 3 例）：

- `test_scan_reads_works_when_repo_root_lives_under_dot_wt`：仓根为
  `<tmp>/.wt/stp-x` 时 `scan_reads` 仍能扫到 `ZZ_WT_VAR`（旧实现返回空）；
- `test_nested_dot_wt_inside_repo_is_still_skipped`：仓库**内部**的
  `backend/.wt/nested/` 仍被跳过（保住原意，防过度放开）；
- `test_example_corpus_survives_dot_wt_worktree_root`：仓根位于 `.wt/` 下时
  `_example_files()` 不再返回空集。

`check:quick` 在 worktree 内先因缺 `frontend/node_modules` 停在 eslint，按本文档给的
符号链接做法（`ln -s ../../../frontend/node_modules frontend/node_modules`、
`ln -s ../../.venv .venv`，两条均实测可用）后 10 gates 全绿。

## Revisit

- 若将来出现第三种「路径组件恰好等于某个跳过名」的布局（例如把仓库放在名为
  `venv/` 的目录下、或 vendored 依赖目录叫 `scripts/`），同一判据仍然成立；但要注意
  `SCAN_SKIP_PARTS` 里的 `scripts` 是**相对扫描根**比较的（`backend/scripts`），
  与本次改动无关，不要一并改成相对 `ROOT`。
- 「worktree 缺依赖导致前端门禁停摆」若在一个月内被第二个会话再次踩到，应把依赖供给
  （或 `check:quick` 的前置自检提示）机制化，而不是继续靠文档。
- 本次只在主检出（普通布局）与 `.wt/` 布局各验证了一遍，**未**在 `node_modules`/`.venv`
  缺失且不链接的裸 worktree 上跑完整 `check:full`。
