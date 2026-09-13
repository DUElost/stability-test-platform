"""#1737 P1 — RunConsole 多实例接线：跨实例互斥 / fail-closed / 止损 / 释放。

注册表内部语义由 `backend/tests/realtime/test_console_registry.py` 覆盖；
本文件用内存 stub 替换注册表函数，只验 RunConsole 的接线行为：
- 第二实例同 key 启动 → RunKeyBusyError（全局互斥生效）；
- 注册表不可用 → fail-closed（拒绝启动且本地状态回滚）；
- 终态/取消 → 互斥键与 owner 键释放；
- 续期 ``lost`` → 止损取消；``unavailable`` → 不动（不误杀）。
"""
from __future__ import annotations

import sys
import time

import pytest

from backend.realtime import console_registry as registry
from backend.services.run_console import RunConsole, RunConsoleError, RunKeyBusyError

_FAST_CMD = [sys.executable, "-c", "print('done')"]
_SLOW_CMD = [sys.executable, "-c", "import time; time.sleep(30)"]


class _StubRegistry:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.owners: dict[str, str] = {}
        self.snapshots: dict[str, dict] = {}
        self.snapshot_ttls: dict[str, int] = {}
        self.refreshed: list[str] = []
        self.fail = False
        self.renew_result = registry.RENEW_OK
        self.renew_owner_result = registry.RENEW_OK
        self.release_calls: list[tuple[str, str]] = []

    def acquire(self, run_key: str, *, run_id: str) -> None:
        if self.fail:
            raise registry.ConsoleRegistryUnavailable("stub down")
        if run_key in self.keys and self.keys[run_key] != run_id:
            raise registry.ConsoleRunKeyBusy(f"busy: {run_key}")
        self.keys[run_key] = run_id

    def renew(self, run_key: str, *, run_id: str) -> str:
        if self.fail:
            return registry.RENEW_UNAVAILABLE
        if self.keys.get(run_key) != run_id:
            # 键被外部接管或丢失：确认失去互斥
            return registry.RENEW_LOST
        return self.renew_result

    def release(self, run_key: str, *, run_id: str) -> None:
        self.release_calls.append(("key", run_key))
        if self.keys.get(run_key) == run_id:
            self.keys.pop(run_key, None)

    def register_owner(self, run_id: str, *, run_key: str) -> None:
        if self.fail:
            raise registry.ConsoleRegistryUnavailable("stub down")
        self.owners[run_id] = run_key

    def renew_owner(self, run_id: str, *, run_key: str) -> str:
        if self.fail:
            return registry.RENEW_UNAVAILABLE
        return self.renew_owner_result

    def release_owner(self, run_id: str, *, run_key: str) -> None:
        self.release_calls.append(("owner", run_id))
        self.owners.pop(run_id, None)

    # ── P2：状态快照 ────────────────────────────────────────────────────────

    def publish_snapshot(self, run_id: str, snapshot: dict, *, ttl_seconds: int) -> None:
        if self.fail:
            raise registry.ConsoleRegistryUnavailable("stub down")
        self.snapshots[run_id] = dict(snapshot)
        self.snapshot_ttls[run_id] = int(ttl_seconds)

    def refresh_snapshot(self, run_id: str, *, ttl_seconds: int) -> bool:
        if run_id not in self.snapshots:
            return False
        self.snapshot_ttls[run_id] = int(ttl_seconds)
        self.refreshed.append(run_id)
        return True

    def read_snapshot(self, run_id: str):
        return dict(self.snapshots[run_id]) if run_id in self.snapshots else None

    def delete_snapshot(self, run_id: str) -> None:
        self.snapshots.pop(run_id, None)


@pytest.fixture()
def stub(monkeypatch):
    s = _StubRegistry()
    monkeypatch.setattr(registry, "console_registry_enabled", lambda: True)
    monkeypatch.setattr(registry, "console_registry_ttl_seconds", lambda: 30)
    monkeypatch.setattr(registry, "control_plane_instance_id", lambda: "cp-test")
    monkeypatch.setattr(registry, "acquire_run_key", s.acquire)
    monkeypatch.setattr(registry, "renew_run_key", s.renew)
    monkeypatch.setattr(registry, "release_run_key", s.release)
    monkeypatch.setattr(registry, "register_owner", s.register_owner)
    monkeypatch.setattr(registry, "renew_owner", s.renew_owner)
    monkeypatch.setattr(registry, "release_owner", s.release_owner)
    monkeypatch.setattr(registry, "publish_status_snapshot", s.publish_snapshot)
    monkeypatch.setattr(registry, "refresh_status_ttl", s.refresh_snapshot)
    monkeypatch.setattr(registry, "read_status_snapshot", s.read_snapshot)
    monkeypatch.setattr(registry, "delete_status_snapshot", s.delete_snapshot)
    yield s
    RunConsole._reset_for_tests()


