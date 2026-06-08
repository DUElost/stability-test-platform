"""Tests for watcher enablement helpers."""

from backend.agent.watcher.enable import job_wants_watcher, watcher_subsystem_enabled


def test_watcher_subsystem_enabled_plan_default(monkeypatch):
    monkeypatch.delenv("STP_WATCHER_ENABLED", raising=False)
    monkeypatch.setenv("STP_WATCHER_PLAN_DEFAULT", "true")
    assert watcher_subsystem_enabled() is True


def test_job_wants_watcher_plan_job(monkeypatch):
    monkeypatch.setenv("STP_WATCHER_PLAN_DEFAULT", "true")
    run = {"plan_id": 1, "pipeline_def": {"lifecycle": {}}}
    assert job_wants_watcher(run, globally_enabled=False, plan_default=True) is True


def test_job_wants_watcher_explicit_disabled():
    run = {
        "plan_id": 1,
        "watcher_policy": {"enabled": False},
        "pipeline_def": {"lifecycle": {}},
    }
    assert job_wants_watcher(run, globally_enabled=False, plan_default=True) is False


def test_job_wants_watcher_explicit_disabled_overrides_global_enable():
    run = {
        "plan_id": 1,
        "watcher_policy": {"enabled": False},
        "pipeline_def": {"lifecycle": {}},
    }
    assert job_wants_watcher(run, globally_enabled=True, plan_default=True) is False


def test_job_wants_watcher_non_plan():
    run = {"pipeline_def": {"stages": {}}}
    assert job_wants_watcher(run, globally_enabled=False, plan_default=True) is False
