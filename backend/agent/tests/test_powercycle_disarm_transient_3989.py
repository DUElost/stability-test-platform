"""Issue #3989 Part A unit tests: pm path rc=1 error classification, transient disarm retry, and device_return hardening."""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from backend.agent import device_return
from backend.agent.operation_scheduler import OperationScheduler, PermitDenied

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / "backend" / "agent" / "scripts"


def _load_disarm_modules(tag: str):
    path = SCRIPTS / "powercycle_disarm" / "powercycle_disarm.py"
    lib_path = path.parent / "_lib.py"
    lib_name = f"powercycle_disarm_3989_{tag}_lib"
    lib_spec = importlib.util.spec_from_file_location(lib_name, lib_path)
    assert lib_spec and lib_spec.loader
    lib = importlib.util.module_from_spec(lib_spec)
    sys.modules[lib_name] = lib
    lib_spec.loader.exec_module(lib)
    sys.modules["_lib"] = lib

    mod_name = f"powercycle_disarm_3989_{tag}_mod"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod, lib


@pytest.fixture
def disarm_pair():
    return _load_disarm_modules("unit")


class TestAutotesttoolInstalledAndReadPrefsRetry:
    @pytest.mark.parametrize(
        ("out", "err"),
        [
            ("", "error: device offline"),
            ("", "error: device 'ABC123' not found"),
            ("", "cmd: Can't find service: package"),
            ("Error: Could not access the Package Manager", ""),
        ],
    )
    def test_pm_path_rc1_with_error_output_returns_none(self, disarm_pair, monkeypatch, out, err):
        _mod, lib = disarm_pair
        monkeypatch.setattr(lib, "adb", lambda *args, **kwargs: (1, out, err))
        assert lib.autotesttool_installed() is None

    def test_pm_path_rc1_clean_still_returns_false(self, disarm_pair, monkeypatch):
        _mod, lib = disarm_pair
        monkeypatch.setattr(lib, "adb", lambda *args, **kwargs: (1, "", ""))
        assert lib.autotesttool_installed() is False

    def test_pm_path_rc0_package_returns_true(self, disarm_pair, monkeypatch):
        _mod, lib = disarm_pair
        monkeypatch.setattr(lib, "adb", lambda *args, **kwargs: (0, "package:/data/app/base.apk\n", ""))
        assert lib.autotesttool_installed() is True

    def test_read_prefs_classified_does_not_misclassify_transient_pm_error_as_not_installed(
        self, disarm_pair, monkeypatch
    ):
        _mod, lib = disarm_pair
        monkeypatch.setattr(lib, "is_root", lambda: True)

        def fake_adb(*args, **kwargs):
            cmd = " ".join(str(a) for a in args)
            if "pm path " in cmd:
                return 1, "", "error: device offline"
            return -1, "", "error: device offline"

        monkeypatch.setattr(lib, "adb", fake_adb)
        kind, xml, installed = lib.read_prefs_classified()
        assert installed is None
        assert kind == "transient"
        assert xml == ""

    def test_read_prefs_evidence_with_retry_recovers_within_budget(self, disarm_pair, monkeypatch):
        mod, _lib = disarm_pair
        clear_xml = '<map><boolean name="running" value="false" /></map>'
        responses = iter(
            [
                ("transient", "", None),
                ("transient", "", None),
                ("ok", clear_xml, True),
            ]
        )
        sleeps: list[float] = []
        stamps: list[dict] = []
        monkeypatch.setattr(mod, "read_prefs_classified", lambda: next(responses))
        monkeypatch.setattr(mod, "is_root", lambda: True)
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: stamps.append(dict(payload)))

        snap = mod._read_prefs_evidence_with_retry(max_attempts=3, delay_seconds=0.25)
        assert snap["readback"] == "clear"
        assert snap["evidence"]["kind"] == "ok"
        assert snap["evidence"]["transient_retries"] == 2
        assert sleeps == [0.25, 0.25]
        assert [s["phase"] for s in stamps] == ["read_prefs_retry", "read_prefs_retry"]

    @pytest.mark.parametrize(
        ("kind", "xml", "installed", "expected_readback"),
        [
            ("denied", "", True, "readback_failed"),
            ("ok", '<map><boolean name="running" value="false" /></map>', True, "clear"),
            ("not_installed", "", False, "not_installed"),
        ],
    )
    def test_read_prefs_evidence_with_retry_skips_non_transient(
        self, disarm_pair, monkeypatch, kind, xml, installed, expected_readback
    ):
        mod, _lib = disarm_pair
        calls = 0
        sleeps: list[float] = []

        def fake_read():
            nonlocal calls
            calls += 1
            return kind, xml, installed

        monkeypatch.setattr(mod, "read_prefs_classified", fake_read)
        monkeypatch.setattr(mod, "is_root", lambda: True)
        monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))

        snap = mod._read_prefs_evidence_with_retry(max_attempts=3, delay_seconds=0.5)
        assert snap["readback"] == expected_readback
        assert calls == 1
        assert sleeps == []
        assert "transient_retries" not in snap["evidence"]

    def test_run_disarm_recovers_on_transient_post_stop_readback(self, disarm_pair, monkeypatch):
        mod, _lib = disarm_pair
        clear_xml = '<map><boolean name="running" value="false" /></map>'
        monkeypatch.setattr(mod, "device_serial", lambda: "S-3989")
        monkeypatch.setattr(mod, "_wait_device_online", lambda _t: True)
        monkeypatch.setattr(mod, "enable_exec_progress", lambda: None)
        monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=60: "1" if "boot_completed" in str(cmd) else "")
        monkeypatch.setattr(mod, "_detect_platform", lambda: "MTK")
        monkeypatch.setattr(mod, "_device_identity_evidence", lambda: {"permission_model": "root"})
        monkeypatch.setattr(mod, "is_root", lambda: True)
        monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(mod, "progress_stamp", lambda _p: None)
        monkeypatch.setattr(
            mod,
            "stop_task",
            lambda **_kwargs: {"before": "stopped", "after": "stopped", "stop_rc": 0, "force_rc": 0},
        )
        reads = iter(
            [
                ("ok", clear_xml, True),
                ("transient", "", None),
                ("ok", clear_xml, True),
            ]
        )
        monkeypatch.setattr(mod, "read_prefs_classified", lambda: next(reads))

        out = mod._run({"mode": "disarm"})
        assert out["success"] is True
        assert out["disarm_result"] == "executed_unverified"
        assert out["readback"] == "clear"
        assert out["evidence"]["transient_retries"] == 1