def _new_console(tmp_path, name: str) -> RunConsole:
    inst = RunConsole()
    inst.configure(
        log_root=str(tmp_path / name),
        cancel_grace_seconds=0.5,
        emit=lambda *a, **k: None,
    )
    return inst


def _wait_terminal(inst: RunConsole, run_id: str, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = inst.status(run_id)
        if st is not None and st["status"] in {"SUCCESS", "FAILED", "CANCELED"}:
            return st
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} 未在 {timeout}s 内到终态")


def test_second_instance_same_key_rejected(stub, tmp_path):
    """全局互斥：A 启动后，B（另一 RunConsole 实例）同 key → RunKeyBusyError。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    run_id = a.start(run_key="jira:transsion", cmd=_SLOW_CMD, label="k")
    try:
        with pytest.raises(RunKeyBusyError):
            b.start(run_key="jira:transsion", cmd=_FAST_CMD, label="k")
        assert stub.keys["jira:transsion"] == run_id
    finally:
        a.cancel(run_id)
        _wait_terminal(a, run_id)


def test_finalize_releases_global_key(stub, tmp_path):
    """终态释放：A 的 run 结束后，B 可用同 key 启动。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    run_id = a.start(run_key="jira:nokia", cmd=_FAST_CMD, label="k")
    _wait_terminal(a, run_id)
    assert "jira:nokia" not in stub.keys
    assert run_id not in stub.owners

    run_b = b.start(run_key="jira:nokia", cmd=_FAST_CMD, label="k")
    _wait_terminal(b, run_b)


def test_fail_closed_when_registry_unavailable(stub, tmp_path):
    """注册表不可用 → 拒绝启动（fail-closed），本地状态回滚。"""
    stub.fail = True
    a = _new_console(tmp_path, "a")
    with pytest.raises(RunConsoleError) as ei:
        a.start(run_key="jira:oppo", cmd=_FAST_CMD, label="k")
    assert "registry unavailable" in str(ei.value)
    assert a.is_key_busy("jira:oppo") is False
    assert a._runs == {}


def test_owner_registration_rollback_on_failure(stub, tmp_path, monkeypatch):
    """owner 登记失败（acquire 成功之后）→ 互斥键回滚 + 本地状态回滚。"""
    a = _new_console(tmp_path, "a")

    def _boom(run_id: str, *, run_key: str) -> None:
        raise registry.ConsoleRegistryUnavailable("owner register down")

    monkeypatch.setattr(registry, "register_owner", _boom)
    with pytest.raises(RunConsoleError):
        a.start(run_key="jira:vivo", cmd=_FAST_CMD, label="k")
    assert "jira:vivo" not in stub.keys
    assert a.is_key_busy("jira:vivo") is False
    assert a._runs == {}


def test_renew_lost_triggers_self_abort(stub, tmp_path):
    """确认失去互斥（lost）→ 止损取消本 run（保全局互斥不变量）。"""
    a = _new_console(tmp_path, "a")
    run_id = a.start(run_key="jira:honor", cmd=_SLOW_CMD, label="k")
    try:
        stub.keys["jira:honor"] = "other-instance-run"  # 模拟被外部实例接管
        a._renew_registrations_once()
        st = a.status(run_id)
        assert st is not None and st["status"] == "CANCELED"
        assert st.get("error", "").startswith("run_key_lost")
    finally:
        a.cancel(run_id)
        _wait_terminal(a, run_id)


def test_renew_unavailable_does_not_abort(stub, tmp_path):
    """纯瞬态 Redis 错误（unavailable）→ 不误杀，等下个 tick。"""
    a = _new_console(tmp_path, "a")
    run_id = a.start(run_key="jira:xiaomi", cmd=_SLOW_CMD, label="k")
    try:
        stub.renew_result = registry.RENEW_UNAVAILABLE
        a._renew_registrations_once()
        st = a.status(run_id)
        assert st is not None and st["status"] == "RUNNING"
    finally:
        a.cancel(run_id)
        _wait_terminal(a, run_id)


