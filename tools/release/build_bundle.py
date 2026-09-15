"""Build a release bundle from a working tree (I5.5 — the local stand-in for R2).

The site installer consumes a *bundle*: a directory with the documented layout
plus a ``release-manifest.json`` whose digests are computed with the ADR-0040
implementation.  Until the release pipeline exists, this tool produces exactly
that from a checkout, so "clone → install" needs no hand-rolled packing.

Usage:
    python tools/release/build_bundle.py --repo-root . --out /srv/stp-bundle \
        [--version local-20260915] [--schema-target <alembic-head>] [--wheelhouse]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "release-manifest.json"
TREE_LAYOUT = ("backend", "deploy", "tools", "frontend/dist-prod")
DEFAULT_VERSION_PREFIX = "local"
SUPPORTED_PLATFORMS = (
    {"distribution": "debian", "versions": ["13"], "cpu_arch": ["x86_64"]},
    {"distribution": "ubuntu", "versions": ["24.04"], "cpu_arch": ["x86_64"]},
)
AGENT_PROTOCOL = ">=1.0,<2.0"
EVIDENCE_REF = "local_build"


class BundleError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{': ' + detail if detail else ''}")


def _copy_tree(source: Path, target: Path) -> None:
    # 与 S2 落地同语义：保持符号链接；先清掉与源链接冲突的旧项
    for root, _dirs, files in os.walk(source):
        for name in files:
            candidate = Path(root) / name
            if not candidate.is_symlink():
                continue
            destination = target / candidate.relative_to(source)
            if destination.is_symlink() or destination.exists():
                destination.unlink()
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)


def _git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _revision(repo_root: Path) -> str:
    revision = _git(repo_root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision or ""):
        raise BundleError("bundle_revision", "not a git checkout; pass --revision explicitly")
    return revision


def _schema_target(repo_root: Path) -> str:
    """Derive the alembic head from the migration files (dependency-free)."""
    versions = repo_root / "backend" / "alembic" / "versions"
    revisions: dict[str, str] = {}
    referenced: set[str] = set()
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        head = re.search(r"^revision(?::[^=]+)?\s*=\s*[\"']([0-9A-Za-z_]+)[\"']", text, re.M)
        down = re.search(r"^down_revision(?::[^=]+)?\s*=\s*(.+)$", text, re.M)
        if head is None:
            continue
        name = head.group(1)
        revisions[name] = path.name
        for token in re.findall(r"[\"']([0-9A-Za-z_]+)[\"']", down.group(1) if down else ""):
            referenced.add(token)
    heads = sorted(name for name in revisions if name not in referenced)
    if len(heads) != 1:
        raise BundleError("bundle_schema_target", f"expected one head, found {heads}")
    return heads[0]


def _load_agent_digest_module(bundle: Path):
    """按路径加载 ADR-0040 实现（stdlib-only，不触发 backend 包链）。"""
    import importlib.util

    module_path = bundle / "backend" / "agent" / "artifact_digest.py"
    spec = importlib.util.spec_from_file_location("stp_agent_artifact_digest", module_path)
    if spec is None or spec.loader is None:
        raise BundleError("bundle_digest", f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digests(bundle: Path) -> dict[str, str]:
    """与 S0（install._digest_bundle）同基准：code / resources 两侧都算。"""
    module = _load_agent_digest_module(bundle)
    agent_dir = bundle / "backend" / "agent"
    extra = {
        "stp_schemas/pipeline_schema.json": str(bundle / "backend" / "schemas" / "pipeline_schema.json"),
    }
    return {
        kind: module.digest_entries(module.collect_artifact_entries(str(agent_dir), extra, kind=kind))
        for kind in ("code", "resources")
    }


def build_bundle(
    repo_root: str | Path,
    out: str | Path,
    *,
    version: str | None = None,
    schema_target: str | None = None,
    revision: str | None = None,
    wheelhouse: bool = False,
    python: str | None = None,
) -> dict:
    repo_root, out = Path(repo_root).resolve(), Path(out).resolve()
    python = python or sys.executable
    missing = [name for name in TREE_LAYOUT if not (repo_root / name).exists()]
    if "frontend/dist-prod" in missing:
        raise BundleError(
            "bundle_frontend", "run `cd frontend && npm ci && npm run build:prod` first",
        )
    if missing:
        raise BundleError("bundle_layout", ",".join(missing))

    out.mkdir(parents=True, exist_ok=True)
    for name in TREE_LAYOUT:
        _copy_tree(repo_root / name, out / name)
    for extra in ("ruff.toml",):
        if (repo_root / extra).exists():
            shutil.copy2(repo_root / extra, out / extra)

    revision = revision or _revision(repo_root)
    short = revision[:8]
    version = version or f"{DEFAULT_VERSION_PREFIX}-{datetime.now(timezone.utc):%Y%m%d}-{short}"
    digests = _digests(out)
    manifest = {
        "manifest_version": 1,
        "product": {"version": version},
        "source": {"revision": revision},
        "components": [
            {"name": "agent-code", "digest": digests["code"]},
            {"name": "host-resources", "digest": digests["resources"]},
        ],
        "database": {"schema_target": schema_target or _schema_target(out)},
        "compatibility": {
            "agent_protocol": AGENT_PROTOCOL,
            "platforms": [dict(platform) for platform in SUPPORTED_PLATFORMS],
        },
        "provenance": {"attestation": "controlled_channel", "evidence_ref": EVIDENCE_REF},
    }
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )

    if wheelhouse:
        requirements = out / "backend" / "requirements.txt"
        target = out / "wheelhouse"
        target.mkdir(exist_ok=True)
        result = subprocess.run(
            [python, "-m", "pip", "download", "--disable-pip-version-check",
             "-r", str(requirements), "-d", str(target)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise BundleError("bundle_wheelhouse", result.stderr.strip()[:200])

    return {
        "out": str(out),
        "version": version,
        "revision": revision,
        "components": {
            "agent-code": digests["code"], "host-resources": digests["resources"],
        },
        "schema_target": manifest["database"]["schema_target"],
        "wheelhouse": wheelhouse,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a site release bundle from a working tree.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--out", required=True, help="Bundle output directory (created if needed).")
    parser.add_argument("--version", default=None)
    parser.add_argument("--schema-target", default=None, help="Override the derived alembic head.")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--wheelhouse", action="store_true", help="Also download wheels for offline installs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_bundle(
            args.repo_root, args.out, version=args.version, schema_target=args.schema_target,
            revision=args.revision, wheelhouse=args.wheelhouse,
        )
    except BundleError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Bundle written: {result['out']}")
        print(f"  version={result['version']} revision={result['revision'][:8]} schema_target={result['schema_target']}")
        for name, digest in result["components"].items():
            print(f"  {name}={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
