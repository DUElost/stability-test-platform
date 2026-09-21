#!/usr/bin/env python3
"""ADR-0033 D0 门禁：禁止在 ``backend/agent/scripts/`` 新增顶层工具族目录。

背景：ADR-0033 §5.2 裁定 D0「外部工具源码不入主仓」**即刻生效**，不等包存储；
D0 分级准入只拦**新工具族**（既有族新版本仍允许 legacy，见 ADR-0033 §5 / D0）。
Epic #745 要求的 in-tree ingestion breaker 此前未落地——本门禁补上机械拦截。

规则（相对 ``--base``，默认 ``origin/main``）：
- 基线中不存在、HEAD 新增的 ``backend/agent/scripts/<family>/`` 顶层目录 → 红；
- 既有族下新增 ``v*`` 版本目录 → 绿（仍受 ADR-0020 不可变门禁约束）；
- 脚本根下的普通文件（如 README）→ 忽略。

包存储 / ``tool_manifest.yaml`` 仍按 §5.4 **未触发不排期**；因此新族的合法出口
不是「先塞 manifest 再入仓」，而是等触发条件成立后走 Contract + 包存储，或走显式
ADR 例外登记。本门禁只保证「不得默默新开 in-tree 族」。

用法::

    python tools/dev/check_new_script_family.py --base origin/main
    python tools/dev/check_new_script_family.py --self-test
"""

from __future__ import annotations

import argparse
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

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_new_script_family self-test 红绿双向")
    return 0


def _report(new_families: list[str], base: str, quiet: bool) -> int:
    if not new_families:
        if not quiet:
            print(
                f"OK: 相对 {base} 无新增 {SCRIPT_ROOT}/<family>/ "
                "（ADR-0033 D0 新族门禁）"
            )
        return 0

    print(
        f"[FAIL] ADR-0033 D0：禁止新增 in-tree 工具族（相对 {base}）：",
        file=sys.stderr,
    )
    for name in new_families:
        print(f"  + {SCRIPT_ROOT}/{name}/", file=sys.stderr)
    print(
        "\n新工具族必须等包存储 §5.4 触发后以 Tool Contract + 包形态登记；"
        "\n既有族可继续新增 v* 版本目录（legacy）。"
        "\n不得把外部工具全量源码拷进主仓冒充新族。"
        "\n若确需例外：先走 ADR 例外登记，不要用本门禁豁免列表绕过。",
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
    return _report(find_new_families(base_families, head_families), args.base, args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
