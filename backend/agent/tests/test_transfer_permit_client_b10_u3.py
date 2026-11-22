"""B10 U3 — Agent TransferPermitClient 超时与 fail-closed 反例。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from backend.agent.transfer_permit_client import (
    CODE_UNAVAILABLE,
    CODE_WAIT_TIMEOUT,
    TransferPermitClient,
    TransferPermitClientError,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))


def _ok_acquire(acquired: bool, **extra: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    payload = {
        "data": {
            "acquired": acquired,
            "pool": "firmware_nfs",
            "host_id": "host-a",
            "lease_token": "tok",
            "active_count": 2 if not acquired else 1,
            "max_hosts": 2,
            "ttl_seconds": 60,
            **extra,
        }
    }
    resp.json.return_value = payload
    return resp


def _session_sequence(responses):
    session = MagicMock()
    session.post.side_effect = responses
    return session


def test_wait_timeout_even_with_bypass():
    clock = _Clock()
    # 一直返回 acquired=false
    session = _session_sequence([_ok_acquire(False)] * 50)
    client = TransferPermitClient(
        api_url="http://cp.example",
        host_id="host-a",
        agent_secret="s",
        wait_timeout_seconds=5,
        unreachable_timeout_seconds=300,
        request_timeout_seconds=1,
        allow_permit_bypass=True,
        session=session,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        uuid_fn=lambda: "tok",
    )
    with pytest.raises(TransferPermitClientError) as exc:
        client.acquire_permit(allow_permit_bypass=True)
    assert exc.value.code == CODE_WAIT_TIMEOUT


def test_unreachable_fail_closed_default():
    clock = _Clock()

    def _boom(*_a, **_k):
        clock.now += 10  # 模拟单次请求耗时
        raise requests.ConnectionError("refused")

    session = MagicMock()
    session.post.side_effect = _boom
    client = TransferPermitClient(
        api_url="http://cp.example",
        host_id="host-a",
        agent_secret="s",
        wait_timeout_seconds=3600,
        unreachable_timeout_seconds=25,
        request_timeout_seconds=10,
        allow_permit_bypass=False,
        session=session,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        uuid_fn=lambda: "tok",
    )
    with pytest.raises(TransferPermitClientError) as exc:
        client.acquire_permit()
    assert exc.value.code == CODE_UNAVAILABLE


def test_unreachable_bypass_marks_degraded():
    clock = _Clock()

    def _boom(*_a, **_k):
        clock.now += 10
        raise requests.Timeout("timed out")

    session = MagicMock()
    session.post.side_effect = _boom
    client = TransferPermitClient(
        api_url="http://cp.example",
        host_id="host-a",
        agent_secret="s",
        wait_timeout_seconds=3600,
        unreachable_timeout_seconds=25,
        request_timeout_seconds=10,
        session=session,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        uuid_fn=lambda: "tok",
    )
    hold = client.acquire_permit(allow_permit_bypass=True)
    assert hold.permit_degraded is True
    hold.release()  # no-op for degraded


def test_acquire_success_releases_on_exit():
    clock = _Clock()

    def _responder(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if str(url).endswith("/acquire"):
            resp.json.return_value = {
                "data": {
                    "acquired": True,
                    "pool": "firmware_nfs",
                    "host_id": "host-a",
                    "lease_token": "tok",
                    "active_count": 1,
                    "max_hosts": 2,
                    "ttl_seconds": 60,
                }
            }
        elif str(url).endswith("/release"):
            resp.json.return_value = {
                "data": {
                    "released": True,
                    "pool": "firmware_nfs",
                    "host_id": "host-a",
                    "lease_token": "tok",
                }
            }
        else:
            resp.json.return_value = {
                "data": {
                    "renewed": True,
                    "pool": "firmware_nfs",
                    "host_id": "host-a",
                    "lease_token": "tok",
                    "ttl_seconds": 60,
                }
            }
        return resp

    session = MagicMock()
    session.post.side_effect = _responder
    client = TransferPermitClient(
        api_url="http://cp.example",
        host_id="host-a",
        agent_secret="s",
        ttl_seconds=3600,  # renew 间隔长，测试窗口内不触发
        session=session,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        uuid_fn=lambda: "tok",
    )
    with client.acquire_permit() as hold:
        assert hold.permit_degraded is False
        assert hold.lease_token == "tok"
    release_calls = [
        c for c in session.post.call_args_list if str(c.args[0]).endswith("/release")
    ]
    assert release_calls


def test_progress_callback_on_queue_wait():
    clock = _Clock()
    events: list[dict] = []

    def _progress(**kwargs):
        events.append(kwargs)
        # 第二次让时钟直接超过 wait，避免死循环
        if len(events) >= 1:
            clock.now = 100

    session = _session_sequence([_ok_acquire(False)] * 5)
    client = TransferPermitClient(
        api_url="http://cp.example",
        host_id="host-a",
        agent_secret="s",
        wait_timeout_seconds=50,
        session=session,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
        progress_callback=_progress,
        uuid_fn=lambda: "tok",
    )
    with pytest.raises(TransferPermitClientError) as exc:
        client.acquire_permit()
    assert exc.value.code == CODE_WAIT_TIMEOUT
    assert events
    assert events[0]["stage"] == "firmware-stage-wait"


def test_client_does_not_auto_acquire_on_construct():
    """缓存命中路径：调用方不触发 acquire_permit 时零请求。"""
    session = MagicMock()
    TransferPermitClient(
        api_url="http://cp.example",
        host_id="host-a",
        agent_secret="s",
        session=session,
    )
    session.post.assert_not_called()
