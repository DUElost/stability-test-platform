# destructive-git 守卫改 shell 感知解析，reference-transaction 修正 stdin 顺序（#3516 G1）

Status: implemented
Class: process

## Decision

#3516 G1 的两个最小项 + 一个接线缺陷，均属 #930 破坏性 git 防线的缺陷修复，**不改变禁止语义集合**
（`git reset --hard`；`git stash` 创建 / `drop` / `clear`；放行 `stash list|show|pop|apply|branch`）：

1. **`.githooks/reference-transaction`**：stdin 协议（`githooks(5)`）每行为 `<old> SP <new> SP <ref>`，脚本原按
   `read -r ref old new` 读取，`refs/stash` 永不命中、stash 留痕告警失效。改为 `read -r old new ref`；
   非阻塞、恒 `exit 0`、只对 `refs/stash` 告警、`$1` 为 state 与告警文案不变。
2. **`tools/dev/check_destructive_git.py`**：原实现「正则切段 → 空白 `split()`」不认 shell 引号与 wrapper，
   既漏拦（`git reset "--hard"`、`git -C "/a b" reset --hard`、`bash -c "…"`、`env|command|sudo git …`、
   绝对路径 git、`$(…)` / 反引号 / 子 shell、`xargs git stash drop`）又误拦（引号 / heredoc 数据里的禁令字样）。
   改为 POSIX shell 子集的一遍词法扫描：尊重单双引号与转义，识别 `&& || ; | &` 与换行，跳过 heredoc 正文
   （`<<-`、带引号定界符），剔除重定向目标；对 `bash|sh|zsh|dash|ksh|ash -c`、`eval`、
   `env|command|builtin|sudo|doas|nohup|time|exec|xargs|nice|setsid|timeout`、绝对路径 git、`$(…)` / 反引号 /
   进程替换、`( … )` 与 `{ …; }` 及 `if/then/do` 后的命令**递归检查**；shell 解释器无 `-c` 时 heredoc /
   here-string 正文按脚本递归检查。docstring 先写「禁止语义集合」再写实现与已知边界。
   **失败语义**（不可逆安全动作）：明确命中 exit 2；词法解析失败（引号 / 括号 / 反引号不平衡、嵌套过深）不当
   PASS，退回对原始文本的保守检查，疑似命中仍阻断并注明「无法解析」，无危险信号则放行；脚本自身异常保持
   exit 1（Claude 显示错误但不阻断，可见、非静默）。对外接口不变（stdin hook JSON、`find_blocked()` 返回
   命中文本或 None）；仅额外容错：JSON 非对象、`tool_input` / `command` 类型异常一律放行而非抛异常。
3. **`.claude/settings.json` 存在性守卫**（独立 commit）：实测 `python3` 打不开不存在的脚本时退出码为 **2**
   （Claude 视为阻断），守卫脚本缺失会使全部 Bash 调用被拦，与文档宣称的「自身异常 fail-open」相反。
   command 前加 `[ -f … ]` 守卫：缺失时 stderr 告警并 `exit 1`（可见但不阻断）；存在时退出码原样透传；
   `permissions` 段不动。

回归：`tests/test_reference_transaction_hook_3516.py`（标准 stdin 样例驱动 `sh .githooks/reference-transaction
<state>`，不依赖 `core.hooksPath`）与 `tests/test_check_destructive_git_3516.py`（`--self-test` + hook 端到端
退出码契约）；两者均为纯离线，自动落入 `pr-agent-tests` 的根 `tests/` 子集。此前 `--self-test` 不在任何 CI 步骤里。
self-test 保留原有全部用例，新增漏拦 / 误拦 / 复合 / 管道后段 / 解析失败边界。

涉及文件：`.githooks/reference-transaction`、`tools/dev/check_destructive_git.py`、`.claude/settings.json`、
两个新测试文件、本 note。

## Alternatives

