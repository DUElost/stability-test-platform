"""#1167 P1/P2（ADR-0036 D1/D2/D5/D9）：投递结果归一化与重试策略单测。"""
from __future__ import annotations

import smtplib

import pytest
import requests

from backend.services.notification_delivery import (
    DEFAULT_RETRY_POLICY,
    DeliveryOutcome,
    DeliveryResult,
    RetryPolicy,
    accepted,
    classify_exception,
    classify_http_status,
    rejected_permanent,
    rejected_transient,
    unknown,
)


class TestClassifyHttpStatus:
    @pytest.mark.parametrize("status", [200, 201, 204, 302])
    def test_success_codes_are_accepted(self, status):
        assert classify_http_status(status).outcome is DeliveryOutcome.ACCEPTED

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_retryable_codes_are_transient(self, status):
        r = classify_http_status(status)
        assert r.outcome is DeliveryOutcome.REJECTED_TRANSIENT
        assert r.retryable

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_client_error_codes_are_permanent(self, status):
        r = classify_http_status(status)
        assert r.outcome is DeliveryOutcome.REJECTED_PERMANENT
        assert not r.retryable


class TestClassifyException:
    def test_timeout_is_unknown_because_request_may_have_succeeded(self):
        r = classify_exception(requests.Timeout("read timeout"))
        assert r.outcome is DeliveryOutcome.UNKNOWN
        assert r.retryable

    def test_connection_error_is_transient(self):
        r = classify_exception(requests.ConnectionError("refused"))
        assert r.outcome is DeliveryOutcome.REJECTED_TRANSIENT
        assert r.retryable

    def test_smtp_auth_error_is_permanent(self):
        r = classify_exception(smtplib.SMTPAuthenticationError(535, b"bad creds"))
        assert r.outcome is DeliveryOutcome.REJECTED_PERMANENT
        assert not r.retryable

    def test_smtp_recipients_refused_is_permanent(self):
        r = classify_exception(smtplib.SMTPRecipientsRefused({"x@y": (550, b"no")}))
        assert r.outcome is DeliveryOutcome.REJECTED_PERMANENT

    def test_smtp_disconnect_is_unknown(self):
        r = classify_exception(smtplib.SMTPServerDisconnected("disconnected"))
        assert r.outcome is DeliveryOutcome.UNKNOWN
        assert r.retryable

    def test_generic_smtp_error_is_transient(self):
        r = classify_exception(smtplib.SMTPException("boom"))
        assert r.outcome is DeliveryOutcome.REJECTED_TRANSIENT

    def test_config_error_is_permanent(self):
        r = classify_exception(ValueError("Webhook URL not configured"))
        assert r.outcome is DeliveryOutcome.REJECTED_PERMANENT

    def test_unclassified_exception_is_unknown(self):
        r = classify_exception(RuntimeError("mystery"))
        assert r.outcome is DeliveryOutcome.UNKNOWN
        assert r.retryable


class TestRetryPolicy:
    def test_accepted_and_permanent_never_retry(self):
        policy = DEFAULT_RETRY_POLICY
        assert not policy.should_retry(accepted(), 1)
        assert not policy.should_retry(rejected_permanent("bad creds"), 1)

    def test_transient_and_unknown_count_toward_cap(self):
        policy = RetryPolicy(max_attempts=3)
        for make in (lambda: rejected_transient("5xx"), lambda: unknown("timeout")):
            assert policy.should_retry(make(), 1)
            assert policy.should_retry(make(), 2)
            assert not policy.should_retry(make(), 3), "UNKNOWN/瞬时均计入上限"

    def test_backoff_is_exponential_and_capped(self):
        policy = RetryPolicy(base_delay_s=5.0, max_delay_s=120.0)
        assert policy.delay_for(1) == 5.0
        assert policy.delay_for(2) == 10.0
        assert policy.delay_for(3) == 20.0
        assert policy.delay_for(10) == 120.0


class TestResultRecord:
    def test_record_shape_for_success_and_failure(self):
        assert accepted("WEBHOOK", "HTTP 200").record() == {
            "status": "ok", "outcome": "ACCEPTED", "error": "HTTP 200",
        }
        rec = rejected_transient("HTTP 503", channel_type="WEBHOOK").record()
        assert rec["status"] == "failed"
        assert rec["outcome"] == "REJECTED_TRANSIENT"

    def test_non_accepted_results_are_not_accepted(self):
        for r in (
            rejected_permanent("x"), rejected_transient("y"), unknown("z"),
        ):
            assert isinstance(r, DeliveryResult)
            assert not r.accepted
