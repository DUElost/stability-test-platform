"""flash_firmware v1.4.2（#3892，15.93 LX3 金丝雀 PlanRun 1255）：V5 回显时序。

V5 工具的 `com portName` 回显在 PIPE 块缓冲下晚于 tty 节点消失到达。1.4.2 用
`stdbuf -oL -eL` 启动 V5 vbus 工具，并在每次尝试里记录上电后见过的「节点 → 口」，
回显的口取实时与历史的并集：恰好一个口才用，没有或多于一个都止损。
"""
import pytest

from backend.agent.tests import test_flash_firmware_v140 as v140
from backend.agent.tests import test_flash_firmware_v141 as v141

ff = v140.ff
_SLOT = v141._SLOT_PORT
_NEIGHBOR = "1-5.1.4"
_ECHO = v141._V5_BOUND_LINE


class _History:
    def __init__(self, seen):
        self._seen = seen

    def ports(self, name):
        return frozenset(self._seen.get(name, ()))


@pytest.mark.parametrize("live,seen,bound,needle", [
    # 节点已消失，历史里只有目标口 → 核对通过（PlanRun 1255 的情形）
    (None, {_SLOT}, _SLOT, None),
    # 节点还在，与历史一致
    (_SLOT, {_SLOT}, _SLOT, None),
    # 历史与实时都没有 → 无法解析
    (None, set(), None, "cannot be resolved"),
    # 历史里只有邻口 → 错口
    (None, {_NEIGHBOR}, _NEIGHBOR, "!= target port"),
    # 同名节点在窗口内挂过两个口 → 歧义
    (None, {_SLOT, _NEIGHBOR}, None, "several USB ports"),
    # 历史是目标口，实时却是邻口 → 歧义，不得取任何一个
    (_NEIGHBOR, {_SLOT}, None, "several USB ports"),
])
def test_check_bound_echo_merges_live_and_history(monkeypatch, live, seen, bound, needle):
    calls = []
    monkeypatch.setattr(ff, "_tty_usb_port",
                        lambda name, base=None: calls.append(name) or live)
    got_bound, reason = ff._check_bound_echo(
        _ECHO, _SLOT, "S", v5_echo=True, tty_history=_History({"ttyACM0": seen}))
    assert got_bound == bound
    if needle is None:
        assert reason is None
    else:
        assert reason and needle in reason
    assert calls == ["ttyACM0"], "每行仍只查一次实时 sysfs"


def _sysfs_tty(tmp_path, port, name="ttyACM0"):
    parts = port.split(".")
    chain = [parts[0]] + [".".join(parts[:i]) for i in range(2, len(parts) + 1)]
    dev = tmp_path / "devices" / "usb1"
    for seg in chain:
        dev = dev / seg
    dev = dev / f"{port}:1.0" / "tty" / name
    dev.mkdir(parents=True)
    tty = tmp_path / "class_tty"
    tty.mkdir(exist_ok=True)
    (tty / name).symlink_to(dev)
    return tty


def test_history_remembers_vanished_node_and_reset_clears(tmp_path):
    tty = _sysfs_tty(tmp_path, _SLOT)
    hist = ff._TtyPortHistory(base=str(tty))
    hist.sample()
    (tty / "ttyACM0").unlink()
    assert ff._tty_usb_port("ttyACM0", str(tty)) is None
    hist.sample()
    assert hist.ports("ttyACM0") == {_SLOT}
    assert hist.snapshot() == {"ttyACM0": [_SLOT]}
    hist.reset()
    assert hist.ports("ttyACM0") == frozenset()


def test_history_drops_sample_that_straddles_reset(monkeypatch):
    hist = ff._TtyPortHistory(base="/nonexistent")

    def _list(base=None):
        hist.reset()  # 采样进行中被上电前的 reset 打断
        return ["ttyACM0"]

    monkeypatch.setattr(ff, "_list_acm_ttys", _list)
    monkeypatch.setattr(ff, "_tty_usb_port", lambda name, base=None: _NEIGHBOR)
    hist.sample()
    assert hist.ports("ttyACM0") == frozenset(), "旧窗口的口不得带进新窗口"


def test_history_thread_samples_until_stopped(tmp_path):
    tty = _sysfs_tty(tmp_path, _SLOT)
    hist = ff._TtyPortHistory(base=str(tty), interval=0.01)
    hist.start()
    try:
        for _ in range(200):
            if hist.ports("ttyACM0"):
                break
            import time
            time.sleep(0.01)
    finally:
        hist.stop()
    assert hist.ports("ttyACM0") == {_SLOT}


# ── 端到端：假工具在节点消失之后才回显 ─────────────────────────────────────

def _capture_histories(monkeypatch):
    made = []

    class _Manual(ff._TtyPortHistory):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)

        def start(self):  # 由测试显式调用 sample()，不起线程
            pass

    monkeypatch.setattr(ff, "_TtyPortHistory", _Manual)
    return made


