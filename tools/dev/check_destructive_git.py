#!/usr/bin/env python3
"""Claude PreToolUse hook：拦截破坏性 git 操作（#930；shell 感知解析 #3516 G1）。

`.claude/settings.json` 的 PreToolUse/Bash 接线本脚本：stdin 收 Claude hook
JSON（tool_input.command），用 shell 感知的解析器把命令拆成简单命令（simple
command），任一简单命令命中下列「禁止语义集合」即 exit 2（Claude 阻断该次工具
调用，stderr 反馈给 agent）。

纪律与风险分级见 repository-workflow.md §Git 破坏性操作纪律。

## 要禁止的语义集合（判定对象是「实际会被 shell 执行的 git 简单命令」）

- 阻断：`git reset --hard`（含 `--hard` 的无歧义前缀缩写 `--ha` / `--har`）；
  `git stash`（裸调用 / `push|save|create|store|…` 创建）；`git stash drop|clear`。
  凡不在放行集的 stash 子命令一律阻断（未知子命令 fail-closed）。
- 放行：`git stash list|show|pop|apply|branch`（读侧 / 恢复侧）；其余全部 git 用法。
- 「实际执行」的含义（本次由字符串正则改为词法解析的原因）：
  * 引号、转义只影响写法，不改变语义：`git reset "--hard"`、`"git" stash`、
    `\\git stash`、`g""it stash` 与 `git reset --hard` 等价 → 阻断；
  * 引号内、heredoc 正文里的禁令字样只是数据，不执行：`git commit -m "…; git stash …"`、
    `grep -E 'a|git stash|b'`、`cat <<EOF … git stash … EOF` → 放行；
  * 会执行其内部命令的结构必须递归检查：`bash|sh|zsh|dash|ksh|ash -c '<cmd>'`
    （含 `-lc` 等组合选项）、`eval <cmd>`、`env [-i] [VAR=v] cmd`（含 `env -S '<cmd>'`）、
    `command|builtin|sudo|doas|nohup|time|exec|xargs|nice|setsid|timeout … cmd`、
    绝对路径 git（basename 为 `git`）、`$(…)` / 反引号 / `<(…)` `>(…)`（含双引号内）、
    `( … )` 子 shell、`{ …; }` 组与 `if/then/do/while` 等保留字之后的命令；
    以 shell 为解释器且无 `-c` 时（`bash <<EOF … EOF`、`bash <<< '…'`），heredoc /
    here-string 正文按脚本递归检查。
- 判定粒度=token（#1045）：跳过 env 前缀与 git 全局选项（`-C <dir>` / `-c k=v` /
  `--no-pager` / `--git-dir=…` 等）后取子命令 token——git 参数里的禁令字样
  （`git commit -m "...git stash..."`）不误伤，子命令前插 git 选项不绕过。

## 失败语义（不可逆安全动作：宁可误拦一条可读的提示，不可静默放过）

1. 明确命中 → exit 2（fail-closed），stderr 给出被拦的简单命令与指引；
2. 词法解析失败（引号 / 括号 / 反引号不平衡、嵌套过深）→ **不当作 PASS**：退回对
   原始文本的保守检查（去掉引号与反斜杠后，凡出现 `git` 且其后同一段内有
   `reset` + `--hard` 或非放行的 `stash` 即疑似命中）；疑似命中 → exit 2 并注明
   「无法解析」；保守检查也无命中 → exit 0（无从判断且无任何危险信号）；
3. 脚本自身异常（未预期的 Python 异常）→ 保持 exit 1：Claude 显示错误但不阻断——
   「可见、非静默」；只有显式命中才 fail-closed；
4. 非 hook JSON / 非 Bash 工具 / 缺 command → exit 0（放行）；
5. hook 接线的脚本缺失（`python3` 打不开脚本时退出码为 2，会被 Claude 当作阻断）
   由 `.claude/settings.json` 的 command 内存在性守卫处理：缺失时 stderr 告警并
   exit 1（可见、不阻断），见该文件。

## 已知边界（如实；同族扩展按棘轮事故驱动，ADR-0058 D6，不在此预先加禁）

- 不覆盖：变量 / 别名 / 函数间接（`$GIT reset --hard`、`git -c alias.x='reset --hard' x`、
  `f() { git stash; }; f` 只在定义处命中）；管道喂给 shell（`echo 'git stash' | bash`）；
  命令串由替换动态产生（`bash -c "$(echo '…')"`）；花括号展开（`git {reset,--hard}`）；
  执行脚本文件（`bash x.sh`、`source x.sh`、`source <(…)`）；`find -exec` / `watch` / `parallel` /
  `ssh` / `python -c` / `awk system()` 等其它「把字符串当命令」的宿主；ANSI-C 引号里的
  十六进制 / 八进制转义（`$'\\x67it'`）；`--hard` 之外的 git 选项缩写；
  `case … in a) … ;; esac` 出现在 `$(…)` 内时的 `)` 歧义。
- 验证方式（#3545 复核后补）：除 `--self-test` 与 tests 外，用「真实 bash + 只记录 argv 的 mock git」
  作差分事实来源——把危险参数形态 × 7 种引号写法 × 约 50 种 shell 宿主结构（列表 / 管道 / 子 shell /
  控制流 / 各类包装器 / heredoc / here-string / eval / 命令替换 …）逐条真实执行，与守卫判断对比；
  修复后一万余次执行中，非上述已知缺口的宿主漏拦为 0。已知缺口宿主的漏拦是预期内的。
- 有意不禁（无事故证据，D6 棘轮）：`git clean` / `git restore` / `git checkout -- .` /
  `git push --force` / `git branch -D` 等同族破坏性命令。
- git 级对 reset --hard 无干净拦截点（ref 事务无法与普通提交区分）——阻断层只在
  Claude PreToolUse；`.githooks/reference-transaction` 仅对 refs/stash 观测告警。
- 本解析器是「够用的 POSIX shell 子集词法」：不实现 `${…}` / 算术 / 数组展开语义，
  这些按字面字符处理；解析不动的一律走上面失败语义第 2 条，而不是放行。

用法:
    echo '{"tool_name":"Bash","tool_input":{"command":"git reset --hard"}}' \\
        | python3 tools/dev/check_destructive_git.py     # → exit 2
    python3 tools/dev/check_destructive_git.py --self-test
"""
from __future__ import annotations

