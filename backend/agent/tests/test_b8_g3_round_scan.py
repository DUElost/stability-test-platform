"""B8 G3 counterexamples: spill before scan, complete mapping and isolated rounds."""

import errno
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
import xlwt

from backend.agent.aee import scan_scope
from backend.agent.aee.scan_scope import (
    UNKNOWN_DEVICE_ID_VALUES,
    ScanReportMappingError,
    report_mapping,
    stage_spill_inputs,
)
from backend.agent.contracts.occurrence_key import compute_occurrence_key
from backend.agent.contracts.round_report_layout import round_manifest_path, round_report_dir
from backend.agent.scan_runner import ScanRunner, _ScanJob
from backend.agent.unisoc_scan_runner import UnisocScanRunner
from backend.agent.upload_manager import UploadManager


REPORT_HEADERS = [
    "Id", "Path", "Version", "ExpTime", "ExpClass", "ExpType ",
    "CurProcess", "Package", "Detail", "CausedBy", "extraTag", "Count",
    "Activity", "DeviceId", "Rom_Ram",
]


def write_report(path, paths, details=None):
    """Write an aeeexp org with the real 15-column header (including DeviceId)."""
    book = xlwt.Workbook()
    sheet = book.add_sheet("aeeexp")
    for column, name in enumerate(REPORT_HEADERS):
        sheet.write(0, column, name)
    for index, value in enumerate(paths, 1):
        sheet.write(index, 1, str(value))
        if details is not None:
            sheet.write(index, 8, details[index - 1])
    book.save(str(path))


@pytest.fixture(autouse=True)
def reset():
    ScanRunner._reset_for_tests()
    UploadManager._reset_for_tests()
    UnisocScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()
    UploadManager._reset_for_tests()
    UnisocScanRunner._reset_for_tests()


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    hdd, nfs = tmp_path / "hdd", tmp_path / "nfs"
    event = hdd / "V1_1104_MonkeyAEEinfo" / "SER-A" / "vendor_aee_exp" / "event"
    event.mkdir(parents=True)
    (event / "main.dbg").write_bytes(b"complete original evidence")
    remote = nfs / "devices" / "42" / "dle-1" / "event"
    shutil.copytree(event, remote)
    key = compute_occurrence_key(platform="MTK", serial="SER-A", db_path="/data/aee_exp/db.00",
                                 aee_ts="2026-11-04 00:01:02.123", raw_event_type="KE")
    ev = {"id": "dle-1", "serial": "SER-A", "platform": "MTK", "plan_run_id": 42,
          "host_id": "host-1", "local_path": str(event), "remote_path": str(remote),
          "occurrence_key": key}
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(nfs))
    monkeypatch.setattr("backend.agent.scan_runner.load_scan_events", lambda *args: [ev])
    runner = ScanRunner.instance()
    runner.configure(scan_tool_python="python", scan_tool_script="/tools/start_log_scan.py", hdd_root=str(hdd))
    UploadManager.instance().configure(nfs_root=str(nfs))

    def tool(argv, **kwargs):
        if "-dedup_org" not in argv:
            root = Path(argv[argv.index("-d") + 1])
            write_report(root / "Result_test_org.xls", sorted(root.rglob("main.dbg")))
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", tool)
    return runner, hdd, nfs, event, ev