def _late_echo_tool(h, monkeypatch, made, *, seen_port, before_power_on=None,
                    lines=(_ECHO, "All command exec done!")):
    """上电后节点出现（被采样）随即消失，回显在消失之后才到。"""
    present = {}
    monkeypatch.setattr(ff, "_list_acm_ttys", lambda base=None: list(present))
    monkeypatch.setattr(ff, "_tty_usb_port", lambda name, base=None: present.get(name))
    inner = h.launch

    def launch(cmd, *a, **k):
        if before_power_on is not None:
            present["ttyACM0"] = before_power_on
            made[-1].sample()
            present.clear()
        k["on_running"]()  # reset + 上电
        k = dict(k, on_running=lambda: None)
        present["ttyACM0"] = seen_port
        made[-1].sample()
        present.clear()
        for line in lines:
            reason = k["abort_check"](line)
            if reason:
                h.launched.append(cmd)
                h.launch_kwargs.append(k)
                raise ff.FlashToolAborted(reason)
        return inner(cmd, *a, **k)

    h.launch = launch


def test_vbus_v5_late_echo_after_tty_vanished_succeeds(tmp_path, monkeypatch, capsys):
    h = v141._v5_harness(monkeypatch, tmp_path)
    made = _capture_histories(monkeypatch)
    _late_echo_tool(h, monkeypatch, made, seen_port=_SLOT)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    attempt = payload["metrics"]["attempts"][-1]
    assert attempt["tty_history"] == {"ttyACM0": [_SLOT]}
    assert h.launched[-1][:3] == ["/usr/bin/stdbuf", "-oL", "-eL"]
    assert payload["metrics"]["identity"]["v5_stdbuf"] == "/usr/bin/stdbuf"


def test_vbus_v5_history_before_power_on_is_discarded(tmp_path, monkeypatch, capsys):
    # 上电前同名节点挂在邻口（别的设备）：reset 后不计入，回显仍按目标口核对通过。
    h = v141._v5_harness(monkeypatch, tmp_path)
    made = _capture_histories(monkeypatch)
    _late_echo_tool(h, monkeypatch, made, seen_port=_SLOT, before_power_on=_NEIGHBOR)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload


def test_vbus_v5_late_echo_on_neighbor_port_aborts(tmp_path, monkeypatch, capsys):
    h = v141._v5_harness(monkeypatch, tmp_path, {"max_attempts": 3})
    made = _capture_histories(monkeypatch)
    _late_echo_tool(h, monkeypatch, made, seen_port=_NEIGHBOR)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "!= target port" in payload["error_message"]
    assert len(h.launched) == 1, "不得重试"
    assert h.power == ["off", "on", "off"], "结算时断电隔离"


def test_vbus_v5_missing_stdbuf_fails_before_power(tmp_path, monkeypatch, capsys):
    h = v141._v5_harness(monkeypatch, tmp_path)
    monkeypatch.setattr(ff, "_shutil_which", lambda cmd: None)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False
    assert "stdbuf" in payload["error_message"]
    assert h.power == [] and h.launched == [] and h.locked == []


def test_vbus_v6_command_line_unchanged(tmp_path, monkeypatch, capsys):
    h = v140.Harness(monkeypatch, tmp_path, {})
    monkeypatch.setattr(ff, "_shutil_which", lambda cmd: "/usr/bin/stdbuf")
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert h.launched[-1][0].endswith("SPFlashToolV6")
    assert "tty_history" not in payload["metrics"]["attempts"][-1]


# ── 复核 #3924 第 1 轮：第 1、3、4 项的反例 ─────────────────────────────────

def test_live_lookup_precedes_history_snapshot(monkeypatch):
    # 第 1 项反例一：实时查询期间采样记入邻口、节点随即消失。先查实时后取快照，邻口必须计入。
    hist = ff._TtyPortHistory(base="/nonexistent")
    hist._seen["ttyACM0"] = [(0.0, _SLOT)]

    def _live(name, base=None):
        hist._seen["ttyACM0"].append((0.0, _NEIGHBOR))
        return None

    monkeypatch.setattr(ff, "_tty_usb_port", _live)
    bound, reason = ff._check_bound_echo(_ECHO, _SLOT, "S", v5_echo=True, tty_history=hist)
    assert bound is None and "several USB ports" in reason


def test_final_check_counts_sample_landing_after_line_check(tmp_path, monkeypatch, capsys):
    # 第 1 项：逐行核对之后才写入、但在回显之前开始的采样，最终判定必须计入。
    h = v141._v5_harness(monkeypatch, tmp_path, {"max_attempts": 3})
    made = _capture_histories(monkeypatch)
    _late_echo_tool(h, monkeypatch, made, seen_port=_SLOT)
    inner = h.launch

    def launch(cmd, *a, **k):
        out = inner(cmd, *a, **k)
        t, _ = made[-1]._seen["ttyACM0"][0]
        made[-1]._seen["ttyACM0"].append((t, _NEIGHBOR))  # 迟到的在途采样
        return out

    h.launch = launch
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False, payload
    assert payload["metrics"]["attempts"][-1]["outcome"] == "aborted-port-unverified"
    assert "not exactly target port" in payload["error_message"]
    assert len(h.launched) == 1 and h.power == ["off", "on", "off"]


