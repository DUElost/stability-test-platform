#!/usr/bin/env python3
"""ADR-0051 Phase 3 等价证明（只读）：``script`` 表 ⇄ 站点包源 ⇄ ``tool_manifest.json`` ⇄ 族源码树。

Phase 3 起每族只留一棵源码树（无 ``v<version>/`` 子目录），历史与活跃版本的不可变发布单元
是站点 ``<packages_root>/<name>/<version>.tar.gz``。本工具对三面做只读等价核验：

1. **库行 ⇄ 清单 ⇄ 站点包**（每条 ``script`` 行）：
   - manifest（``kind == "script"``）有对应条目且 ``package_sha256`` 合法；
   - 站点 ``<name>/<version>.tar.gz`` 存在、整包 sha == manifest 登记值；
   - 解包所得入口名/入口 sha/伴随文件 sha/capabilities 与数据库行一致，且 ``nfs_path`` basename
     == 入口文件名、行上 ``package_sha256`` 为空或等于登记值。
   - 已退役且不在 script manifest 中的历史行，或 manifest 已标 ``retired: true`` 且无站点包的
     历史行（ADR-0051 D5）仅作归档统计，不判红；仍在线的非豁免未登记活跃行判红。
2. **清单头版本 ⇄ 站点包**：每族最新未退役条目在 ``<packages_root>`` 须已 ``--publish`` 且 sha 一致。
3. **源码树 ⇄ 清单**（除非 ``--skip-tree``）：复用 ``tools/dev/check_script_packages.py`` 校验各族源码树
   重建 sha 与 manifest 最新未退役条目一致。

退出码：0 全等价；1 有不等价；2 无法判定（缺库配置 / 清单 / 站点包根 / 源码树）。

用法（控制面主机或开发机，只读；连接串经环境变量或 ``.env.backend`` 传入，不进 argv）::

    python -m backend.scripts.check_script_package_equivalence [--json] [--packages-root DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

# 支持路径形态直接执行（`python backend/scripts/check_script_package_equivalence.py`），
# 避免 sys.path 缺仓库根导致 ImportError（exit 1 伪装成等价失败）。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, inspect as sa_inspect, text

from backend.agent.contracts.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.core.database import normalize_sync_database_url
from backend.core.env_source import resolve_database_url
from backend.services.script_catalog import (
    _is_package_sha,
    default_packages_root,
    load_manifest,
    read_package_facts,
    script_entries,
)

NON_FAILING_STATUSES = frozenset({
    "ok",
    "retired_untracked",
    "retired_without_package",
    "legacy_exempt",
})


def _load_checker():
    """Load ``tools/dev/check_script_packages.py`` for source-tree and head checks."""
    spec = importlib.util.spec_from_file_location(
        "check_script_packages", REPO_ROOT / "tools" / "dev" / "check_script_packages.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _coerce_dict(val: Any) -> dict[str, Any]:
    if isinstance(val, str):
        val = json.loads(val) if val.strip() else {}
    return dict(val or {})


def _coerce_list(val: Any) -> list[Any]:
    if isinstance(val, str):
        val = json.loads(val) if val.strip() else []
    return list(val or [])


def _fetch_rows(database_url: str) -> tuple[list[dict[str, Any]], bool]:
    engine = create_engine(normalize_sync_database_url(database_url), future=True)
    with engine.connect() as conn:
        col_names = {c["name"] for c in sa_inspect(conn).get_columns("script")}
        has_pkg_col = "package_sha256" in col_names
        cols = "name, version, nfs_path, content_sha256, support_files_manifest, capabilities, is_active"
        if has_pkg_col:
            cols += ", package_sha256"
        rows = [
            dict(r._mapping)
            for r in conn.execute(text(f"select {cols} from script order by name, version"))
        ]
    return rows, has_pkg_col


def _evaluate_row(
    row: dict[str, Any],
    manifest_idx: dict[tuple[str, str], dict[str, Any]],
    packages_root: Path,
    has_pkg_col: bool,
) -> dict[str, Any]:
    """Compare one DB ``script`` row against ``tool_manifest.json`` and its site tarball."""
    name = str(row["name"])
    version = str(row["version"])
    is_active = bool(row.get("is_active"))
    rec: dict[str, Any] = {"name": name, "version": version, "is_active": is_active}

    me = manifest_idx.get((name, version))
    if me is None:
        if name in LEGACY_AEE_SCRIPT_NAMES:
            rec.update(status="legacy_exempt", detail="历史 AEE 工具名豁免")
        elif not is_active:
            rec.update(status="retired_untracked", detail="非 script manifest 历史退役行（不判红）")
        else:
            rec.update(status="unregistered_active", detail="活跃行不在 script manifest 中")
        return rec

    sha = str(me.get("package_sha256") or "")
    if not _is_package_sha(sha):
        rec.update(status="manifest_package_sha_missing", detail="manifest 缺少合法 package_sha256")
        return rec

    tarball = packages_root / name / f"{version}.tar.gz"
    facts, err = read_package_facts(tarball, sha, str(me.get("script") or ""))
    if facts is None:
        if err == "package_missing" and not is_active and bool(me.get("retired")):
            rec.update(status="retired_without_package", detail="manifest 已退役且站点包已清理（D5）")
        else:
            rec.update(status=err or "package_error", detail=f"站点包校验失败：{err} ({tarball.name})")
        return rec

    if os.path.basename(row.get("nfs_path") or "") != facts.entry_name:
        rec.update(status="entry_name_mismatch", detail=f"nfs_path basename ≠ 包入口 {facts.entry_name!r}")
        return rec
    if row.get("content_sha256") != facts.content_sha256:
        rec.update(status="entry_sha_mismatch", detail="content_sha256 ≠ 包内入口文件 sha")
        return rec
    if _coerce_dict(row.get("support_files_manifest")) != facts.support_files_manifest:
        rec.update(status="support_mismatch", detail="support_files_manifest ≠ 包内伴随文件")
        return rec
    if _coerce_list(row.get("capabilities")) != facts.capabilities:
        rec.update(status="capabilities_mismatch", detail="capabilities ≠ 包内 capabilities.json")
        return rec
    if has_pkg_col and row.get("package_sha256") not in (None, sha):
        rec.update(status="db_package_sha_mismatch", detail="script.package_sha256 ≠ manifest 登记")
        return rec

    rec.update(
        status="ok",
        detail="package_sha256 已回填" if (has_pkg_col and row.get("package_sha256")) else "package_sha256 待 scan 回填",
    )
    return rec


def evaluate(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    packages_root: Path,
    *,
    has_pkg_col: bool = True,
) -> list[dict[str, Any]]:
    """纯函数：逐行核验 ``script`` 行 ⇄ ``tool_manifest.json`` ⇄ 站点包源。"""
    manifest_idx = {(name, str(entry["version"])): entry for name, entry in script_entries(manifest)}
    return [
        _evaluate_row(row, manifest_idx, packages_root, has_pkg_col)
        for row in rows
    ]


def evaluate_manifest_heads(
    manifest: dict[str, Any],
    packages_root: Path,
    checker,
) -> list[dict[str, str]]:
    """Check that each family's latest non-retired entry is published with matching sha."""
    problems: list[dict[str, str]] = []
    for name, versions in sorted(checker.script_families(manifest).items()):
        latest = checker.latest_entry(versions)
        if latest is None:
            continue
        ver = str(latest.get("version") or "")
        expected_sha = str(latest.get("package_sha256") or "")
        tarball = packages_root / name / f"{ver}.tar.gz"
        if not tarball.is_file():
            problems.append({"name": name, "version": ver, "reason": "head_package_missing"})
            continue
        actual_sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if not _is_package_sha(expected_sha) or actual_sha != expected_sha:
            problems.append({"name": name, "version": ver, "reason": "head_package_sha_mismatch"})
    return problems


