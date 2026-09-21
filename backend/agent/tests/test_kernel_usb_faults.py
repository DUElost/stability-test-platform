"""#2900：内核 USB 子系统故障信号（xHCI 死亡 / 慢性链路劣化）的判据与观测面。

回放素材取自 issue 的三例现场（8.87 / .102 / .63）逐字日志行——「对真实 host 注入不可
行，则以 journal 行做离线回放」即本文件的判据来源。
"""

from __future__ import annotations

import ast
import inspect
import threading
import time
from pathlib import Path

from backend.agent import heartbeat_thread as hb_mod
from backend.agent.capacity_reporter import compute_capacity
from backend.agent.kernel_usb_faults import (
    CHANNEL_OK,
    CHANNEL_STATES,
    CHANNEL_UNKNOWN,
    CHANNEL_UNAVAILABLE,
    LINK_ERROR_THRESHOLD,
    REASON_HC_DEAD,
    REASON_LINK_DEGRADED,
    KernelUsbFaults,
    KernelUsbWatch,
    parse_kernel_usb_faults,
    usb_kernel_fault_reasons,
)

# ── 现场日志（issue 三例同签名；时间戳/主机名已归一，语义逐字） ──────────────
XHCI_DEATH_LINES = [
    "xhci_hcd 0000:00:14.0: xHCI host not responding to stop endpoint command",
    "xhci_hcd 0000:00:14.0: xHCI host controller not responding, assume dead",
    "xhci_hcd 0000:00:14.0: HC died; cleaning up",
]
CHRONIC_LINK_LINES = [
    "usb 2-5: USB disconnect, device number 3",
    "usb 2-5: device not accepting address 4, error -71",
    "usb 2-5: device descriptor read/64, error -110",
    "usb 2-5: Maybe the USB cable is bad?",
    "hub 2-0:1.0: port 5 disabled by hub (EMI?), re-enabling...",
]


class TestParse:
    def test_xhci_death_signature_counts(self):
        faults = parse_kernel_usb_faults(XHCI_DEATH_LINES)
        assert faults.hc_dead == 1
        assert faults.not_responding == 1
        assert faults.hc_dead_seen is True
        assert faults.link_errors == 0

    def test_chronic_link_lines_count(self):
        faults = parse_kernel_usb_faults(CHRONIC_LINK_LINES)
        assert faults.link_errors == 2      # -71 / -110 各一行
        assert faults.cable_suspect == 1
        assert faults.hc_dead_seen is False

    def test_clean_log_is_clean(self):
        faults = parse_kernel_usb_faults(
            ["Linux version 6.12", "", "usb 1-1: new high-speed USB device number 2"]
        )
        assert faults == KernelUsbFaults(lines=2)

    def test_matching_is_case_insensitive(self):
        """journald 的 `-o cat` 与 dmesg 大小写一致，但内核版本/包装脚本可能改写。"""
        assert parse_kernel_usb_faults(["xHCI Host Controller Not Responding", "hc DIED"]).hc_dead_seen


class TestScan:
    """扫描器：把「读不到」与「读到且干净」分开（本会话反复出现的假绿形态）。"""

    @staticmethod
    def _run(monkeypatch, *, stdout="", stderr="", returncode=0):
        from backend.agent import kernel_usb_faults as kuf

        class _Proc:
            pass

        proc = _Proc()
        proc.stdout = stdout
        proc.stderr = stderr
        proc.returncode = returncode
        monkeypatch.setattr(kuf.subprocess, "run", lambda *a, **k: proc)
        return kuf.scan_kernel_usb_faults(since=None)

    def test_happy_path_parses_stdout(self, monkeypatch):
        faults = self._run(monkeypatch, stdout="\n".join(XHCI_DEATH_LINES) + "\n")
        assert faults is not None and faults.hc_dead_seen

    def test_permission_hint_is_unknown_not_clean(self, monkeypatch):
        """journalctl 看不到系统消息时 **exit 0**，只在 stderr 打提示——本机实测；

        若按「空输出 = 内核干净」处理，探测在缺权限的 host 上永远静默（假绿）。
        """
        faults = self._run(
            monkeypatch,
            stdout="\n",
            stderr=(
                "Hint: You are currently not seeing messages from other users and "
                "the system.\n      Users in groups 'adm', 'systemd-journal' can see "
                "all messages.\n"
            ),
        )
        assert faults is None

    def test_nonzero_rc_is_unknown(self, monkeypatch):
        assert self._run(monkeypatch, returncode=1, stderr="boom") is None

    def test_missing_journalctl_is_unknown(self, monkeypatch):
        from backend.agent import kernel_usb_faults as kuf

        def _boom(*a, **k):
            raise FileNotFoundError("journalctl")

        monkeypatch.setattr(kuf.subprocess, "run", _boom)
        assert kuf.scan_kernel_usb_faults(since=None) is None


