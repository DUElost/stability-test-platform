"""ADR-0026 — INIT→PATROL barrier wired into PipelineEngine lifecycle."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from backend.agent.coordinator import HostRunCoordinator
from backend.agent.pipeline_engine import PipelineEngine


class FakeScriptRegistry:
    def __init__(self, path: str):
        self.path = path

    def resolve(self, name: str, version: str):
        return SimpleNamespace(
            script_id=1,
            name=name,
            version=version,
            script_type="python",
            nfs_path=self.path,
            content_sha256="c" * 64,
        )


def _write_script(path, source: str) -> str:
    path.write_text(source, encoding="utf-8")
    return str(path)


def _lifecycle(init_ok: bool = True, patrol_cycles: int = 1) -> dict:
    init_action = "script:ok" if init_ok else "script:fail"
    return {
        "lifecycle": {
            "init": [
                {
                    "step_id": "init_step",
                    "action": init_action,
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 5,
                }
            ],
            "patrol": {
                "interval_seconds": 0,
                "steps": [
                    {
                        "step_id": "patrol_step",
                        "action": "script:ok",
                        "version": "1.0.0",
                        "params": {},
                        "timeout_seconds": 5,
                    }
                ],
            },
            "teardown": [],
            # End patrol quickly via timeout after first cycle.
            "timeout_seconds": 1 if patrol_cycles else 0,
        }
    }


def _make_engine(
    tmp_path,
    *,
    run_id: int,
    coordinator: HostRunCoordinator,
    barrier_total: int,
    registry: FakeScriptRegistry,
):
    return PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial=f"SER{run_id}",
        run_id=run_id,
        script_registry=registry,
        coordinator=coordinator,
        plan_run_host_id=1,
        barrier_total=barrier_total,
        # No API URL → skip lease verify.
        api_url=None,
    )


def test_pipeline_init_patrol_barrier_aligns_peers(tmp_path, monkeypatch):
    """Three jobs finish init at different times; patrol starts only after all arrive."""
    ok = _write_script(tmp_path / "ok.py", "print('ok')")
    registry = FakeScriptRegistry(ok)
    coord = HostRunCoordinator("http://x", "h1", "inst")
    coord.register_plan_run_host(1, 10)

    # Short barrier poll; patrol timeout ends the loop.
    monkeypatch.setenv("STP_BARRIER_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("STP_PHASE_BARRIER_ENABLED", "1")

    patrol_entered = []
    lock = threading.Lock()
    original_patrol = PipelineEngine._run_patrol_loop

    def _tracking_patrol(self, *args, **kwargs):
        with lock:
            patrol_entered.append(time.monotonic())
        # End immediately — we only care that barrier released before patrol.
        return "completed", ""

    monkeypatch.setattr(PipelineEngine, "_run_patrol_loop", _tracking_patrol)

    results = [None, None, None]

    def worker(idx: int):
        engine = _make_engine(
            tmp_path,
            run_id=100 + idx,
            coordinator=coord,
            barrier_total=3,
            registry=registry,
        )
        # Stagger init completion slightly via sleep before execute's barrier.
        time.sleep(0.05 * idx)
        results[idx] = engine.execute(_lifecycle())

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert all(r is not None and r.success for r in results)
    assert len(patrol_entered) == 3
    # All three entered patrol within a tight window after the barrier.
    assert max(patrol_entered) - min(patrol_entered) < 1.0
    assert coord._plan_run_hosts[1].phase == "PATROL"
    # Avoid unused warning if monkeypatch keeps reference.
    assert original_patrol is not None


def test_pipeline_init_failure_still_counts_toward_barrier(tmp_path, monkeypatch):
    """A failed init must arrive so successful peers are not stuck."""
    ok = _write_script(tmp_path / "ok.py", "print('ok')")
    fail = _write_script(tmp_path / "fail.py", "import sys; sys.exit(1)")

    class DualRegistry:
        def resolve(self, name: str, version: str):
            path = fail if name == "fail" else ok
            return SimpleNamespace(
                script_id=1,
                name=name,
                version=version,
                script_type="python",
                nfs_path=path,
                content_sha256="c" * 64,
            )

    coord = HostRunCoordinator("http://x", "h1", "inst")
    coord.register_plan_run_host(1, 10)
    monkeypatch.setenv("STP_BARRIER_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("STP_PHASE_BARRIER_ENABLED", "1")

    patrol_started = threading.Event()

    def _tracking_patrol(self, *args, **kwargs):
        patrol_started.set()
        return "completed", ""

    monkeypatch.setattr(PipelineEngine, "_run_patrol_loop", _tracking_patrol)

    def failing_job():
        engine = PipelineEngine(
            adb=SimpleNamespace(adb_path="adb"),
            serial="FAIL",
            run_id=1,
            script_registry=DualRegistry(),
            coordinator=coord,
            plan_run_host_id=1,
            barrier_total=2,
            api_url=None,
        )
        return engine.execute(_lifecycle(init_ok=False))

    def succeeding_job():
        engine = PipelineEngine(
            adb=SimpleNamespace(adb_path="adb"),
            serial="OK",
            run_id=2,
            script_registry=DualRegistry(),
            coordinator=coord,
            plan_run_host_id=1,
            barrier_total=2,
            api_url=None,
        )
        return engine.execute(_lifecycle(init_ok=True))

    fail_result = [None]
    ok_result = [None]

    t_fail = threading.Thread(target=lambda: fail_result.__setitem__(0, failing_job()))
    t_ok = threading.Thread(target=lambda: ok_result.__setitem__(0, succeeding_job()))
    t_fail.start()
    t_ok.start()
    t_fail.join(timeout=10)
    t_ok.join(timeout=10)

    assert fail_result[0] is not None
    assert fail_result[0].success is False
    assert ok_result[0] is not None and ok_result[0].success is True
    assert patrol_started.wait(timeout=2), "peer should pass barrier after init-fail arrive"


def test_pipeline_barrier_disabled_when_total_lt_2(tmp_path):
    """Single-device PlanRunHost skips barrier (no hang)."""
    ok = _write_script(tmp_path / "ok.py", "print('ok')")
    engine = PipelineEngine(
        adb=SimpleNamespace(adb_path="adb"),
        serial="SOLO",
        run_id=7,
        script_registry=FakeScriptRegistry(ok),
        coordinator=HostRunCoordinator("http://x", "h1", "inst"),
        plan_run_host_id=1,
        barrier_total=1,
        api_url=None,
    )
    # No patrol — just init; barrier not required.
    result = engine.execute({
        "lifecycle": {
            "init": [
                {
                    "step_id": "i",
                    "action": "script:ok",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 5,
                }
            ],
            "teardown": [],
        }
    })
    assert result.success is True
