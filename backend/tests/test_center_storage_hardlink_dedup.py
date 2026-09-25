"""#3308：中心存储 devices/ 跨 run 硬链接去重（过渡项 center-storage-interim-dedup）的安全性质。

夹具形态镜像生产上送目标 ``devices/{run_id}/{event_id}/{basename}``（artifact_uploader）。
负向用例优先：每条安全约束都有一个「不该链接」的构造，证明判据有判别力。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from backend.models.plan_run import PlanRun
from backend.scripts import center_storage_hardlink_dedup as dd

OLD = time.time() - 3 * 24 * 3600  # 早于缺省 24h 文件年龄门槛
SIZE = 3 * dd.EDGE  # 大于首尾两段之和：快速哈希只覆盖首尾，中间只有全量哈希看得到


def _blob(seed: int, size: int = SIZE) -> bytes:
    return bytes((seed + i) % 251 for i in range(size))


def _write(root: Path, run: int, rel: str, data: bytes, *, mtime: float = OLD) -> Path:
    p = root / str(run) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.utime(p, (mtime, mtime))
    return p


def _ino(p: Path) -> int:
    return os.lstat(p).st_ino


@pytest.fixture
def devices(tmp_path: Path) -> Path:
    root = tmp_path / "devices"
    root.mkdir()
    return root


def test_dry_run_reports_but_changes_nothing(devices):
    a = _write(devices, 1, "e1/db.dump", _blob(1))
    b = _write(devices, 2, "e1/db.dump", _blob(1))
    lines: list[str] = []

    s = dd.dedup(devices, [1, 2], out=lines.append)

    assert (s.dup_groups, s.linked, s.freed_bytes) == (1, 1, SIZE)
    assert _ino(a) != _ino(b)
    assert lines == []


def test_execute_links_duplicate_keeping_paths_and_content(devices):
    a = _write(devices, 1, "e1/db.dump", _blob(1))
    b = _write(devices, 2, "e9/db.dump", _blob(1))
    lines: list[str] = []

    s = dd.dedup(devices, [2, 1], execute=True, out=lines.append)

    assert (s.linked, s.freed_bytes, s.skipped) == (1, SIZE, 0)
    assert _ino(a) == _ino(b) and os.lstat(a).st_nlink == 2
    assert b.read_bytes() == _blob(1)
    assert lines == [f"LINKED\t{b}\t<=\t{a}\t{SIZE}"]  # canonical = (run, path) 最小者


def test_same_edges_different_middle_is_not_linked(devices):
    data = bytearray(_blob(1))
    a = _write(devices, 1, "e1/x", bytes(data))
    data[SIZE // 2] ^= 0xFF
    b = _write(devices, 2, "e1/x", bytes(data))
    assert dd.quick_hash(str(a), SIZE) == dd.quick_hash(str(b), SIZE)  # 构造确实只差在中段

    s = dd.dedup(devices, [1, 2], execute=True)

    assert (s.dup_groups, s.linked) == (0, 0)
    assert _ino(a) != _ino(b)


def test_small_recent_symlink_and_unlisted_run_are_left_alone(devices):
    big_a = _write(devices, 1, "e1/big", _blob(7))
    big_b = _write(devices, 2, "e1/big", _blob(7))
    small_a = _write(devices, 1, "e2/small", _blob(3, 100))
    small_b = _write(devices, 2, "e2/small", _blob(3, 100))
    now = time.time()
    fresh_a = _write(devices, 1, "e3/fresh", _blob(5), mtime=now)
    fresh_b = _write(devices, 2, "e3/fresh", _blob(5), mtime=now)
    link = devices / "2" / "e4" / "via-link"
    link.parent.mkdir(parents=True)
    link.symlink_to(big_a)
    unlisted = _write(devices, 3, "e1/big", _blob(7))

    s = dd.dedup(devices, [1, 2], execute=True)

    assert s.linked == 1 and _ino(big_a) == _ino(big_b)
    assert _ino(small_a) != _ino(small_b)
    assert _ino(fresh_a) != _ino(fresh_b)
    assert link.is_symlink()
    assert _ino(unlisted) != _ino(big_a)


def test_symlink_is_never_a_candidate_even_when_size_and_age_pass(devices):
    target = _write(devices, 1, "e1/x", _blob(1))
    link = devices / "1" / "e1" / "x-link"
    link.symlink_to(target)
    os.utime(link, (OLD, OLD), follow_symlinks=False)  # 让软链自身也过得了年龄门槛

    by_size, _ = dd.collect_candidates(
        devices, [1], min_size=1, mtime_cutoff=time.time() - 3600, execute=False,
    )

    paths = {c.path for items in by_size.values() for c in items}
    assert str(target) in paths and str(link) not in paths


def test_rerun_is_idempotent(devices):
    _write(devices, 1, "e1/x", _blob(1))
    _write(devices, 2, "e1/x", _blob(1))
    dd.dedup(devices, [1, 2], execute=True)

    again = dd.dedup(devices, [1, 2], execute=True)

    assert (again.dup_groups, again.linked, again.skipped) == (0, 0, 0)


def test_file_changed_after_scan_is_skipped(devices):
    a = _write(devices, 1, "e1/x", _blob(1))
    b = _write(devices, 2, "e1/x", _blob(1))
    by_size, _ = dd.collect_candidates(
        devices, [1, 2], min_size=dd.EDGE, mtime_cutoff=time.time() - 3600, execute=False,
    )
    groups = dd.find_duplicate_groups(by_size)
    os.utime(b, (OLD + 60, OLD + 60))  # 扫描与替换之间被改动

    s = dd.link_groups(groups, execute=True)

    assert (s.linked, s.skipped) == (0, 1)
    assert _ino(a) != _ino(b)


def test_stale_tmp_link_is_removed_only_on_execute(devices):
    tmp = _write(devices, 1, "e1/x" + dd.TMP_SUFFIX, b"leftover")

    assert dd.dedup(devices, [1]).stale_tmp == 1 and tmp.exists()
    assert dd.dedup(devices, [1], execute=True).stale_tmp == 1 and not tmp.exists()


def test_dry_run_frees_a_shared_inode_only_once(devices):
    a = _write(devices, 1, "e1/x", _blob(1))
    b = _write(devices, 2, "e1/x", _blob(1))
    c = devices / "3" / "e1" / "x"
    c.parent.mkdir(parents=True)
    os.link(b, c)  # b、c 已共享一个 inode：换掉两条链接后它的块才真正释放

    dry = dd.dedup(devices, [1, 2, 3])
    real = dd.dedup(devices, [1, 2, 3], execute=True)

    assert (dry.linked, dry.freed_bytes) == (2, SIZE)
    assert (real.linked, real.freed_bytes) == (2, SIZE)
    assert _ino(a) == _ino(b) == _ino(c)


def test_refuses_root_other_than_devices(tmp_path, capsys):
    jira = tmp_path / "jira"
    jira.mkdir()
    runs_file = tmp_path / "runs.json"
    runs_file.write_text("[1]", encoding="utf-8")

    with pytest.raises(ValueError):
        dd.dedup(jira, [1])
    assert dd.main(["--root", str(jira), "--runs-file", str(runs_file)]) == 2
    assert "devices" in capsys.readouterr().err


def test_cli_runs_file_end_to_end(devices, tmp_path, capsys):
    a = _write(devices, 1, "e1/x", _blob(1))
    b = _write(devices, 2, "e1/x", _blob(1))
    runs_file = tmp_path / "runs.json"
    runs_file.write_text(json.dumps([1, 2]), encoding="utf-8")

    rc = dd.main(["--root", str(devices), "--runs-file", str(runs_file), "--execute"])

    out, err = capsys.readouterr()
    assert rc == 0 and _ino(a) == _ino(b)
    assert out.startswith("LINKED\t")
    assert "== EXECUTED: runs=2 dup_groups=1 files_linked=1" in err


def _plan_run(db_session, plan_id: int, status: str, ended_at):
    run = PlanRun(
        plan_id=plan_id, status=status, plan_snapshot={"plan_id": plan_id},
        run_type="MANUAL", triggered_by="pytest", ended_at=ended_at,
    )
    db_session.add(run)
    db_session.flush()
    return run.id


def test_eligible_runs_are_terminal_and_old_enough(db_session, sample_plan):
    now = datetime.now(timezone.utc)
    eligible = {
        _plan_run(db_session, sample_plan.id, "SUCCESS", now - timedelta(days=3)),
        _plan_run(db_session, sample_plan.id, "PARTIAL_SUCCESS", now - timedelta(days=2)),
        _plan_run(db_session, sample_plan.id, "FAILED", now - timedelta(hours=30)),
    }
    excluded = {
        _plan_run(db_session, sample_plan.id, "SUCCESS", now - timedelta(hours=1)),
        _plan_run(db_session, sample_plan.id, "RUNNING", None),
        # 病态行：状态未终态但带了 ended_at——只能靠状态谓词挡住
        _plan_run(db_session, sample_plan.id, "RUNNING", now - timedelta(days=3)),
    }

    got = set(dd.eligible_runs_from_db(db_session.connection(), min_run_age_hours=24, now=now))

    assert eligible <= got
    assert not (excluded & got)


def test_readonly_engine_connection_is_read_only():
    engine = dd.open_readonly_engine(os.environ["TEST_DATABASE_URL"])
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on"
    finally:
        engine.dispose()
