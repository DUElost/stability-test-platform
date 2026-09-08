"""#1005 — terminal-report durability: outbox triage + active-record guard.

complete_job 三分结果（远端确认 / 仅本地持久化 / 两者均失败）与
_cleanup_after_job_exit 的恢复依据守卫。双故障注入测试。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.agent.api_client import TerminalReportLostError, complete_job

_PAYLOAD = {"status": "FINISHED", "exit_code": 0, "error_code": None}


def _post_fail(url, payload, context=None):
    raise RuntimeError("network down")


# ── complete_job outcome triage ───────────────────────────────────────────────


def test_http_fail_without_outbox_raises_terminal_lost():
    """验收：入队失败 + HTTP 失败 → raise TerminalReportLostError，不伪称
    deferred，不 ack。"""
    local_db = MagicMock()
    local_db.enqueue_terminal.side_effect = RuntimeError("sqlite locked")

    with patch("backend.agent.api_client._post_with_retry", side_effect=_post_fail):
        with pytest.raises(TerminalReportLostError):
            complete_job(
                "http://x", 7, dict(_PAYLOAD),
                fencing_token="t7", local_db=local_db,
            )

    local_db.enqueue_terminal.assert_called_once()
    local_db.ack_terminal.assert_not_called()


def test_http_fail_with_outbox_defers_without_raising():
    """仅本地持久化：enqueue ok + HTTP 失败 → 打 deferred 返回（outbox
    drain 会补送），不 raise。"""
    local_db = MagicMock()  # enqueue ok

    with patch("backend.agent.api_client._post_with_retry", side_effect=_post_fail):
        complete_job(
            "http://x", 7, dict(_PAYLOAD),
            fencing_token="t7", local_db=local_db,
        )

    local_db.ack_terminal.assert_not_called()


def test_http_ok_acks_outbox_and_returns():
    """远端确认：HTTP ok + outbox ok → ack；HTTP ok + enqueue 失败 → 不
    raise 不 ack（远端已确认，本地无行可 ack）。"""
    local_db = MagicMock()

    with patch("backend.agent.api_client._post_with_retry", return_value=None):
        complete_job(
            "http://x", 7, dict(_PAYLOAD),
            fencing_token="t7", local_db=local_db,
        )
    local_db.ack_terminal.assert_called_once()

    local_db_no_enqueue = MagicMock()
    local_db_no_enqueue.enqueue_terminal.side_effect = RuntimeError("sqlite locked")
    with patch("backend.agent.api_client._post_with_retry", return_value=None):
        complete_job(
            "http://x", 8, dict(_PAYLOAD),
            fencing_token="t8", local_db=local_db_no_enqueue,
        )
    local_db_no_enqueue.ack_terminal.assert_not_called()


def test_no_local_db_http_fail_still_raises():
    """无 local_db 时保持原语义：HTTP 失败直接 raise。"""
    with patch("backend.agent.api_client._post_with_retry", side_effect=_post_fail):
        with pytest.raises(RuntimeError, match="network down"):
            complete_job("http://x", 9, dict(_PAYLOAD), fencing_token="t9")


# ── _cleanup_after_job_exit active-record guard ──────────────────────────────


def _cleanup_args(**overrides):
    from backend.agent.main import _cleanup_after_job_exit

    args = dict(
        job_id=10,
        fencing_token="t10",
        active_jobs_lock=MagicMock(),
        active_job_ids={10},
        active_device_ids=set(),
        active_job_tokens={10: "t10"},
        lease_renewer=MagicMock(),
        local_db=None,
    )
    args.update(overrides)
    return _cleanup_after_job_exit, args


def test_cleanup_keeps_active_record_when_terminal_fact_missing():
    """验收：双故障（outbox 无行）→ 恢复依据保留，delete_active_job 不调。"""
    local_db = MagicMock()
    local_db.has_terminal_fact.return_value = False
    lease_renewer = MagicMock()
    lease_renewer.clear_fencing_token_if_current.return_value = 63

    _cleanup_after_job_exit, args = _cleanup_args(
        local_db=local_db, lease_renewer=lease_renewer,
    )
    _cleanup_after_job_exit(**args)

    local_db.delete_active_job.assert_not_called()
    local_db.has_terminal_fact.assert_called_once_with(10)


def test_cleanup_deletes_active_record_when_terminal_fact_durable():
    """终态已有可靠落点（outbox 行存在 / 远端 ack）→ 正常删除。"""
    local_db = MagicMock()
    local_db.has_terminal_fact.return_value = True
    lease_renewer = MagicMock()
    lease_renewer.clear_fencing_token_if_current.return_value = 63

    _cleanup_after_job_exit, args = _cleanup_args(
        local_db=local_db, lease_renewer=lease_renewer,
    )
    _cleanup_after_job_exit(**args)

    local_db.delete_active_job.assert_called_once_with(10)


def test_cleanup_without_local_db_keeps_legacy_semantics():
    """local_db=None（真实调用点不出现）：守卫不改变行为——与旧版相同，
    delete_active_job 分支会 AttributeError。"""
    lease_renewer = MagicMock()
    lease_renewer.clear_fencing_token_if_current.return_value = 63

    _cleanup_after_job_exit, args = _cleanup_args(lease_renewer=lease_renewer)
    with pytest.raises(AttributeError):
        _cleanup_after_job_exit(**args)  # local_db=None → 与旧版一致的崩溃路径
