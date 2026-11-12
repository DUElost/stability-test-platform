"""#3935 r2：手动合并轮次窗口的端到端反例（真实路由 + 合并器输入记录）。

依据 PR #3943 第 1 轮复核评论 6817887914 的 B1–B3：
- B1：终态 run 的锚点按可解析轮次开始时间选最新，旧轮晚登记不得拉早窗口；
- B2：非终态或缺 ended_at 时沿用 #1077 单轮选择；
- B3：非 ISO 轮号按原 ID 精确匹配，NULL 轮号沿用兼容下界。

每条用例都走带认证的真实手动路由，并在假合并子进程里记录实际收到的
`-merge_files_list` 文件名，而不是只断言传给 loader 的参数。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import xlwt

from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import dedup_scan as ds


@pytest.fixture
def merge_recorder(db_session, sample_plan_run, tmp_path, monkeypatch):
    """配置 stub 扫描工具，记录每次 merge 子进程收到的 listfile。"""
    center = tmp_path / "center"
    center.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(center))
    tool_dir = tmp_path / "tool"
    tool_dir.mkdir()
    script = tool_dir / "start_log_scan.py"
    script.write_text("# stub", encoding="utf-8")
    monkeypatch.setenv("STP_BACKEND_DEDUP_SCAN_PYTHON", "/usr/bin/python3")
    monkeypatch.setenv("STP_BACKEND_DEDUP_SCAN_SCRIPT", str(script))
    monkeypatch.setattr(ds, "scan_tool_supports_merge_files_list", lambda _tool: True)

    calls: list[dict] = []
    merge_root = tool_dir / "merge_result"

    def _fake_run(argv, **_kwargs):
        listfile = Path(argv[argv.index("-merge_files_list") + 1])
        paths = [
            line for line in listfile.read_text(encoding="utf-8").splitlines() if line
        ]
        calls.append({
            "paths": paths,
            "names": [Path(p).name for p in paths],
        })
        out = merge_root / f"2026_11_12_10_00_{len(calls):02d}"
        out.mkdir(parents=True, exist_ok=True)
        book = xlwt.Workbook()
        sheet = book.add_sheet("S")
        sheet.write(0, 0, "Path")
        book.save(str(out / "Result_MergeFiles.xls"))
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    monkeypatch.setattr("backend.services.dedup_scan.subprocess.run", _fake_run)
    return {"calls": calls, "center": center, "tool_dir": tool_dir}


def _artifact(
    db_session, recorder, run_id, *, host, name, round_id, created_at,
    platform="mtk",
):
    uri = str(
        recorder["center"] / "dedup" / str(run_id) / "scan" / platform / name
    )
    db_session.add(PlanRunArtifact(
        plan_run_id=run_id,
        host_id=host,
        storage_uri=uri,
        artifact_type=ds.ARTIFACT_TYPE_SCAN,
        size_bytes=1,
        scan_round_id=round_id,
        created_at=created_at,
    ))
    return uri


def _merge(client, auth_headers, run_id):
    resp = client.post(
        f"/api/v1/plan-runs/{run_id}/dedup/merge",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _merge_names(recorder) -> set[str]:
    assert len(recorder["calls"]) == 1, recorder["calls"]
    return set(recorder["calls"][0]["names"])


def test_1256_terminal_window_covers_ten_hosts_and_logs_three_late(
    client, auth_headers, db_session, sample_plan_run, merge_recorder, caplog,
):
    """1256 形态：最新轮 7 台 8 份 + 旧轮 3 台，终态窗口后 10 台 10 份。"""
    run_id = sample_plan_run.id
    old_round = "2026-11-11T03:01:30.767403+00:00"
    new_round = "2026-11-11T03:03:07.007712+00:00"
    old_at = datetime.fromisoformat(old_round)
    new_at = datetime.fromisoformat(new_round)
    sample_plan_run.status = "FAILED"
    sample_plan_run.ended_at = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)

    for idx in range(1, 7):
        _artifact(
            db_session, merge_recorder, run_id,
            host=f"h{idx:02d}", name=f"h{idx:02d}_new_org.xls",
            round_id=new_round, created_at=new_at + timedelta(seconds=1),
        )
    _artifact(
        db_session, merge_recorder, run_id, host="15.83",
        name="15.83_new_org.xls", round_id=new_round,
        created_at=new_at + timedelta(seconds=2),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="15.83",
        name="15.83_new_repeat_org.xls", round_id=new_round,
        created_at=new_at + timedelta(seconds=3),
    )
    for host in ("15.84", "15.86", "9.126"):
        _artifact(
            db_session, merge_recorder, run_id, host=host,
            name=f"{host}_old_org.xls", round_id=old_round,
            created_at=old_at + timedelta(seconds=1),
        )
    db_session.commit()

    with caplog.at_level("INFO", logger="backend.services.dedup_scan"):
        body = _merge(client, auth_headers, run_id)

    assert body["scan_round_id"] == new_round
    names = _merge_names(merge_recorder)
    assert len(names) == 10, names
    assert "15.83_new_repeat_org.xls" in names
    assert "15.83_new_org.xls" not in names
    assert {"15.84_old_org.xls", "15.86_old_org.xls", "9.126_old_org.xls"} <= names
    assert "hosts=15.84,15.86,9.126" in caplog.text
    assert f"anchor={new_round}" in caplog.text
    assert "floor=2026-11-11T03:00:00+00:00" in caplog.text


def test_b1_terminal_anchor_uses_round_start_not_last_registration(
    client, auth_headers, db_session, sample_plan_run, merge_recorder, caplog,
):
    """B1：旧轮 02:59 晚于新轮登记，锚点仍须是新轮；窗口外旧轮不进入。"""
    run_id = sample_plan_run.id
    old_round = "2026-11-11T02:59:00+00:00"
    new_round = "2026-11-11T03:03:07.007712+00:00"
    sample_plan_run.status = "FAILED"
    sample_plan_run.ended_at = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
    _artifact(
        db_session, merge_recorder, run_id, host="h_history_pre_end",
        name="h_history_pre_end_org.xls", round_id=old_round,
        created_at=datetime(2026, 11, 11, 3, 4, 30, tzinfo=timezone.utc),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="h_latest",
        name="h_latest_latest_org.xls", round_id=new_round,
        created_at=datetime(2026, 11, 11, 3, 3, 20, tzinfo=timezone.utc),
    )
    db_session.commit()

    with caplog.at_level("INFO", logger="backend.services.dedup_scan"):
        body = _merge(client, auth_headers, run_id)

    assert body["scan_round_id"] == new_round
    assert _merge_names(merge_recorder) == {"h_latest_latest_org.xls"}
    assert f"anchor={new_round}" in caplog.text
    assert "floor=2026-11-11T03:00:00+00:00" in caplog.text


def test_b1_terminal_window_keeps_post_end_old_round_host_with_late_log(
    client, auth_headers, db_session, sample_plan_run, merge_recorder, caplog,
):
    """B1 后半：旧轮在终态之后（但仍早于新轮）→ 两份都保留并记迟到。"""
    run_id = sample_plan_run.id
    old_round = "2026-11-11T03:01:00+00:00"
    new_round = "2026-11-11T03:03:07.007712+00:00"
    sample_plan_run.status = "FAILED"
    sample_plan_run.ended_at = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
    _artifact(
        db_session, merge_recorder, run_id, host="h_post_end",
        name="h_post_end_old_org.xls", round_id=old_round,
        created_at=datetime(2026, 11, 11, 3, 4, 30, tzinfo=timezone.utc),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="h_latest",
        name="h_latest_latest_org.xls", round_id=new_round,
        created_at=datetime(2026, 11, 11, 3, 3, 20, tzinfo=timezone.utc),
    )
    db_session.commit()

    with caplog.at_level("INFO", logger="backend.services.dedup_scan"):
        body = _merge(client, auth_headers, run_id)

    assert body["scan_round_id"] == new_round
    assert _merge_names(merge_recorder) == {
        "h_latest_latest_org.xls", "h_post_end_old_org.xls",
    }
    assert "hosts=h_post_end" in caplog.text


def test_b2_running_without_ended_at_keeps_1077_single_round(
    client, auth_headers, db_session, sample_plan_run, merge_recorder,
):
    """B2：RUNNING 无 ended_at，旧轮最后登记 → #1077 锚定旧轮且只取单轮。"""
    run_id = sample_plan_run.id
    old_round = "2026-11-11T02:59:00+00:00"
    new_round = "2026-11-11T03:03:07+00:00"
    _artifact(
        db_session, merge_recorder, run_id, host="h_old",
        name="h_old_old_org.xls", round_id=old_round,
        created_at=datetime(2026, 11, 11, 3, 5, 0, tzinfo=timezone.utc),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="h_new",
        name="h_new_new_org.xls", round_id=new_round,
        created_at=datetime(2026, 11, 11, 3, 3, 20, tzinfo=timezone.utc),
    )
    db_session.commit()

    body = _merge(client, auth_headers, run_id)

    assert body["scan_round_id"] == old_round
    assert _merge_names(merge_recorder) == {"h_old_old_org.xls"}


