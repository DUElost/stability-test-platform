"""#1881：租约校验退避预算（分钟级）与「控制面中断 vs 真丢锁」分级。

- 退避预算：`_LEASE_VERIFY_RETRY_DELAYS` 覆盖 ~60s，不再被一次 nginx 502 /
  控制面重启（分钟级）打穿（生产实证：24h/45h soak 被 1 分钟中断成批判死）；
- 分级判死：`lock_verification_*`（我方不可达/5xx）连续
  `_LEASE_VERIFY_OUTAGE_ABORT_STREAK` 个校验窗口才终止；`device_lease_not_held`
  （409）与 `lock_verify_auth_failed`（401）仍立即终止（非目标：不改该语义）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from requests import exceptions as requests_exceptions

from backend.agent.pipeline_engine import (
    PipelineEngine,
    StepResult,
    _LEASE_VERIFY_OUTAGE_ABORT_STREAK,
    _LEASE_VERIFY_RETRY_DELAYS,
    _lease_verify_outage_decision,
)
from backend.agent.tests.test_pipeline_engine_patrol import (
    _make_engine_with_patrol_uploader,
    _patrol_pipeline,
)

_BASE_KWARGS = dict(
    adb=MagicMock(),
    serial="MOCK_SERIAL",
    run_id=999,
    log_dir="/tmp/test_logs",
)


def _engine() -> PipelineEngine:
    mq = MagicMock()
    mq.connected = True
    return PipelineEngine(mq_producer=mq, api_url="http://cp.invalid", **_BASE_KWARGS)


class TestRetryBudget:
    def test_budget_covers_minute_scale_outage(self):
        # [1,2,4]≈7s 只挡秒级抖动；预算必须覆盖一分钟级中断
        assert sum(_LEASE_VERIFY_RETRY_DELAYS) >= 60

    def test_verify_uses_full_budget_before_giving_up(self):
        """持续 502：重试次数 = 预算长度，且逐次按预算退避，最后返回可判别错误串。"""
        engine = _engine()
        resp = MagicMock(status_code=502)

        class _Err(resp.__class__):
            pass

        resp.raise_for_status.side_effect = requests_exceptions.HTTPError(
            "502", response=resp,
        )
        sleeps: list[float] = []

        with patch("requests.post", return_value=resp) as post, patch(
            "backend.agent.pipeline_engine.time.sleep", side_effect=lambda s: sleeps.append(s),
        ):
            err = engine._verify_device_lease()

        # #2061：退避是「两次尝试之间的间隔」→ len(delays)+1 次探测，
        # 最后一次落在 t≈sum(delays)≈60s（原先末位 delay 花在最后一次失败之后，
        # t≈60 处没有探测，30s 后才结束的中断仍判死）。
        assert post.call_count == len(_LEASE_VERIFY_RETRY_DELAYS) + 1
        assert sleeps == list(_LEASE_VERIFY_RETRY_DELAYS), "退避只发生在两次探测之间"
        assert err is not None and err.error_message == "lock_verification_http_502"

    def test_connection_error_also_uses_full_budget(self):
        engine = _engine()
        sleeps: list[float] = []
        with patch(
            "requests.post", side_effect=requests_exceptions.ConnectionError("boom"),
        ) as post, patch(
            "backend.agent.pipeline_engine.time.sleep", side_effect=lambda s: sleeps.append(s),
        ):
            err = engine._verify_device_lease()

        # #2061：连接中断同样走满 len(delays)+1 次探测
        assert post.call_count == len(_LEASE_VERIFY_RETRY_DELAYS) + 1
        assert sleeps == list(_LEASE_VERIFY_RETRY_DELAYS)
        assert err is not None and err.error_message == "lock_verification_unreachable"


    def test_last_probe_lands_at_full_budget_not_after_it(self):
        """#2061：末次探测必须落在 t≈sum(delays)，而不是在 60s 处"放弃"。"""
        engine = _engine()
        resp = MagicMock(status_code=502)
        resp.raise_for_status.side_effect = requests_exceptions.HTTPError("502", response=resp)
        clock = {"t": 0.0}
        probe_times: list[float] = []

        def _fake_post(*_a, **_k):
            probe_times.append(round(clock["t"], 1))
            return resp

        with patch("requests.post", side_effect=_fake_post), patch(
            "backend.agent.pipeline_engine.time.sleep",
            side_effect=lambda s: clock.__setitem__("t", clock["t"] + s),
        ), patch("backend.agent.pipeline_engine.time.monotonic",
                 side_effect=lambda: clock["t"]):
            engine._verify_device_lease()

        assert probe_times == [0.0, 1.0, 3.0, 7.0, 15.0, 30.0, 60.0], (
            "探测时刻应为累计退避（末次 60s）——这是「1 分钟中断不打穿」的判据"
        )


