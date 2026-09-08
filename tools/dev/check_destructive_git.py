#!/usr/bin/env python3
"""Claude PreToolUse hook：拦截破坏性 git 操作（#930）。

`.claude/settings.json` 的 PreToolUse/Bash 接线本脚本：stdin 收 Claude hook
JSON（tool_input.command），按 shell 段（&& | ; 换行）解析，段首为 git 且
token 级命中破坏性子命令即 exit 2（Claude 阻断该次工具调用，stderr 反馈给
agent）。

纪律与风险分级见 repository-workflow.md §Git 破坏性操作纪律：
- 阻断：`git reset --hard`；`git stash` / `stash push|save|drop|clear`（创建或销毁）
- 放行：`stash list|show|pop|apply|branch`（读侧/恢复侧）；非 git 段
  （`grep 'git stash'` 这类含禁令字样的误报面）与 env 前缀外的伪装段
- 判定粒度=token（#1045）：跳过 env 前缀与 git 全局选项（`-C <dir>` /
  `-c k=v` / `--no-pager` / `--git-dir=…` 等）后取子命令 token——git 参数
  里的禁令字样（`git commit -m "...git stash..."`）不误伤，子命令前插
  git 自身选项（`git -C /x reset --hard`）不绕过
- 已知边界：子 shell `(git stash)` 段首判定不覆盖；git 级对 reset --hard
  无干净拦截点（ref 事务无法与普通提交区分）——同族扩展（git restore . /
  git clean -f）按棘轮事故驱动
- fail-open：脚本自身异常 exit 1（Claude 显示错误但不阻断）；只有显式命中
  才 fail-closed（exit 2）

用法:
    echo '{"tool_name":"Bash","tool_input":{"command":"git reset --hard"}}' \
        | python3 tools/dev/check_destructive_git.py     # → exit 2
    python3 tools/dev/check_destructive_git.py --self-test
"""
from __future__ import annotations

import json
import re
import sys

# git 自身全局选项中「带独立值」的：判定子命令时须连值一起跳过（#1045）
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                   "--super-prefix"}
# stash 读侧/恢复侧子命令（其余 stash 子命令与裸 stash 一律阻断）
_STASH_READONLY = {"list", "show", "pop", "apply", "branch"}

_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_ENV_ASSIGN = re.compile(r"[A-Za-z_]\w*=\S*")


def _subcommand(words: list[str], i: int) -> tuple[str | None, list[str]]:
    """从 words[i:] 跳过 git 全局选项后返回 (子命令, 其余 token)。纯函数。"""
    while i < len(words):
        w = words[i]
        if w in _GIT_VALUE_OPTS:
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w, words[i + 1:]
    return None, []


def find_blocked(command: str) -> str | None:
    """返回第一个命中破坏性模式的 shell 段；无则 None。纯函数（自测共用）。

    token 级判定（#1045）：匹配只作用于 token，不回拼整段文本——参数里的
    禁令字样不误伤，git 选项前缀不构成绕过。stash 放行判定取 stash 后第一个
    非选项 token；裸 stash / 未知识子命令 fail-closed。
    """
    for segment in _SEGMENT_SPLIT.split(command):
        words = segment.strip().split()
        i = 0
        while i < len(words) and _ENV_ASSIGN.fullmatch(words[i]):
            i += 1
        if i >= len(words) or words[i] != "git":
            continue
        sub, rest = _subcommand(words, i + 1)
        if sub == "reset" and "--hard" in rest:
            return segment.strip()
        if sub == "stash":
            j = 0
            while j < len(rest) and rest[j].startswith("-"):
                j += 1
            if j >= len(rest) or rest[j] not in _STASH_READONLY:
                return segment.strip()
    return None


def run_self_test() -> int:
    failures: list[str] = []

    def expect(name: str, command: str, should_block: bool) -> None:
        hit = find_blocked(command)
        if bool(hit) != should_block:
            failures.append(f"{name}: 预期{'阻断' if should_block else '放行'}，实际 {hit!r}")

    # 红向（应阻断）
    expect("reset --hard", "git reset --hard", True)
    expect("reset --hard 带参数", "git reset --hard HEAD~1", True)
    expect("reset 尾置 --hard", "git reset HEAD~1 --hard", True)
    expect("复合命令", "cd /tmp/x && git reset --hard", True)
    expect("env 前缀", "FOO=1 git stash", True)
    expect("stash 创建", "git stash push -m wip", True)
    expect("stash 裸调用", "git stash", True)
    expect("stash drop", "git stash drop stash@{0}", True)
    expect("stash clear", "git stash clear", True)
    expect("分号串联", "git add -A; git stash", True)
    # 红向·选项前缀不构成绕过（#1045 修复面）
    expect("-C 绕过已堵", "git -C /tmp reset --hard", True)
    expect("--no-pager 绕过已堵", "git --no-pager stash drop", True)
    expect("-c 绕过已堵", "git -c x.y=1 reset --hard", True)
    expect("--git-dir 绕过已堵", "git --git-dir=/tmp reset --hard", True)
    expect("-C + stash drop", "git -C /tmp stash drop", True)
    expect("多重选项后 reset", "git --no-pager -C /tmp reset --hard", True)
    # 绿向（应放行）
    expect("stash 读侧 list", "git stash list", False)
    expect("stash 恢复 pop", "git stash pop", False)
    expect("stash 恢复 apply", "git stash apply stash@{1}", False)
    expect("stash show", "git stash show -p", False)
    expect("stash 选项后读侧", "git stash --quiet list", False)
    expect("grep 误报面", "grep -rn 'git stash' docs/", False)
    expect("echo 误报面", "echo 'git reset --hard is forbidden'", False)
    expect("普通 git", "git status --short", False)
    expect("reset 软形式", "git reset --soft HEAD~1", False)
    expect("非 git 工具", "pytest -q", False)
    # 绿向·git 参数内禁令字样不误伤（#1045 修复面）
    expect("commit 参数含禁令词", 'git commit -m "save work: git stash later"', False)
    expect("log grep 含禁令词", 'git log --grep "git stash push"', False)
    expect("--no-pager 读侧", "git --no-pager stash list", False)
    expect("-c 前缀普通命令", "git -c x.y=1 status --short", False)

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_destructive_git self-test 通过（阻断/放行红绿双向 + 段解析边界）")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return run_self_test()
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # 非 hook JSON 调用形态，放行
    if data.get("tool_name") != "Bash":
        return 0
    command = (data.get("tool_input") or {}).get("command", "") or ""
    hit = find_blocked(command)
    if hit:
        print(
            f"[BLOCKED] 破坏性 git 操作被纪律拦截：{hit!r}\n"
            "未提交工作请显式 commit/分支保存，不要 stash/reset --hard；\n"
            "禁令、风险分级与放行清单见 repository-workflow.md §Git 破坏性操作纪律。",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
