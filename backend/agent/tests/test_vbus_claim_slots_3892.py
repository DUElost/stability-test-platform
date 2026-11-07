"""#3892 / ADR-0063 D4 第 5 条：vbus 刷机站的认领名额。

- 映射文件存在才申请 1 个 vbus 名额，否则 0；路径与 wrapper 同源；
- 槽位为 0 但有 vbus 名额时仍调用认领，且 capacity 传 0；
- 没有 vbus 名额时请求体与旧版逐字一致（不带 vbus_slots 键）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent import usb_power_slots
from backend.agent.api_client import fetch_pending_jobs
from backend.agent.claim_loop import process_claim_tick
from backend.agent.tests.test_claim_loop_736 import _tick_kwargs

ROOT = Path(__file__).resolve().parents[3]


def test_slots_follow_mapping_file(tmp_path):
    path = tmp_path / "usb-power-slots.json"
    assert usb_power_slots.vbus_claim_slots(str(path)) == 0
    path.write_text("{}", encoding="utf-8")
    assert usb_power_slots.vbus_claim_slots(str(path)) == 1
    assert usb_power_slots.vbus_claim_slots(str(tmp_path)) == 0


def test_path_matches_wrapper_constant():
    spec = importlib.util.spec_from_file_location(
        "stp_agent_priv_vbus_path", ROOT / "backend/agent/stp_agent_priv.py")
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    assert usb_power_slots.USB_POWER_SLOTS_PATH == wrapper.USB_POWER_SLOTS_PATH


def test_zero_slots_without_vbus_station_does_not_claim():
    kwargs = _tick_kwargs()
    kwargs["heartbeat_thread"].effective_slots = 0
    with patch("backend.agent.claim_loop.vbus_claim_slots", return_value=0), \
            patch("backend.agent.claim_loop.fetch_pending_jobs") as fetch:
        process_claim_tick(**kwargs)
    fetch.assert_not_called()


def test_zero_slots_on_vbus_station_claims_one_vbus_job():
    kwargs = _tick_kwargs()
    kwargs["heartbeat_thread"].effective_slots = 0
    job = {"id": 9, "device_id": 90, "fencing_token": "tok", "device_serial": "S"}
    with patch("backend.agent.claim_loop.vbus_claim_slots", return_value=1), \
            patch("backend.agent.claim_loop.fetch_pending_jobs", return_value=[job, dict(job, id=10, device_id=91)]) as fetch:
        process_claim_tick(**kwargs)
    assert fetch.call_args.kwargs["capacity"] == 0
    assert fetch.call_args.kwargs["vbus_slots"] == 1
    kwargs["executor"].submit.assert_called_once()


def test_request_body_unchanged_without_vbus_slots():
    response = MagicMock()
    response.json.return_value = {"data": []}
    with patch("backend.agent.api_client.requests.post", return_value=response) as post:
        fetch_pending_jobs("http://x", "h1", "inst", capacity=2)
        assert "vbus_slots" not in post.call_args.kwargs["json"]
        fetch_pending_jobs("http://x", "h1", "inst", capacity=0, vbus_slots=1)
        assert post.call_args.kwargs["json"]["vbus_slots"] == 1
        assert post.call_args.kwargs["json"]["capacity"] == 0