class TestDeviceReturnTransientRetryAndLogging:
    def _make_init_engine(self):
        traces: list[tuple] = []
        engine = SimpleNamespace(
            _api_url="http://control-plane.invalid",
            _run_id=101,
            _fencing_token="fence-3989",
            _local_db=SimpleNamespace(get_state=lambda _k: "1"),
            _report_step_trace_mq=lambda *args, **kwargs: traces.append((args, kwargs)),
        )
        return engine, traces

    def test_run_init_checks_retries_disarm_once_on_transient_readback(self, monkeypatch, caplog):
        monkeypatch.setitem(device_return.ONBOARDED, "power-cycle", dict(device_return.ONBOARDED["power-cycle"]))
        monkeypatch.delitem(device_return.ONBOARDED, "gpu", raising=False)
        engine, traces = self._make_init_engine()
        responses = iter(
            [
                {
                    "result": "failed",
                    "evidence": {"readback": "readback_failed", "kind": "denied"},
                },
                {
                    "result": "failed",
                    "evidence": {"readback": "readback_failed", "kind": "transient"},
                },
                {
                    "result": "confirmed",
                    "evidence": {"readback": "clear", "kind": "ok"},
                },
            ]
        )
        calls: list[str] = []
        reports: list[dict] = []

        def fake_exec(_engine, _family, mode, **_kwargs):
            calls.append(mode)
            return next(responses)

        monkeypatch.setattr(device_return, "execute_registered", fake_exec)
        monkeypatch.setattr(
            device_return,
            "report_init_return_check",
            lambda _url, _rid, _tok, payload: reports.append(payload),
        )

        with caplog.at_level(logging.WARNING, logger=device_return.logger.name):
            res = device_return.run_init_checks(engine, lambda **kw: SimpleNamespace(**kw))

        assert res.success is True
        assert calls == ["probe", "disarm", "disarm"]
        assert len(reports) == 1
        assert reports[0]["power-cycle"]["result"] == "confirmed"
        assert "init_return_check_transient_retry" in caplog.text
        assert len(traces[-1][1]["metadata"]["executions"]) == 3

    def test_run_init_checks_does_not_retry_non_transient_or_loop(self, monkeypatch):
        monkeypatch.setitem(device_return.ONBOARDED, "power-cycle", dict(device_return.ONBOARDED["power-cycle"]))
        monkeypatch.delitem(device_return.ONBOARDED, "gpu", raising=False)
        engine, _traces = self._make_init_engine()

        # Non-transient failure: no extra disarm attempt
        non_transient_calls: list[str] = []

        def fake_exec_denied(_engine, _family, mode, **_kwargs):
            non_transient_calls.append(mode)
            return {"result": "failed", "evidence": {"readback": "readback_failed", "kind": "denied"}}

        monkeypatch.setattr(device_return, "execute_registered", fake_exec_denied)
        monkeypatch.setattr(device_return, "report_init_return_check", lambda *_a, **_kw: None)
        res = device_return.run_init_checks(engine, lambda **kw: SimpleNamespace(**kw))
        assert res.success is False
        assert non_transient_calls == ["probe", "disarm"]

        # Persistent transient: strictly 1 bounded disarm retry
        transient_calls: list[str] = []

        def fake_exec_transient(_engine, _family, mode, **_kwargs):
            transient_calls.append(mode)
            return {"result": "failed", "evidence": {"readback": "readback_failed", "kind": "transient"}}

        monkeypatch.setattr(device_return, "execute_registered", fake_exec_transient)
        res2 = device_return.run_init_checks(engine, lambda **kw: SimpleNamespace(**kw))
        assert res2.success is False
        assert transient_calls == ["probe", "disarm", "disarm"]

    def _make_consumer(self, scheduler=None):
        now = 100.0
        sched = scheduler if scheduler is not None else OperationScheduler(max_concurrent=1)
        consumer = device_return.ReturnDebtConsumer(
            api_url="http://control-plane.invalid",
            adb_path="adb",
            script_registry=SimpleNamespace(),
            local_db=SimpleNamespace(),
            get_scheduler=lambda: sched,
            is_active=lambda _did: False,
            engine_factory=lambda *args, **kwargs: SimpleNamespace(),
            clock=lambda: now,
            launch=lambda _fn: None,
        )
        debt = device_return._Debt(device_id=7, serial="DEV-7", token="tok-7", families=["power-cycle"])
        consumer._debts[7] = debt
        consumer._epochs["DEV-7"] = 1
        return consumer, debt, sched

    def test_debt_execute_retries_once_on_transient_and_logs(self, monkeypatch, caplog):
        consumer, debt, _sched = self._make_consumer()
        outcomes = iter(
            [
                {"result": "failed", "evidence": {"readback": "readback_failed", "kind": "transient"}},
                {"result": "confirmed", "evidence": {"readback": "clear", "kind": "ok"}},
            ]
        )
        calls: list[str] = []
        discharges: list[dict] = []

        def fake_exec(_engine, _family, mode, **_kwargs):
            calls.append(mode)
            return next(outcomes)

        monkeypatch.setattr(device_return, "execute_registered", fake_exec)
        monkeypatch.setattr(
            device_return,
            "report_return_discharge",
            lambda _url, _did, _tok, payload: discharges.append(payload),
        )

        with caplog.at_level(logging.INFO, logger=device_return.logger.name):
            consumer._execute(debt, ["power-cycle"])

        assert calls == ["disarm", "disarm"]
        assert len(discharges) == 1
        assert discharges[0]["power-cycle"]["result"] == "confirmed"
        assert debt.attempts["power-cycle"] == (100.0, "confirmed")
        assert "return_debt_transient_retry" in caplog.text
        assert "return_debt_discharged_attempt" in caplog.text

    def test_debt_execute_logs_permit_denied_and_report_failures(self, monkeypatch, caplog):
        class BusyScheduler(OperationScheduler):
            def acquire(self, _device_id, timeout=0):
                raise PermitDenied("busy")

        consumer, debt, _sched = self._make_consumer(scheduler=BusyScheduler())
        with caplog.at_level(logging.INFO, logger=device_return.logger.name):
            consumer._execute(debt, ["power-cycle"])
        assert "return_debt_permit_denied" in caplog.text

        # HTTP 409 clears debt and records token rejection
        caplog.clear()
        consumer409, debt409, _ = self._make_consumer()
        monkeypatch.setattr(
            device_return,
            "execute_registered",
            lambda *_a, **_kw: {"result": "confirmed", "evidence": {}},
        )
        resp409 = requests.Response()
        resp409.status_code = 409
        monkeypatch.setattr(
            device_return,
            "report_return_discharge",
            lambda *_a, **_kw: (_ for _ in ()).throw(requests.HTTPError(response=resp409)),
        )
        with caplog.at_level(logging.INFO, logger=device_return.logger.name):
            consumer409._execute(debt409, ["power-cycle"])
        assert "return_debt_token_rejected" in caplog.text
        assert 7 not in consumer409._debts

        # HTTP 422 maps to executed_unverified with warning log
        caplog.clear()
        consumer422, debt422, _ = self._make_consumer()
        resp422 = requests.Response()
        resp422.status_code = 422
        monkeypatch.setattr(
            device_return,
            "report_return_discharge",
            lambda *_a, **_kw: (_ for _ in ()).throw(requests.HTTPError(response=resp422)),
        )
        with caplog.at_level(logging.WARNING, logger=device_return.logger.name):
            consumer422._execute(debt422, ["power-cycle"])
        assert "return_debt_report_http_error" in caplog.text
        assert debt422.attempts["power-cycle"] == (100.0, "executed_unverified")

        # RequestException defers cleanly with retry_after_s log
        caplog.clear()
        consumer_net, debt_net, _ = self._make_consumer()
        monkeypatch.setattr(
            device_return,
            "report_return_discharge",
            lambda *_a, **_kw: (_ for _ in ()).throw(requests.ConnectionError("boom")),
        )
        with caplog.at_level(logging.WARNING, logger=device_return.logger.name):
            consumer_net._execute(debt_net, ["power-cycle"])
        assert "return_debt_report_deferred" in caplog.text
        assert debt_net.attempts["power-cycle"] == (100.0, "unreported")
