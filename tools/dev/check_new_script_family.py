#!/usr/bin/env python3
"""ADR-0033 D0 门禁：禁止在 ``backend/agent/scripts/`` 新增顶层工具族目录。

背景：ADR-0033 §5.2 裁定 D0「外部工具源码不入主仓」**即刻生效**，不等包存储；
D0 分级准入只拦**新工具族**（既有族新版本仍允许 legacy，见 ADR-0033 §5 / D0）。
Epic #745 要求的 in-tree ingestion breaker 此前未落地——本门禁补上机械拦截。

规则（相对 ``--base``，默认 ``origin/main``）：
- 基线中不存在、HEAD 新增的 ``backend/agent/scripts/<family>/`` 顶层目录 → **要求归类声明**；
- 既有族下新增 ``v*`` 版本目录 → 绿（仍受 ADR-0020 不可变门禁约束）；
- 脚本根下的普通文件（如 README）→ 忽略。

归类声明（#3014 案 3A-1，选项 A）：diff 或提交说明内出现一行

    ADR-0033 归类：<family> = platform-authored   # 纯 adb/python，无厂商二进制/第三方源码/大体积资产
    ADR-0033 归类：<family> = external-tool       # 外部工具族

三态判据：

- 全部新族 = ``platform-authored`` → **绿**（平台自研能力不是 D0 对象，见 ADR-0033 §5.6）；
- 有族声明为 ``external-tool`` → **红**：外部工具在 §5.4 触发前无合法 in-tree 出口，
  须走中心存储 + env 路径并显式登记 legacy 例外；
- 有族**未声明** → **红**，并给出声明写法。**读不到 diff 时同样按未声明处理（fail-closed）**。

刻意**不建豁免清单**：豁免清单会变成第二个事实源；声明随 PR 一起被审。

包存储 / ``tool_manifest.yaml`` 仍按 §5.4 **未触发不排期**；因此新族的合法出口
不是「先塞 manifest 再入仓」，而是等触发条件成立后走 Contract + 包存储，或走显式
ADR 例外登记。本门禁只保证「不得默默新开 in-tree 族」。

用法::

    python tools/dev/check_new_script_family.py --base origin/main
    python tools/dev/check_new_script_family.py --self-test
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

SCRIPT_ROOT = "backend/agent/scripts"


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败:\n{proc.stderr.strip()}")
    return proc.stdout


def list_top_level_families(ref: str) -> set[str]:
    """``ref`` 上 ``SCRIPT_ROOT`` 下的顶层目录名（工具族）。"""
    raw = _git("ls-tree", "-d", "--name-only", f"{ref}:{SCRIPT_ROOT}")
    out: set[str] = set()
    for line in raw.splitlines():
        name = line.strip()
        if not name or name.startswith("."):
            continue
        # ls-tree 对 ``ref:path`` 返回相对 path 的 basename
        out.add(name.split("/")[-1])
    return out


def find_new_families(base_families: set[str], head_families: set[str]) -> list[str]:
    """纯函数：HEAD 相对 base 新增的族名（排序稳定）。"""
    return sorted(head_families - base_families)


#: 归类声明行——同一族后写的覆盖先写的（便于评审中改判）。
_DECLARATION_RE = re.compile(
    r"ADR-0033\s*归类\s*[：:]\s*([A-Za-z0-9_]+)\s*=\s*(platform-authored|external-tool)"
)


def parse_declarations(text: str) -> dict[str, str]:
    """纯函数：从 diff / 提交说明文本抽 ``<family> -> kind`` 声明表。"""
    return {m.group(1): m.group(2) for m in _DECLARATION_RE.finditer(text)}


def classify_new_families(
    new_families: list[str], declarations: dict[str, str]
) -> tuple[list[str], list[str], list[str]]:
    """纯函数：按声明把新族分为 (放行=platform-authored, 未声明, external-tool)。"""
    ok: list[str] = []
    undeclared: list[str] = []
    external: list[str] = []
    for name in new_families:
        kind = declarations.get(name)
        if kind == "platform-authored":
            ok.append(name)
        elif kind == "external-tool":
            external.append(name)
        else:
            undeclared.append(name)
    return ok, undeclared, external


def collect_declaration_text(base: str, head: str) -> tuple[str, str | None]:
    """返回 (可扫描文本, 错误说明)。取不到 diff **且** 取不到提交说明时不报错而是降级。

    两路都取不到 → 返回空文本，让未声明族自然走 fail-closed 的红；错误原因回传给调用方打印。
    """
    chunks: list[str] = []
    errors: list[str] = []
    for spec in (f"{base}...{head}", f"{base}..{head}"):  # 先 merge-base，退化 then 两端
        try:
            chunks.append(_git("diff", "--no-color", "--unified=0", spec))
            break
        except SystemExit as exc:  # 浅克隆 / 无共同祖先
            errors.append(f"diff {spec}: {exc}")
    try:
        chunks.append(_git("log", f"{base}..{head}", "--format=%B"))
    except SystemExit as exc:
        errors.append(f"log: {exc}")
    return "\n".join(chunks), ("; ".join(errors) or None)


def run_self_test() -> int:
    failures: list[str] = []

    got = find_new_families({"check_device", "noop"}, {"check_device", "noop"})
    if got:
        failures.append(f"无新增预期 []，实际 {got!r}")

    got = find_new_families({"check_device"}, {"check_device", "brand_new_tool"})
    if got != ["brand_new_tool"]:
        failures.append(f"单新增预期 ['brand_new_tool']，实际 {got!r}")

    got = find_new_families(set(), {"a", "b"})
    if got != ["a", "b"]:
        failures.append(f"全新增预期 ['a','b']，实际 {got!r}")

    # 既有族保留、仅版本目录变化时顶层集合不变 → 绿
    got = find_new_families({"gpu_check"}, {"gpu_check"})
    if got:
        failures.append(f"既有族不变预期 []，实际 {got!r}")

    # 归类声明解析（#3014 案 3A-1）
    got = parse_declarations(
        "+ADR-0033 归类：brand_new_tool = platform-authored\n"
        "+ADR-0033 归类：brand_new_tool = external-tool\n"  # 后写覆盖
    )
    if got != {"brand_new_tool": "external-tool"}:
        failures.append(f"声明解析（后写覆盖）预期 external-tool，实际 {got!r}")
    if parse_declarations("ADR-0033 归类: = bad\n"):
        failures.append("空族名不应被解析成声明")
    ok, undeclared, external = classify_new_families(
        ["a", "b", "c"], {"a": "platform-authored", "c": "external-tool"}
    )
    if (ok, undeclared, external) != (["a"], ["b"], ["c"]):
        failures.append(f"三态划分预期 (['a'],['b'],['c'])，实际 {(ok, undeclared, external)!r}")
    if classify_new_families([], {}) != ([], [], []):
        failures.append("无新族预期三空列表")

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_new_script_family self-test 红绿双向")
    return 0


def _report(
    new_families: list[str],
    declarations: dict[str, str],
    base: str,
    quiet: bool,
    scan_error: str | None = None,
) -> int:
    if not new_families:
        if not quiet:
            print(
                f"OK: 相对 {base} 无新增 {SCRIPT_ROOT}/<family>/ "
                "（ADR-0033 D0 新族门禁）"
            )
        return 0

    ok, undeclared, external = classify_new_families(new_families, declarations)
    if ok and not quiet:
        print("OK: 已声明为平台自研能力（非 D0 对象，见 ADR-0033 §5.6）：" + ", ".join(ok))
    if not undeclared and not external:
        return 0

    print(
        f"[FAIL] ADR-0033 D0 新族门禁：相对 {base} 新增顶层族未通过归类"
        "（三态：platform-authored 放行 / external-tool 禁止 in-tree / 未声明 禁止）：",
        file=sys.stderr,
    )
    for name in undeclared:
        print(f"  ? {SCRIPT_ROOT}/{name}/ 未声明归类", file=sys.stderr)
    for name in external:
        print(f"  ✗ {SCRIPT_ROOT}/{name}/ 已声明 external-tool", file=sys.stderr)
    if scan_error and undeclared:
        print(
            f"  （声明扫描降级：{scan_error}——按未声明处理，fail-closed）",
            file=sys.stderr,
        )
    print(
        "\n声明写法（diff 或提交说明内任一行）："
        "\n    ADR-0033 归类：<family> = platform-authored"
        "\n    ADR-0033 归类：<family> = external-tool"
        "\n判据：纯 adb/python、无厂商二进制 / 第三方源码树 / 需独立分发的大体积资产"
        " → platform-authored 放行；否则属外部工具，在 §5.4 触发前**无合法 in-tree 出口**，"
        "请走中心存储 + env 路径并显式登记 legacy 例外。"
        "\n不要指望本门禁的豁免清单——它刻意没有（避免第二个事实源）。",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="比较基线 ref（默认 origin/main）；CI 传 PR base",
    )
    parser.add_argument(
        "--head",
        default="HEAD",
        help="比较目标 ref（默认 HEAD）",
    )
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    base_families = list_top_level_families(args.base)
    head_families = list_top_level_families(args.head)
    new_families = find_new_families(base_families, head_families)
    if not new_families:
        return _report(new_families, {}, args.base, args.quiet)
    text, scan_error = collect_declaration_text(args.base, args.head)
    return _report(
        new_families,
        parse_declarations(text),
        args.base,
        args.quiet,
        scan_error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