def test_shutdown_releases_registry_entries(stub, tmp_path):
    """shutdown：显式兜底释放互斥键与 owner（CAS 幂等）。"""
    a = _new_console(tmp_path, "a")
    a.start(run_key="jira:realme", cmd=_SLOW_CMD, label="k")
    a.shutdown()
    assert "jira:realme" not in stub.keys
    assert stub.owners == {}


# ── P2：跨实例 status（快照）────────────────────────────────────────────────


def _wait_snapshot(stub, run_id: str, status: str, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = stub.snapshots.get(run_id)
        if snap is not None and snap.get("status") == status:
            return snap
        time.sleep(0.05)
    raise AssertionError(f"snapshot {run_id} 未在 {timeout}s 内到 {status}")


def test_cross_instance_status_reads_snapshot(stub, tmp_path):
    """B（另一实例）本地无 run → status 读 owner 快照（RUNNING 可见）。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    run_id = a.start(run_key="jira:transsion", cmd=_SLOW_CMD, label="k")
    try:
        st_b = b.status(run_id)
        assert st_b is not None
        assert st_b["status"] == "RUNNING"
        assert st_b["run_id"] == run_id
        assert st_b["run_key"] == "jira:transsion"
        assert "updated_at" in st_b
    finally:
        a.cancel(run_id)
        _wait_terminal(a, run_id)


def test_terminal_snapshot_kept_with_retention_ttl(stub, tmp_path):
    """终态快照保留（TTL=本地终态保留期）：run 结束后 B 仍可读 SUCCESS。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    run_id = a.start(run_key="jira:nokia", cmd=_FAST_CMD, label="k")
    _wait_terminal(a, run_id)
    snap = _wait_snapshot(stub, run_id, "SUCCESS")
    assert snap["exit_code"] == 0
    assert stub.snapshot_ttls[run_id] == int(a._terminal_retention_seconds)
    st_b = b.status(run_id)
    assert st_b is not None and st_b["status"] == "SUCCESS"


def test_snapshot_refresh_and_republish_when_missing(stub, tmp_path):
    """tick 续期快照 TTL；键丢失 → 重发全文。"""
    a = _new_console(tmp_path, "a")
    run_id = a.start(run_key="jira:vivo", cmd=_SLOW_CMD, label="k")
    try:
        a._renew_registrations_once()
        assert run_id in stub.refreshed
        stub.snapshots.pop(run_id)          # 键被淘汰/丢失
        stub.refreshed.clear()
        a._renew_registrations_once()
        assert run_id in stub.snapshots     # tick 重发全文
    finally:
        a.cancel(run_id)
        _wait_terminal(a, run_id)


def test_status_local_precedence(stub, tmp_path):
    """本地有 run → 优先本地（快照被篡改也不影响本实例视图）。"""
    a = _new_console(tmp_path, "a")
    run_id = a.start(run_key="jira:honor", cmd=_SLOW_CMD, label="k")
    try:
        stub.snapshots[run_id] = {"run_id": run_id, "status": "TAMPERED"}
        assert a.status(run_id)["status"] == "RUNNING"
    finally:
        a.cancel(run_id)
        _wait_terminal(a, run_id)


def test_snapshot_publish_failure_does_not_block_start(stub, tmp_path, monkeypatch):
    """best-effort：快照发布失败仅告警，run 照常启动并完成。"""
    a = _new_console(tmp_path, "a")

    def _boom(run_id: str, snapshot: dict, *, ttl_seconds: int) -> None:
        raise registry.ConsoleRegistryUnavailable("snapshot down")

    monkeypatch.setattr(registry, "publish_status_snapshot", _boom)
    run_id = a.start(run_key="jira:oppo", cmd=_FAST_CMD, label="k")
    _wait_terminal(a, run_id)


def test_sweep_deletes_snapshot_for_evicted_terminal_runs(stub, tmp_path, monkeypatch):
    """本地终态淘汰 → 同步清理快照（best-effort）。"""
    a = _new_console(tmp_path, "a")
    run_id = a.start(run_key="jira:realme", cmd=_FAST_CMD, label="k")
    _wait_terminal(a, run_id)
    assert run_id in stub.snapshots
    monkeypatch.setattr(a, "_terminal_retention_seconds", 0.0)
    a._sweep_terminal_runs()           # ended_at 距今 > 保留期(0) → 淘汰 + 删快照
    assert run_id not in stub.snapshots
