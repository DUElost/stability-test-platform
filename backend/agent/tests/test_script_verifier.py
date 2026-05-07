"""ADR-0021 — Agent-side script content verifier unit tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from backend.agent.script_verifier import (
    hash_local_script_file,
    verify_scripts_payload,
)


def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def script_dir(tmp_path: Path) -> Path:
    """Create a tiny fake script tree under tmp_path."""
    a_dir = tmp_path / "alpha" / "v1.0.0"
    a_dir.mkdir(parents=True)
    (a_dir / "alpha.py").write_bytes(b"print('alpha v1')\n")

    b_dir = tmp_path / "beta" / "v2.0.0"
    b_dir.mkdir(parents=True)
    (b_dir / "beta.py").write_bytes(b"print('beta v2 with more bytes')\n")
    return tmp_path


# ---------------------------------------------------------------------------
# hash_local_script_file
# ---------------------------------------------------------------------------


def test_hash_local_script_file_returns_sha_for_existing_file(script_dir: Path):
    path = script_dir / "alpha" / "v1.0.0" / "alpha.py"
    expected = _sha256_of(b"print('alpha v1')\n")
    assert hash_local_script_file(str(path)) == expected


def test_hash_local_script_file_returns_none_for_missing_file(tmp_path: Path):
    path = tmp_path / "ghost" / "v0" / "missing.py"
    assert hash_local_script_file(str(path)) is None


def test_hash_local_script_file_returns_none_for_empty_path():
    assert hash_local_script_file("") is None
    assert hash_local_script_file(None) is None


def test_hash_local_script_file_returns_none_for_directory(script_dir: Path):
    """Pointing the path at a directory must not raise — return None."""
    assert hash_local_script_file(str(script_dir / "alpha")) is None


# ---------------------------------------------------------------------------
# verify_scripts_payload
# ---------------------------------------------------------------------------


def test_verify_scripts_payload_marks_match_when_sha_aligned(script_dir: Path):
    alpha_path = script_dir / "alpha" / "v1.0.0" / "alpha.py"
    expected_sha = _sha256_of(alpha_path.read_bytes())

    payload = verify_scripts_payload(
        [
            {
                "name": "alpha",
                "version": "v1.0.0",
                "nfs_path": str(alpha_path),
                "sha256": expected_sha,
            }
        ],
        host_id="host-101",
        agent_version="test-rev",
    )

    assert payload["host_id"] == "host-101"
    assert payload["agent_version"] == "test-rev"
    assert "checked_at" in payload
    assert len(payload["results"]) == 1
    r0 = payload["results"][0]
    assert r0["name"] == "alpha"
    assert r0["version"] == "v1.0.0"
    assert r0["expected_sha"] == expected_sha
    assert r0["actual_sha"] == expected_sha
    assert r0["exists"] is True
    assert r0["ok"] is True
    assert r0["error"] is None


def test_verify_scripts_payload_marks_mismatch_when_sha_diverges(script_dir: Path):
    alpha_path = script_dir / "alpha" / "v1.0.0" / "alpha.py"
    payload = verify_scripts_payload(
        [
            {
                "name": "alpha",
                "version": "v1.0.0",
                "nfs_path": str(alpha_path),
                "sha256": "deadbeef" * 8,
            }
        ],
        host_id="host-1",
    )

    r0 = payload["results"][0]
    assert r0["exists"] is True
    assert r0["actual_sha"] is not None
    assert r0["actual_sha"] != "deadbeef" * 8
    assert r0["ok"] is False
    assert r0["error"] is None


def test_verify_scripts_payload_marks_missing_file_with_error(tmp_path: Path):
    payload = verify_scripts_payload(
        [
            {
                "name": "ghost",
                "version": "v0.0.0",
                "nfs_path": str(tmp_path / "absent.py"),
                "sha256": "abcd" * 16,
            }
        ],
        host_id="host-2",
    )

    r0 = payload["results"][0]
    assert r0["exists"] is False
    assert r0["actual_sha"] is None
    assert r0["ok"] is False
    assert r0["error"] == "file_missing_or_unreadable"


def test_verify_scripts_payload_handles_multiple_entries(script_dir: Path):
    alpha = script_dir / "alpha" / "v1.0.0" / "alpha.py"
    beta = script_dir / "beta" / "v2.0.0" / "beta.py"

    payload = verify_scripts_payload(
        [
            {
                "name": "alpha",
                "version": "v1.0.0",
                "nfs_path": str(alpha),
                "sha256": _sha256_of(alpha.read_bytes()),
            },
            {
                "name": "beta",
                "version": "v2.0.0",
                "nfs_path": str(beta),
                "sha256": "wrongsha" * 8,
            },
            {
                "name": "ghost",
                "version": "v9",
                "nfs_path": str(script_dir / "ghost.py"),
                "sha256": "x" * 64,
            },
        ],
        host_id="host-3",
    )

    assert [r["ok"] for r in payload["results"]] == [True, False, False]
    assert [r["exists"] for r in payload["results"]] == [True, True, False]
    assert payload["results"][2]["error"] == "file_missing_or_unreadable"


def test_verify_scripts_payload_empty_input_yields_empty_results():
    payload = verify_scripts_payload([], host_id="host-4")
    assert payload["results"] == []
    assert payload["host_id"] == "host-4"
    assert "checked_at" in payload


def test_verify_scripts_payload_uses_env_for_default_agent_version(monkeypatch):
    monkeypatch.setenv("STP_AGENT_VERSION", "from-env-2026")
    payload = verify_scripts_payload([], host_id="host-5")
    assert payload["agent_version"] == "from-env-2026"


def test_verify_scripts_payload_falls_back_when_env_missing(monkeypatch):
    monkeypatch.delenv("STP_AGENT_VERSION", raising=False)
    payload = verify_scripts_payload([], host_id="host-6")
    assert payload["agent_version"] == "unknown"