- **把 `git clean` / `git restore` / `git checkout -- .` / `git push --force` / `git branch -D` 等同族命令加入拦截**：否。
  无事故证据，且存在大量合法用法（丢弃自己的单文件改动等）；ADR-0058 D6「事故驱动棘轮」——无复发证据不预建机制。
  docstring「已知边界」如实写明有意不禁；出现事故再按不变量违规处置流程扩展。
- **引入第三方 shell 解析库（bashlex 等）**：否。hook 在每次 Bash 调用前运行，须零依赖、启动快、在任何解释器上可跑
  （云端容器、WSL、CI 都不保证装了额外包）；第三方解析器对不合法输入的行为也各异，反而难给出统一的 fail 语义。
  标准库 `shlex` 只有词法、没有命令分隔 / heredoc / 命令替换，不足以单独承担，故自写小型扫描器，
  解析不动的一律走保守回退，而不是放行。
- **一律拦 `bash -c` / `sh -c` / `env` / `sudo` 等 wrapper**：否。这些是日常合法用法（`bash -c 'pytest …'`、
  `sudo systemctl …`），一刀切会瘫痪工作流；wrapper 只是「把内部字符串当命令」的载体，递归检查内部命令才与
  禁止语义精确对应，且不新增禁止项。
- **脚本内部异常也 fail-closed（exit 2）**：否（沿用 #930 裁决）——hook 自身 bug 会瘫痪全部 Bash 调用；
  保持 exit 1（可见、非静默）。词法解析失败是**可预期输入**而非脚本 bug，故走保守回退而不是 exit 1。
- **缺失脚本时 exit 0 静默放行**：否，静默即失效不可见；选 exit 1 + stderr 告警。

## Verification

- `python3 tools/dev/check_destructive_git.py --self-test`：通过；
- 改前（origin/main）→ 改后逐条对比脚本：原漏拦 14 条改后全部阻断、原误拦 4 条改后全部放行（含
  `git commit -m "$(cat <<'EOF' … EOF)"` 标准提交形态）、解析失败样例按保守回退；
- 20 万条随机拼接输入的模糊测试 + 深嵌套 / 超长输入：`find_blocked` 无 `_ParseError` 之外的异常
  （避免脚本异常 exit 1 造成绕过）；
- 端到端（子进程喂 hook JSON）：阻断 exit 2、放行 exit 0、非 JSON / 非 Bash / 缺 command exit 0；
- settings.json 接线实测：脚本缺失 → exit 1 + 告警，脚本存在 → 阻断 2 / 放行 0 原样透传；缺失时裸 `python3` 退出码 2（问题复现）；
- 新增两个测试文件在临时安装于会话草稿目录的 pytest 下 `36 passed`；`tests/test_offline_subset_guard.py` 7 passed；
  `ruff check` 新增 / 改动 py 通过（临时装的 PyPI 最新版，与仓库 lock 版本可能有差异，以 CI `lint` 为准）；
- `python3 tools/dev/check_governance_surface.py --check --base origin/main` 与 `--self-test`：通过；`py_compile` 通过；
- 未验证：`check:quick`（云端容器无 `psycopg`，交给 CI）；真实 Claude 会话内的 hook 触发（云端会话的 hook
  仍是旧脚本，合入后由本地会话验证）。

## Revisit

- `repository-workflow.md`「Git 破坏性操作纪律」一节措辞仍是「按 shell 段解析、段首为 git」，与新实现不符；
  #3540 触碰同文件，待其合入后另开小 PR 同步（本 PR 不改该文件以免冲突）；
- #3516 G2：Codex hook 与其他 Harness 对该守卫的覆盖（本守卫只接线 Claude PreToolUse；其余 Harness 仍靠纪律）；
- 同族破坏性命令（`clean` / `restore` / `checkout -- .` / `push --force` / `branch -D`）出现事故时按棘轮入清单；
- 「已知边界」里的间接执行（管道喂 shell、`find -exec`、变量 / 别名间接、脚本文件）若出现真实绕过事故，
  再评估是否补齐；届时优先补宿主表而不是放宽保守回退。
