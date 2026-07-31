#!/usr/bin/env python3
"""ADR-0020 门禁:已发布的脚本版本目录内容不可变。

背景(2026-07-31 生产事故):`script` 表的 `content_sha256` 是**扫描那一刻**
磁盘内容的快照。ADR-0020 规定,某个版本的内容变了就必须新建版本 ——
`scan_script_root` 遇到 sha 不一致只记 `conflicts`、**不动 DB**,所以 DB 里
的期望值会被永久冻结在首次扫描时刻。

而 `ef8808e`(ruff --fix 全仓清理 262 处未使用导入)直接原地改写了
`backend/agent/scripts/<name>/v<ver>/` 下的文件。没人拦,也没人发现:
27 行 script 中 18 行的 DB sha 就此与磁盘永久对不上,平台上仅有的两个 Plan
全部在准入阶段 `script_verify_failed`,派发彻底中断。自愈推送也修不好 ——
它把磁盘内容推给 Agent,而 Agent 上本来就是同样的内容,对不上的是 DB。

所以这条门禁拦的不是风格问题,是**会静默打断生产派发的二值违约**:
版本目录一旦提交,里面的文件就只能新增版本,不能原地改。

同样拦 `_` 开头的辅助模块(如 `_adb.py`):扫描器不把它们算进 entry sha,
因此改它们**连 conflicts 都不会报**,是比改入口文件更隐蔽的漂移。

用法:
    # 对比默认基线(origin/main)
    python tools/dev/check-script-version-immutability.py

    # CI 里显式指定 PR base
    python tools/dev/check-script-version-immutability.py --base origin/main

违约后的正确做法**不是**加豁免,而是新建版本目录:
    cp -r backend/agent/scripts/foo/v1.0.0 backend/agent/scripts/foo/v1.1.0
    # 改 v1.1.0,保持 v1.0.0 原样
    # 然后 POST /api/v1/scripts/scan 让新版本入库,并把 PlanStep 指向它
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

SCRIPT_ROOT = "backend/agent/scripts"

# backend/agent/scripts/<name>/v<version>/<相对路径>
_VERSIONED = re.compile(rf"^{re.escape(SCRIPT_ROOT)}/(?P<name>[^/]+)/(?P<version>v[^/]+)/(?P<rest>.+)$")

# 只有「新增」是安全的。修改/删除/改名都会让既有版本的字节变化。
_MUTATING_STATUS = {"M": "修改", "D": "删除", "R": "改名", "T": "类型变更"}


def _git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败:\n{proc.stderr.strip()}")
    return proc.stdout


def _changed_paths(base: str) -> list[tuple[str, str]]:
    """返回 [(status, path)]。三点 diff = 与 merge-base 比,只看本分支引入的改动。"""
    raw = _git("diff", "--name-status", "-M", f"{base}...HEAD", "--", SCRIPT_ROOT)
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0][:1]
        # 改名是 "R100\told\tnew" —— 旧路径消失了,按旧路径记违约。
        path = parts[1]
        out.append((status, path))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="比较基线 ref(默认 origin/main);CI 里传 PR 的 base",
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="通过时不输出")
    args = parser.parse_args()

    violations: list[tuple[str, str, str, str]] = []
    for status, path in _changed_paths(args.base):
        if status not in _MUTATING_STATUS:
            continue
        m = _VERSIONED.match(path)
        if not m:
            continue
        violations.append((path, _MUTATING_STATUS[status], m["name"], m["version"]))

    if not violations:
        if not args.quiet:
            print(f"OK:{SCRIPT_ROOT} 下没有已发布版本目录被原地改动(基线 {args.base})")
        return 0

    print(f"违反 ADR-0020「版本内容不可变」:{len(violations)} 个文件", file=sys.stderr)
    print("", file=sys.stderr)
    affected: dict[tuple[str, str], list[str]] = {}
    for path, action, name, version in violations:
        print(f"  [{action}] {path}", file=sys.stderr)
        affected.setdefault((name, version), []).append(path)
    print("", file=sys.stderr)
    print(
        "已发布的版本目录一旦入库,其 sha256 就是控制面 precheck 的期望值;"
        "\n原地修改会让 DB 期望值与磁盘永久对不上,导致引用该脚本的 Plan 在"
        "\n准入阶段 script_verify_failed —— 且 scan 不会自愈(设计如此)。"
        "\n"
        "\n正确做法:新建版本目录,保持旧版本字节不变。受影响的版本:",
        file=sys.stderr,
    )
    for name, version in sorted(affected):
        old = f"{SCRIPT_ROOT}/{name}/{version}"
        print(f"    cp -r {old} {SCRIPT_ROOT}/{name}/<新版本号>", file=sys.stderr)
    print(
        "改新目录,并把 PlanStep.script_version 指向新版本,再 POST /api/v1/scripts/scan。"
        "\n"
        "\n若这是 ruff/formatter 一类的全仓机械改写:请把改动从"
        f"\n{SCRIPT_ROOT} 撤回 —— ruff.toml 已将该目录加入 extend-exclude。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