def test_stale_reader_from_previous_attempt_cannot_verify(tmp_path, monkeypatch, capsys):
    # 第 1 项反例二：第 1 轮超时，遗留 reader 在第 2 轮才回调目标口回显；第 2 轮没有任何回显。
    import subprocess
    h = v141._v5_harness(monkeypatch, tmp_path, {"max_attempts": 2})
    _capture_histories(monkeypatch)
    monkeypatch.setattr(ff, "_tty_usb_port", lambda name, base=None: _SLOT)
    checks = []

    def launch(cmd, *a, **k):
        h.launched.append(cmd)
        k["on_running"]()
        checks.append(k["abort_check"])
        if len(checks) == 1:
            raise subprocess.TimeoutExpired(cmd, 1)
        assert checks[0](_ECHO) is None  # 旧回调：不得写入第 2 轮
        return v140._PASS_OUTPUT, 0

    h.launch = launch
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False, payload
    outcomes = [a["outcome"] for a in payload["metrics"]["attempts"]]
    assert outcomes == ["timeout", "aborted-port-unverified"]


def test_neighbor_sampled_before_power_on_returns_is_discarded(tmp_path, monkeypatch, capsys):
    # 第 3 项反例：wrapper 通电过程中（reset 原位置之后）采到邻口，随即消失。
    h = v141._v5_harness(monkeypatch, tmp_path)
    made = _capture_histories(monkeypatch)
    _late_echo_tool(h, monkeypatch, made, seen_port=_SLOT)
    power = h._fake_power
    present = {"ttyACM0": _NEIGHBOR}

    def _power(serial, action, slot):
        out = power(serial, action, slot)
        if action == "on" and made:
            # wrapper 返回之前（reset 的旧位置之后）采到邻口，随即消失。
            saved = (ff._list_acm_ttys, ff._tty_usb_port)
            ff._list_acm_ttys = lambda base=None: list(present)
            ff._tty_usb_port = lambda name, base=None: present.get(name)
            try:
                made[-1].sample()
            finally:
                ff._list_acm_ttys, ff._tty_usb_port = saved
        return out

    monkeypatch.setattr(ff, "_usb_power", _power)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is True, payload
    assert payload["metrics"]["attempts"][-1]["tty_history"] == {"ttyACM0": [_SLOT]}


def test_stop_reports_blocked_sampler_and_freezes_history(monkeypatch):
    # 第 4 项：采样阻塞在目录读取时，stop 返回 False；之后线程放行也写不进记录。
    import threading
    gate = threading.Event()
    entered = threading.Event()

    def _blocked(base=None):
        entered.set()
        gate.wait(5)
        return ["ttyACM0"]

    monkeypatch.setattr(ff, "_list_acm_ttys", _blocked)
    monkeypatch.setattr(ff, "_tty_usb_port", lambda name, base=None: _NEIGHBOR)
    hist = ff._TtyPortHistory(base="/nonexistent", interval=0.01)
    hist.start()
    assert entered.wait(5)
    assert hist.stop(timeout=0.05) is False
    gate.set()
    hist._thread.join(5)
    assert hist.ports("ttyACM0") == frozenset()
    assert hist.stop() is True


def test_sampler_not_stopped_fails_closed(tmp_path, monkeypatch, capsys):
    h = v141._v5_harness(monkeypatch, tmp_path, {"max_attempts": 3})
    made = _capture_histories(monkeypatch)
    _late_echo_tool(h, monkeypatch, made, seen_port=_SLOT)
    monkeypatch.setattr(ff._TtyPortHistory, "stop", lambda self, timeout=2.0: False)
    payload = h.run(monkeypatch, capsys)
    assert payload["success"] is False, payload
    assert "tty sampler did not stop" in payload["error_message"]
    assert len(h.launched) == 1 and h.power == ["off", "on", "off"]


def test_signal_settle_stops_sampler_before_isolating(tmp_path, monkeypatch, capsys):
    # 第 4 项：信号结算先停采样、冻结记录，再断电与释放锁。
    h = v141._v5_harness(monkeypatch, tmp_path)
    made = _capture_histories(monkeypatch)
    v141._feed_tool_lines(h, monkeypatch, [_ECHO], {"ttyACM0": _SLOT})
    seen = []
    power = h._fake_power

    def _power(serial, action, slot):
        if action == "off" and made and made[-1]._stop.is_set():
            seen.append(made[-1]._closed)
        return power(serial, action, slot)

    monkeypatch.setattr(ff, "_usb_power", _power)
    h.launch_script = ["signal"]
    h.run(monkeypatch, capsys)
    assert seen and seen[0] is True, "隔离断电前采样已停止并冻结"
