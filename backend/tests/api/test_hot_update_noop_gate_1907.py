"""ADR-0040 D3/D5（#1907）：hot-update 端点 no-op gate 与 force。"""

from __future__ import annotations

import types

import backend.api.routes.hosts as hosts_mod

DIGEST = "sha256:" + "c" * 64


def _converged():
    return {
        "ok": True, "converged": True, "reason": "digest-matched",
        "message": "converged: artifact digest matched", "duration_ms": 0,
        "deps_refreshed": False, "env_keys_synced": [], "env_paths_missing": {},
        "code_version": "", "priv_mode": "unknown",
        "artifact_digest": DIGEST, "phases": {"digest": 0},
    }

from backend.services.artifact_digest import ConvergencePlan

_EMPTY_OP = None


def _plan(code_digest=DIGEST, code_drift=False, resources_drift=False,
          resources_skipped_empty=True, converged=True):
    return ConvergencePlan(
        code_digest=code_digest, code_drift=code_drift,
        resources_digest="sha256:" + "f" * 64,
        resources_drift=resources_drift,
        resources_skipped_empty=resources_skipped_empty,
        converged=converged,
        no_op_result=_converged() if converged else None,
    )


def test_hot_update_noop_returns_converged_without_ssh(
    client, sample_host, db_session, monkeypatch, admin_headers,
):
    """desired == current → 200 converged；不取凭据、不占维护窗口、不 SSH。"""
    monkeypatch.setattr(hosts_mod, "plan_convergence", lambda host, force=False: _plan())
    finalized = {"n": 0}
    monkeypatch.setattr(
        hosts_mod, "finalize_hot_update_outcome",
        lambda db, host, result, **kw: finalized.__setitem__("n", finalized["n"] + 1),
    )

    response = client.post(
        f"/api/v1/hosts/{sample_host.id}/hot-update", headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["converged"] is True
    assert data["reason"] == "digest-matched"
    assert data["artifact_digest"] == DIGEST
    assert finalized["n"] == 1


def test_hot_update_force_bypasses_gate_and_deploys(
    client, sample_host, db_session, monkeypatch, admin_headers,
):
    """force=True → gate 不收敛（返回 None），进入全量分支；结果统一留痕。"""
    gate_calls = {"force": None}

    def fake_plan(host, force=False):
        gate_calls["force"] = force
        return _plan(converged=False)

    monkeypatch.setattr(hosts_mod, "plan_convergence", fake_plan)
    monkeypatch.setattr(
        hosts_mod, "resolve_host_ssh_credentials",
        lambda host, inventory_lookup=None: (
            types.SimpleNamespace(
                user="u", password="p", key_path="", known_hosts_path=""
            ),
            False,
        ),
    )
    exec_calls = {"digest": None}

    def fake_exec(**kwargs):
        exec_calls["digest"] = kwargs.get("artifact_digest")
        return {
            "ok": True, "converged": False, "reason": "deployed",
            "message": "OK", "duration_ms": 1, "deps_refreshed": False,
            "env_keys_synced": [], "env_paths_missing": {},
            "code_version": "deadbeef", "priv_mode": "wrapper",
            "artifact_digest": DIGEST, "phases": {},
        }

    monkeypatch.setattr(hosts_mod, "execute_hot_update", fake_exec)
    monkeypatch.setattr(hosts_mod, "get_agent_code_version", lambda: "deadbeef")
    finalized = {"n": 0}
    monkeypatch.setattr(
        hosts_mod, "finalize_hot_update_outcome",
        lambda db, host, result, **kw: finalized.__setitem__("n", finalized["n"] + 1),
    )

    response = client.post(
        f"/api/v1/hosts/{sample_host.id}/hot-update?force=true",
        headers=admin_headers,
    )
    assert gate_calls["force"] is True
    assert response.status_code == 200, response.text
    assert response.json()["converged"] is False
    assert exec_calls["digest"] == DIGEST
    assert finalized["n"] == 1
