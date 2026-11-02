"""B8 G0-a: obsolete hardlink entry point cannot touch old logs or query a DB."""
from __future__ import annotations

import json
import os

import pytest

from backend.scripts import center_storage_hardlink_dedup as dd


@pytest.mark.parametrize("execute", [False, True])
def test_retired_entry_refuses_before_reading_or_mutating(tmp_path, capsys, monkeypatch, execute):
    devices = tmp_path / "old" / "devices"
    files = [devices / str(run) / "ev" / "data" for run in (1, 2)]
    for f in files:
        f.parent.mkdir(parents=True)
        f.write_bytes(b"same" * 32768)
        os.utime(f, (1, 1))
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps([1, 2]))
    before = [(p.stat().st_ino, p.read_bytes()) for p in files]
    args = ["--root", str(devices), "--runs-file", str(runs)]
    if execute:
        args.append("--execute")
    assert dd.main(args) == 2
    assert "retired" in capsys.readouterr().err
    assert [(p.stat().st_ino, p.read_bytes()) for p in files] == before
    monkeypatch.setenv("DATABASE_URL", "invalid-do-not-connect")
    assert dd.main(["--eligible-from-db", "--execute"]) == 2