def test_b2_running_with_stale_ended_at_keeps_single_round(
    client, auth_headers, db_session, sample_plan_run, merge_recorder,
):
    """B2：RUNNING 即便残留 ended_at，也不得扩窗混轮。"""
    run_id = sample_plan_run.id
    # 旧轮在 ended_at 之后（d8b95c8b 会把两轮都纳入窗口），用于暴露混轮。
    old_round = "2026-11-11T03:01:00+00:00"
    new_round = "2026-11-11T03:03:07+00:00"
    sample_plan_run.status = "RUNNING"
    sample_plan_run.ended_at = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
    _artifact(
        db_session, merge_recorder, run_id, host="h_old",
        name="h_old_old_org.xls", round_id=old_round,
        created_at=datetime(2026, 11, 11, 3, 3, 10, tzinfo=timezone.utc),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="h_new",
        name="h_new_new_org.xls", round_id=new_round,
        created_at=datetime(2026, 11, 11, 3, 3, 20, tzinfo=timezone.utc),
    )
    db_session.commit()

    body = _merge(client, auth_headers, run_id)

    assert body["scan_round_id"] == new_round
    assert _merge_names(merge_recorder) == {"h_new_new_org.xls"}


def test_b3_running_non_iso_round_keeps_exact_id_hosts(
    client, auth_headers, db_session, sample_plan_run, merge_recorder,
):
    """B3：RUNNING 下同一非 ISO 轮号的两台主机都保留（原 ID 精确匹配）。"""
    run_id = sample_plan_run.id
    legacy = "legacy-round"
    _artifact(
        db_session, merge_recorder, run_id, host="h_legacy_a",
        name="h_legacy_a_org.xls", round_id=legacy,
        created_at=datetime(2026, 11, 11, 3, 3, 1, tzinfo=timezone.utc),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="h_legacy_b",
        name="h_legacy_b_org.xls", round_id=legacy,
        created_at=datetime(2026, 11, 11, 3, 3, 2, tzinfo=timezone.utc),
    )
    db_session.commit()

    body = _merge(client, auth_headers, run_id)

    assert body["scan_round_id"] == legacy
    assert _merge_names(merge_recorder) == {
        "h_legacy_a_org.xls", "h_legacy_b_org.xls",
    }