@pytest.mark.parametrize("keep_local", [False, True])
def test_spill_before_first_scan_remains_input_and_preserves_identity(campaign, monkeypatch, caplog, keep_local):
    runner, hdd, nfs, event, ev = campaign
    if not keep_local:
        shutil.rmtree(event)
    real_link = os.link

    def cross_fs(src, dst):
        if Path(src).is_relative_to(nfs):
            raise OSError(errno.EXDEV, "cross filesystem")
        return real_link(src, dst)
    monkeypatch.setattr("backend.agent.aee.scan_scope.os.link", cross_fs)
    caplog.set_level(logging.INFO)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"],
                               run_date_stamps=["1104"], fact_round_id=7,
                               spill_inputs=[{"dle_id": ev["id"], "remote_path": ev["remote_path"],
                                              "occurrence_key": ev["occurrence_key"]}])
    report = round_report_dir(nfs, 42, 7, "mtk") / "host-1_Result_test_org.xls"
    mapping = json.loads(Path(str(report) + ".mapping.json").read_text())
    assert len(mapping["rows"]) == 1 + keep_local
    assert {r["occurrence_key"] for r in mapping["rows"]} == {ev["occurrence_key"]}
    assert {r["source"] for r in mapping["rows"]} == ({"local", "spill"} if keep_local else {"spill"})
    assert all(r["dle_id"] == "dle-1" for r in mapping["rows"])
    assert "spill_unstaged" not in mapping
    assert "temporary_bytes=26" in caplog.text
    assert not list((hdd / ".stp-scan").iterdir())
    assert Path(ev["remote_path"]).is_dir()


def test_mapping_covers_all_rows_without_guessing(tmp_path):
    root = tmp_path / "scan"
    known = root / "V1" / "SER-A" / "vendor_aee_exp" / "known"
    unknown = root / "V1" / "SER-A" / "vendor_aee_exp" / "unknown"
    known.mkdir(parents=True)
    unknown.mkdir()
    report = root / "Result_x_org.xls"
    write_report(report, [known / "a.dbg", unknown / "b.dbg"])
    sources = [{"id": "one", "serial": "SER-A", "scan_path": str(known),
                "local_path": str(known), "source": "local", "occurrence_key": "k"}]
    mapping = report_mapping(str(report), serials=["SER-A"], sources=sources, scan_root=str(root),
                             plan_run_id=42, host_id="h", platform="mtk")
    assert [r["row"] for r in mapping["rows"]] == [2, 3]
    assert mapping["rows"][0]["dle_id"] == "one"
    assert mapping["rows"][1] == {"sheet": "aeeexp", "row": 3, "path": str(unknown / "b.dbg"),
                                    "dle_id": None, "occurrence_key": None, "source": None, "mapped": False}


@pytest.mark.parametrize(
    "sentinel",
    sorted(UNKNOWN_DEVICE_ID_VALUES) + ["None", "UNDEFINED", "Discard_Device_Id"],
)
def test_unknown_device_id_sentinel_with_scoped_path_is_accepted(tmp_path, sentinel):
    root = tmp_path / "scan"
    event = root / "V1_1104_MonkeyAEEinfo" / "SER-A" / "vendor_aee_exp" / "2026_1101_db.00.JE"
    event.mkdir(parents=True)
    report = root / "Result_x_org.xls"
    write_report(report, [event / "__"],
                 details=[f"Device_id: {sentinel}\n解析库版本：recognizeexception 1.0.8"])
    mapping = report_mapping(str(report), serials=["SER-A"], sources=[], scan_root=str(root),
                             plan_run_id=42, host_id="h", platform="mtk")
    assert [r["row"] for r in mapping["rows"]] == [2]


def test_unknown_device_id_without_scoped_path_is_unprovable(tmp_path):
    root = tmp_path / "scan"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "SER-A" / "vendor_aee_exp" / "2026_1101_db.00.JE"
    outside.mkdir(parents=True)
    report = root / "Result_x_org.xls"
    write_report(report, [outside / "__"], details=["Device_id: None"])
    with pytest.raises(ScanReportMappingError) as caught:
        report_mapping(str(report), serials=["SER-A"], sources=[], scan_root=str(root),
                       plan_run_id=42, host_id="h", platform="mtk")
    assert caught.value.reason == "unprovable"
    assert caught.value.row == 2


def test_missing_xls_reader_refuses_unvalidated_upload(campaign, monkeypatch):
    runner, hdd, nfs, event, ev = campaign
    monkeypatch.setattr("backend.agent.aee.scan_scope.xlrd", None)
    with pytest.raises(RuntimeError, match="requires Agent dependency xlrd"):
        runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"])
    assert not (nfs / "dedup").exists()
    assert not list((hdd / ".stp-scan").iterdir())


