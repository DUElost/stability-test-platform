#!/usr/bin/env python3
"""ADR-0051 Phase 2a 等价证明（只读）：``script`` 表 ⇄ 当前树 ⇄ ``tool_manifest.json``。

对每个 ``script`` 行核四件事（任一不成立即该行不等价）：

1. ``dir``：``<scripts_root>/<name>/v<version>/`` 存在且入口文件名 == ``nfs_path`` 的 basename；
2. ``entry``：入口文件 sha256 == ``content_sha256``（基准 = **当前树字节**，不是首次发布
   commit——生产 audit 实测 ``scan_rebaseline`` 执行过 5 次 / 重锚 24 版本，见 ADR-0051 §1.3）；
3. ``support``：伴随文件 sha == ``support_files_manifest``；
4. ``package``：manifest 有该版本条目、重建包 sha == 登记 sha，且行上 ``package_sha256``
   为空或等于登记值（列尚未迁移时跳过该子项）。

退出码：0 全等价；1 有不等价；2 无法判定（缺 ``DATABASE_URL`` / 树根不存在）。
已退役且**无目录**的行（Phase 2a 前即存在的 2 行）只列出、不判红——它们不在包模型射程，
按 ADR-0051 D5 继承条款处理。

用法（控制面主机，只读；连接串经环境变量传入，不进 argv）::

    DATABASE_URL=... python -m backend.scripts.check_script_package_equivalence [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.core.database import normalize_sync_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_script_packages", REPO_ROOT / "tools" / "dev" / "check_script_packages.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fetch_rows(database_url: str) -> tuple[list[dict], bool]:
    engine = create_engine(normalize_sync_database_url(database_url))
    with engine.connect() as conn:
        has_pkg_col = bool(conn.execute(text(
            "select 1 from information_schema.columns where table_name='script' and column_name='package_sha256'"
        )).first())
        cols = "name, version, nfs_path, content_sha256, support_files_manifest, is_active"
        if has_pkg_col:
            cols += ", package_sha256"
        rows = [dict(r._mapping) for r in conn.execute(text(f"select {cols} from script order by name, version"))]
    return rows, has_pkg_col


def evaluate(rows: list[dict], scripts_root: Path, manifest: dict, expected: dict, has_pkg_col: bool) -> list[dict]:
    """纯函数：逐行判定，返回 ``[{name, version, is_active, status, detail}]``。"""
    idx: dict[tuple[str, str], dict] = {}
    for name, tool in (manifest.get("tools") or {}).items():
        for e in tool.get("versions") or []:
            idx[(name, str(e.get("version")))] = e
    out: list[dict] = []
    for r in rows:
        key = (r["name"], r["version"])
        rec = {"name": r["name"], "version": r["version"], "is_active": bool(r["is_active"])}
        vdir = scripts_root / r["name"] / f"v{r['version']}"
        if not vdir.is_dir():
            rec.update(status="dir_missing", detail="无版本目录（已退役行不判红）" if not r["is_active"] else "活跃行缺目录")
            out.append(rec)
            continue
        entry_name = os.path.basename(r["nfs_path"] or "")
        entry = vdir / entry_name if entry_name else None
        if not entry or not entry.is_file():
            rec.update(status="entry_missing", detail=f"nfs_path basename {entry_name!r} 不在目录内")
            out.append(rec)
            continue
        if _sha256(entry) != r["content_sha256"]:
            rec.update(status="entry_sha_mismatch", detail="入口文件 sha ≠ content_sha256")
            out.append(rec)
            continue
        manifest_files = dict(r["support_files_manifest"] or {})
        bad = [f for f, s in manifest_files.items() if not (vdir / f).is_file() or _sha256(vdir / f) != s]
        if bad:
            rec.update(status="support_mismatch", detail=f"伴随文件不等：{bad}")
            out.append(rec)
            continue
        exp = expected.get(key)
        me = idx.get(key)
        if exp is None or me is None:
            rec.update(status="manifest_missing", detail="manifest 无该版本条目或树上无入口")
            out.append(rec)
            continue
        if me.get("package_sha256") != exp["package_sha256"]:
            rec.update(status="package_sha_mismatch", detail="重建包 sha ≠ manifest 登记")
            out.append(rec)
            continue
        if has_pkg_col and r.get("package_sha256") not in (None, exp["package_sha256"]):
            rec.update(status="db_package_sha_mismatch", detail="script.package_sha256 ≠ manifest 登记")
            out.append(rec)
            continue
        rec.update(
            status="ok",
            detail="package_sha256 已回填" if (has_pkg_col and r.get("package_sha256")) else "package_sha256 待 scan 回填",
        )
        out.append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scripts-root", type=Path, default=REPO_ROOT / "backend" / "agent" / "scripts")
    ap.add_argument("--manifest", type=Path, default=REPO_ROOT / "tool_manifest.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        print("EQUIVALENCE UNKNOWN: DATABASE_URL 未设置", file=sys.stderr)
        return 2
    if not args.scripts_root.is_dir() or not args.manifest.is_file():
        print("EQUIVALENCE UNKNOWN: scripts root 或 manifest 不存在", file=sys.stderr)
        return 2

    checker = _load_checker()
    packer = checker._load_packer()
    expected = checker.expected_entries(args.scripts_root, packer)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows, has_pkg_col = _fetch_rows(database_url)
    results = evaluate(rows, args.scripts_root, manifest, expected, has_pkg_col)

    failing = [r for r in results if r["status"] != "ok" and not (r["status"] == "dir_missing" and not r["is_active"])]
    summary = {
        "rows": len(results),
        "ok": sum(r["status"] == "ok" for r in results),
        "backfilled": sum(r["status"] == "ok" and r["detail"].startswith("package_sha256 已回填") for r in results),
        "retired_without_dir": sum(r["status"] == "dir_missing" and not r["is_active"] for r in results),
        "failing": len(failing),
        "package_column_present": has_pkg_col,
    }
    if args.json:
        print(json.dumps({"summary": summary, "rows": results}, ensure_ascii=False, indent=2))
    else:
        for r in results:
            if r["status"] != "ok":
                print(f"[{r['status']}] {r['name']}@{r['version']} active={r['is_active']} {r['detail']}")
        print("EQUIVALENCE " + ("OK" if not failing else "FAIL") + ": " + json.dumps(summary, ensure_ascii=False))
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