import json
import posixpath
import re
import shlex
import sys

# git 自身全局选项中「带独立值」的：判定子命令时须连值一起跳过（#1045）
# 取自 git.c handle_options 的封闭集合：以下全局选项都接受「选项 SP 值」写法（另有 `=` 连写写法，
# 由下面 startswith("-") 分支覆盖）。漏一个就会把它的值误当子命令而放行（#3545 复核：--config-env）。
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                   "--super-prefix", "--config-env", "--attr-source"}
# stash 读侧/恢复侧子命令（其余 stash 子命令与裸 stash 一律阻断）
_STASH_READONLY = {"list", "show", "pop", "apply", "branch"}

_ENV_ASSIGN = re.compile(r"[A-Za-z_]\w*=")
# 命令位置上的 shell 保留字：其后才是真正的命令
_RESERVED = {"{", "}", "!", "if", "then", "else", "elif", "do", "while", "until"}
_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash"}
# 包装器 → (带独立值的选项, 命令前需跳过的位置参数个数)
_WRAPPERS: dict[str, tuple[set[str], int]] = {
    "env": ({"-u", "--unset", "-C", "--chdir"}, 0),
    "command": (set(), 0),
    "builtin": (set(), 0),
    "sudo": ({"-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U", "-r", "-t",
              "--user", "--group", "--host", "--prompt", "--chdir", "--chroot",
              "--role", "--type", "--other-user", "--close-from"}, 0),
    "doas": ({"-u", "-C"}, 0),
    "nohup": (set(), 0),
    "time": ({"-f", "--format", "-o", "--output"}, 0),
    "exec": ({"-a"}, 0),
    "xargs": ({"-I", "-L", "-l", "-n", "-P", "-s", "-d", "-E", "-a", "-J",
               "--max-args", "--max-procs", "--max-lines", "--delimiter",
               "--arg-file", "--max-chars", "--eof"}, 0),
    "nice": ({"-n", "--adjustment"}, 0),
    "setsid": (set(), 0),
    "timeout": ({"-s", "--signal", "-k", "--kill-after"}, 1),  # 1 = DURATION
}
_MAX_DEPTH = 24  # 嵌套（$(…) / -c 递归）上限；超限按「无法解析」走保守回退


class _ParseError(Exception):
    """词法解析失败（引号/括号/反引号不平衡、嵌套过深）；由 _analyze 接住做保守回退。"""


class _Cmd:
    """一条简单命令：words 为去引号后的词；subs 为其内命令替换 / 子 shell 解析出的
    命令；stdin 为 heredoc 正文 / here-string 词（仅当解释器是 shell 时当代码）。"""

    __slots__ = ("words", "subs", "stdin")

    def __init__(self) -> None:
        self.words: list[str] = []
        self.subs: list[_Cmd] = []
        self.stdin: list[str] = []