def test_spill_copy_fallback_has_measured_local_bytes_and_keeps_center_source(campaign, tmp_path, monkeypatch):
    runner, hdd, nfs, event, ev = campaign
    root = tmp_path / "staging"
    def cross_fs(*args):
        raise OSError(errno.EXDEV, "cross filesystem")
    monkeypatch.setattr("backend.agent.aee.scan_scope.os.link", cross_fs)
    sources = stage_spill_inputs(hdd, root, nfs, 42, ["SER-A"],
                                 [{"dle_id": "dle-1", "remote_path": ev["remote_path"],
                                   "occurrence_key": ev["occurrence_key"]}], [ev])
    copied = Path(sources[0]["scan_path"]) / "main.dbg"
    remote = Path(ev["remote_path"]) / "main.dbg"
    assert copied.read_bytes() == remote.read_bytes()
    assert copied.stat().st_size == 26
    assert copied.stat().st_nlink == 1
    assert copied.stat().st_ino != remote.stat().st_ino
    assert copied.stat().st_blocks * 512 >= 26
    shutil.rmtree(root)
    assert remote.read_bytes() == b"complete original evidence"


@pytest.mark.parametrize("fact_round_id", [None, 7])
def test_foreign_serial_is_rejected_before_any_upload(campaign, monkeypatch, fact_round_id):
    runner, hdd, nfs, event, ev = campaign
    def tool(argv, **kwargs):
        root = Path(argv[argv.index("-d") + 1])
        write_report(root / "Result_test_org.xls", [root / "V1" / "FOREIGN" / "bad"])
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", tool)
    with pytest.raises(ScanReportMappingError, match="serial outside") as caught:
        runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=fact_round_id)
    assert caught.value.reason == "outside_run"
    assert caught.value.row == 2
    assert not (nfs / "dedup").exists()
    assert not (nfs / "_meta").exists()


def test_report_without_path_column_reports_missing_path(tmp_path):
    report = tmp_path / "Result_x_org.xls"
    book = xlwt.Workbook()
    book.add_sheet("aeeexp").write(0, 0, "Id")
    book.save(str(report))
    with pytest.raises(ScanReportMappingError) as caught:
        report_mapping(str(report), serials=["SER-A"], sources=[], scan_root=str(tmp_path),
                       plan_run_id=42, host_id="h", platform="mtk")
    assert caught.value.reason == "missing_path"
    assert caught.value.row == 1


def test_mtk_mapping_rejection_does_not_skip_unisoc_upload(campaign, monkeypatch, caplog):
    mtk, hdd, nfs, event, ev = campaign

    def tool(argv, **kwargs):
        root = Path(argv[argv.index("-d") + 1])
        write_report(root / "Result_test_org.xls", [hdd.parent / "outside" / "a.dbg"],
                     details=["Device_id: None"])
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", tool)

    unisoc = UnisocScanRunner.instance()
    unisoc._configured = True
    monkeypatch.setattr("backend.agent.scan_runner.detect_device_platform", lambda *args: "UNISOC")
    monkeypatch.setattr("backend.agent.unisoc_scan_runner.detect_device_platform", lambda *args: "UNISOC")
    monkeypatch.setattr("backend.agent.unisoc_scan_runner.load_scan_events", lambda *args: [ev])
    monkeypatch.setattr(unisoc, "_run_log_scan_gt", lambda *args: True)

    def scanned(root, *args, **kwargs):
        report = Path(root) / "Result_unisoc_org.xls"
        write_report(report, sorted(Path(root).rglob("main.dbg")))
        return str(report)
    monkeypatch.setattr(unisoc, "run_scan_result", scanned)
    caplog.set_level(logging.ERROR)

    ScanRunner._execute_job(_ScanJob(42, "host-1", False, ("SER-A",), (), None))

    assert (nfs / "dedup/42/unisoc/host-1_Result_unisoc_org.xls").is_file()
    assert not (nfs / "dedup/42/mtk").exists()
    assert ScanRunner.report_mapping_rejected_total() == 1
    assert "scan_report_mapping_rejected" in caplog.text
    assert "platform=mtk" in caplog.text
    assert "reason=unprovable" in caplog.text
    assert "row=2" in caplog.text


