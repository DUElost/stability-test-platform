#!/usr/bin/env python3
"""Compute the deployment artifact digest for the Ansible track (ADR-0040 D2, #1997).

Usage (from the playbook checkout, control machine):

    python3 tools/ansible/compute_deploy_digest.py \
        --source-dir /path/to/repo/backend/agent \
        --schema-file /path/to/repo/backend/schemas/pipeline_schema.json

Prints::

    CODE_DIGEST=sha256:<hex>

Only the ``agent-code`` identity: the ``host-resources`` layer is retired
(ADR-0040 D8 R2) — Ansible no longer ships ``resources/`` nor writes
``ARTIFACT_DIGEST_RESOURCES``.

stdlib-only by design: loads ``backend/agent/contracts/artifact_digest.py``
（ADR-0054 D1 唯一实现；控制面 services 引用同一算法，故两侧不会各自演化）
via importlib — never imports the ``backend.*`` package chain (no DATABASE_URL /
settings deps). The digest is computed on the SAME basis as the control
plane's desired identity (code tree + pipeline schema arcname), so a host
updated by Ansible reports a digest the next control-plane convergence
recognizes (no-op steady state; #1943 class lag avoided).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENT_DIGEST_MODULE = _REPO_ROOT / "backend" / "agent" / "contracts" / "artifact_digest.py"


def _load_agent_digest_module():
    spec = importlib.util.spec_from_file_location(
        "ansible_deploy_artifact_digest", _AGENT_DIGEST_MODULE,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {_AGENT_DIGEST_MODULE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source-dir", required=True,
        help="agent source tree (same dir the playbook rsyncs from)",
    )
    parser.add_argument(
        "--schema-file", default="",
        help="pipeline schema path; included in the code identity as "
             "stp_schemas/pipeline_schema.json when present",
    )
    args = parser.parse_args(argv)

    mod = _load_agent_digest_module()
    source_dir = Path(args.source_dir).resolve()
    if not source_dir.is_dir():
        print(f"ERROR: source dir not found: {source_dir}", file=sys.stderr)
        return 1

    schema_file = Path(args.schema_file).resolve() if args.schema_file else None
    extra_files: dict[str, str] | None = None
    if schema_file is not None and schema_file.is_file():
        extra_files = {"stp_schemas/pipeline_schema.json": str(schema_file)}

    entries = mod.collect_artifact_entries(
        str(source_dir), extra_files=extra_files, kind=mod.ARTIFACT_KIND_CODE,
    )
    # 空集打印空值：playbook 据此跳过写入（误指向空树时不落一个「空载荷」身份）
    print(f"CODE_DIGEST={mod.digest_entries(entries) if entries else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