def _find_backtick(text: str, i: int) -> int:
    """text[i:] 起找未转义的收尾反引号，返回其下标。"""
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i
        i += 1
    raise _ParseError("反引号未闭合")


class _Scanner:
    """POSIX shell 子集的一遍扫描：尊重单/双引号与反斜杠，识别 `; | & 换行 ( )`，
    跳过 heredoc 正文（含 `<<-` 与带引号定界符），把重定向目标从 words 中剔除，
    递归解析 `$(…)` / 反引号 / `<(…)`。nested=True 时遇到失衡的 `)` 即结束。"""

    def __init__(self, text: str, start: int = 0, nested: bool = False,
                 depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            raise _ParseError("嵌套过深")
        self.t = text
        self.n = len(text)
        self.i = start
        self.nested = nested
        self.depth = depth
        self.paren = 0
        self.cmds: list[_Cmd] = []
        self.cmd = _Cmd()
        self.cur: list[str] = []
        self.in_word = False
        self.quoted = False
        self.expect: str | None = None  # drop | here | heredoc
        self.strip = False
        self.pending: list[tuple[_Cmd, str, bool, bool]] = []

    # -- 词与命令边界 -----------------------------------------------------
    def _add(self, s: str, quote: bool = True) -> None:
        self.cur.append(s)
        self.in_word = True
        if quote:
            self.quoted = True

    def _flush(self) -> None:
        if not self.in_word:
            return
        word, quoted = "".join(self.cur), self.quoted
        self.cur, self.in_word, self.quoted = [], False, False
        expect, self.expect = self.expect, None
        if expect is None:
            self.cmd.words.append(word)
        elif expect == "here":
            self.cmd.stdin.append(word)
        elif expect == "heredoc":
            self.pending.append((self.cmd, word, self.strip, quoted))
        # expect == "drop"：重定向目标，丢弃

    def _end_cmd(self) -> None:
        self._flush()
        self.expect = None
        self.cmds.append(self.cmd)
        self.cmd = _Cmd()

    # -- 嵌套 -------------------------------------------------------------
    def _nested_cmds(self, start: int) -> int:
        sub = _Scanner(self.t, start, nested=True, depth=self.depth + 1)
        self.cmd.subs.extend(sub.run())
        return sub.i

    def _backtick(self, start: int) -> int:
        end = _find_backtick(self.t, start)
        inner = self.t[start:end].replace("\\`", "`")
        self.cmd.subs.extend(_Scanner(inner, depth=self.depth + 1).run())
        return end + 1

    # -- 各类记号 ---------------------------------------------------------
    def _dquote(self) -> None:
        t, n = self.t, self.n
        j, buf = self.i + 1, []
        while True:
            if j >= n:
                raise _ParseError("双引号未闭合")
            c = t[j]
            if c == '"':
                break
            if c == "\\" and j + 1 < n and t[j + 1] in '"\\$`\n':
                if t[j + 1] != "\n":
                    buf.append(t[j + 1])
                j += 2
            elif c == "$" and t[j + 1:j + 2] == "(":
                j = self._nested_cmds(j + 2)
            elif c == "`":
                j = self._backtick(j + 1)
            else:
                buf.append(c)
                j += 1
        self._add("".join(buf))
        self.i = j + 1

    def _dollar(self) -> None:
        t, n, i = self.t, self.n, self.i
        nxt = t[i + 1:i + 2]
        if nxt == "(":
            self._add("")
            self.i = self._nested_cmds(i + 2)
        elif nxt == "'":  # ANSI-C 引号：反斜杠转义按「取下一字符字面」近似
            j, buf = i + 2, []
            while j < n and t[j] != "'":
                if t[j] == "\\" and j + 1 < n:
                    buf.append(t[j + 1])
                    j += 2
                else:
                    buf.append(t[j])
                    j += 1
            if j >= n:
                raise _ParseError("$'…' 未闭合")
            self._add("".join(buf))
            self.i = j + 1
        elif nxt == '"':
            self.i = i + 1  # 丢掉 `$`，由下一轮按普通双引号处理
        else:
            self._add("$", quote=False)
            self.i = i + 1

    def _redirect(self) -> None:
        t, n = self.t, self.n
        # 词若是纯数字 fd 前缀（`2>`）则丢弃，否则先收尾
        if self.in_word and not self.quoted and "".join(self.cur).isdigit():
            self.cur, self.in_word = [], False
        else:
            self._flush()
        k = self.i + 1 if t[self.i] == "&" else self.i  # `&>` / `&>>`
        j = k
        while j < n and t[j] in "<>":
            j += 1
        op = t[k:j]
        if j < n and t[j] == "(" and op in ("<", ">"):  # 进程替换
            self._add("")
            self.i = self._nested_cmds(j + 1)
            return
        if op == "<<<":
            self.expect = "here"
        elif op == "<<":
            self.strip = j < n and t[j] == "-"
            if self.strip:
                j += 1
            self.expect = "heredoc"
        else:
            if j < n and t[j] in "&|" and t[self.i] != "&":  # `>&` `>|` `<&`
                j += 1
            self.expect = "drop"
        self.i = j

    def _read_heredocs(self) -> None:
        t, n = self.t, self.n
        for cmd, delim, strip, quoted in self.pending:
            lines: list[str] = []
            while self.i < n:
                eol = t.find("\n", self.i)
                line = t[self.i:] if eol < 0 else t[self.i:eol]
                self.i = n if eol < 0 else eol + 1
                probe = line.lstrip("\t") if strip else line
                if probe.rstrip("\r") == delim:
                    break
                lines.append(line)
            body = "\n".join(lines)
            cmd.stdin.append(body)
            if not quoted:  # 定界符未加引号 → 正文里的 $(…) / 反引号会被执行
                cmd.subs.extend(_body_subs(body, self.depth + 1))
        self.pending = []

    # -- 主循环 -----------------------------------------------------------
    def run(self) -> list[_Cmd]:
        t, n = self.t, self.n
        while self.i < n:
            c = t[self.i]
            if c in " \t\r":
                self._flush()
                self.i += 1
            elif c == "\n":
                self._end_cmd()
                self.i += 1
                if self.pending:
                    self._read_heredocs()
            elif c == "\\":
                if self.i + 1 < n and t[self.i + 1] != "\n":
                    self._add(t[self.i + 1])
                self.i += 2
            elif c == "'":
                j = t.find("'", self.i + 1)
                if j < 0:
                    raise _ParseError("单引号未闭合")
                self._add(t[self.i + 1:j])
                self.i = j + 1
            elif c == '"':
                self._dquote()
            elif c == "`":
                self._add("")
                self.i = self._backtick(self.i + 1)
            elif c == "$":
                self._dollar()
            elif c == "#" and not self.in_word:
                j = t.find("\n", self.i)
                self.i = n if j < 0 else j
            elif c in "<>" or (c == "&" and t[self.i + 1:self.i + 2] == ">"):
                self._redirect()
            elif c == "(":
                self._end_cmd()
                self.paren += 1
                self.i += 1
            elif c == ")":
                self._end_cmd()
                self.i += 1
                if self.nested and self.paren == 0:
                    return self.cmds
                self.paren = max(0, self.paren - 1)
            elif c in ";|&":
                self._end_cmd()
                self.i += 1
            else:
                self._add(c, quote=False)
                self.i += 1
        if self.nested:
            raise _ParseError("$(…) 未闭合")
        self._end_cmd()
        return self.cmds


def _body_subs(body: str, depth: int) -> list[_Cmd]:
    """未加引号定界符的 heredoc 正文里会被执行的 `$(…)` / 反引号。正文本身是数据。

    **解析失败一律向上抛**（由 _analyze 走保守回退），不在此吞掉：深度超限是合法 Bash（内层
    命令会执行），不是语法错误（#3545 复核：26 层 `$(` 被静默放行）；括号 / 反引号「失衡」也
    可能只是本扫描器的误判（如 `$( case … esac )` 的 `)` 歧义，见 docstring 已知边界），
    不能当作「shell 自己会报错所以不执行」。代价：未加引号的 heredoc 数据里含失衡的 `$(` 时，
    只有正文同时出现 `git reset --hard` / `git stash` 才会被保守回退拦下，其余照常放行。"""
    subs: list[_Cmd] = []
    j = 0
    while j < len(body):
        c = body[j]
        if c == "\\":
            j += 2
        elif c == "$" and body[j + 1:j + 2] == "(":
            sub = _Scanner(body, j + 2, nested=True, depth=depth)
            subs.extend(sub.run())
            j = sub.i
        elif c == "`":
            end = _find_backtick(body, j + 1)
            subs.extend(_Scanner(body[j + 1:end].replace("\\`", "`"),
                                 depth=depth).run())
            j = end + 1
        else:
            j += 1
    return subs


# ── 语义判定 ──────────────────────────────────────────────────────────────

def _subcommand(words: list[str], i: int) -> tuple[str | None, list[str]]:
    """从 words[i:] 跳过 git 全局选项后返回 (子命令, 其余 token)。纯函数。"""
    while i < len(words):
        w = words[i]
        if w in _GIT_VALUE_OPTS:
            i += 2
        elif w.startswith("-"):
            i += 1
        elif "=" in w:
            # 兜底：子命令名不含 `=`。未来 git 新增的「选项 SP name=value」全局选项，其值必含 `=`，
            # 在此吞掉而不是误当子命令放行（宁可对罕见的含 `=` 别名多拦，也不漏拦）
            i += 1
        else:
            return w, words[i + 1:]
    return None, []


def _is_hard(w: str) -> bool:
    """`--hard` 及其无歧义前缀缩写（git parse-options 接受 `--ha` / `--har`）。"""
    return len(w) >= 4 and "--hard".startswith(w)


def _stash_blocked(rest: list[str]) -> bool:
    j = 0
    while j < len(rest) and rest[j].startswith("-"):
        j += 1
    return j >= len(rest) or rest[j] not in _STASH_READONLY


def _git_hit(words: list[str]) -> str | None:
    """words[0] 是 git 时判定禁止语义集合；命中返回该简单命令文本。"""
    sub, rest = _subcommand(words, 1)
    if (sub == "reset" and any(_is_hard(w) for w in rest)) or (
            sub == "stash" and _stash_blocked(rest)):
        return shlex.join(words)
    return None


def _unwrap(prog: str, args: list[str]) -> tuple[list[str], str | None]:
    """剥掉包装器自身的选项 / 位置参数，返回 (内部命令词, 需递归检查的代码串|None)。"""
    valopts, positional = _WRAPPERS[prog]
    code: str | None = None
    i = 0
    while i < len(args):
        w = args[i]
        if w == "--":
            i += 1
            break
        if prog == "env" and w in ("-S", "--split-string") and i + 1 < len(args):
            code = args[i + 1]  # env -S 'git stash'：值本身是命令行
            i += 2
        elif w.startswith("-") and len(w) > 1:
            i += 2 if w in valopts else 1
        else:
            break
    i += positional
    return args[i:], code


def _shell_code(args: list[str]) -> str | None:
    """`bash -c '<cmd>'`（含 `-lc` / `-ec` / `--login -c` 等）→ `<cmd>`；无 -c 返回 None。"""
    i = 0
    while i < len(args):
        w = args[i]
        if w == "--":
            return None
        if w.startswith("--"):
            i += 2 if w in ("--rcfile", "--init-file") else 1
        elif w[:1] in "-+" and len(w) > 1:
            letters = w[1:]
            if w[0] == "-" and "c" in letters:
                j = i + 1
                while j < len(args) and args[j][:1] in "-+" and len(args[j]) > 1:
                    j += 2 if args[j] in ("-o", "+o", "-O", "+O") else 1
                return args[j] if j < len(args) else None
            i += 2 if letters[-1] in "oO" else 1
        else:
            return None  # 首个位置参数是脚本文件，不在检查范围
    return None


def _check_text(text: str, depth: int) -> str | None:
    if depth > _MAX_DEPTH:
        raise _ParseError("嵌套过深")
    return _check_cmds(_Scanner(text, depth=depth).run(), depth)


def _check_cmds(cmds: list[_Cmd], depth: int) -> str | None:
    for cmd in cmds:
        hit = _check_cmds(cmd.subs, depth) or _check_words(cmd.words, cmd.stdin, depth)
        if hit:
            return hit
    return None


def _check_words(words: list[str], stdin: list[str], depth: int) -> str | None:
    while True:
        i = 0
        while i < len(words) and (words[i] in _RESERVED
                                  or _ENV_ASSIGN.match(words[i])):
            i += 1
        words = words[i:]
        if not words:
            return None
        prog = posixpath.basename(words[0])
        if prog == "git":
            return _git_hit(words)
        if prog in _WRAPPERS:
            words, code = _unwrap(prog, words[1:])
            if code is not None:
                hit = _check_text(code, depth + 1)
                if hit:
                    return hit
            continue
        if prog in _SHELLS:
            code = _shell_code(words[1:])
            if code is not None:
                return _check_text(code, depth + 1)
            for body in stdin:  # `bash <<EOF` / `bash <<< '…'`：正文即脚本
                hit = _check_text(body, depth + 1)
                if hit:
                    return hit
            return None
        if prog == "eval":
            return _check_text(" ".join(words[1:]), depth + 1)
        return None


def _conservative_hit(text: str) -> str | None:
    """解析失败时的保守回退：去引号 / 反斜杠后，对每个 `git` 出现处检查同一段。

    先还原两种「看着不像、shell 里等价」的写法（#3545 复核后差分验证发现回退路径认不出）：
    行续接（`\\` 换行）与 `$'…'` / `$"…"` 的 `$` 前缀（其 `\\xNN` 类转义仍不覆盖，见已知边界）。"""
    clean = text.replace("\\\n", "")
    clean = re.sub(r"\$(?=['\"])", "", clean)
    clean = re.sub(r"['\"\\]", "", clean)
    for m in re.finditer(r"(?<![\w.-])git(?![\w-])", clean):
        seg = re.split(r"[;&|\n()`]", clean[m.start():], maxsplit=1)[0]
        words = seg.split()
        if "reset" in words and any(_is_hard(w) for w in words):
            return seg.strip()
        if "stash" in words and _stash_blocked(words[words.index("stash") + 1:]):
            return seg.strip()
    return None


def _analyze(command: str) -> tuple[str | None, bool]:
    """返回 (命中文本|None, 是否走了「无法解析」保守回退)。"""
    try:
        return _check_text(command, 0), False
    except _ParseError:
        return _conservative_hit(command), True


def find_blocked(command: str) -> str | None:
    """返回第一个命中破坏性模式的简单命令文本；无则 None。纯函数（自测共用）。

    shell 感知（见模块 docstring）：按引号 / 转义 / heredoc 切成简单命令，递归进入
    `bash -c` / env / sudo / xargs / `$(…)` / 子 shell 等；解析失败走保守回退而非放行。
    """
    return _analyze(command)[0]


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

    # ── #3516 G1：shell 感知——原漏拦（应阻断）────────────────────────────
    expect("引号包裹选项", 'git reset "--hard"', True)
    expect("单引号包裹选项", "git reset '--hard'", True)
    expect("引号包裹 git", '"git" stash', True)
    expect("反斜杠 git", r"\git stash", True)
    expect("引号拆词 git", 'g""it stash', True)
    expect("-C 带空格引号路径", 'git -C "/tmp/repo path" reset --hard', True)
    expect("bash -c 双引号", 'bash -c "git reset --hard"', True)
    expect("bash -c 单引号", "bash -c 'git reset --hard'", True)
    expect("sh -c stash", 'sh -c "git stash"', True)
    expect("bash -lc 组合选项", "bash -lc 'git stash drop'", True)
    expect("bash -l -c", "bash -l -c 'git reset --hard'", True)
    expect("zsh -c", "zsh -c 'git stash'", True)
    expect("dash -ec", "dash -ec 'git stash clear'", True)
    expect("bash -c 嵌套 bash -c", """bash -c "bash -c 'git stash'" """, True)
    expect("env 包装", "env git reset --hard", True)
    expect("env VAR=v 包装", "env FOO=1 BAR=2 git stash", True)
    expect("env -i 包装", "env -i git stash", True)
    expect("env -u 带值", "env -u FOO git reset --hard", True)
    expect("env -S 命令串", "env -S 'git stash'", True)
    expect("command 包装", "command git reset --hard", True)
    expect("sudo 包装", "sudo git reset --hard", True)
    expect("sudo -u 带值", "sudo -u root git reset --hard", True)
    expect("nohup 包装", "nohup git stash &", True)
    expect("time 包装", "time git reset --hard", True)
    expect("exec 包装", "exec git stash", True)
    expect("timeout 位置参数", "timeout 5 git stash", True)
    expect("nice 选项", "nice -n 10 git reset --hard", True)
    expect("多层包装", "sudo env FOO=1 nohup git stash", True)
    expect("sudo bash -c", "sudo bash -c 'git stash'", True)
    expect("绝对路径 git", "/usr/bin/git reset --hard", True)
    expect("绝对路径 git stash", "/usr/local/bin/git stash", True)
    expect("命令替换 $()", "echo $(git reset --hard)", True)
    expect("命令替换独占", "$(git reset --hard)", True)
    expect("反引号", "echo `git stash`", True)
    expect("双引号内命令替换", 'echo "$(git stash)"', True)
    expect("双引号内反引号", 'echo "`git reset --hard`"', True)
    expect("变量赋值内命令替换", "X=$(git stash) true", True)
    expect("进程替换", "cat <(git stash)", True)
    expect("子 shell", "(git reset --hard)", True)
    expect("子 shell 内 stash", "(cd /x && git stash)", True)
    expect("花括号组", "{ git stash; }", True)
    expect("if/then", "if true; then git reset --hard; fi", True)
    expect("for/do", "for i in 1 2; do git stash; done", True)
    expect("取反", "! git stash", True)
    expect("xargs stash drop", "echo x | xargs git stash drop", True)
    expect("xargs 带选项", "printf 'a\\n' | xargs -I{} git reset --hard {}", True)
    expect("xargs -n 带值", "echo a | xargs -n 1 git stash", True)
    expect("eval", "eval 'git reset --hard'", True)
    expect("eval 多词", "eval git stash", True)
    expect("bash heredoc 脚本", "bash <<EOF\ngit reset --hard\nEOF\n", True)
    expect("sh heredoc 带引号定界符", "sh <<'EOF'\ngit stash\nEOF\n", True)
    expect("bash here-string", "bash <<< 'git stash'", True)
    expect("heredoc 正文里的命令替换", "cat <<EOF\n$(git stash)\nEOF\n", True)
    expect("重定向后命令", "git reset --hard > /dev/null 2>&1", True)
    expect("fd 重定向紧贴", "git stash 2>&1", True)
    expect("&> 重定向", "git reset --hard &> /tmp/x", True)
    expect("--hard 缩写 --har", "git reset --har", True)
    expect("换行分隔", "echo hi\ngit stash", True)
    expect("反斜杠续行不影响", "git reset \\\n  --hard", True)
    expect("ANSI-C 引号", "git reset $'--hard'", True)
    expect("续命令块 && 之后", "cd /x && ls && git stash push", True)
    # 边界：复合命令里只有一段危险 / 危险在管道后段 / 后台 / 注释之后
    expect("复合里仅一段危险", "git status && git log -1 && git reset --hard HEAD", True)
    expect("管道后段危险", "echo y | git stash", True)
    expect("管道链末段 xargs", "git ls-files | head -1 | xargs git stash drop", True)
    expect("|| 之后", "false || git stash", True)
    expect("& 之后", "sleep 1 & git reset --hard", True)
    expect("多行 heredoc 之后的命令", "cat <<EOF > /tmp/x\ndata\nEOF\ngit stash\n", True)
    expect("注释行后的下一行", "# note\ngit stash\n", True)
    # 解析失败 → 保守回退：疑似命中仍阻断（不静默放行）
    expect("引号不平衡 + reset --hard", 'git reset --hard "unterminated', True)
    expect("括号不平衡 + stash", "echo $(git stash", True)
    expect("反引号不平衡 + stash", "echo `git stash", True)
    expect("嵌套过深回退", "$(" * 40 + "git stash", True)

    # ── #3545 复核返修：全局选项封闭集 + heredoc 命令替换深度超限走保守回退 ─────────
    expect("--config-env 独立值", "git --config-env core.abbrev=STP_ENV reset --hard", True)
    expect("--config-env= 连写", "git --config-env=core.abbrev=STP_ENV reset --hard", True)
    expect("--attr-source 独立值", "git --attr-source HEAD reset --hard", True)
    expect("--config-env 后 stash 创建", "git --config-env a.b=X stash", True)
    expect("含 = 的未知带值全局选项兜底", "git --future-opt k=v reset --hard", True)
    expect("--config-env 后 stash list 放行", "git --config-env a.b=X stash list", False)
    expect("--config-env 后 status 放行", "git --config-env a.b=X status", False)
    _deep = lambda inner: "cat <<EOF\n" + "$(" * 26 + inner + ")" * 26 + "\nEOF\n"
    expect("未加引号 heredoc 26 层命令替换", _deep("git reset --hard"), True)
    expect("heredoc 深度超限 + ANSI-C 引号", _deep("git $'reset' $'--hard'"), True)
    expect("heredoc 深度超限 + 行续接", _deep("git \\\n reset \\\n --hard"), True)
    expect("heredoc 深度超限 + stash", _deep("git stash"), True)
    expect("嵌套过深回退 + ANSI-C 引号", "$(" * 40 + "git $'stash' $'drop'", True)
    expect("带引号 heredoc 26 层是数据", _deep("git reset --hard").replace("<<EOF", "<<'EOF'"), False)
    expect("未加引号 heredoc 深度超限但无危险信号", _deep("echo hi"), False)

    # ── #3516 G1：shell 感知——原误拦（应放行）────────────────────────────
    expect("commit -m 内分号+禁令词", 'git commit -m "docs; git stash is forbidden"', False)
    expect("commit -m 单引号内 reset", "git commit -m 'never run; git reset --hard'", False)
    expect("grep -E 内管道符", "grep -E 'a|git stash|b' file", False)
    expect("grep 双引号内管道符", 'grep -E "x|git reset --hard|y" file', False)
    expect("heredoc 数据含禁令词", "cat <<EOF\ngit stash\ngit reset --hard\nEOF\n", False)
    expect("heredoc 带引号定界符数据", "cat <<'EOF' > notes.md\ngit stash push\nEOF\n", False)
    expect("heredoc <<- 缩进定界符", "cat <<-EOF\n\tgit stash\n\tEOF\n", False)
    expect("heredoc 后正常命令", "cat <<EOF\ngit stash\nEOF\ngit status\n", False)
    expect("log --grep 内分号", 'git log --grep "x; git stash push"', False)
    expect("echo 含分号禁令词", "echo 'done; git stash'", False)
    expect("commit 内 $(cat <<EOF) 含撇号与禁令词",
           "git commit -m \"$(cat <<'EOF'\nfix: don't use git stash; use branches\n"
           "git reset --hard is banned\nEOF\n)\"", False)
    expect("bash -c 内为良性命令", "bash -c 'git status && git log -1'", False)
    expect("bash -c 内读侧 stash", "bash -c 'git stash list'", False)
    expect("env 读侧", "env FOO=1 git stash list", False)
    expect("sudo 读侧", "sudo git status", False)
    expect("xargs 良性", "git ls-files | xargs grep -n 'git stash'", False)
    expect("绝对路径 git 良性", "/usr/bin/git status", False)
    expect("命令替换内良性", "echo $(git rev-parse HEAD)", False)
    expect("子 shell 良性", "(cd /x && git status)", False)
    expect("注释里的禁令词", "git status # git stash later", False)
    expect("注释行", "# git reset --hard\nls\n", False)
    expect("bash 脚本文件不递归", "bash scripts/run.sh --hard", False)
    expect("bash heredoc 良性", "bash <<EOF\ngit status\nEOF\n", False)
    expect("cat 写文件 heredoc", "cat > /tmp/a.sh <<'EOF'\ngit stash\nEOF\n", False)
    expect("git grep 检索禁令词", "git grep -n 'git stash' -- docs", False)
    expect("git commit -F - heredoc", "git commit -F - <<EOF\nnote: git stash banned\nEOF\n", False)
    expect("重定向文件名含禁令词", "echo x > 'git stash.txt'", False)
    expect("reset --hard 出现在 echo 参数", "echo git reset --hard", False)
    expect("--hard 前缀太短不误判", "git reset --h", False)
    # 解析失败但无危险信号 → 放行（保守回退无命中）
    expect("引号不平衡无危险", "echo 'unterminated", False)
    expect("括号不平衡无危险", "echo $(ls", False)
    expect("不平衡 + 读侧 stash", 'git stash list "unterminated', False)
    expect("空命令", "", False)
    expect("纯空白", "   \n\t ", False)

    # 解析失败路径必须标记「无法解析」
    hit, unparsable = _analyze('git reset --hard "x')
    if not (hit and unparsable):
        failures.append(f"无法解析标记: 预期 (命中, True)，实际 {(hit, unparsable)!r}")
    hit, unparsable = _analyze("git reset --hard")
    if not (hit and not unparsable):
        failures.append(f"正常命中标记: 预期 (命中, False)，实际 {(hit, unparsable)!r}")

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_destructive_git self-test 通过（阻断/放行红绿双向 + shell 感知解析边界）")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return run_self_test()
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # 非 hook JSON 调用形态，放行
    if not isinstance(data, dict) or data.get("tool_name") != "Bash":
        return 0
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str) or not command:
        return 0
    hit, unparsable = _analyze(command)
    if hit:
        reason = (
            "无法解析该命令（引号/括号不平衡或嵌套过深），按保守规则疑似命中"
            if unparsable else "破坏性 git 操作被纪律拦截"
        )
        print(
            f"[BLOCKED] {reason}：{hit!r}\n"
            "未提交工作请显式 commit/分支保存，不要 stash/reset --hard；\n"
            "禁令、风险分级与放行清单见 repository-workflow.md §Git 破坏性操作纪律。",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