class TestReasons:
    def test_blind_when_dead_and_no_usb_devices(self):
        assert usb_kernel_fault_reasons(
            hc_dead_latched=True, usb_device_count=0,
            link_errors_in_window=0, cable_suspect_in_window=0,
        ) == [REASON_HC_DEAD]

    def test_dead_but_devices_visible_is_not_blind(self):
        """死过但已 unbind/rebind 救回：设备回树即回落，不留永久红。"""
        assert usb_kernel_fault_reasons(
            hc_dead_latched=True, usb_device_count=23,
            link_errors_in_window=0, cable_suspect_in_window=0,
        ) == []

    def test_zero_devices_without_kernel_evidence_is_not_blind(self):
        """本来就没接设备 ≠ 失明：没有内核证据不许报。"""
        assert usb_kernel_fault_reasons(
            hc_dead_latched=False, usb_device_count=0,
            link_errors_in_window=0, cable_suspect_in_window=0,
        ) == []

    def test_unknown_device_count_is_not_zero(self):
        """枚举失败（None）不主张失明——未知 ≠ 0（与 count_usb_devices 同口径）。"""
        assert usb_kernel_fault_reasons(
            hc_dead_latched=True, usb_device_count=None,
            link_errors_in_window=0, cable_suspect_in_window=0,
        ) == []

    def test_link_degraded_threshold(self):
        assert usb_kernel_fault_reasons(
            hc_dead_latched=False, usb_device_count=5,
            link_errors_in_window=LINK_ERROR_THRESHOLD, cable_suspect_in_window=0,
        ) == [REASON_LINK_DEGRADED]
        assert usb_kernel_fault_reasons(
            hc_dead_latched=False, usb_device_count=5,
            link_errors_in_window=LINK_ERROR_THRESHOLD - 1, cable_suspect_in_window=0,
        ) == []

    def test_cable_suspect_alone_degrades(self):
        assert usb_kernel_fault_reasons(
            hc_dead_latched=False, usb_device_count=5,
            link_errors_in_window=0, cable_suspect_in_window=5,
        ) == [REASON_LINK_DEGRADED]


class _Clock:
    """可控时钟：monotonic / wall 分离推进，便于断言节流与增量游标。"""

    def __init__(self) -> None:
        self.mono = 1000.0
        self.wall = 1_700_000_000.0

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += seconds


class _Recorder:
    """记录每次扫描的 since 并回放预设结果；可选在扫描时阻塞。"""

    def __init__(self, results, gate: threading.Event | None = None) -> None:
        self.calls: list = []
        self._results = list(results)
        self._gate = gate
        self.scanned = threading.Event()

    def __call__(self, *, since=None):
        self.calls.append(since)
        if self._gate is not None:
            self._gate.wait(timeout=5)
        self.scanned.set()
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0] if self._results else None


