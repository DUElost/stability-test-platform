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
