# ip-leak 默认扫描集并入未跟踪文件：消掉「提交前门禁看不见新文件」（#2432）

Status: implemented
Class: process

## Decision

`tools/dev/check-internal-ip-leak.py` 的默认目标集从 `git ls-files` 改为
**`git ls-files` ∪ `git ls-files --others --exclude-standard`**（抽成可测的
`default_targets(root)`）。

### 为什么是这一处，而不是「给 check:quick 加一个 gate」

我上一轮在 PR #2422 上先给出的解释是「ip-leak 不在 `check:quick` 的 gate 集合里」——
**那句是错的**，`scripts/run_gates.py:184/277` 证明它一直在，命令也是 `--check -q`。
真因在扫描集：本地跑的那一刻，`tools/dev/fake_agent.py` 与那条 Note **还是 untracked**，
默认集里没有它们，于是门禁连绿两轮、push 后 CI 才红（第一次红在夹具里的真实内网 IP，
第二次红在「记录这个坑」的 Note 自己把地址抄进了文档）。

也就是说：**gate 在、命令对、结果假绿**——最坏的一类失效，因为它让人以为有防线。
而且「提交前」正是唯一还来得及便宜的时点：文件一旦进了 Git，改它就要走 PR、
历史里还留着那个值（本仓是 public 仓库，#538 的整条前提就是这个）。

### 仍然不扫 `.gitignore` 命中项（`--exclude-standard`）

这条不是可选项：本机的 `node_modules`、`.venv`、以及 **37 个 `.wt/*` 并行 worktree**
（每个都是整仓副本）都在忽略集里。把它们扫进来不只是慢——`.wt` 里别人在途的文件
被我的门禁判红，等于跨 Execution 误伤。所以「未跟踪」与「被忽略」必须分开，
两者各有正反用例钉住。

### 非 git 环境的行为未改

`_git_ls_files` 失败仍 `SystemExit(2)`（与原先一致）。显式传 `paths` 的分支不变
（目录走 `rglob`，本来就含未跟踪文件——所以 `--staged`/显式路径的既有用法不受影响）。

## Alternatives

- **只在文档里写「提交前先 `git add` 再跑门禁」**：否决。它把一条安全红线（public 仓库
  不泄漏内网资产）的成立与否，押在操作者是否记得一个顺序上；而这道门禁的存在理由
  恰恰是「人会忘」。
- **把 CI 的 gate 抄进 `check:quick`**：不解决本问题——`check:quick` **已经**含 ip-leak，
  缺的是文件集。
- **默认改成「全仓 rglob」**：否决，会读进 `.git`、`node_modules`、`.wt`，既慢又制造
  跨 worktree 误伤；`.gitignore` 是现成且已被维护的边界。
- **顺手把其它 `ls-files` 型门禁一起改**：本单只做 ip-leak。其它门禁是否有同型盲区，
  判据是「默认集来源是否为 `git ls-files` 且新文件在提交前需要受检」——不批量改
  是为了让每条都有正反用例，而不是把同一动作复制到没验证过的地方。

## Verification

- `tests/test_internal_ip_leak_allowlist.py` **7 passed**（原 4 条不动，新增 3 条）：
  默认集含未跟踪、默认集不含被忽略项、**端到端**（往未跟踪文件里写真实内网地址 →
  `main(["--check"])` 退出 1 且明细点名该文件）。
- **红绿自证**：回退工具（保留用例）→ **3 failed**（`default_targets` 不存在 /
  端到端检出不到 → 正是旧的盲区）。恢复后 7 passed。
  过程里我自己的用例先红过一次：明细走 stdout、处置提示走 stderr，我一开始只查
  `capsys` 的 `err`——断言范围写错，不是实现问题；已改为合并 out+err 并在测试里注明。
- 真仓复跑新默认集：`[ip-leak] 通过：3139 个文件无真实内网主机地址`（旧口径 3100+，
  差值即原先看不见的那批文件）；本机主工作树现存 2 个未跟踪文件（
  `backend/services/plan_run_event_feed.py`、`plan_run_read_common.py`）单独扫过为
  **无命中**，所以这次收口不会把别人在途文件判红。
- `scripts/run_gates.py check:quick`、`ruff`：见 PR。

## Revisit

- 若将来再加「默认集来自 git」型门禁，直接复用 `default_targets(root)`，不要各自再写
  一遍 `git ls-files`——本单的失效就是「各自实现 + 只取已跟踪」的合成品。
- 更彻底的收口是给 pre-commit 钩子传 `--staged` + 显式 paths（`--staged` 已支持，
  但要求显式文件列表）。本单不动钩子：改门禁语义与改提交钩子是两件事，混在一起
  会让「谁拦的」变难判——先让默认集正确。
