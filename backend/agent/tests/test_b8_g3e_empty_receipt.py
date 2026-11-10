"""B8 G3e: shadow-round zero-result hosts upload an empty receipt (#3920).

Counterexamples first: on main a shadow scan whose tool exits 0 without a
fresh `_org.xls` returns `None` from `ScanRunner.run_local_scan`, so the host
stays `MISSING`.  v2.1.1 does leave machine-readable zero-result evidence
(`*_members.json` with `rows == []` and `*_summary.json` with
`counts.aee_result_*_count == 0`); these tests pin that evidence to a manifest
empty receipt and read it back with the real control-plane readers.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import xlwt

from backend.agent.contracts.round_report_layout import round_manifest_path
from backend.agent.scan_runner import ScanRunner
from backend.agent.upload_manager import ShardRegistrationError, UploadManager
from backend.services.shadow_scan_rounds import classify_round_coverage, read_round_reports

REPORT_HEADERS = [
    "Id", "Path", "Version", "ExpTime", "ExpClass", "ExpType ",
    "CurProcess", "Package", "Detail", "CausedBy", "extraTag", "Count",
    "Activity", "DeviceId", "Rom_Ram",
]


@pytest.fixture(autouse=True)
def reset():
    from backend.agent.unisoc_scan_runner import UnisocScanRunner

    ScanRunner._reset_for_tests()
    UploadManager._reset_for_tests()
    UnisocScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()
    UploadManager._reset_for_tests()
    UnisocScanRunner._reset_for_tests()


@pytest.fixture
def shadow(tmp_path, monkeypatch):
    hdd, nfs = tmp_path / "hdd", tmp_path / "nfs"
    (hdd / "V1_1104_MonkeyAEEinfo" / "SER-A").mkdir(parents=True)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    monkeypatch.setattr("backend.agent.scan_runner.load_scan_events", lambda *args: [])
    runner = ScanRunner.instance()
    runner.configure(
        scan_tool_python="python",
        scan_tool_script="/tools/start_log_scan.py",
        hdd_root=str(hdd),
    )
    UploadManager.instance().configure(nfs_root=str(nfs))
    return runner, hdd, nfs


def _write_proof(root: Path, *, rows: int = 0, count: int = 0, name: str = "Result_x_None_MonkeyAEE_SH_20261110_120000"):
    members = {
        "schema": "stp.scan_membership",
        "schema_version": "1.0",
        "meta": {
            "tool": {
                "name": "start-log-scan",
                "version": "2.1.1",
                "version_string": "V2.1.1_20261105",
                "git_commit": "dc5d9c1fe22273dcfc28e41b285e8fdb3bfb6694",
                "git_dirty": False,
            }
        },
        "rows": [{"path": f"/device/{index}"} for index in range(rows)],
        "groups": [],
        "stats": {"rows": rows, "groups": 0},
    }
    summary = {
        "tool": "stability_Start-Log-Scan",
        "counts": {
            "aee_result_org_count": count,
            "aee_result_final_count": count,
            "dbg_count": 0,
        },
        "generated_files": {
            "members_json": str(root / f"{name}_members.json"),
            "summary_json": str(root / f"{name}_summary.json"),
        },
    }
    (root / f"{name}_members.json").write_text(json.dumps(members), encoding="utf-8")
    (root / f"{name}_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _write_empty_report(path: Path) -> None:
    book = xlwt.Workbook()
    sheet = book.add_sheet("aeeexp")
    for column, header in enumerate(REPORT_HEADERS):
        sheet.write(0, column, header)
    book.save(str(path))


def _tool(write):
    def run(argv, **kwargs):
        # -dedup_org is the follow-up invocation; this test only fakes scan.
        if "-d" not in argv:
            return subprocess.CompletedProcess(argv, 0)
        root = Path(argv[argv.index("-d") + 1])
        write(root)
        return subprocess.CompletedProcess(argv, 0)

    return run


def _assert_staging_reclaimed(hdd: Path) -> None:
    staging = hdd / ".stp-scan"
    assert not staging.exists() or not list(staging.iterdir())


def test_shadow_zero_result_with_v211_proof_registers_empty_receipt(shadow, monkeypatch, caplog):
    runner, hdd, nfs = shadow
    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(_write_proof))
    caplog.set_level(logging.INFO)

    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)

    manifest = json.loads(round_manifest_path(nfs, 42, 7, "host-1").read_text())
    assert len(manifest["artifacts"]) == 1
    entry = manifest["artifacts"][0]
    assert entry["fact_round_id"] == 7
    assert entry["platform"] == "mtk"
    assert entry["empty_receipt"] is True
    assert entry["registerable"] is False
    assert "file_key" not in entry

    expected = [{"host_id": "host-1", "platform": "mtk"}]
    reports = read_round_reports(nfs, 42, 7, expected)
    assert reports == [
        {"host_id": "host-1", "platform": "mtk", "fact_round_id": 7, "empty_receipt": True}
    ]
    now = datetime.now(timezone.utc)
    coverage = classify_round_coverage(
        [dict(item) for item in expected],
        reports,
        observed_at=now,
        input_cutoff_at=now + timedelta(minutes=5),
    )
    assert coverage[0]["coverage"] == "EMPTY"
    assert UploadManager.instance().shadow_report_uploaded_total() == 1
    assert "scan_runner_empty_result_proven" in caplog.text
    assert "shadow_round_empty_receipt_registered" in caplog.text
    assert "upload_scan_report_ok" not in caplog.text
    _assert_staging_reclaimed(hdd)


@pytest.mark.parametrize("kind", ["members", "summary", "both"])
def test_shadow_zero_result_accepts_members_or_summary_proof(shadow, monkeypatch, kind):
    runner, hdd, nfs = shadow

    def write(root):
        name = "Result_x_None_MonkeyAEE_SH_20261110_120000"
        _write_proof(root, name=name)
        if kind == "members":
            (root / f"{name}_summary.json").unlink()
        elif kind == "summary":
            (root / f"{name}_members.json").unlink()

    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(write))
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)
    manifest = json.loads(round_manifest_path(nfs, 42, 7, "host-1").read_text())
    assert manifest["artifacts"][0]["empty_receipt"] is True
    _assert_staging_reclaimed(hdd)


@pytest.mark.parametrize("case", ["none", "stale", "nonzero"])
def test_shadow_zero_result_without_valid_proof_stays_missing(shadow, monkeypatch, caplog, case):
    runner, hdd, nfs = shadow

    def write(root):
        if case == "none":
            return
        if case == "stale":
            _write_proof(root)
            old = time.time() - 60
            for path in root.glob("Result_*"):
                os.utime(path, (old, old))
            return
        _write_proof(root, rows=1, count=1)

    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(write))
    caplog.set_level(logging.WARNING)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)

    assert not round_manifest_path(nfs, 42, 7, "host-1").exists()
    expected = [{"host_id": "host-1", "platform": "mtk"}]
    assert read_round_reports(nfs, 42, 7, expected) == []
    now = datetime.now(timezone.utc)
    coverage = classify_round_coverage(
        [dict(item) for item in expected],
        [],
        observed_at=now,
        input_cutoff_at=now + timedelta(minutes=5),
    )
    assert coverage[0]["coverage"] == "MISSING"
    assert "scan_runner_empty_result_unproven" in caplog.text
    assert "shadow_round_empty_receipt_registered" not in caplog.text
    _assert_staging_reclaimed(hdd)


@pytest.mark.parametrize("failure", ["rc", "timeout", "exception"])
def test_shadow_tool_failure_never_registers_empty_receipt(shadow, monkeypatch, caplog, failure):
    runner, hdd, nfs = shadow

    def run(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=1)
        if failure == "exception":
            raise OSError("tool launch failed")
        return subprocess.CompletedProcess(argv, 2)

    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", run)
    caplog.set_level(logging.WARNING)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)

    assert not round_manifest_path(nfs, 42, 7, "host-1").exists()
    assert read_round_reports(nfs, 42, 7, [{"host_id": "host-1", "platform": "mtk"}]) == []
    assert "shadow_round_empty_receipt_registered" not in caplog.text
    _assert_staging_reclaimed(hdd)


def test_shadow_scan_root_preparation_failure_never_registers(shadow, monkeypatch, caplog):
    runner, hdd, nfs = shadow
    shutil.rmtree(hdd)
    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(_write_proof))
    caplog.set_level(logging.WARNING)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)

    assert not round_manifest_path(nfs, 42, 7, "host-1").exists()
    assert "scan_runner_skip_bad_scan_root" in caplog.text
    assert "shadow_round_empty_receipt_registered" not in caplog.text


def test_legacy_zero_result_with_proof_keeps_current_scan_untouched(shadow, monkeypatch, caplog):
    runner, hdd, nfs = shadow
    meta = nfs / "_meta" / "42" / "host-1.json"
    meta.parent.mkdir(parents=True)
    meta.write_text('{"sentinel": "legacy"}', encoding="utf-8")
    before = meta.read_bytes()

    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(_write_proof))
    caplog.set_level(logging.WARNING)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"])

    assert meta.read_bytes() == before
    assert list((nfs / "_meta" / "42").glob("*.json")) == [meta]
    assert not (nfs / "_meta" / "42" / "_rounds").exists()
    assert not (nfs / "dedup" / "42").exists()
    assert "control_scan_now_scan_failed" in caplog.text
    assert "shadow_round_empty_receipt_registered" not in caplog.text
    _assert_staging_reclaimed(hdd)


def test_shadow_report_upload_keeps_sibling_observation_marker(shadow, monkeypatch, caplog):
    runner, hdd, nfs = shadow

    def write(root):
        _write_empty_report(root / "Result_test_org.xls")

    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(write))
    caplog.set_level(logging.INFO)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)

    assert "upload_scan_report_ok" in caplog.text
    assert "shadow_round_report_uploaded" in caplog.text
    assert "shadow_round_empty_receipt_registered" not in caplog.text
    _assert_staging_reclaimed(hdd)


def test_later_report_keeps_existing_empty_receipt_entry(shadow, monkeypatch):
    runner, hdd, nfs = shadow
    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(_write_proof))
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)

    monkeypatch.setattr(
        "backend.agent.scan_runner.subprocess.run",
        _tool(lambda root: _write_empty_report(root / "Result_test_org.xls")),
    )
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7)

    manifest = json.loads(round_manifest_path(nfs, 42, 7, "host-1").read_text())
    # The explicit receipt has no file_key; the 0-row org.xls keeps its own
    # registerable entry (and its own empty_receipt flag) unchanged.
    assert sum(
        1 for item in manifest["artifacts"]
        if item.get("empty_receipt") is True and "file_key" not in item
    ) == 1
    assert sum(1 for item in manifest["artifacts"] if item.get("registerable")) == 1
    expected = [{"host_id": "host-1", "platform": "mtk"}]
    reports = read_round_reports(nfs, 42, 7, expected)
    assert len(reports) == 2
    now = datetime.now(timezone.utc)
    coverage = classify_round_coverage(
        [dict(item) for item in expected],
        reports,
        observed_at=now,
        input_cutoff_at=now + timedelta(minutes=5),
    )
    assert coverage[0]["coverage"] == "EMPTY"


def test_empty_receipt_registration_failure_raises_shard_error(shadow, monkeypatch):
    runner, hdd, nfs = shadow
    blocker = nfs / "_meta" / "42" / "_rounds" / "7"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a directory", encoding="utf-8")

    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", _tool(_write_proof))
    with pytest.raises(ShardRegistrationError):
        runner.run_scan_and_upload(
            42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=7
        )
    _assert_staging_reclaimed(hdd)
