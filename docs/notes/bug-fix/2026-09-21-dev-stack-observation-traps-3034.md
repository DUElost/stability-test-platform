# dev 栈 / 假 Agent 观测类陷阱登记（#3034）

Status: implemented
Class: bug-fix

## Decision

把七条「踩中不报错、只给错误观测」的判据写进既有检索路径，不新建技能：

1. `.claude/skills/test-env-self-check/SKILL.md`（经 `.agents/skills` symlink）新增
   「dev 栈与假 Agent 驱动（观测类）」——收录 #1/#2/#3/#4/#7（compose `-e`、
   `/proc` 自匹配、coordinator 不喂执行心跳、nginx 空响应、镜像无 ps/pkill）。
2. `tools/dev/fake_agent.py` 模块头坑清单追加 #3/#6/#7（serve 不发 coordinator；
   跨 worktree 须显式 commit；无 ps/pkill）。
3. `docs/development/testing.md` §4 补「UI / worktree 取证」——#5 PageHeader∉main、
   #6 跨 worktree HEAD。

## Alternatives

- **单独新建技能**：与 #2700 已落在 testing.md / test-env-self-check 的读者路径分裂，
  立刻失效；否决。

## Verification

- 人工核对：技能节含 5 条可抄判据；夹具 docstring 含「serve 不发 coordinator」；
  testing.md 含 PageHeader / worktree 两条。
- `python scripts/run_gates.py check:quick`（文档-only；eslint 视 worktree
  node_modules 而定）

## Revisit

若假 Agent 日后补发 coordinator / extend-batch，同步改技能第 3 条与夹具 docstring。