def _watch(clock: _Clock, recorder: _Recorder) -> KernelUsbWatch:
    return KernelUsbWatch(
        interval_seconds=60.0,
        window_seconds=3600.0,
        scanner=recorder,
        monotonic=lambda: clock.mono,
        wall_clock=lambda: clock.wall,
    )


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestWatch:
    def test_first_scan_reads_whole_boot_then_incremental(self):
        clock, rec = _Clock(), _Recorder([KernelUsbFaults()])
        watch = _watch(clock, rec)

        assert watch.poll(usb_device_count=0) == []
        assert _wait(lambda: len(rec.calls) == 1)
        assert rec.calls == [None]          # 首扫 --boot：覆盖「开机即死 / agent 后起」

        first_scan_wall = clock.wall
        clock.advance(61)
        watch.poll(usb_device_count=0)
        assert _wait(lambda: len(rec.calls) == 2)
        # 增量游标 = **上次扫描开始时刻**（宁可重复读，不可漏读扫描期间产生的事件）
        assert rec.calls[1] == first_scan_wall

    def test_poll_is_throttled(self):
        clock, rec = _Clock(), _Recorder([KernelUsbFaults()])
        watch = _watch(clock, rec)
        watch.poll(usb_device_count=0)
        assert _wait(lambda: len(rec.calls) == 1)
        for _ in range(10):
            clock.advance(5)                # 心跳 5s 一拍
            watch.poll(usb_device_count=0)
        time.sleep(0.05)
        assert len(rec.calls) == 1          # 60s 内不再扫

    def test_poll_never_blocks_on_slow_scan(self):
        """journalctl 可能秒级：心跳主循环不得被拖慢（超时会被 watchdog 判 OFFLINE）。"""
        clock = _Clock()
        gate = threading.Event()
        rec = _Recorder([KernelUsbFaults()], gate=gate)
        watch = _watch(clock, rec)

        started = time.monotonic()
        watch.poll(usb_device_count=0)      # 扫描线程被 gate 卡住
        elapsed = time.monotonic() - started
        assert elapsed < 0.5, f"poll 阻塞了 {elapsed:.3f}s"
        gate.set()
        assert _wait(lambda: watch.poll(usb_device_count=0) == [])

    def test_latch_survives_when_devices_stay_invisible(self):
        clock, rec = _Clock(), _Recorder([KernelUsbFaults(not_responding=1, hc_dead=1)])
        watch = _watch(clock, rec)
        watch.poll(usb_device_count=0)
        assert _wait(lambda: rec.calls)
        assert _wait(lambda: watch.poll(usb_device_count=0) == [REASON_HC_DEAD])

        # 后续扫描没有新证据（日志增量里没有死亡行）：latch 仍在
        rec._results = [KernelUsbFaults()]  # noqa: SLF001 — 测试内直接改回放序列
        clock.advance(120)
        watch.poll(usb_device_count=0)
        clock.advance(120)
        assert _wait(lambda: watch.poll(usb_device_count=0) == [REASON_HC_DEAD])

    def test_devices_returning_clears_latch(self):
        clock, rec = _Clock(), _Recorder([KernelUsbFaults(hc_dead=1)])
        watch = _watch(clock, rec)
        watch.poll(usb_device_count=0)
        assert _wait(lambda: watch.poll(usb_device_count=0) == [REASON_HC_DEAD])

        # unbind/rebind 救回：设备回树 ⇒ 自动回落（不需要人工清位）
        assert watch.poll(usb_device_count=23) == []
        assert watch.poll(usb_device_count=0) == []   # 且不会因旧证据再次报

    def test_scan_failure_is_unknown_not_clean(self):
        clock, rec = _Clock(), _Recorder([None])
        watch = _watch(clock, rec)
        assert watch.poll(usb_device_count=0) == []
        assert _wait(lambda: rec.calls)
        assert watch.poll(usb_device_count=0) == []   # 无 reason，也不留半截状态

    def test_chronic_errors_accumulate_in_window(self):
        """单轮 7 条不报（正常热插拔抖动），窗口内累积到 20 才报（.102 的两周风暴）。"""
        clock, rec = _Clock(), _Recorder([KernelUsbFaults(link_errors=7)])
        watch = _watch(clock, rec)

        watch.poll(usb_device_count=5)              # 第 1 轮
        assert _wait(lambda: len(rec.calls) == 1)
        assert watch.poll(usb_device_count=5) == []  # 7 < 20

        clock.advance(61)
        watch.poll(usb_device_count=5)              # 第 2 轮
        assert _wait(lambda: len(rec.calls) == 2)
        assert watch.poll(usb_device_count=5) == []  # 14 < 20

        clock.advance(61)
        watch.poll(usb_device_count=5)              # 第 3 轮
        assert _wait(lambda: len(rec.calls) == 3)
        assert watch.poll(usb_device_count=5) == [REASON_LINK_DEGRADED]  # 21 ≥ 20


