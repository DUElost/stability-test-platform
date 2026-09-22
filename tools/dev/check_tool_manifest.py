#!/usr/bin/env python3
"""ADR-0033 Phase B（#3075）门禁：``tool_manifest.json`` schema lint + append-only。

规则（对齐 #3075「实现前裁决」）：

**lint（C2/C5 字段面）**
- 顶层仅 ``schema_version``(=1) 与 ``tools``；
- ``tools`` 为 族名→{versions:[...]} 映射；族名/版本号限 ``[A-Za-z0-9][A-Za-z0-9._-]*``；
- 版本条目字段集**恰好**为 {version, package_sha256, artifact, python, script, retired}——
  多一个字段（如执行契约要素）即红：分发面与契约面不得混装（C5）；
- ``package_sha256`` 64 位小写十六进制（整包 sha，区别于 ``script.content_sha256`` 的
  entry-file sha，C1）；``artifact`` 必须等于 ``packages/{name}/{version}.tar.gz``（C4 布局）；
- ``python``/``script`` 为包内相对路径（无绝对/``..``/空段）；``retired`` 必须是 bool；
- 族名与版本条目不得重复。

**append-only（相对 --base）**
- 条目删除 → 红（退役 = ``retired: false→true`` 的单向翻转，先例 ``script.is_active``＋ADR-0039）；
- 除 ``retired`` 外任何字段改动 → 红（sha 变了必须新版本号）；
- ``retired`` true→false 翻转 → 红（退役不可逆，恢复=新条目新版本）；
- 新条目只能出现在该族 ``versions`` 列表**末尾**（登记史可读）。

读不到 base 版文件时按空文档处理（首次登记合法）；读不到 head 版文件（本仓路径）即红——
唯一事实源不许消失。

用法::

    python tools/dev/check_tool_manifest.py --base origin/main
    python tools/dev/check_tool_manifest.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

MANIFEST_PATH = "tool_manifest.json"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VERSION_RE = NAME_RE
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ENTRY_FIELDS = frozenset({"version", "package_sha256", "artifact", "python", "script", "retired"})


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败:\n{proc.stderr.strip()}")
    return proc.stdout


def validate_relative_member(p: str, *, field: str) -> str | None:
    """包内相对路径合法性——与 package_tool_asset 同判据（此处独立实现避免跨工具 import 脆链）。"""
    if not isinstance(p, str) or not p or p.startswith("/"):
        return f"{field} 必须是包内相对路径（非空、非绝对）：{p!r}"
    if any(seg in ("", "..") for seg in p.split("/")):
        return f"{field} 不得含 '..' 或空段：{p!r}"
    return None


def lint_manifest(doc: object) -> list[str]:
    """纯函数：schema lint，返回错误列表（空 = 绿）。"""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["顶层必须是对象"]
    if doc.get("schema_version") != 1:
        errs.append(f"schema_version 必须为 1，实际 {doc.get('schema_version')!r}")
    extra_top = set(doc) - {"schema_version", "tools"}
    if extra_top:
        errs.append(f"顶层多余字段：{sorted(extra_top)}")
    tools = doc.get("tools")
    if not isinstance(tools, dict):
        errs.append("tools 必须是对象")
        return errs
    for name, tool in tools.items():
        if not NAME_RE.match(str(name)):
            errs.append(f"非法族名 {name!r}（限 {NAME_RE.pattern}）")
            continue
        if not isinstance(tool, dict) or set(tool) != {"versions"} or not isinstance(tool["versions"], list):
            errs.append(f"{name}: 必须且只能含 versions 数组")
            continue
        seen: set[str] = set()
        for i, entry in enumerate(tool["versions"]):
            tag = f"{name}[{i}]"
            if not isinstance(entry, dict):
                errs.append(f"{tag}: 条目必须是对象")
                continue
            fields = set(entry)
            if fields - ENTRY_FIELDS:
                errs.append(f"{tag}: 多余字段 {sorted(fields - ENTRY_FIELDS)}——分发面与契约面不得混装（C5）")
            if ENTRY_FIELDS - fields:
                errs.append(f"{tag}: 缺字段 {sorted(ENTRY_FIELDS - fields)}")
                continue
            ver = entry["version"]
            if not isinstance(ver, str) or not VERSION_RE.match(ver):
                errs.append(f"{tag}: 非法版本号 {ver!r}")
            elif ver in seen:
                errs.append(f"{tag}: 版本 {ver} 重复")
            else:
                seen.add(ver)
            if not isinstance(entry["package_sha256"], str) or not SHA_RE.match(entry["package_sha256"]):
                errs.append(f"{tag}: package_sha256 必须是 64 位小写十六进制")
            want = f"packages/{name}/{ver}.tar.gz"
            if entry["artifact"] != want:
                errs.append(f"{tag}: artifact 必须等于 {want!r}（C4 布局）")
            for f in ("python", "script"):
                err = validate_relative_member(entry[f], field=f"{tag}.{f}")
                if err:
                    errs.append(err)
            if not isinstance(entry["retired"], bool):
                errs.append(f"{tag}: retired 必须是 bool")
    return errs


def append_only_diff(base_doc: dict, head_doc: dict) -> list[str]:
    """纯函数：head 相对 base 的 append-only 违例列表。"""
    errs: list[str] = []
    base_tools = base_doc.get("tools", {})
    for name, base_tool in base_tools.items():
        head_tool = head_doc.get("tools", {}).get(name)
        if head_tool is None:
            errs.append(f"族 {name} 整体消失——删除禁止；退役请用 retired:true")
            continue
        base_versions = base_tool["versions"]
        head_versions = head_tool["versions"]
        head_list = [e.get("version") for e in head_versions]
        for i, base_entry in enumerate(base_versions):
            ver = base_entry.get("version")
            if i >= len(head_versions) or head_list[i] != ver:
                errs.append(f"{name}: 既有条目 {ver} 不在原位第 {i} 位——只能追加到末尾，不许重排/插入")
                continue
            head_entry = head_versions[i]
            for field, bval in base_entry.items():
                hval = head_entry.get(field)
                if field == "retired":
                    if bval is False and hval is True:
                        continue  # 唯一合法变更：退役单向翻转
                    if bval is True and hval is not True:
                        errs.append(f"{name}@{ver}: retired 不得 true→false（恢复=新版本条目）")
                elif bval != hval:
                    errs.append(f"{name}@{ver}: 字段 {field} 被原地改写（{bval!r}→{hval!r}）——内容变了请发新版本号")
        missing = [e.get("version") for e in base_versions if e.get("version") not in head_list]
        if missing:
            errs.append(f"{name}: 条目被删除 {missing}——删除禁止")
    return errs


def _git_env() -> dict:
    # 判据走退出码，stderr 文案不参与判定（#2957 教训：本地化 git 会换文案）。
    return {**os.environ, "LC_ALL": "C"}


def load_ref_manifest(ref: str) -> dict | None:
    """读 ``ref`` 上的 manifest；文件不存在返回 None（首登合法），JSON 坏 → SystemExit。"""
    rev = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True, check=False, env=_git_env(),
    )
    if rev.returncode != 0:
        raise SystemExit(f"基线 ref 不可解析：{ref}（CI 浅克隆需 fetch base；fail-closed）")
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{MANIFEST_PATH}"],
        capture_output=True, text=True, check=False, env=_git_env(),
    )
    if exists.returncode != 0:
        return None  # 该 ref 无此文件（首次登记合法）
    raw = subprocess.run(
        ["git", "show", f"{ref}:{MANIFEST_PATH}"], capture_output=True, text=True, check=False, env=_git_env()
    ).stdout
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{ref}:{MANIFEST_PATH} 不是合法 JSON: {exc}") from None


def run_self_test() -> int:
    failures: list[str] = []

    def entry(**over):
        base = {
            "version": "v1.0.0",
            "package_sha256": "a" * 64,
            "artifact": "packages/tool/v1.0.0.tar.gz",
            "python": "venv/bin/python",
            "script": "run.py",
            "retired": False,
        }
        base.update(over)
        return base

    good = {"schema_version": 1, "tools": {"tool": {"versions": [entry(), entry(version="v1.0.1", artifact="packages/tool/v1.0.1.tar.gz")]}}}
    if lint_manifest(good):
        failures.append(f"合法文档应绿，实际 {lint_manifest(good)}")
    if lint_manifest({"schema_version": 2, "tools": {}}) == []:
        failures.append("schema_version=2 应红")
    bad_field = json.loads(json.dumps(good))
    bad_field["tools"]["tool"]["versions"][0]["exit_codes"] = {"0": "ok"}
    if not any("多余字段" in e for e in lint_manifest(bad_field)):
        failures.append("混入契约字段应红（C5）")
    bad_sha = json.loads(json.dumps(good))
    bad_sha["tools"]["tool"]["versions"][0]["package_sha256"] = "XYZ"
    if not any("package_sha256" in e for e in lint_manifest(bad_sha)):
        failures.append("非法 sha 应红")
    bad_artifact = json.loads(json.dumps(good))
    bad_artifact["tools"]["tool"]["versions"][0]["artifact"] = "packages/tool/v9/other.tar.gz"
    if not any("artifact" in e for e in lint_manifest(bad_artifact)):
        failures.append("artifact 与布局不符应红（C4）")
    bad_traverse = json.loads(json.dumps(good))
    bad_traverse["tools"]["tool"]["versions"][0]["python"] = "../system/bin/python"
    if not any("python" in e for e in lint_manifest(bad_traverse)):
        failures.append("python 字段含 .. 应红")
    dup = json.loads(json.dumps(good))
    dup["tools"]["tool"]["versions"].append(entry())
    if not any("重复" in e for e in lint_manifest(dup)):
        failures.append("重复版本应红")

    # append-only
    if append_only_diff(good, json.loads(json.dumps(good))):
        failures.append("无变化应绿")
    removed = {"schema_version": 1, "tools": {"tool": {"versions": [entry()]}}}
    if not append_only_diff(good, removed):
        failures.append("删除条目应红")
    flipped = json.loads(json.dumps(good))
    flipped["tools"]["tool"]["versions"][1]["retired"] = True
    if append_only_diff(good, flipped):
        failures.append("retired false→true 应绿（退役）")
    resurrect = json.loads(json.dumps(flipped))
    resurrect["tools"]["tool"]["versions"][1]["retired"] = False
    if not any("true→false" in e for e in append_only_diff(flipped, resurrect)):
        failures.append("retired true→false 应红")
    reshaped = json.loads(json.dumps(good))
    reshaped["tools"]["tool"]["versions"][1]["package_sha256"] = "b" * 64
    if not append_only_diff(good, reshaped):
        failures.append("原地改 sha 应红")
    inserted = json.loads(json.dumps(good))
    inserted["tools"]["tool"]["versions"].insert(1, entry(version="v0.9.0", artifact="packages/tool/v0.9.0.tar.gz"))
    if not any("原位" in e for e in append_only_diff(good, inserted)):
        failures.append("插入到列表中部应红（只能追加）")
    appended = json.loads(json.dumps(good))
    appended["tools"]["tool"]["versions"].append(entry(version="v1.0.2", artifact="packages/tool/v1.0.2.tar.gz"))
    if append_only_diff(good, appended):
        failures.append("末尾追加应绿")
    if not any("整体消失" in e for e in append_only_diff(good, {"schema_version": 1, "tools": {}})):
        failures.append("族删除应红")

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_tool_manifest self-test 红绿双向")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main", help="比较基线 ref（默认 origin/main；CI 传 PR base）")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()

    head_doc = load_ref_manifest(args.head)
    if head_doc is None:
        # head 连文件都没有 = 唯一事实源消失（fail-closed；本 PR 起该文件恒存在）
        print(f"[FAIL] {args.head}:{MANIFEST_PATH} 不存在——工具包登记唯一事实源不许消失", file=sys.stderr)
        return 1
    errs = lint_manifest(head_doc)
    base_doc = load_ref_manifest(args.base) or {"schema_version": 1, "tools": {}}
    errs = errs + append_only_diff(base_doc, head_doc)
    if errs:
        print(f"[FAIL] ADR-0033 tool_manifest 门禁（{MANIFEST_PATH}）：", file=sys.stderr)
        for e in errs:
            print(f"  ✗ {e}", file=sys.stderr)
        return 1
    if not args.quiet:
        n = sum(len(t["versions"]) for t in head_doc["tools"].values())
        print(f"OK: tool_manifest 绿（{len(head_doc['tools'])} 族 / {n} 版本条目；相对 {args.base} append-only）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