class TestContractErrorGrading:
    """#1922：非 409/401 的契约类 4xx 立即终止，不进中断 streak。"""

    def test_verify_returns_contract_prefix_for_4xx(self):
        engine = _engine()
        resp = MagicMock(status_code=404)
        resp.raise_for_status.side_effect = requests_exceptions.HTTPError(
            "404", response=resp,
        )
        with patch("requests.post", return_value=resp) as post, patch(
            "backend.agent.pipeline_engine.time.sleep", return_value=None,
        ):
            err = engine._verify_device_lease()
        assert post.call_count == 1  # 契约错误不重试
        assert err is not None
        assert err.error_message == "lock_verify_contract_http_404"
        assert err.error_message.startswith("lock_verify_contract_")

    def test_contract_4xx_aborts_immediately(self):
        abort, streak = _lease_verify_outage_decision(
            "lock_verify_contract_http_404", 2,
        )
        assert abort is True and streak == 0  # 不累计、立即判死

    def test_contract_prefix_not_swallowed_by_outage_streak(self):
        # 前缀不同：`lock_verification*` streak 不被契约错误串命中
        assert not "lock_verify_contract_http_404".startswith("lock_verification")


class TestOutageDecision:
    @pytest.mark.parametrize("message", ["device_lease_not_held", "lock_verify_auth_failed"])
    def test_server_refusals_abort_immediately(self, message):
        abort, streak = _lease_verify_outage_decision(message, 0)
        assert abort is True and streak == 0

    def test_outage_accumulates_then_aborts(self):
        streak = 0
        for expected_streak in range(1, _LEASE_VERIFY_OUTAGE_ABORT_STREAK + 1):
            abort, streak = _lease_verify_outage_decision(
                "lock_verification_unreachable", streak,
            )
            assert streak == expected_streak
            assert abort is (expected_streak >= _LEASE_VERIFY_OUTAGE_ABORT_STREAK)

    def test_mixed_reasons_reset_and_abort(self):
        abort, streak = _lease_verify_outage_decision("lock_verification_http_502", 1)
        assert (abort, streak) == (False, 2)
        # 真丢锁插进来：立即终止并清零计数
        assert _lease_verify_outage_decision("device_lease_not_held", streak) == (True, 0)


class TestPatrolIntegration:
    @patch("backend.agent.pipeline_engine.time.sleep", return_value=None)
    def test_first_outage_window_keeps_patrol_running(self, _mock_sleep, caplog):
        """一次控制面中断不判死：patrol 继续跑（只记 WARNING），不发 lease lost abort。"""
        uploader = MagicMock()
        cycles = [0]

        def ack(**_kwargs):
            cycles[0] += 1
            if cycles[0] >= 3:
                engine._canceled = True
            return {"manual_action": None}

        uploader.send.side_effect = ack
        engine, _ = _make_engine_with_patrol_uploader(uploader)
        engine._execute_step = MagicMock(return_value=StepResult(success=True))
        engine._verify_device_lease = MagicMock(
            return_value=StepResult(
                success=False, exit_code=1,
                error_message="lock_verification_unreachable",
            ),
        )

        with caplog.at_level("WARNING", logger="backend.agent.pipeline_engine"), patch(
            "backend.agent.pipeline_engine.time.time", return_value=1_000_000.0,
        ):
            engine._execute_lifecycle(_patrol_pipeline(interval=0))

        assert engine._verify_device_lease.call_count >= 1
        text = caplog.text
        assert "lease verify outage 1/" in text, text
        assert "lease lost during patrol" not in text, text
