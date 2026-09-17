"""#2188 D 步单3（#2475）：run_scan_sync 清单优先注册 + per-host legacy 过渡降级。

R-4 结构判据的机械代理：
  ① 分片齐全的 run 上主路径零产物树遍历（``Path.glob`` 触达 ``dedup/`` 即失败）；
  ② 注册主路径无文件名解析（非约定 file_key 也按分片字段注册）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.models.plan_run_artifact import PlanRunArtifact
from backend.services import dedup_scan


@pytest.fixture
def run_row(db_session):
    plan = Plan(name="scan-manifest")
    db_session.add(plan)
    db_session.flush()
    run = PlanRun(plan_id=plan.id, status="RUNNING", run_type="MANUAL",
                  plan_snapshot={"plan": {"id": plan.id}})
    db_session.add(run)
    db_session.commit()
    return run


def _write_shard(nfs: Path, run_id: int, host_id: str, artifacts: list[dict]) -> None:
    shard = nfs / "_meta" / str(run_id) / f"{host_id}.json"
    shard.parent.mkdir(parents=True, exist_ok=True)
    shard.write_text(
        json.dumps({
            "schema_version": 1,
            "host_id": host_id,
            "plan_run_id": run_id,
            "updated_at": "2026-09-17T00:00:00+00:00",
            "artifacts": artifacts,
        }),
        encoding="utf-8",
    )


def _rows(db, run_id):
    return {r.storage_uri: r for r in
            db.query(PlanRunArtifact).filter_by(plan_run_id=run_id).all()}


def test_manifest_registration_composes_legacy_uri(db_session, run_row, tmp_path):
    """分片条目注册；storage_uri 与 legacy glob 同形（共享幂等键）。"""
    _write_shard(tmp_path, run_row.id, "h1", [
        {"file_key": "h1_Result_a_org.xls", "platform": "",
         "size_bytes": 11, "registerable": True},
    ])
    n = dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1"],
    )
    assert n == "1"
    rows = _rows(db_session, run_row.id)
    expected_uri = str(tmp_path / "dedup" / str(run_row.id) / "h1_Result_a_org.xls")
    assert expected_uri in rows
    assert rows[expected_uri].host_id == "h1"
    assert rows[expected_uri].size_bytes == 11


def test_reader_does_not_parse_filenames(db_session, run_row, tmp_path):
    """R-4 ②：非约定 file_key 也按分片字段注册——主路径无文件名解析。"""
    _write_shard(tmp_path, run_row.id, "h1", [
        {"file_key": "weird/dir/oddball.bin", "platform": "mtk",
         "size_bytes": 3, "registerable": True},
    ])
    dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1"],
    )
    rows = _rows(db_session, run_row.id)
    uri = str(tmp_path / "dedup" / str(run_row.id) / "weird/dir/oddball.bin")
    assert rows[uri].host_id == "h1"


def test_registerable_false_is_skipped(db_session, run_row, tmp_path):
    _write_shard(tmp_path, run_row.id, "h1", [
        {"file_key": "mtk/h1_Result_x_dedup.xls", "platform": "mtk",
         "size_bytes": 3, "registerable": False},
    ])
    n = dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1"],
    )
    assert n == ""
    assert _rows(db_session, run_row.id) == {}


def test_manifest_read_is_idempotent(db_session, run_row, tmp_path):
    _write_shard(tmp_path, run_row.id, "h1", [
        {"file_key": "h1_Result_a_org.xls", "platform": "",
         "size_bytes": 7, "registerable": True},
    ])
    assert dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1"],
    ) == "1"
    assert dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1"],
    ) == ""


def test_degrade_registers_only_shardless_hosts(db_session, run_row, tmp_path):
    """评审未决②：h1 有分片走清单；h2 无分片才走 legacy（仅 h2 的文件）。"""
    _write_shard(tmp_path, run_row.id, "h1", [
        {"file_key": "h1_Result_a_org.xls", "platform": "",
         "size_bytes": 1, "registerable": True},
    ])
    dedup_dir = tmp_path / "dedup" / str(run_row.id)
    dedup_dir.mkdir(parents=True)
    (dedup_dir / "h2_Result_b_org.xls").write_text("b")   # 旧 Agent 产物 → legacy
    (dedup_dir / "h1_Result_stray_org.xls").write_text("s")  # h1 有分片 → legacy 不碰

    n = dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1", "h2"],
    )
    assert n == "2"
    rows = _rows(db_session, run_row.id)
    assert str(dedup_dir / "h1_Result_a_org.xls") in rows
    assert str(dedup_dir / "h2_Result_b_org.xls") in rows
    assert str(dedup_dir / "h1_Result_stray_org.xls") not in rows


def test_shardless_and_fileless_host_is_pending_not_error(db_session, run_row, tmp_path):
    """「还没写」：无分片且无文件 → 不注册、不报错（轮询预算内继续等）。"""
    assert dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["ghost"],
    ) == ""


def test_expected_hosts_none_keeps_full_legacy_sweep(db_session, run_row, tmp_path):
    """既有调用点（不传 expected_hosts）保持全目录 legacy 扫描（向后兼容）。"""
    dedup_dir = tmp_path / "dedup" / str(run_row.id)
    dedup_dir.mkdir(parents=True)
    (dedup_dir / "h1_Result_a_org.xls").write_text("a")
    (dedup_dir / "h2_Result_b_org.xls").write_text("b")

    n = dedup_scan._sync_scan_artifacts(db_session, run_row.id, str(tmp_path))
    assert n == "2"
    assert len(_rows(db_session, run_row.id)) == 2


def test_corrupt_and_foreign_shards_tolerated(db_session, run_row, tmp_path):
    bad = tmp_path / "_meta" / str(run_row.id) / "broken.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    v2 = tmp_path / "_meta" / str(run_row.id) / "future.json"
    v2.write_text(json.dumps({"schema_version": 99, "host_id": "h2",
                              "plan_run_id": run_row.id, "artifacts": []}),
                  encoding="utf-8")
    _write_shard(tmp_path, run_row.id, "h1", [
        {"file_key": "h1_Result_a_org.xls", "platform": "",
         "size_bytes": 1, "registerable": True},
    ])

    n = dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1"],
    )
    assert n == "1"


def test_r4_no_glob_touches_dedup_tree_when_shards_complete(
    db_session, run_row, tmp_path, monkeypatch,
):
    """R-4 ①：分片齐全的 run 上主路径零产物树遍历（glob 触达 dedup/ 即失败）。"""
    _write_shard(tmp_path, run_row.id, "h1", [
        {"file_key": "h1_Result_a_org.xls", "platform": "",
         "size_bytes": 1, "registerable": True},
    ])

    real_glob = Path.glob

    def guard(self, pattern):
        if "dedup" in self.parts:
            raise AssertionError(f"主路径遍历产物树: {self}.glob({pattern!r})")
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", guard)
    n = dedup_scan._sync_scan_artifacts(
        db_session, run_row.id, str(tmp_path), expected_hosts=["h1"],
    )
    assert n == "1"
    assert len(_rows(db_session, run_row.id)) == 1
