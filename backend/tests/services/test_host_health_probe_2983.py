"""#2983：控制面 host 健康探针——解析/对账/SSH 白名单执行器。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.services.host_health_probe import (
    ProbeArgvRefused,
    ProbeRound,
    ProbeVerdict,
    TOPOLOGY_BLIND,
    TOPOLOGY_EMPTY_CABINET,
    TOPOLOGY_OK,
    assert_argv_whitelisted,
    build_sudo_s_command,
    classify_lsusb_topology,
    collect_probe_round_via_ssh,
    consecutive_strike_open,
    parse_journal_probe,
    reconcile_agent_health,
    resolve_probe_argv,
    run_whitelisted_sudo,
    select_probe_host_ids,
)

# .102 死亡定式（与 kernel_usb_faults / issue 同字面）
_HC_DIED_JOURNAL = [
    "xhci_hcd 0000:00:14.0: xHCI host not responding to stop endpoint command",
    "xhci_hcd 0000:00:14.0: xHCI host controller not responding, assume dead",
    "xhci_hcd 0000:00:14.0: HC died; cleaning up",
]

_LSUSB_BLIND = [
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
    "Bus 001 Device 002: ID 0bda:5411 Realtek Semiconductor Corp. Hub",
    "Bus 001 Device 003: ID 0bda:5411 Realtek Semiconductor Corp. Hub",
]

_LSUSB_EMPTY_CABINET = [
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
    "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation 3.0 root hub",
]

_LSUSB_OK = [
    "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub",
    "Bus 001 Device 002: ID 0bda:5411 Realtek Semiconductor Corp. Hub",
    "Bus 001 Device 010: ID 18d1:4ee7 Google Inc. Nexus/Pixel Device",
]


def test_parse_journal_probe_hc_died_replay():
    facts = parse_journal_probe(_HC_DIED_JOURNAL)
    assert facts.hc_dead_seen is True
    assert facts.lines == 3


def test_classify_topology_blind_vs_empty_cabinet():
    blind = classify_lsusb_topology(_LSUSB_BLIND)
    assert blind.classification == TOPOLOGY_BLIND
    assert blind.cascade_hub_count >= 1
    assert blind.peripheral_count == 0

    empty = classify_lsusb_topology(_LSUSB_EMPTY_CABINET)
    assert empty.classification == TOPOLOGY_EMPTY_CABINET
    assert empty.cascade_hub_count == 0

    ok = classify_lsusb_topology(_LSUSB_OK)
    assert ok.classification == TOPOLOGY_OK
    assert ok.peripheral_count == 1


def test_reconcile_agent_mute_on_hc_died_when_agent_healthy():
    round_ = ProbeRound(
        journal=parse_journal_probe(_HC_DIED_JOURNAL),
        topology=classify_lsusb_topology(_LSUSB_BLIND),
    )
    result = reconcile_agent_health([], round_)
    assert result.verdict == ProbeVerdict.AGENT_MUTE
    assert "probe_hc_dead" in result.signals


def test_reconcile_empty_cabinet_not_blind():
    round_ = ProbeRound(
        journal=parse_journal_probe(["unrelated kernel line"]),
        topology=classify_lsusb_topology(_LSUSB_EMPTY_CABINET),
    )
    result = reconcile_agent_health(["usb_tree_empty"], round_)
    assert result.verdict == ProbeVerdict.ALIGNED
    assert "empty_cabinet_not_blind" in result.signals


def test_consecutive_strike_requires_n_rounds():
    assert consecutive_strike_open([ProbeVerdict.AGENT_MUTE], need=2) is False
    assert (
        consecutive_strike_open(
            [ProbeVerdict.ALIGNED, ProbeVerdict.AGENT_MUTE, ProbeVerdict.AGENT_MUTE],
            need=2,
        )
        is True
    )
    assert (
        consecutive_strike_open(
            [ProbeVerdict.AGENT_MUTE, ProbeVerdict.PROBE_QUIET],
            need=2,
        )
        is False
    )


def test_probe_argv_whitelist_refuses_unknown():
    with pytest.raises(ProbeArgvRefused):
        resolve_probe_argv("rm_rf")
    with pytest.raises(ProbeArgvRefused):
        assert_argv_whitelisted(["bash", "-c", "id"])
    cmd = build_sudo_s_command(resolve_probe_argv("lsusb"))
    assert cmd.startswith("sudo -S -p '' -- ")
    assert "lsusb" in cmd
    assert ";" not in cmd


class _FakeChannel:
    def __init__(self, rc: int = 0):
        self._rc = rc

    def shutdown_write(self) -> None:
        return None

    def recv_exit_status(self) -> int:
        return self._rc


class _FakeFile:
    def __init__(self, data: bytes = b"", *, channel: _FakeChannel | None = None):
        self._data = data
        self.channel = channel or _FakeChannel()

    def write(self, _data: str) -> None:
        return None

    def flush(self) -> None:
        return None

    def read(self) -> bytes:
        return self._data


class _FakeSSH:
    def __init__(self, outputs: dict[str, tuple[int, str, str]]):
        self.outputs = outputs
        self.commands: list[str] = []

    def exec_command(self, cmd: str, timeout: int = 10):
        self.commands.append(cmd)
        key = "lsusb" if "lsusb" in cmd else "journal_kernel_2h"
        rc, out, err = self.outputs[key]
        ch = _FakeChannel(rc)
        return (
            _FakeFile(channel=ch),
            _FakeFile(out.encode(), channel=ch),
            _FakeFile(err.encode(), channel=ch),
        )


def test_run_whitelisted_sudo_and_collect_round():
    client = _FakeSSH(
        {
            "lsusb": (0, "\n".join(_LSUSB_BLIND) + "\n", ""),
            "journal_kernel_2h": (0, "\n".join(_HC_DIED_JOURNAL) + "\n", ""),
        }
    )
    out = run_whitelisted_sudo(client, "lsusb", sudo_password="secret-not-logged")
    assert out.rc == 0
    assert "0bda" in out.stdout
    assert all("secret" not in c for c in client.commands)

    round_ = collect_probe_round_via_ssh(client, sudo_password="secret-not-logged")
    assert round_.journal.hc_dead_seen is True
    assert round_.topology.classification == TOPOLOGY_BLIND
    result = reconcile_agent_health([], round_)
    assert result.verdict == ProbeVerdict.AGENT_MUTE


def test_select_probe_host_ids_filters_retired_maintenance_offline():
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    hosts = [
        SimpleNamespace(
            id="online", status="ONLINE", retired_at=None, maintenance_until=None,
        ),
        SimpleNamespace(
            id="offline", status="OFFLINE", retired_at=None, maintenance_until=None,
        ),
        SimpleNamespace(
            id="retired", status="ONLINE",
            retired_at=now - timedelta(days=1), maintenance_until=None,
        ),
        SimpleNamespace(
            id="maint", status="ONLINE", retired_at=None,
            maintenance_until=now + timedelta(minutes=10),
        ),
    ]
    assert select_probe_host_ids(hosts, now=now) == ["online"]


def test_apply_probe_result_opens_strike_after_need_agent_mute_rounds():
    from backend.services.host_health_probe import (
        HEALTH_PROBE_EXTRA_KEY,
        apply_probe_result_to_extra,
    )

    now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
    extra = apply_probe_result_to_extra(
        {"health": {"reasons": []}},
        verdict=ProbeVerdict.AGENT_MUTE.value,
        signals=("probe_hc_dead",),
        topology=TOPOLOGY_BLIND,
        now=now,
        strike_need=2,
    )
    assert extra[HEALTH_PROBE_EXTRA_KEY]["strike_open"] is False

    extra = apply_probe_result_to_extra(
        extra,
        verdict=ProbeVerdict.AGENT_MUTE.value,
        signals=("probe_hc_dead",),
        topology=TOPOLOGY_BLIND,
        now=now + timedelta(minutes=10),
        strike_need=2,
    )
    assert extra[HEALTH_PROBE_EXTRA_KEY]["strike_open"] is True
    assert extra[HEALTH_PROBE_EXTRA_KEY]["recent_verdicts"] == [
        "agent_mute",
        "agent_mute",
    ]


def test_probe_one_host_writes_extra_and_audits_on_strike(engine, monkeypatch):
    from backend.core.database import SessionLocal
    from backend.core.ssh_security import ResolvedSshCredentials, encrypt_ssh_password
    from backend.models.audit import AuditLog
    from backend.models.enums import HostStatus
    from backend.models.host import Host
    from backend.services.host_health_probe import (
        HEALTH_PROBE_EXTRA_KEY,
        ProbeRound,
        parse_journal_probe,
        classify_lsusb_topology,
        probe_one_host,
    )

    db = SessionLocal()
    try:
        host = Host(
            id="probe-host-102",
            hostname="probe-102",
            ip_address="192.0.2.102",
            status=HostStatus.ONLINE.value,
            ssh_user="root",
            ssh_port=22,
            ssh_password_enc=encrypt_ssh_password("not-logged"),
            extra={"health": {"status": "HEALTHY", "reasons": []}},
        )
        db.add(host)
        db.commit()
    finally:
        db.close()

    mute_round = ProbeRound(
        journal=parse_journal_probe(_HC_DIED_JOURNAL),
        topology=classify_lsusb_topology(_LSUSB_BLIND),
    )

    class _FakeClient:
        def close(self):
            return None

    def _fake_connect(**kwargs):
        assert kwargs.get("hostname") == "192.0.2.102"
        return _FakeClient()

    def _fake_collect(client, *, sudo_password, timeout):
        assert sudo_password == "not-logged"
        return mute_round

    monkeypatch.setattr(
        "backend.core.ssh_security.resolve_host_ssh_credentials",
        lambda host, inventory_lookup=None: (
            ResolvedSshCredentials(user="root", password="not-logged"),
            False,
        ),
    )

    # First tick — no strike yet
    s1 = probe_one_host(
        "probe-host-102",
        timeout=5,
        strike_need=2,
        ssh_connect=_fake_connect,
        collect_round=_fake_collect,
    )
    assert s1["ok"] is True
    assert s1["strike_open"] is False

    s2 = probe_one_host(
        "probe-host-102",
        timeout=5,
        strike_need=2,
        ssh_connect=_fake_connect,
        collect_round=_fake_collect,
    )
    assert s2["strike_open"] is True

    db = SessionLocal()
    try:
        host = db.get(Host, "probe-host-102")
        assert host.extra[HEALTH_PROBE_EXTRA_KEY]["strike_open"] is True
        audits = (
            db.query(AuditLog)
            .filter(AuditLog.action == "host_health_probe_agent_mute")
            .all()
        )
        assert len(audits) == 1
        assert "password" not in str(audits[0].details).lower()
        assert "not-logged" not in str(audits[0].details)
    finally:
        db.close()
