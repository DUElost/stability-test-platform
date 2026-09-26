"""#3422 — ``gather_verify`` 主机并发加界与按主机归因。

生产实测（2026-09-26）：单次 gather 内 ≤5 主机恒成功、≥30 主机 ack 大面积
超时（admission 全有全无 ⇒ 大 run 恒不准入）；48 个**独立**单机 RPC 全 200。
本用例锁住「加界生效」与「异常不打断其余主机」两个形状。
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_gather_verify_bounds_host_concurrency(monkeypatch):
    from backend.services.precheck import verify as v

    monkeypatch.setattr(v, "VERIFY_CONCURRENCY", 3)

    active = 0
    peak = 0

    async def fake_one(hid, expected):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return True, [{"host_id": hid}], None

    monkeypatch.setattr(v, "verify_one_host", fake_one)

    out = await v.gather_verify([f"h{i}" for i in range(12)], [])

    assert set(out) == {f"h{i}" for i in range(12)}
    assert peak <= 3, f"并发峰值 {peak} 超过加界 3"
    assert out["h0"] == (True, [{"host_id": "h0"}], None)
    assert all(res[0] for res in out.values())


@pytest.mark.asyncio
async def test_gather_verify_isolates_host_exceptions(monkeypatch):
    """单主机异常仍按 host 归因、不打断其余主机（admission 依赖该形状）。"""
    from backend.services.precheck import verify as v

    monkeypatch.setattr(v, "VERIFY_CONCURRENCY", 4)

    async def fake_one(hid, expected):
        if hid == "boom":
            raise RuntimeError("kaboom")
        return True, [], None

    monkeypatch.setattr(v, "verify_one_host", fake_one)

    out = await v.gather_verify(["a", "boom", "b"], [])

    assert out["a"] == (True, [], None)
    assert out["b"] == (True, [], None)
    ok, _res, err = out["boom"]
    assert ok is False and (err or "").startswith("verify_exception:")
