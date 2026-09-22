# main 树里混入仓库根 `venv` symlink：尾斜杠 ignore 不匹配非目录条目 + `git add -A`

Status: implemented
Class: bug-fix

## Decision

`main` 的树里出现了一个仓库根的 `venv` **symlink**（mode 120000，指向构建机绝对路径
`/home/debian13/stability-test-platform/venv`），由 PR #3114 的第二个 commit（`ce3a0612`）
引入。成因是两件各自无害、叠加后有害的事：

1. 该 PR 的专属 worktree 里为了让测试跑起来建了 `ln -sfn <主树>/venv .wt/<wt>/venv`；
2. 提交时用了 `git add -A`；
3. `.gitignore` 写的是 `venv/`——**尾斜杠只匹配目录**，而工作树里那个条目是 symlink，
   于是不被忽略 → 被 stage 并提交。

后果不是「多一个文件」：生产工作树（仓库根）该路径是**真目录** `venv/`，`git pull` 要把
这个 tracked 条目落到同一位置会撞位失败 ⇒ **主工作树无法快进、部署被挡**（本机实测
`git ls-tree origin/main -- venv` 为 symlink、本地为目录）。另有一条指向构建机绝对路径的
悬空条目进了树（CI 目前不引用仓库根 venv，故未直接翻红——是运气，不是设计）。

改动三处：

- `git rm --cached venv`：把该条目移出树（`git rm` 不跟随 symlink，删的是条目不是目标）；
- `.gitignore`：`venv/` → `venv`（无尾斜杠），与同区的 `.venv` 写法对齐——覆盖 symlink
  与普通文件形态；并留注释说明「尾斜杠只匹配目录」这个具体约束；
- `tests/test_repo_root_venv_hygiene_3117.py`：两条判据分开钉——① `HEAD` 里不存在 `venv`
  条目；② 在**真造了 symlink** 的临时仓库里 `git check-ignore venv` 命中（并反向自证
  目录形态仍被忽略，防止把覆盖弄窄）。

## Alternatives

- **只删条目、不动 `.gitignore`**：否决。成因链的最后一环（尾斜杠不匹配非目录）会原样
  留着，下一个在 worktree 里软链 venv 并 `add -A` 的人会重新踩上。
- **只改 `.gitignore`（`/venv` 根锚定）**：否决。根锚定只堵住仓库根，子目录里同形态的
  软链（如 `backend/venv`）照样能进树；`venv`（无斜杠、全路径匹配）与既有 `.venv`
  一致，覆盖面更合理。
- **改成 `venv` 且保留 `venv/`**：否决。后者成冗余行，「同一路径两条规则」会让后来者
  不知道该信哪条。
- **加一条 gate「根目录不许有 symlink」**：本轮不做。判据不清（合法软链存在，例如
  `CLAUDE.md → AGENTS.md` 就是刻意的软链形态），拍脑袋的通用禁令更容易被绕过或误伤；
  本 Note 的 Revisit 记了它的替代形态。
- **只靠纪律（不用 `git add -A`）**：否决为**唯一**手段。纪律仍然要（已在
  `shared-worktree` 类记忆里记过「显式列文件」），但 `add -A` 是常规写法，判据应当兜住
  它——所以这一条要有测试，而不是只有约定。

## Verification

- 缺陷机制反向自证：临时仓库用**旧**模式 `venv/` + `venv` symlink →
  `git check-ignore -v venv` 返回 **1**（不被忽略，正是本缺陷）；换成新模式后测试
  `test_gitignore_ignores_symlink_named_venv` 通过。
- `test_repo_tree_tracks_no_root_venv_entry` 在提交修复前**红**（HEAD 仍跟踪 `venv`）、
  提交后绿——即这条测试确实钉住了缺陷，不是恒真断言。
- `python -m pytest tests/test_repo_root_venv_hygiene_3117.py -q` → 2 passed。
- 合并后复核：`git ls-tree -r --name-only origin/main -- venv` 为空，且主工作树可以
  正常 `git pull`（pull 成功本身就是这条修复的端到端验证）。
- `python scripts/run_gates.py check:quick`（见 PR 说明）。

## Revisit

- **通用形态的兜底**：本轮只钉 `venv`。若再出现「尾斜杠 ignore + 同名非目录条目」进树，
  考虑在 CI 加一条**面向后果**的判据（例如「树里新增的 symlink 一律要求显式白名单」或
  「tracked 内容里不得含指向仓库外的绝对路径」——后者还能顺带覆盖本 Note 里那条
  `/home/debian13/...` 路径泄漏），而不是泛化的「不许 symlink」。
- **提交习惯**：`git add -A` 在共享 worktree / 带软链的 worktree 里风险已成事实。若之后
  还有类似误收，考虑在 pre-commit 里警告「本次新增了 symlink 或仓库外绝对路径」。