def _build_summary(
    results: list[dict[str, Any]],
    head_errors: list[dict[str, str]],
    tree_errors: list[str],
    has_pkg_col: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failing_rows = [r for r in results if r["status"] not in NON_FAILING_STATUSES]
    failing_count = len(failing_rows) + len(head_errors) + len(tree_errors)
    summary = {
        "rows": len(results),
        "ok": sum(r["status"] == "ok" for r in results),
        "backfilled": sum(r["status"] == "ok" and r["detail"] == "package_sha256 已回填" for r in results),
        "retired_exempt": sum(
            r["status"] in {"retired_untracked", "retired_without_package", "legacy_exempt"}
            for r in results
        ),
        "failing_rows": len(failing_rows),
        "head_errors": len(head_errors),
        "tree_errors": len(tree_errors),
        "failing": failing_count,
        "package_column_present": has_pkg_col,
    }
    return summary, failing_rows


def _resolve_manifest_path(cli_manifest: Path | None) -> Path:
    if cli_manifest is not None:
        return cli_manifest
    env_manifest = (os.getenv("STP_TOOL_MANIFEST") or "").strip()
    return Path(env_manifest) if env_manifest else REPO_ROOT / "tool_manifest.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scripts-root", type=Path, default=REPO_ROOT / "backend" / "agent" / "scripts")
    ap.add_argument("--manifest", type=Path, default=None)
    ap.add_argument("--packages-root", type=Path, default=None)
    ap.add_argument("--skip-tree", action="store_true", help="跳过族源码树与 manifest 最新条目重建比对")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    manifest_path = _resolve_manifest_path(args.manifest)
    if not manifest_path.is_file():
        print(f"EQUIVALENCE UNKNOWN: tool_manifest 不存在 ({manifest_path})", file=sys.stderr)
        return 2

    pk_root = args.packages_root if args.packages_root is not None else default_packages_root()
    if pk_root is None or not pk_root.is_dir():
        print("EQUIVALENCE UNKNOWN: packages_root 未配置或目录不存在", file=sys.stderr)
        return 2

    if not args.skip_tree and not args.scripts_root.is_dir():
        print(f"EQUIVALENCE UNKNOWN: scripts_root 不存在 ({args.scripts_root})", file=sys.stderr)
        return 2

    try:
        database_url, _source = resolve_database_url()
    except RuntimeError as exc:
        print(f"EQUIVALENCE UNKNOWN: {exc}", file=sys.stderr)
        return 2

    manifest = load_manifest(manifest_path)
    checker = _load_checker()

    tree_errors: list[str] = []
    if not args.skip_tree:
        packer = checker._load_packer()
        rebuilt = checker.rebuild_all(args.scripts_root, packer)
        tree_errors = checker.check(manifest, rebuilt, args.scripts_root)

    head_errors = evaluate_manifest_heads(manifest, pk_root, checker)
    rows, has_pkg_col = _fetch_rows(database_url)
    results = evaluate(rows, manifest, pk_root, has_pkg_col=has_pkg_col)
    summary, failing_rows = _build_summary(results, head_errors, tree_errors, has_pkg_col)

    if args.json:
        print(json.dumps({
            "summary": summary,
            "failing_rows": failing_rows,
            "head_errors": head_errors,
            "tree_errors": tree_errors,
            "rows": results,
        }, ensure_ascii=False, indent=2))
    else:
        for r in failing_rows:
            print(f"[{r['status']}] {r['name']}@{r['version']} active={r['is_active']} {r['detail']}")
        for h in head_errors:
            print(f"[{h['reason']}] {h['name']}@{h['version']}")
        for err in tree_errors:
            print(f"[tree_mismatch] {err}")
        print("EQUIVALENCE " + ("OK" if summary["failing"] == 0 else "FAIL") + ": " + json.dumps(summary, ensure_ascii=False))
    return 1 if summary["failing"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