class TestCapacityIntegration:
    def _capacity(self, **kwargs) -> dict:
        return compute_capacity(
            active_job_count=0,
            active_device_count=0,
            online_healthy_devices=kwargs.pop("online_healthy_devices", 0),
            total_devices=kwargs.pop("total_devices", 0),
            system_stats={"cpu_load": 10, "ram_usage": 20, "disk_usage": {"usage_percent": 30}},
            mount_status={},
            **kwargs,
        )

    def test_usb_fault_reasons_degrade_host(self):
        health = self._capacity(usb_fault_reasons=[REASON_HC_DEAD])["health"]
        assert health["status"] == "DEGRADED"
        assert REASON_HC_DEAD in health["reasons"]

    def test_usb_fault_reasons_do_not_block_scheduling(self):
        """打闸口径归 #2902：本单只把 host 从 HEALTHY 拉成 DEGRADED。"""
        result = self._capacity(usb_fault_reasons=[REASON_HC_DEAD])
        assert result["health"]["status"] != "UNSCHEDULABLE"
        assert result["capacity"]["effective_slots"] == 0  # 无在线设备，本来就为 0

    def test_no_faults_keeps_health(self):
        health = self._capacity(online_healthy_devices=3, total_devices=3)["health"]
        assert health["status"] == "HEALTHY"
        assert health["reasons"] == []

    def test_duplicate_reason_not_repeated(self):
        health = self._capacity(
            usb_fault_reasons=[REASON_HC_DEAD, REASON_HC_DEAD],
        )["health"]
        assert health["reasons"].count(REASON_HC_DEAD) == 1


def test_heartbeat_tick_wires_kernel_usb_watch():
    """接线守卫：心跳必须把内核 watch 的判据传给 capacity（#2900）。

    用源码 AST 断言而不是造整条心跳桩：本单的失败形态是「探测写了但没接上」，
    接线本身就该被机械钉住（与仓内 SourceGuard 同思路，agent 测试自足不引控制面）。
    """
    source = Path(inspect.getsourcefile(hb_mod)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]
    poll_kwargs = [
        kw.arg for call in calls
        for kw in call.keywords
        if isinstance(call.func, ast.Attribute) and call.func.attr == "poll"
    ]
    assert "usb_device_count" in poll_kwargs, "心跳未调用内核 watch 的 poll"

    capacity_kwargs = [
        kw.arg for call in calls
        for kw in call.keywords
        if isinstance(call.func, ast.Name) and call.func.id == "compute_capacity"
    ]
    assert "usb_fault_reasons" in capacity_kwargs, (
        "心跳未把 usb_fault_reasons 传给 compute_capacity——探测算了但上不了报"
    )
    # #2957：通道可用性走的是**另一条**上报路径（capacity，不是 reason）。它最容易在
    # 重构时被当成"同一个 watch 的同一个结论"合并掉——合掉就等于把「没查过」重新
    # 塞回 reason 列表，控制面又会把它读成「查过且没问题」。
    state_attr_calls = [
        call.func.attr for call in calls
        if isinstance(call.func, ast.Attribute) and call.func.attr == "channel_state"
    ]
    assert state_attr_calls, "心跳未读取 watch.channel_state()——#2957 的通道态上不了报"
    assert "usb_kernel_log_channel" in capacity_kwargs, (
        "心跳未把 usb_kernel_log_channel 传给 compute_capacity"
    )


