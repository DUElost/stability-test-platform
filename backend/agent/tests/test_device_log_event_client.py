"""Scoped DLE list queries and opt-in explicit failures for scan mapping."""

from unittest.mock import MagicMock

import pytest
import requests

from backend.agent.aee.device_log_event_client import DeviceLogEventClient, DeviceLogEventListError


@pytest.mark.parametrize("plan_run_id", [None, 42])
def test_optional_run_filter_preserves_legacy_request(monkeypatch, plan_run_id):
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": {"events": []}}
    get = MagicMock(return_value=response)
    monkeypatch.setattr("backend.agent.aee.device_log_event_client.requests.get", get)
    client = DeviceLogEventClient(api_url="http://test", agent_secret="test", host_id="host")
    assert client.list_events(state="LOCAL", plan_run_id=plan_run_id) == []
    params = {"host_id": "host", "state": "LOCAL", "limit": 1}
    if plan_run_id is not None:
        params["plan_run_id"] = plan_run_id
    assert get.call_args.kwargs["params"] == params


@pytest.mark.parametrize("failure", ["timeout", "http", "json", "shape"])
def test_strict_list_distinguishes_failure_from_successful_empty(monkeypatch, failure):
    response = MagicMock(status_code=503 if failure == "http" else 200)
    response.json.return_value = {"data": {"events": "invalid" if failure == "shape" else []}}
    if failure == "json":
        response.json.side_effect = ValueError("invalid json")
    get = MagicMock(return_value=response)
    if failure == "timeout":
        get.side_effect = requests.Timeout()
    monkeypatch.setattr("backend.agent.aee.device_log_event_client.requests.get", get)
    client = DeviceLogEventClient(api_url="http://test", agent_secret="test", host_id="host")
    with pytest.raises(DeviceLogEventListError):
        client.list_events(state="", limit=None, plan_run_id=42, raise_on_error=True)


@pytest.mark.parametrize("failure", ["timeout", "http"])
def test_legacy_call_still_returns_empty_on_failure(monkeypatch, failure):
    get = MagicMock(return_value=MagicMock(status_code=503))
    if failure == "timeout":
        get.side_effect = requests.Timeout()
    monkeypatch.setattr("backend.agent.aee.device_log_event_client.requests.get", get)
    client = DeviceLogEventClient(api_url="http://test", agent_secret="test", host_id="host")
    assert client.list_events(state="LOCAL") == []
