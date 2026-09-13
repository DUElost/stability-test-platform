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
import threading
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
        self.cancel_requests: dict[str, dict] = {}
        self.cancel_acks: dict[str, dict] = {}
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

    # ── P3：取消转发 ────────────────────────────────────────────────────────

    def request_cancel(self, run_id: str, *, requested_at: str) -> None:
        if self.fail:
            raise registry.ConsoleRegistryUnavailable("stub down")
        self.cancel_requests[run_id] = {
            "requested_at": requested_at, "instance_id": "cp-test",
        }

    def read_cancel_request(self, run_id: str):
        return (
            dict(self.cancel_requests[run_id])
            if run_id in self.cancel_requests else None
        )

    def clear_cancel_request(self, run_id: str) -> None:
        self.cancel_requests.pop(run_id, None)

    def publish_cancel_ack(
        self, run_id: str, *, requested_at: str, canceled: bool,
    ) -> None:
        self.cancel_acks[run_id] = {
            "requested_at": requested_at, "canceled": canceled, "by": "cp-test",
        }

    def read_cancel_ack(self, run_id: str, *, requested_at: str):
        ack = self.cancel_acks.get(run_id)
        if ack is None or ack.get("requested_at") != requested_at:
            return None
        return dict(ack)


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
    monkeypatch.setattr(registry, "request_cancel", s.request_cancel)
    monkeypatch.setattr(registry, "read_cancel_request", s.read_cancel_request)
    monkeypatch.setattr(registry, "clear_cancel_request", s.clear_cancel_request)
    monkeypatch.setattr(registry, "publish_cancel_ack", s.publish_cancel_ack)
    monkeypatch.setattr(registry, "read_cancel_ack", s.read_cancel_ack)
    yield s
    RunConsole._reset_for_tests()


def _new_console(tmp_path, name: str, log_root: str | None = None) -> RunConsole:
    inst = RunConsole()
    inst.configure(
        log_root=log_root or str(tmp_path / name),
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


# ── P3：跨实例 cancel（请求位 + 有界等待 ack）────────────────────────────────


def test_cross_instance_cancel_forwards_and_waits_ack(stub, tmp_path, monkeypatch):
    """B 发起 cancel → 请求位投递 → A（owner）消费并 ack → B 返回 True，run 被取消。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    # 停掉 A 的自动 tick（60s），由测试手动驱动消费——避免与断言竞态
    monkeypatch.setattr(a, "_control_tick_seconds", lambda: 60.0)
    run_id = a.start(run_key="jira:transsion", cmd=_SLOW_CMD, label="k")
    results: list[bool] = []
    worker = threading.Thread(
        target=lambda: results.append(b.cancel(run_id)), daemon=True,
    )
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and run_id not in stub.cancel_requests:
            time.sleep(0.02)
        assert run_id in stub.cancel_requests, "取消请求应已投递"
        a._process_cancel_requests_once()
        worker.join(timeout=5)
        assert results == [True]
        assert a.status(run_id)["status"] == "CANCELED"
    finally:
        a.cancel(run_id)


def test_cross_instance_cancel_timeout_fail_closed(stub, tmp_path, monkeypatch):
    """无 owner 消费 → 有界等待超时 fail-closed（False），请求位仍留待 owner。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    monkeypatch.setattr(a, "_control_tick_seconds", lambda: 60.0)
    monkeypatch.setattr(b, "_cancel_wait_seconds", 0.3)
    run_id = a.start(run_key="jira:nokia", cmd=_SLOW_CMD, label="k")
    try:
        assert b.cancel(run_id) is False
        assert run_id in stub.cancel_requests   # 请求已投递（等 owner 下次 tick）
    finally:
        a.cancel(run_id)
        _wait_terminal(a, run_id)


def test_cross_instance_cancel_terminal_short_circuit(stub, tmp_path, monkeypatch):
    """终态短路：快照已终态 → 不投递请求，直接 False。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    monkeypatch.setattr(a, "_control_tick_seconds", lambda: 60.0)
    run_id = a.start(run_key="jira:honor", cmd=_FAST_CMD, label="k")
    _wait_snapshot(stub, run_id, "SUCCESS")
    assert b.cancel(run_id) is False
    assert run_id not in stub.cancel_requests


# ── P4：跨实例 replay（共享 log_root 前提）────────────────────────────────────


def test_cross_instance_read_log_from_shared_log_root(stub, tmp_path):
    """共享 log_root：B 读 A 写入的文件行，status/seq 由 P2 快照补全。"""
    shared = str(tmp_path / "shared-console")
    a = _new_console(tmp_path, "a", log_root=shared)
    b = _new_console(tmp_path, "b", log_root=shared)
    run_id = a.start(
        run_key="jira:transsion",
        cmd=[sys.executable, "-c", "print('hello'); print('world')"],
        label="k",
    )
    _wait_terminal(a, run_id)
    out = b.read_log(run_id)
    assert out["lines"] == ["hello", "world"]
    assert out["status"] == "SUCCESS"          # 非 UNKNOWN：快照补全
    assert out["seq"] == 2
    assert out.get("replay_unavailable") is None


def test_cross_instance_read_log_flags_missing_file(stub, tmp_path):
    """未共享 log_root：本实例无文件但 owner 有输出 → 显式 replay_unavailable。"""
    a = _new_console(tmp_path, "a")            # log_root = tmp/a
    b = _new_console(tmp_path, "b")            # log_root = tmp/b（未共享）
    run_id = a.start(
        run_key="jira:nokia",
        cmd=[sys.executable, "-c", "print('only-on-a')"],
        label="k",
    )
    _wait_terminal(a, run_id)
    out = b.read_log(run_id)
    assert out["lines"] == []
    assert out.get("replay_unavailable") is True
    assert out["status"] == "SUCCESS"           # 快照补全（不假装 UNKNOWN）
    assert out["seq"] == 1                      # owner 报告的 seq（提示仍有内容）


def test_cross_instance_read_log_flags_file_behind(stub, tmp_path):
    """文件落后于 owner 快照（部分共享/陈旧）→ 标记 replay_unavailable。"""
    a = _new_console(tmp_path, "a")
    b = _new_console(tmp_path, "b")
    run_id = a.start(
        run_key="jira:vivo",
        cmd=[sys.executable, "-c", "print('one'); print('two')"],
        label="k",
    )
    _wait_terminal(a, run_id)
    stale = tmp_path / "b" / f"{run_id}.log"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale\n", encoding="utf-8")
    out = b.read_log(run_id)
    assert out["lines"] == ["stale"]
    assert out.get("replay_unavailable") is True
    assert out["seq"] == 2                      # max(file=1, owner=2)
