"""JIRA project key 存在性探测（#710 / ADR-0029 D12）。

纯函数单测：mock requests.get，不依赖 PG/网络。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.jira_project_key import probe_jira_project_key


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("STP_JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("STP_JIRA_TOKEN", raising=False)


def _resp(status):
    r = MagicMock()
    r.status_code = status
    return r


def test_returns_none_without_base_url():
    assert probe_jira_project_key("ABC") is None


def test_returns_none_for_empty_key(monkeypatch):
    monkeypatch.setenv("STP_JIRA_BASE_URL", "https://jira.example.com")
    assert probe_jira_project_key("") is None


def test_true_on_200(monkeypatch):
    monkeypatch.setenv("STP_JIRA_BASE_URL", "https://jira.example.com/")
    monkeypatch.setenv("STP_JIRA_TOKEN", "t")
    with patch("requests.get", return_value=_resp(200)) as get:
        assert probe_jira_project_key("ABC") is True
    args, kwargs = get.call_args
    assert args[0] == "https://jira.example.com/rest/api/2/project/ABC"
    assert kwargs["headers"]["Authorization"] == "Bearer t"


def test_false_on_404(monkeypatch):
    monkeypatch.setenv("STP_JIRA_BASE_URL", "https://jira.example.com")
    with patch("requests.get", return_value=_resp(404)):
        assert probe_jira_project_key("NOPE") is False


def test_none_on_other_status(monkeypatch):
    monkeypatch.setenv("STP_JIRA_BASE_URL", "https://jira.example.com")
    with patch("requests.get", return_value=_resp(500)):
        assert probe_jira_project_key("ABC") is None


def test_none_on_exception(monkeypatch):
    monkeypatch.setenv("STP_JIRA_BASE_URL", "https://jira.example.com")
    with patch("requests.get", side_effect=ConnectionError("down")):
        assert probe_jira_project_key("ABC") is None
