#!/usr/bin/env python3
"""收敛 IDE/插件造成的"逐行空行注入"污染。

背景:某些编辑器插件会在保存时于每两行之间插入一个空行。一次污染后,
文件整体空行率会飙到 ~50%,此后每次改动都在延续它 —— diff 虚胖一倍,
review 成本翻倍。`.githooks/pre-commit` 拦的是"单次提交新增行的空行占比",
对已经稳定在 ~50% 的文件无效,所以需要一次性清理。

**先判定、再清理**:单看一处,"函数体内一个空行"既可能是注入产物,也可能是
作者刻意的段落分隔 —— 局部无法区分。所以先用**文件整体空行率**判定是否被
污染(正常代码 8~15%,逐行注入后 ~50%),只有超阈的文件才做下面的收敛;
未超阈的文件一行不动。阈值与 `.githooks/pre-commit` 的 THRESHOLD_FILE_RATIO 一致。

规则(仅动空行,绝不动任何有内容的行):
  - 函数/类体内(下一行有缩进):
      1 个连续空行  → 删除(注入产物)
      ≥2 个连续空行 → 收敛为 1(原本的段落分隔被翻倍了)
  - 顶层之间(下一行顶格):>2 个空行收敛为 2,否则原样不动
  - 多行字符串(docstring / 长文本)内部的空行原样保留

**只收不放**:任何情况下都不会新增空行 —— 本工具是污染清理器,
不是 formatter,不该对本来干净的文件提出 PEP8 风格意见。

安全保证:改写前后 `ast.dump()` 必须完全一致,否则中止并不落盘。

用法:
    python tools/dev/collapse-blank-pollution.py <file.py> [more.py ...]
    python tools/dev/collapse-blank-pollution.py --check <file.py>   # 只报告
"""
from __future__ import annotations

import argparse
import ast
import io
import sys
import token as token_mod
import tokenize
from pathlib import Path


def _protected_lines(src: str) -> set[int]:
    """多行字符串内部的物理行号(1-based),这些行的空行不能动。"""
    protected: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok in tokens:
            if tok.type not in (token_mod.STRING, getattr(token_mod, "FSTRING_START", -1)):
                continue
            start_line, end_line = tok.start[0], tok.end[0]
            if end_line > start_line:
                # 起始行本身含内容,只保护其后的续行
                protected.update(range(start_line + 1, end_line + 1))
    except tokenize.TokenError:
        # 语法不完整时保守处理:全保护 = 不改写
        return set(range(1, len(src.splitlines()) + 2))
    return protected


def collapse(src: str) -> str:
    lines = src.splitlines()
    protected = _protected_lines(src)
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if line.strip() != "" or (i + 1) in protected:
            out.append(line)
            i += 1
            continue

        # 收集一段连续空行
        run_start = i
        while i < n and lines[i].strip() == "" and (i + 1) not in protected:
            i += 1
        run_len = i - run_start

        nxt = lines[i] if i < n else None
        if nxt is None:
            # 文件尾部的空行:全部丢弃,由末尾换行收口
            continue

        at_top_level = not nxt.startswith((" ", "\t"))
        if at_top_level:
            # 只封顶,不补齐:干净文件里 1 个空行是作者的选择,不该被改成 2
            keep = 0 if not out else min(run_len, 2)
        else:
            keep = 0 if run_len == 1 else 1
        out.extend([""] * keep)

    return "\n".join(out) + "\n"


# 与 .githooks/pre-commit 的 THRESHOLD_FILE_RATIO 保持一致
POLLUTION_RATIO = 35
MIN_LINES = 200


def blank_ratio(src: str) -> tuple[int, int, int]:
    lines = src.splitlines()
    blanks = sum(1 for ln in lines if not ln.strip())
    total = len(lines)
    return blanks, total, (blanks * 100 // total if total else 0)


def process(path: Path, *, check_only: bool, force: bool = False) -> tuple[bool, str]:
    src = path.read_text(encoding="utf-8")
    if not src.strip():
        return False, "空文件"

    blanks, total, ratio = blank_ratio(src)
    if not force:
        if total < MIN_LINES:
            return False, f"跳过(仅 {total} 行,短文件空行率波动大)"
        if ratio < POLLUTION_RATIO:
            return False, f"未超阈({ratio}% < {POLLUTION_RATIO}%),判定为正常文件"

    try:
        before = ast.dump(ast.parse(src))
    except SyntaxError as exc:
        return False, f"跳过(语法错误): {exc}"

    new = collapse(src)
    if new == src:
        return False, "无需改动"

    try:
        after = ast.dump(ast.parse(new))
    except SyntaxError as exc:
        return False, f"中止 —— 改写结果无法解析: {exc}"
    if before != after:
        return False, "中止 —— AST 发生变化,改写不安全"

    old_n, new_n = len(src.splitlines()), len(new.splitlines())
    msg = f"空行率 {ratio}% → {old_n} → {new_n} 行(-{old_n - new_n})"
    if not check_only:
        path.write_text(new, encoding="utf-8")
    return True, msg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--check", action="store_true", help="只报告,不写回")
    ap.add_argument(
        "--force", action="store_true",
        help="跳过空行率判定,强制收敛(慎用:会吃掉正常文件里刻意的段落分隔)",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="只打印被判定为污染的文件")
    args = ap.parse_args()

    changed = 0
    for p in args.paths:
        ok, msg = process(p, check_only=args.check, force=args.force)
        if ok or not args.quiet:
            print(f"{'[CHANGE]' if ok else '[  skip]'} {p}: {msg}")
        changed += ok
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