def test_unisoc_mapping_rejection_does_not_affect_mtk(monkeypatch, caplog):
    mtk, unisoc = ScanRunner.instance(), UnisocScanRunner.instance()
    monkeypatch.setattr(mtk, "is_configured", lambda: True)
    mtk_run = MagicMock()
    monkeypatch.setattr(mtk, "run_scan_and_upload", mtk_run)
    monkeypatch.setattr(unisoc, "is_configured", lambda: True)
    monkeypatch.setattr(
        unisoc, "run_scan_and_upload",
        MagicMock(side_effect=ScanReportMappingError("no_rows", row=0, detail="no rows")),
    )
    monkeypatch.setattr("backend.agent.scan_runner.detect_device_platform", lambda *args: "UNISOC")
    caplog.set_level(logging.ERROR)

    ScanRunner._execute_job(_ScanJob(42, "h", False, ("SER-A",), (), None))

    mtk_run.assert_called_once()
    assert ScanRunner.report_mapping_rejected_total() == 1
    assert "scan_report_mapping_rejected" in caplog.text
    assert "platform=unisoc" in caplog.text


def test_shadow_cannot_change_legacy_files_or_manifest(campaign):
    runner, hdd, nfs, event, ev = campaign
    runner.run_scan_and_upload(42, "host-1", is_final=True, device_serials=["SER-A"])
    legacy_report = nfs / "dedup/42/mtk/host-1_Result_test_org.xls"
    legacy_manifest = nfs / "_meta/42/host-1.json"
    before = legacy_report.read_bytes(), legacy_manifest.read_bytes()
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=8)
    assert before == (legacy_report.read_bytes(), legacy_manifest.read_bytes())
    assert list((nfs / "_meta/42").glob("*.json")) == [legacy_manifest]
    shadow = json.loads(round_manifest_path(nfs, 42, 8, "host-1").read_text())
    assert shadow["schema_version"] == 1
    assert len(shadow["artifacts"]) == 2
    assert all(a["fact_round_id"] == 8 for a in shadow["artifacts"])
    assert sum(a["registerable"] for a in shadow["artifacts"]) == 1
    assert "fact_round_id" not in json.loads(legacy_manifest.read_text())["artifacts"][0]


@pytest.mark.parametrize("fact_round_id", [None, 7])
@pytest.mark.parametrize("failure", [None, "timeout", "http"])
def test_query_failure_is_visible_in_uploaded_mapping(campaign, monkeypatch, caplog, fact_round_id, failure):
    runner, hdd, nfs, event, ev = campaign
    monkeypatch.setenv("API_URL", "http://test")
    monkeypatch.setenv("AGENT_SECRET", "test")
    monkeypatch.setattr("backend.agent.scan_runner.load_scan_events", scan_scope.load_scan_events)
    response = MagicMock(status_code=503 if failure == "http" else 200)
    response.json.return_value = {"data": {"events": []}}
    get = MagicMock(return_value=response)
    if failure == "timeout":
        get.side_effect = requests.Timeout()
    monkeypatch.setattr("backend.agent.aee.device_log_event_client.requests.get", get)
    caplog.set_level(logging.WARNING)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=fact_round_id)
    directory = (round_report_dir(nfs, 42, fact_round_id, "mtk") if fact_round_id is not None
                 else nfs / "dedup/42/mtk")
    report = directory / "host-1_Result_test_org.xls"
    assert report.is_file()
    mapping = json.loads(Path(str(report) + ".mapping.json").read_text())
    assert mapping.get("events_unavailable", False) is (failure is not None)
    assert mapping["rows"][0]["mapped"] is False
    assert ("scan_mapping_events_unavailable" in caplog.text) is (failure is not None)
    assert get.call_args.kwargs["params"] == {"host_id": "host-1", "state": "", "plan_run_id": 42}


