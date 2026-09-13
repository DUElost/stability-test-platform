"""unisoc_signal_trigger v1.0.1：#73 展锐异常诱发/取证脚本。

验证（对照 test_aee_prepare_v100.py 范式）：
- 触发方式：``am crash <pkg>`` 为主、``kill -<sig>`` 兜底，二者都执行；
- **差分列表**只报"新增"条目（不把既有目录误报为事件）；
- 新条目出现时 dump 该目录并尝试读取 ``unievent_info.json``；
- 输出契约（stdout JSON success/metrics）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "unisoc_signal_trigger" / "v1.0.1"
)

spec = importlib.util.spec_from_file_location(
    "unisoc_signal_trigger_v101", _SCRIPT_DIR / "unisoc_signal_trigger.py"
)
ust = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ust)


class _FakeProc:
    def __init__(self, rc: int, stdout: str) -> None:
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


def _wire(monkeypatch, *, before: str = "", after: str = "",
          before_root: str = "/data/tombstones", pid: str = "4956"):
    """stub subprocess.run。

    ``ls -1 <root>`` 按**根**计数：该根第 1 次调用 = 触发前快照，
    之后 = 触发后（脚本每拍会对每个根都调一次，全局序列会错位）。
    """
    calls: list[str] = []
    seen: dict[str, int] = {}

    def _run(cmd, capture_output, text, timeout):  # noqa: ANN001
        shell_cmd = cmd[-1]
        calls.append(shell_cmd)
        c = shell_cmd.strip()
        if c == "echo ok":
            return _FakeProc(0, "ok")
        if "pidof" in c or "ps -A -o PID,NAME" in c:
            return _FakeProc(0, pid)
        if "monkey -p" in c:
            return _FakeProc(0, "Events injected: 1")
        if "am crash" in c:
            return _FakeProc(0, "")
        if "kill -" in c:
            return _FakeProc(0, "rc=0")
        if "getprop debug.uniview" in c:
            return _FakeProc(0, '["event_id":"104001002"]')
        if c.startswith("ls -1 "):
            # 命令形态： ls -1 <path> 2>/dev/null  → 取第 3 个 token 才是根
            root = c.split()[2]
            n = seen.get(root, 0)
            seen[root] = n + 1
            if root == before_root:
                return _FakeProc(0, before if n == 0 else after)
            return _FakeProc(0, "")
        if c.startswith("ls -la "):
            return _FakeProc(0, "total 9\nunievent_info.json")
        if c.startswith("cat "):
            return _FakeProc(0, '{"event_name":"NE"}')
        return _FakeProc(0, "")

    monkeypatch.setattr(ust.subprocess, "run", _run)
    return calls


def _capture(monkeypatch, *, params: dict | None = None, serial: str = "SERIAL1"):
    """跑 main()，返回 (stdout_json, SystemExit code)；假 time 保证不空转。"""
    monkeypatch.setattr(ust, "_serial", lambda: serial)
    if params is not None:
        monkeypatch.setattr(ust, "_params", lambda: params)

    clock = {"t": 0.0}

    def _now() -> float:
        clock["t"] += 1.0
        return clock["t"]

    monkeypatch.setattr(
        ust, "time",
        type("T", (), {"sleep": staticmethod(lambda *_: None), "time": staticmethod(_now)}),
    )

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            ust.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return (json.loads(lines[-1]) if lines else {}), code


class TestUnisocSignalTriggerV101:
    def test_missing_serial_fails(self, monkeypatch):
        _wire(monkeypatch)
        payload, code = _capture(monkeypatch, serial="")
        assert code == 1
        assert payload["success"] is False
        assert "STP_DEVICE_SERIAL" in payload["error_message"]

    def test_am_crash_and_kill_both_issued(self, monkeypatch):
        calls = _wire(monkeypatch)
        payload, code = _capture(monkeypatch, params={
            "package_name": "com.android.settings", "method": "am_crash",
            "poll_timeout_seconds": 0.01, "poll_interval_seconds": 0.0,
        })
        assert code == 0 and payload["success"] is True
        joined = "\n".join(calls)
        assert "am crash com.android.settings" in joined
        assert "kill -11 4956" in joined
        assert payload["metrics"]["pid"] == "4956"

    def test_only_new_entries_reported(self, monkeypatch):
        """差分列表：触发前有 tombstone_00，触发后新增 tombstone_01 → 只报后者。"""
        _wire(monkeypatch, before="tombstone_00", after="tombstone_00\ntombstone_01")
        payload, _ = _capture(monkeypatch, params={
            "poll_timeout_seconds": 5, "poll_interval_seconds": 0.0,
        })
        new = payload["metrics"]["new_entries"]
        assert new, "应检出新增条目"
        flat = [n for names in new.values() for n in names]
        assert "tombstone_01" in flat
        assert "tombstone_00" not in flat, "既有条目不得误报为事件"

    def test_event_dir_dump_attempts_unievent_info(self, monkeypatch):
        _wire(monkeypatch, before="", after="evt_1")
        payload, _ = _capture(monkeypatch, params={
            "poll_timeout_seconds": 5, "poll_interval_seconds": 0.0,
        })
        dumps = payload["metrics"]["event_dir_dump"]
        assert dumps, "应对新增目录做 dump"
        assert any(v.get("unievent_info_json") for v in dumps.values())

    def test_kill_method_still_sends_signal(self, monkeypatch):
        calls = _wire(monkeypatch)
        payload, _ = _capture(monkeypatch, params={
            "method": "kill", "signal": 6,
            "poll_timeout_seconds": 0.01, "poll_interval_seconds": 0.0,
        })
        joined = "\n".join(calls)
        assert "am crash" not in joined, "method=kill 时不应发 am crash（否则用例无判别力）"
        assert "kill -6 4956" in joined
        assert payload["metrics"]["method"] == "kill"
