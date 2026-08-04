"""#144 — dispatch_complete repair when dispatch_state sync fails."""

from unittest.mock import MagicMock, patch

from backend.services.precheck.dispatch_complete import dispatch_complete


def test_dispatch_complete_repairs_after_update_failure():
    pr = MagicMock()
    pr.id = 9001
    pr.status = "RUNNING"
    db = MagicMock()

    calls: list[str] = []

    def fake_update(plan_run, session, **kwargs):
        calls.append(kwargs.get("status", ""))
        if len(calls) == 1:
            raise RuntimeError("simulated commit failure")

    with patch(
        "backend.services.precheck.dispatch_complete.update_dispatch_state",
        side_effect=fake_update,
    ), patch(
        "backend.services.precheck.dispatch_complete.plan_run_has_jobs",
        return_value=True,
    ):
        outcome = dispatch_complete(pr, db, out_of_sync_hosts=[])

    assert outcome == "passed"
    assert calls == ["completed", "completed"]
    db.commit.assert_called_once()