@pytest.mark.parametrize("failure", ["timeout", "http"])
def test_query_failure_with_spill_uploads_report_and_lists_unstaged_inputs(campaign, monkeypatch, caplog, failure):
    runner, hdd, nfs, event, ev = campaign
    monkeypatch.setenv("API_URL", "http://test")
    monkeypatch.setenv("AGENT_SECRET", "test")
    monkeypatch.setattr("backend.agent.scan_runner.load_scan_events", scan_scope.load_scan_events)
    get = MagicMock(return_value=MagicMock(status_code=503))
    if failure == "timeout":
        get.side_effect = requests.Timeout()
    monkeypatch.setattr("backend.agent.aee.device_log_event_client.requests.get", get)
    spill_inputs = [
        {"dle_id": ev["id"], "remote_path": ev["remote_path"], "occurrence_key": ev["occurrence_key"]},
        {"dle_id": "dle-2", "remote_path": str(nfs / "devices/42/dle-2/event"), "occurrence_key": "other"},
    ]
    stage = MagicMock(wraps=stage_spill_inputs)
    monkeypatch.setattr("backend.agent.scan_runner.stage_spill_inputs", stage)
    caplog.set_level(logging.WARNING)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"],
                               fact_round_id=7, spill_inputs=spill_inputs)

    report = round_report_dir(nfs, 42, 7, "mtk") / "host-1_Result_test_org.xls"
    assert report.is_file()
    mapping = json.loads(Path(str(report) + ".mapping.json").read_text())
    assert mapping["events_unavailable"] is True
    assert mapping["spill_unstaged"] == ["dle-1", "dle-2"]
    assert len(mapping["rows"]) == 1
    assert mapping["rows"][0]["mapped"] is False
    stage.assert_not_called()  # Serial and original local layout require the DLE list.
    assert "scan_mapping_events_unavailable" in caplog.text
    assert Path(ev["remote_path"]).is_dir()
    assert not list((hdd / ".stp-scan").iterdir())


@pytest.mark.parametrize("fact_round_id", [None, 7])
def test_unisoc_query_failure_is_visible_without_suppressing_report(campaign, monkeypatch, caplog, fact_round_id):
    mtk, hdd, nfs, event, ev = campaign
    runner = UnisocScanRunner.instance()
    runner._configured = True
    monkeypatch.setenv("API_URL", "http://test")
    monkeypatch.setenv("AGENT_SECRET", "test")
    monkeypatch.setattr("backend.agent.unisoc_scan_runner.detect_device_platform", lambda *args: "UNISOC")
    monkeypatch.setattr("backend.agent.aee.device_log_event_client.requests.get", MagicMock(side_effect=requests.Timeout()))
    monkeypatch.setattr(runner, "_run_log_scan_gt", lambda *args: True)
    def scanned(root, *args, **kwargs):
        report = Path(root) / "Result_test_org.xls"
        write_report(report, sorted(Path(root).rglob("main.dbg")))
        return str(report)
    monkeypatch.setattr(runner, "run_scan_result", scanned)
    caplog.set_level(logging.WARNING)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=fact_round_id)
    directory = (round_report_dir(nfs, 42, fact_round_id, "unisoc") if fact_round_id is not None
                 else nfs / "dedup/42/unisoc")
    report = directory / "host-1_Result_test_org.xls"
    assert report.is_file()
    assert json.loads(Path(str(report) + ".mapping.json").read_text())["events_unavailable"] is True
    assert "scan_mapping_events_unavailable" in caplog.text