class TestChannelState:
    """#2957：判据**通道**的可用性必须单独可问——它决定控制面那条告警是「查过且干净」还是「没查过」。

    实测前提（本文件不重复取证，见 #2957）：Agent 以 `User=android` 运行、不在
    adm/systemd-journal 组，非特权 `journalctl -k` 退出码 0 且 stdout 只有
    `-- No entries --` ⇒ `scan_kernel_usb_faults` 返回 None。此时若只有 reason 列表，
    控制面看到的就是「48/48 台没有任何 USB 故障」，而真相是「48/48 台从未检查过」。
    """

    def test_unknown_until_first_scan_completes(self):
        clock, rec = _Clock(), _Recorder([KernelUsbFaults()])
        watch = _watch(clock, rec)
        assert watch.channel_state() == CHANNEL_UNKNOWN   # 还没扫过：不许冒充「正常」
        watch.poll(usb_device_count=3)
        assert _wait(lambda: len(rec.calls) == 1)
        assert _wait(lambda: watch.channel_state() == CHANNEL_OK)

    def test_blind_scan_is_unavailable_and_never_ok(self):
        """扫描返回 None ⇒ `unavailable`。这一条是 #2957 的全部动机：不能咽成「干净」。"""
        clock, rec = _Clock(), _Recorder([None])
        watch = _watch(clock, rec)
        reasons = watch.poll(usb_device_count=0)
        assert _wait(lambda: len(rec.calls) == 1)
        assert _wait(lambda: watch.channel_state() == CHANNEL_UNAVAILABLE)
        # 未知 ≠ 故障：通道黑时既不报失明、也不报正常
        assert reasons == []

    def test_channel_recovers_when_access_is_granted(self):
        """ops 加了组之后下一拍自动回 `ok`——本字段是**最近一次**结论，不是启动期快照。"""
        clock, rec = _Clock(), _Recorder([None, KernelUsbFaults(hc_dead=0)])
        watch = _watch(clock, rec)
        watch.poll(usb_device_count=0)
        assert _wait(lambda: watch.channel_state() == CHANNEL_UNAVAILABLE)
        clock.advance(61)
        watch.poll(usb_device_count=0)
        assert _wait(lambda: watch.channel_state() == CHANNEL_OK)

    def test_scan_crash_counts_as_unavailable_not_ok(self):
        """扫描器抛异常同样按「未知」处理：异常不等于「读到了干净日志」。"""
        clock = _Clock()

        def boom(*, since=None):
            raise RuntimeError("journalctl 不存在")

        watch = KernelUsbWatch(
            interval_seconds=60.0, window_seconds=3600.0, scanner=boom,
            monotonic=lambda: clock.mono, wall_clock=lambda: clock.wall,
        )
        watch.poll(usb_device_count=0)
        assert _wait(lambda: watch.channel_state() == CHANNEL_UNAVAILABLE)

    def test_reason_and_channel_vocabularies_are_disjoint(self):
        """reason 与通道态是两套词表，混用会让「传感器坏了」伪装成「设备坏了」。"""
        assert not ({REASON_HC_DEAD, REASON_LINK_DEGRADED} & set(CHANNEL_STATES))
        assert set(CHANNEL_STATES) == {CHANNEL_OK, CHANNEL_UNAVAILABLE, CHANNEL_UNKNOWN}