def test_b3_terminal_iso_anchor_excludes_other_non_iso_but_keeps_null_floor(
    client, auth_headers, db_session, sample_plan_run, merge_recorder,
):
    """B3 反向：ISO 锚点 + 其它非 ISO 轮 + NULL 行，只收锚点与下界内 NULL。"""
    run_id = sample_plan_run.id
    anchor = "2026-11-11T03:03:07+00:00"
    sample_plan_run.status = "FAILED"
    sample_plan_run.ended_at = datetime(2026, 11, 11, 3, 0, tzinfo=timezone.utc)
    _artifact(
        db_session, merge_recorder, run_id, host="h_iso",
        name="h_iso_anchor_org.xls", round_id=anchor,
        created_at=datetime(2026, 11, 11, 3, 3, 20, tzinfo=timezone.utc),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="h_other_non_iso",
        name="h_other_non_iso_org.xls", round_id="other-non-iso",
        created_at=datetime(2026, 11, 11, 3, 4, 0, tzinfo=timezone.utc),
    )
    _artifact(
        db_session, merge_recorder, run_id, host="h_null",
        name="h_null_org.xls", round_id=None,
        created_at=datetime(2026, 11, 11, 3, 4, 10, tzinfo=timezone.utc),
    )
    db_session.commit()

    body = _merge(client, auth_headers, run_id)

    assert body["scan_round_id"] == anchor
    assert _merge_names(merge_recorder) == {"h_iso_anchor_org.xls", "h_null_org.xls"}


def test_no_reliable_lower_bound_never_calls_merge_tool(
    db_session, sample_plan_run, merge_recorder,
):
    """无轮次/下界、或仅坏标记：loader 拒绝，merge 子进程不被调用。"""
    run_id = sample_plan_run.id
    _artifact(
        db_session, merge_recorder, run_id, host="h_bad",
        name="h_bad_org.xls", round_id="bad-round",
        created_at=datetime(2026, 11, 11, 3, 3, 1, tzinfo=timezone.utc),
    )
    db_session.commit()

    assert ds.run_merge_sync(
        run_id, scan_round_id="bad-round", allow_failed=True,
    ) == ""
    assert ds.run_merge_sync(run_id, allow_failed=True) == ""
    assert merge_recorder["calls"] == []
