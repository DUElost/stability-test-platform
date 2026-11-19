"""B8 U5（#4045 第 2 刀）：``compat_reruns/`` 派生族的逐笔留存判定与人工确认回收。

每条判据都对回 #4045 验收第 2 条与 #3800 §2.1 第 3 条的一句话：

- 只列「终态完成 + 两侧都超期 + 无 I4 保护 + 无链引用」的独立产物；
- **单边超期、完成时间未知、在途重试、链上引用、run 行缺失**都必须保留；
- ``FAILED`` 作为事故证据一律保留，且理由必须是 ``terminal_failed_evidence``，
  不得伪装成「完成时间未知」；
- 已回收项单独成栏，不再进入保留栏制造噪音（裁决 3 = 3a）；
- 回收只按显式 rerun id、只删叶子与空父目录，共享源 / ``evidence/`` / ``devices/`` /
  其它补跑产物一律不动，登记行不删、状态不改；
- 空列表与超批次上限**整笔拒绝**；Dry-run 的输出不构成删除授权；
- 候选为空时必须能区分「旋钮问题」与「真的干净」（裁决 1 = (a)+(c)）；
- 每个候选条件都有变异自证：改动一个条件即从候选变保留。

DB 为隔离 PG16，文件系统在临时根；不连生产、不读凭据。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from backend.models.audit import AuditLog
from backend.models.issue_fact import CompatRerun
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import compat_rerun_retention as rr
from backend.services import issue_fact_store as facts

PLATFORM = "mtk"
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
DAYS = 30


def _run(db, *, status="SUCCESS", started_days_ago=400, parent=None, root=None):
    plan = Plan(name=f"u5-{uuid4().hex[:6]}", created_by="pytest")
    db.add(plan)
    db.flush()
    run = PlanRun(
        plan_id=plan.id, status=status, plan_snapshot={}, run_type="MANUAL",
        triggered_by="pytest", started_at=NOW - timedelta(days=started_days_ago),
        parent_plan_run_id=parent.id if parent is not None else None,
        root_plan_run_id=root.id if root is not None else None,
    )
    db.add(run)
    db.flush()
    return run


def _leaf(root: Path, run_id: int, rerun_id: int) -> Path:
    return root / "compat_reruns" / str(run_id) / PLATFORM / f"r{rerun_id}"


def _materialize(leaf: Path) -> Path:
    event = leaf / "events" / "COLLECT_DIR" / "SN-1" / "aee_exp" / "2026_1119_120000_1_db.74.ANR"
    event.mkdir(parents=True)
    (event / "main.dbg").write_bytes(b"x" * 10)
    (leaf / "Result_MergeFiles_Compat.xls").write_bytes(b"xls" * 3)
    (leaf / "compat_manifest.json").write_text(json.dumps({"schema": "test"}), encoding="utf-8")
    return leaf


def _rerun(
    db, root, run, *, status=None, completed_days_ago=100, with_dir=True,
    source_records=None, missing_items=None, version_no=3,
):
    """一笔补跑登记 + 它的产物目录；返回 ``(row, leaf, shared_source)``。"""
    row = facts.record_compat_rerun(
        db, plan_run_id=run.id, platform=PLATFORM,
        request_key=f"u5-{uuid4().hex[:8]}", requested_by="owner",
        source_delivery_version_no=version_no,
    )
    db.flush()
    leaf = _leaf(root, run.id, int(row.id))
    shared = root / "dedup" / str(run.id) / "evt" / "main.dbg"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_bytes(b"shared-source")
    if with_dir:
        _materialize(leaf)
    copied = leaf / "events" / "COLLECT_DIR" / "SN-1" / "aee_exp" / "2026_1119_120000_1_db.74.ANR"
    facts.update_compat_rerun(
        db, row=row,
        status=status or facts.COMPAT_RERUN_STATUS_COMPLETED,
        attempts=1,
        completed_at=(
            NOW - timedelta(days=completed_days_ago) if completed_days_ago is not None else None
        ),
        artifact_dir=str(leaf),
        merge_xls_path=str(leaf / "Result_MergeFiles_Compat.xls"),
        manifest_path=str(leaf / "compat_manifest.json"),
        coverage_scope={"covered_events": 1, "missing_events": 0 if not missing_items else 1},
        source_records=source_records if source_records is not None else [{
            "dle_id": str(uuid4()), "source_kind": "center_evidence_verified",
            "i4_status": "i4_center_verified", "original_dle_state": "ARCHIVED",
            "source_path": str(shared), "copied_path": str(copied),
            "digest_sha256": "a" * 64,
        }],
        missing_items=missing_items or [],
        is_partial=bool(missing_items),
    )
    db.flush()
    return row, leaf, shared


def _evaluate(db, root, **kwargs):
    kwargs.setdefault("retention_days", DAYS)
    return rr.evaluate_compat_rerun_retention(db, now=NOW, log_root=str(root), **kwargs)


def _reasons_for(report, rerun_id):
    hits = [item for item in report["retained"] if item["rerun_id"] == rerun_id]
    assert len(hits) == 1, hits
    return set(hits[0]["reasons"])


def test_eligible_unit_is_listed_with_four_way_cross_check(db_session, tmp_path):
    """双超期且无保护 → 候选，且四路对拍字段齐（候选目录↔原交付↔证据↔共享源）。"""
    root = tmp_path / "log"
    run = _run(db_session)
    row, leaf, shared = _rerun(db_session, root, run)
    publication_run = _run(db_session)
    assert publication_run.id != run.id

    report = _evaluate(db_session, root)
    assert report["counts"]["candidates"] == 1 and report["counts"]["retained"] == 0
    item = report["candidates"][0]
    assert item["rerun_id"] == int(row.id) and item["plan_run_id"] == run.id
    assert item["artifact_dir"] == str(leaf)
    assert item["bytes"] > 0 and item["files"] == 3 and item["dirs"] >= 5
    assert item["merge_xls_present"] is True and item["manifest_present"] is True
    assert item["source_delivery_version"] == {"id": None, "no": 3}
    assert item["completed_at"].startswith("2026-08")
    # 共享源：回收只删 copied_path 那一侧，source_path 永不触碰。
    assert item["shared_sources"][0]["source_path"] == str(shared)
    assert item["shared_sources"][0]["copied_path"].startswith(str(leaf))
    assert item["evidence"] == {"total": 0, "verified": 0, "unresolved": 0}
    assert report["anchor_days"] == DAYS and report["anchor_note"] is None


@pytest.mark.parametrize(
    "completed_days_ago,started_days_ago,expected",
    [
        (5, 400, {rr.REASON_RERUN_WITHIN_RETENTION}),      # 只有补跑侧没到期
        (100, 5, {rr.REASON_PLAN_RUN_WITHIN_RETENTION}),   # 只有 run 侧没到期
    ],
)
def test_single_side_expiry_is_retained(db_session, tmp_path, completed_days_ago, started_days_ago, expected):
    root = tmp_path / "log"
    run = _run(db_session, started_days_ago=started_days_ago)
    row, _leaf, _shared = _rerun(db_session, root, run, completed_days_ago=completed_days_ago)

    report = _evaluate(db_session, root)
    assert report["counts"]["candidates"] == 0
    assert _reasons_for(report, int(row.id)) == expected


def test_unknown_completion_time_and_non_terminal_run_are_retained(db_session, tmp_path):
    root = tmp_path / "log"
    done_no_stamp = _run(db_session)
    row1, _l1, _s1 = _rerun(db_session, root, done_no_stamp, completed_days_ago=None)
    live_run = _run(db_session, status="RUNNING", started_days_ago=400)
    row2, _l2, _s2 = _rerun(db_session, root, live_run)

    report = _evaluate(db_session, root)
    assert _reasons_for(report, int(row1.id)) == {rr.REASON_COMPLETION_UNKNOWN}
    assert _reasons_for(report, int(row2.id)) == {rr.REASON_PLAN_RUN_NOT_TERMINAL}


def test_in_flight_and_failed_have_distinct_reasons(db_session, tmp_path):
    """在途≠失败：``FAILED`` 的理由必须是「终态失败作证据」，不得伪装成完成时间未知。"""
    root = tmp_path / "log"
    run_pending = _run(db_session)
    row_p, _lp, _sp = _rerun(db_session, root, run_pending, status=facts.COMPAT_RERUN_STATUS_PENDING)
    run_running = _run(db_session)
    row_r, _lr, _sr = _rerun(db_session, root, run_running, status=facts.COMPAT_RERUN_STATUS_RUNNING)
    run_failed = _run(db_session)
    row_f, _lf, _sf = _rerun(
        db_session, root, run_failed, status=facts.COMPAT_RERUN_STATUS_FAILED,
        completed_days_ago=None,
    )

    report = _evaluate(db_session, root)
    assert _reasons_for(report, int(row_p.id)) == {rr.REASON_IN_FLIGHT}
    assert _reasons_for(report, int(row_r.id)) == {rr.REASON_IN_FLIGHT}
    reasons = _reasons_for(report, int(row_f.id))
    assert reasons == {rr.REASON_FAILED_EVIDENCE}
    assert rr.REASON_COMPLETION_UNKNOWN not in reasons


@pytest.mark.parametrize("kind", ["publication_retrying", "round_open"])
def test_i4_protection_retains_whole_run(db_session, tmp_path, kind):
    root = tmp_path / "log"
    run = _run(db_session)
    row, _leaf, _shared = _rerun(db_session, root, run)
    if kind == "publication_retrying":
        publication = facts.record_evidence_publication(
            db_session, plan_run_id=run.id, platform=PLATFORM, dle_id=uuid4(),
            root_plan_run_id=run.id, source_kind=facts.PUBLICATION_SOURCE_LOCAL,
            source_path="/x", collection_folder="COLLECT_DIR", naming_rule_version="v1",
            destination_rel_path="evidence/1/SN/evt",
            state=facts.PUBLICATION_STATE_PENDING,
        )
        facts.update_evidence_publication_state(
            db_session, row=publication, state=facts.PUBLICATION_STATE_RETRYING,
        )
    else:
        round_row = facts.record_scan_round(
            db_session, plan_run_id=run.id, platform=PLATFORM, round_no=9,
            kind=facts.SCAN_ROUND_KIND_SUPPLEMENT, expected_set=[],
            input_cutoff_at=NOW - timedelta(days=1),
        )
        facts.set_scan_round_status(db_session, round_id=round_row.id, status="RUNNING")
    db_session.flush()

    report = _evaluate(db_session, root)
    assert report["counts"]["candidates"] == 0
    assert _reasons_for(report, int(row.id)) == {rr.REASON_I4_PROTECTED}


def test_chain_reference_and_missing_run_are_retained_and_counted(db_session, tmp_path):
    """链上引用保留；run 行缺失=「不可证明」，必须**单列计数**而不是被当成干净。"""
    root = tmp_path / "log"
    referenced = _run(db_session)
    row_chain, _lc, _sc = _rerun(db_session, root, referenced)
    _dependent = _run(db_session, parent=referenced, root=referenced)

    orphan_run = _run(db_session)
    row_orphan, _lo, _so = _rerun(db_session, root, orphan_run)
    orphan_run_id = orphan_run.id
    db_session.delete(orphan_run)
    db_session.flush()
    # run 行没了，登记行仍在（`plan_run_id` 无外键、也无任何清理器删它）——这就是
    # 「不可证明」会永久累积的根因，必须作为事实钉住，而不是留给注释去相信。
    assert db_session.query(CompatRerun).filter_by(id=int(row_orphan.id)).count() == 1
    assert db_session.query(PlanRun).filter_by(id=orphan_run_id).count() == 0

    report = _evaluate(db_session, root)
    assert report["counts"]["candidates"] == 0
    assert _reasons_for(report, int(row_chain.id)) == {rr.REASON_CHAIN_REFERENCED}
    assert _reasons_for(report, int(row_orphan.id)) == {rr.REASON_PLAN_RUN_MISSING}
    assert report["counts"]["unprovable_missing_run"] == 1
    # 被引用者不能因为「本批里没人引用我」就消失：引用方在批外也要算（批外 run 已建）。
    db_session.expire_all()


@pytest.mark.parametrize("shape", ["absent", "symlink"])
def test_absent_or_symlinked_artifact_is_retained(db_session, tmp_path, shape):
    root = tmp_path / "log"
    run = _run(db_session)
    row, leaf, _shared = _rerun(db_session, root, run, with_dir=(shape != "absent"))
    if shape == "symlink":
        # 整棵已生成的子树搬到族根之外，再把原位置做成 symlink：解析后逃逸 ⇒ 不可回收。
        leaf.rename(root / "elsewhere")
        leaf.symlink_to(root / "elsewhere", target_is_directory=True)

    report = _evaluate(db_session, root)
    assert report["counts"]["candidates"] == 0
    assert _reasons_for(report, int(row.id)) == {rr.REASON_ARTIFACT_UNSAFE}


def test_purged_marker_moves_the_row_out_of_retained(db_session, tmp_path):
    """裁决 3 = 3a：已回收项单列，不再以「目录不存在」的异常形态污染保留栏。"""
    root = tmp_path / "log"
    run = _run(db_session)
    row, _leaf, _shared = _rerun(db_session, root, run, with_dir=False)

    before = _evaluate(db_session, root)
    assert _reasons_for(before, int(row.id)) == {rr.REASON_ARTIFACT_UNSAFE}

    facts.mark_compat_rerun_artifact_purged(db_session, row=row, now=NOW)
    db_session.flush()
    after = _evaluate(db_session, root)
    assert after["counts"]["retained"] == 0 and after["counts"]["purged"] == 1
    assert after["purged"][0]["rerun_id"] == int(row.id)
    assert after["purged"][0]["purged_at"] == NOW.isoformat()

    # 重复标记不改第一次时间（回收是一次性事实）。
    facts.mark_compat_rerun_artifact_purged(db_session, row=row, now=NOW + timedelta(days=1))
    db_session.flush()
    assert _evaluate(db_session, root)["purged"][0]["purged_at"] == NOW.isoformat()


def test_purge_removes_only_named_leaf_and_keeps_everything_else(db_session, tmp_path):
    root = tmp_path / "log"
    run = _run(db_session)
    row_a, leaf_a, shared_a = _rerun(db_session, root, run)
    row_b, leaf_b, shared_b = _rerun(db_session, root, run)
    sibling_run = _run(db_session)
    _row_c, leaf_c, shared_c = _rerun(db_session, root, sibling_run)
    evidence_dir = root / "evidence" / "1_DIR" / "SN-1" / "evt"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "main.dbg").write_bytes(b"evidence")
    devices_dir = root / "devices" / str(run.id)
    devices_dir.mkdir(parents=True)
    (devices_dir / "evt.txt").write_text("device")

    result = rr.purge_compat_rerun_artifacts(
        db_session, rerun_ids=[int(row_a.id)], operator="alice",
        now=NOW, retention_days=DAYS, log_root=str(root),
    )
    assert [item["rerun_id"] for item in result["removed"]] == [int(row_a.id)]
    assert not leaf_a.exists()
    assert leaf_b.is_dir() and leaf_c.is_dir()          # 未点名的不动
    assert (root / "compat_reruns" / str(run.id) / PLATFORM).is_dir()  # 父目录仍非空
    assert shared_a.is_file() and shared_b.is_file() and shared_c.is_file()
    assert (evidence_dir / "main.dbg").read_bytes() == b"evidence"
    assert (devices_dir / "evt.txt").read_text() == "device"
    assert (root / "compat_reruns").is_dir()            # 族根永不删

    db_session.refresh(row_a)
    assert row_a.artifact_purged_at is not None
    assert row_a.status == facts.COMPAT_RERUN_STATUS_COMPLETED  # 状态不改、行不删
    audits = db_session.query(AuditLog).filter_by(action="compat_rerun_purge").all()
    assert len(audits) == 1 and audits[0].username == "alice"
    assert audits[0].details["artifact_dir"] == str(leaf_a)

    # 第二笔回收后，平台/run 空父目录才被清掉。
    second = rr.purge_compat_rerun_artifacts(
        db_session, rerun_ids=[int(row_b.id)], operator="bob",
        now=NOW, retention_days=DAYS, log_root=str(root),
    )
    assert [item["rerun_id"] for item in second["removed"]] == [int(row_b.id)]
    assert not (root / "compat_reruns" / str(run.id)).exists()
    assert (root / "compat_reruns" / str(sibling_run.id) / PLATFORM).is_dir()


def test_purge_rejects_empty_selection_and_batch_limit_without_partial_work(db_session, tmp_path):
    root = tmp_path / "log"
    run = _run(db_session)
    row_a, leaf_a, _s = _rerun(db_session, root, run)
    row_b, leaf_b, _s = _rerun(db_session, root, run)

    with pytest.raises(rr.CompatRerunRetentionError) as empty:
        rr.purge_compat_rerun_artifacts(db_session, rerun_ids=[], operator="alice")
    assert empty.value.code == "EMPTY_SELECTION"

    with pytest.raises(rr.CompatRerunRetentionError) as over:
        rr.purge_compat_rerun_artifacts(
            db_session, rerun_ids=[int(row_a.id), int(row_b.id)], operator="alice",
            batch_limit=1, log_root=str(root),
        )
    assert over.value.code == "BATCH_LIMIT_EXCEEDED"
    assert leaf_a.is_dir() and leaf_b.is_dir()  # 整笔拒绝，一个都不删


def test_dry_run_output_is_not_a_deletion_authorization(db_session, tmp_path):
    """先看到候选，再把状态改成未到期 → 同一 id 的回收必须被拒，目录仍在。"""
    root = tmp_path / "log"
    run = _run(db_session)
    row, leaf, _shared = _rerun(db_session, root, run)
    assert _evaluate(db_session, root)["counts"]["candidates"] == 1

    facts.update_compat_rerun(
        db_session, row=row, status=facts.COMPAT_RERUN_STATUS_COMPLETED,
        attempts=1, completed_at=NOW - timedelta(days=1),
    )
    db_session.flush()
    result = rr.purge_compat_rerun_artifacts(
        db_session, rerun_ids=[int(row.id)], operator="alice",
        now=NOW, retention_days=DAYS, log_root=str(root),
    )
    assert result["removed"] == []
    assert result["skipped"][0]["reasons"] == [rr.REASON_RERUN_WITHIN_RETENTION]
    assert leaf.is_dir()


@pytest.mark.parametrize(
    "days,expect_note",
    [
        (36500, "候选几乎必然为空"),      # 生产覆盖值：旋钮问题，不是真的干净
        (0, "cutoff=now"),                 # 0 = 该层全量到期（既有家族语义）
    ],
)
def test_anchor_note_makes_an_empty_candidate_list_explainable(db_session, tmp_path, days, expect_note):
    root = tmp_path / "log"
    run = _run(db_session, started_days_ago=100)
    _row, _leaf, _shared = _rerun(db_session, root, run, completed_days_ago=90)

    report = _evaluate(db_session, root, retention_days=days)
    assert report["anchor_note"] and expect_note in report["anchor_note"]
    # days=0 时两侧都到期 → 候选应有；days=36500 时必然为空。两者读数同为 0/1，
    # 但只有带 anchor_note 的报表才能区分「没有垃圾」与「期限被设成一百年」。
    assert report["counts"]["candidates"] == (1 if days == 0 else 0)


def test_each_candidate_condition_is_mutation_proven(db_session, tmp_path):
    """同一份合格数据，逐项打开一个条件 → 每一次都从候选变保留，且理由串精确。"""
    root = tmp_path / "log"

    def fresh():
        run = _run(db_session)
        row, _leaf, _shared = _rerun(db_session, root, run)
        return run, row

    run, row = fresh()
    assert _evaluate(db_session, root)["counts"]["candidates"] == 1

    flips = []
    # 1) 补跑侧回到期内
    facts.update_compat_rerun(
        db_session, row=row, status=facts.COMPAT_RERUN_STATUS_COMPLETED, attempts=1,
        completed_at=NOW - timedelta(days=1),
    )
    flips.append(_reasons_for(_evaluate(db_session, root), int(row.id)))
    facts.update_compat_rerun(
        db_session, row=row, status=facts.COMPAT_RERUN_STATUS_COMPLETED, attempts=1,
        completed_at=NOW - timedelta(days=100),
    )
    # 2) run 非终态
    run.status = "RUNNING"
    db_session.flush()
    flips.append(_reasons_for(_evaluate(db_session, root), int(row.id)))
    run.status = "SUCCESS"
    db_session.flush()
    # 3) I4：留一个未闭的补充轮
    round_row = facts.record_scan_round(
        db_session, plan_run_id=run.id, platform=PLATFORM, round_no=1,
        kind=facts.SCAN_ROUND_KIND_FINAL, expected_set=[], input_cutoff_at=NOW,
    )
    flips.append(_reasons_for(_evaluate(db_session, root), int(row.id)))
    facts.set_scan_round_status(db_session, round_id=round_row.id, status="CLOSED", closed_at=NOW)
    db_session.flush()
    # 4) 链上引用
    dependent = _run(db_session, parent=run, root=run)
    flips.append(_reasons_for(_evaluate(db_session, root), int(row.id)))
    # 撤掉引用方，让下一步的翻转只反映「目录不见」这一个条件。
    db_session.delete(dependent)
    db_session.flush()
    # 5) 产物目录不见（真实删除，不是把路径改成不存在）
    shutil.rmtree(_leaf(root, run.id, int(row.id)))
    flips.append(_reasons_for(_evaluate(db_session, root), int(row.id)))

    assert flips[0] == {rr.REASON_RERUN_WITHIN_RETENTION}
    assert flips[1] == {rr.REASON_PLAN_RUN_NOT_TERMINAL}
    assert flips[2] == {rr.REASON_I4_PROTECTED}
    assert flips[3] == {rr.REASON_CHAIN_REFERENCED}
    assert flips[4] == {rr.REASON_ARTIFACT_UNSAFE}
    # 反证：全部条件都不打开时，同根下新的一笔仍是候选。
    run2, row2 = fresh()
    assert int(row2.id) in {item["rerun_id"] for item in _evaluate(db_session, root)["candidates"]}