class TestReadabilityProbe:
    """#2957：0 行内核日志**不许**被读成「查过且干净」——本机实测的假绿形状。

    本类存在的理由是一次真实的判据失效：#2911 用 `_BLIND_HINT_MARKERS` 匹配 stderr 提示串
    来区分「读不到」与「干净」，而**该串在模块真正使用的 argv 形状下根本不出现**：

        $ journalctl -k --no-pager -o cat --boot     # 非特权用户
        rc=0  stdout=0 字节  stderr=0 字节           # ← 与「这台机没有内核日志」同形
        $ sudo journalctl -k --no-pager -o cat --boot | wc -l
        373600                                       # ← 真读到的形状

    于是 36/36 台已升级 host 全部上报 `usb_kernel_log=ok`，fleet 级"通道失明"告警恒绿。
    现有 `test_permission_hint_is_unknown_not_clean` 之所以没抓到它：它喂的是 `-n 3` /
    `--since -1h` 那类**别的**形状才会产生的 stderr——**桩的形状必须是被测调用的形状**。
    """

    @staticmethod
    def _fake(monkeypatch, *, window=("", "", 0), probe=("", "", 0)):
        """按 argv 分派输出：含 `--lines=1` 的是可读性探针，其余是窗口扫描。"""
        from backend.agent import kernel_usb_faults as kuf

        calls: list = []

        def _run(argv, **kwargs):
            class _P:
                pass

            proc = _P()
            is_probe = any("--lines=1" in a for a in argv)
            out, err, rc = probe if is_probe else window
            calls.append((list(argv), kwargs))
            proc.stdout, proc.stderr, proc.returncode = out, err, rc
            return proc

        monkeypatch.setattr(kuf.subprocess, "run", _run)
        return calls

    def test_real_blind_shape_is_unknown_not_clean(self, monkeypatch):
        """生产形状（rc=0/stdout 空/stderr 空）必须判未知——这就是现网 36 台的真实状态。"""
        from backend.agent import kernel_usb_faults as kuf

        self._fake(monkeypatch)  # 两个形状都空
        assert kuf.scan_kernel_usb_faults(since=None) is None
        assert kuf.scan_kernel_usb_faults(since=1_700_000_000.0) is None

    def test_quiet_window_still_counts_as_read(self, monkeypatch):
        """反向钉子：窗口 0 行但 boot 里有日志 = **静默窗**，不许误判成读不到。

        没有这条，修复会走成另一个假红：增量扫描在健康 host 上大多数时候就是 0 行。
        """
        from backend.agent import kernel_usb_faults as kuf

        self._fake(
            monkeypatch,
            window=("", "", 0),
            probe=("xhci_hcd 0000:00:14.0: HC died; cleaning up\n", "", 0),
        )
        faults = kuf.scan_kernel_usb_faults(since=1_700_000_000.0)
        assert faults is not None and faults.lines == 0

    def test_window_with_lines_needs_no_probe(self, monkeypatch):
        """读到东西就别再花一次 fork：探针只在 0 行时跑。"""
        from backend.agent import kernel_usb_faults as kuf

        calls = self._fake(
            monkeypatch, window=("\n".join(XHCI_DEATH_LINES) + "\n", "", 0)
        )
        faults = kuf.scan_kernel_usb_faults(since=None)
        assert faults is not None and faults.hc_dead_seen
        assert [a[0] for a in calls] == [
            ["journalctl", "-k", "--no-pager", "-o", "cat", "--boot"]
        ]

    def test_journalctl_runs_locale_pinned(self, monkeypatch):
        """提示串匹配依赖文本 ⇒ 子进程语言环境必须钉死（systemd 消息是翻译过的）。"""
        from backend.agent import kernel_usb_faults as kuf

        calls = self._fake(monkeypatch, window=("\n".join(XHCI_DEATH_LINES) + "\n", "", 0))
        kuf.scan_kernel_usb_faults(since=None)
        self._fake(monkeypatch)
        kuf.kernel_log_is_readable()
        assert len(calls) == 1, f"窗口有日志时不该再跑探针：{[c[0] for c in calls]}"
        # **每个** journalctl 调用都得钉 locale：只断言 calls[0] 会被「把 env 从另一处删掉」
        # 这种变异骗过（本单 M3 第一版实测就是这样假绿的）。
        for argv, kwargs in calls:
            env = kwargs.get("env")
            assert env is not None, f"{argv} 未固定语言环境——提示串匹配会随 locale 静默失效"
            assert env["LC_ALL"] == "C" and env["LANG"] == "C"
            assert env.get("PATH"), "env 必须是 os.environ 的副本，不能把 PATH 弄丢"

    def test_probe_failure_shapes_are_not_readable(self, monkeypatch):
        from backend.agent import kernel_usb_faults as kuf

        self._fake(monkeypatch, probe=("", "", 1))
        assert kuf.kernel_log_is_readable() is False
        self._fake(monkeypatch, probe=("   \n", "", 0))
        assert kuf.kernel_log_is_readable() is False
        self._fake(monkeypatch, probe=("one kernel line\n", "", 0))
        assert kuf.kernel_log_is_readable() is True

    def test_watch_reports_unavailable_on_the_real_shape(self, monkeypatch):
        """端到端：真 `scan_kernel_usb_faults` + 空 journalctl 形状 ⇒ 通道必须是 unavailable。

        这一条是给 `channel_state()` 兜底的：前面几条测判据，这条测**上报**——
        现网看到的正是这个字段，它报 `ok` 就等于替失明盖了章。
        """
        clock = _Clock()
        # 不伪造扫描结果：走真 `scan_kernel_usb_faults` + 真 argv，只把 journalctl 的
        # 输出替成实测形状——否则又会犯「桩的形状不是被测调用的形状」那个原错。
        self._fake(monkeypatch)
        watch = KernelUsbWatch(
            interval_seconds=60.0,
            window_seconds=3600.0,
            monotonic=lambda: clock.mono,
            wall_clock=lambda: clock.wall,
        )
        assert watch.channel_state() == CHANNEL_UNKNOWN
        assert watch.poll(usb_device_count=0) == []
        assert _wait(lambda: watch.channel_state() == CHANNEL_UNAVAILABLE), (
            "真扫描器在 rc=0/全空 的形状下仍把通道报成可读——现网 36 台的假绿就是这么来的"
        )