def test_queue_coalesces_only_within_the_same_fact_round(monkeypatch):
    monkeypatch.setattr(ScanRunner, "_ensure_worker", lambda: None)
    ScanRunner.enqueue_scan_now(42, "h", is_final=True)
    ScanRunner.enqueue_scan_now(42, "h", is_final=False, fact_round_id=1, spill_inputs=[])
    ScanRunner.enqueue_scan_now(42, "h", is_final=True, fact_round_id=2, spill_inputs=[])
    ScanRunner.enqueue_scan_now(42, "h", is_final=False, fact_round_id=1, spill_inputs=[])
    jobs = [ScanRunner._dequeue_next() for _ in range(3)]
    assert [(j.fact_round_id, j.is_final) for j in jobs] == [(None, True), (1, False), (2, True)]
    ScanRunner.enqueue_scan_now(42, "h", is_final=True)
    ScanRunner.enqueue_scan_now(42, "h", is_final=False)
    assert ScanRunner._dequeue_next().is_final is False  # unchanged legacy latest-wins


def test_valid_empty_shadow_report_is_an_explicit_empty_receipt(campaign, monkeypatch):
    runner, hdd, nfs, event, ev = campaign
    def tool(argv, **kwargs):
        root = Path(argv[argv.index("-d") + 1])
        write_report(root / "Result_empty_org.xls", [])
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr("backend.agent.scan_runner.subprocess.run", tool)
    runner.run_scan_and_upload(42, "host-1", is_final=False, device_serials=["SER-A"], fact_round_id=10)
    manifest = json.loads(round_manifest_path(nfs, 42, 10, "host-1").read_text())
    entry = next(a for a in manifest["artifacts"] if a["registerable"])
    assert entry["fact_round_id"] == 10 and entry["platform"] == "mtk"
    assert entry["empty_receipt"] is True


@pytest.mark.parametrize("fact_round_id", [None, 7])
def test_empty_pure_mtk_run_never_runs_unisoc(monkeypatch, fact_round_id):
    monkeypatch.setattr("backend.agent.scan_runner.detect_device_platform", lambda *args: "MTK")
    mtk, unisoc = ScanRunner.instance(), UnisocScanRunner.instance()
    monkeypatch.setattr(mtk, "is_configured", lambda: True)
    monkeypatch.setattr(mtk, "run_scan_and_upload", MagicMock())
    monkeypatch.setattr(unisoc, "is_configured", lambda: True)
    upload = MagicMock()
    monkeypatch.setattr(unisoc, "run_scan_and_upload", upload)
    ScanRunner._execute_job(_ScanJob(42, "h", False, ("SER-A",), (), fact_round_id))
    upload.assert_not_called()


@pytest.mark.parametrize("bad", [0, -1, True, "7", "../7"])
def test_round_layout_refuses_invalid_ids(tmp_path, bad):
    with pytest.raises(ValueError):
        round_report_dir(tmp_path, 42, bad, "mtk")
    with pytest.raises(ValueError):
        round_manifest_path(tmp_path, 42, bad, "h")


def test_timeout_with_no_serial_evidence_rejects_org_before_upload(tmp_path, monkeypatch):
    runner = UnisocScanRunner.instance()
    runner._configured = True
    root = tmp_path / "root"
    root.mkdir()
    org = root / "Result_test_org.xls"
    write_report(org, [])
    monkeypatch.setattr("backend.agent.unisoc_scan_runner.detect_device_platform", lambda *args: "UNISOC")
    monkeypatch.setattr(ScanRunner.instance(), "_prepare_scan_root", lambda *args: str(root))
    monkeypatch.setattr(runner, "_run_log_scan_gt", lambda *args: True)
    monkeypatch.setattr(runner, "run_scan_result", lambda *args, **kwargs: str(org))
    runner._last_scan_timed_out = True
    uploader = UploadManager.instance()
    uploader.configure(nfs_root=str(tmp_path / "nfs"))
    with pytest.raises(ScanReportMappingError, match="no run serial evidence") as caught:
        runner.run_scan_and_upload(42, "h", is_final=False, device_serials=["SER-A"])
    assert caught.value.reason == "no_rows"
    assert not (tmp_path / "nfs").exists()
