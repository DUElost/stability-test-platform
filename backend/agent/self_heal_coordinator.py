"""#2972 / ADR-0037 D9：主机 USB/ADB 自愈协调器（xHCI rebind + L3 adb offline）。

职责：
1. ``ClaimPauseGate``：进程内互斥门闸——自愈执行及重枚举冷却（15s）期间暂停
   ``claim_loop`` 认领（同时覆盖普通 ``effective_slots`` 与 ``vbus_slots``），
   并等待当前在途 claim tick 退出后再复核占用；
2. ``SelfHealCoordinator``：
   - xHCI 主控自动恢复（D9.5–D9.6）：门控合取 + 持闸复核（occupancy + local_db
     活跃作业为 0、门控快照鲜度 ≤ 1 心跳周期、非维护窗）+ 按 ``boot_id`` 持久化
     ``RebindFuse``（位于 ``BASE_DIR``，热更新同步目录 ``agent/`` 之外）+
     先 ``record_attempt`` 落盘再调 ``sudo -n stp-agent-priv xhci-rebind`` +
     ``probe_failed`` 立即转 ``hardware_suspect`` + 重枚举冷却后核验；
   - L3 批量 ``adb offline`` 非提权自愈（D9.7）：不经 root wrapper，同构遵守
     ``ClaimPauseGate`` 空闲窗互斥与独立 ``boot_id`` 持久化熔断规则；
   - 心跳 ``self_heal`` 状态快照输出（上报 ``hardware_suspect`` / ``need_human``
     供控制面展示于 ``/hosts`` 并升级告警）。
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from . import device_discovery
from .config import BASE_DIR
from .identity import read_boot_id
from .xhci_auto_rebind import (
    Action,
    GateInput,
    PersistentFuseStore,
    REASON_FUSE_BOOT,
    REASON_FUSE_CORRUPT,
    REASON_FUSE_PROBE_FAILED,
    REASON_FUSE_RETRY,
    REASON_FUSE_WINDOW,
    REASON_WRAPPER_UNAVAILABLE,
    RebindFuse,
    decide,
    evaluate_gate,
    is_usb_tree_verifiably_empty,
)

logger = logging.getLogger(__name__)

PRIV_WRAPPER = "/usr/local/sbin/stp-agent-priv"
XHCI_REBIND_SUBCOMMAND = "xhci-rebind"
PRIV_PROBE_TIMEOUT_SECONDS = 10.0
#: wrapper 内部总超时 30s；外层 35s 让 wrapper 的 STP_XHCI_REBIND_TIMEOUT 先行生效。
WRAPPER_REBIND_TIMEOUT_SECONDS = 35.0
#: 重枚举冷却窗口（Owner 2026-11-23 裁定：15s）。
REENUM_COOLDOWN_SECONDS = 15.0

MARKER_NO_PCI_IDS = "STP_XHCI_REBIND_NO_PCI_IDS"
MARKER_WRITE_FAILED = "STP_XHCI_REBIND_WRITE_FAILED"
MARKER_PARTIAL = "STP_XHCI_REBIND_PARTIAL"
MARKER_TIMEOUT = "STP_XHCI_REBIND_TIMEOUT"
MARKER_PROBE_FAILED = "STP_XHCI_REBIND_PROBE_FAILED"

_ALL_MARKERS = (
    MARKER_PROBE_FAILED,
    MARKER_TIMEOUT,
    MARKER_PARTIAL,
    MARKER_WRITE_FAILED,
    MARKER_NO_PCI_IDS,
)

_FUSE_NEED_HUMAN_REASONS = frozenset({
    REASON_FUSE_BOOT,
    REASON_FUSE_WINDOW,
    REASON_FUSE_CORRUPT,
})
_FUSE_HARDWARE_SUSPECT_REASONS = frozenset({
    REASON_FUSE_RETRY,
    REASON_FUSE_PROBE_FAILED,
})

_priv_xhci_cache: Optional[bool] = None


def priv_wrapper_has_xhci_rebind() -> bool:
    """本机 wrapper 是否具备 ``xhci-rebind`` 子命令（进程内缓存）。"""
    global _priv_xhci_cache
    if _priv_xhci_cache is None:
        try:
            proc = subprocess.run(
                ["sudo", "-n", PRIV_WRAPPER, "capabilities"],
                capture_output=True,
                text=True,
                timeout=PRIV_PROBE_TIMEOUT_SECONDS,
            )
            _priv_xhci_cache = proc.returncode == 0 and XHCI_REBIND_SUBCOMMAND in (
                proc.stdout or ""
            ).split()
        except (OSError, subprocess.SubprocessError):
            _priv_xhci_cache = False
    return _priv_xhci_cache


def extract_xhci_marker(stderr: str) -> Optional[str]:
    text = stderr or ""
    for marker in _ALL_MARKERS:
        if marker in text:
            return marker
    return None


def invoke_priv_xhci_rebind(
    *,
    timeout: float = WRAPPER_REBIND_TIMEOUT_SECONDS,
) -> Tuple[int, Optional[str], str]:
    """经 ``sudo -n stp-agent-priv xhci-rebind`` 执行无参数主控恢复。

    Returns:
        ``(exit_code, marker, detail)``
    """
    try:
        proc = subprocess.run(
            ["sudo", "-n", PRIV_WRAPPER, XHCI_REBIND_SUBCOMMAND],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 3, MARKER_TIMEOUT, f"outer_timeout_{timeout}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return 3, MARKER_WRITE_FAILED, str(exc)

    if proc.returncode == 0:
        return 0, None, (proc.stdout or "").strip()[:300]
    stderr = (proc.stderr or "").strip()
    marker = extract_xhci_marker(stderr)
    return proc.returncode, marker, stderr[:300]


class ClaimPauseGate:
    """进程内互斥门闸：协调 ``claim_loop`` 认领与自愈窗口（ADR-0037 D9.5）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._paused = False
        self._in_claim_ticks = 0

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @contextmanager
    def claim_scope(self) -> Iterator[bool]:
        """``process_claim_tick`` 入口使用：若门闸已暂停则立即 yield False 跳过认领。"""
        with self._lock:
            if self._paused:
                entered = False
            else:
                self._in_claim_ticks += 1
                entered = True
        if not entered:
            yield False
            return
        try:
            yield True
        finally:
            with self._lock:
                self._in_claim_ticks -= 1
                if self._in_claim_ticks == 0:
                    self._cv.notify_all()

    def acquire_pause(self, *, timeout: float = 5.0) -> bool:
        """置位暂停并等待在途 claim tick 退出；超时尚未退出则撤销暂停返回 False。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._lock:
            if self._paused:
                return False
            self._paused = True
            while self._in_claim_ticks > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._paused = False
                    self._cv.notify_all()
                    return False
                self._cv.wait(timeout=remaining)
            return True

    def release_pause(self) -> None:
        with self._lock:
            self._paused = False
            self._cv.notify_all()


def _fuse_state_label(fuse: RebindFuse, *, healing: bool, last_recovered: bool) -> Tuple[str, bool, Optional[str]]:
    reason = fuse.open_reason
    if reason in _FUSE_HARDWARE_SUSPECT_REASONS:
        return "hardware_suspect", True, reason
    if reason in _FUSE_NEED_HUMAN_REASONS:
        return "need_human", True, reason
    if reason:
        return "need_human", True, reason
    if healing:
        return "healing", False, None
    if last_recovered:
        return "recovered", False, None
    return "idle", False, None


class SelfHealCoordinator:
    """主机 USB/ADB 自愈协调器（单飞后台执行、持久化熔断、心跳状态上报）。"""

    def __init__(
        self,
        *,
        host_id: str,
        adb_path: str = "adb",
        claim_pause_gate: Optional[ClaimPauseGate] = None,
        get_occupancy_counts: Optional[Callable[[], Tuple[int, int]]] = None,
        get_local_db_active_count: Optional[Callable[[], int]] = None,
        state_dir: Path | str = BASE_DIR,
        boot_id_provider: Callable[[], str] = read_boot_id,
        wrapper_has_xhci: Callable[[], bool] = priv_wrapper_has_xhci_rebind,
        run_xhci_rebind: Callable[[], Tuple[int, Optional[str], str]] = invoke_priv_xhci_rebind,
        run_adb_reconnect: Callable[[str], bool] = device_discovery.adb_reconnect_offline,
        probe_usb_summary: Callable[[], Tuple[Optional[int], Optional[int], Optional[int]]] = device_discovery.count_usb_lsusb_summary,
        probe_adb_devices: Callable[[str], List[Dict[str, Any]]] = device_discovery.discover_devices,
        sleep_fn: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
        mono_clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = REENUM_COOLDOWN_SECONDS,
        background: bool = True,
    ) -> None:
        self._host_id = str(host_id)
        self._adb_path = adb_path
        self._claim_pause_gate = claim_pause_gate or ClaimPauseGate()
        self._get_occupancy_counts = get_occupancy_counts or (lambda: (0, 0))
        self._get_local_db_active_count = get_local_db_active_count or (lambda: 0)
        state_root = Path(state_dir)
        self._xhci_store = PersistentFuseStore(
            state_root / "xhci_rebind_fuse.json", boot_id_provider=boot_id_provider,
        )
        self._adb_store = PersistentFuseStore(
            state_root / "adb_reconnect_fuse.json", boot_id_provider=boot_id_provider,
        )
        self._wrapper_has_xhci = wrapper_has_xhci
        self._run_xhci_rebind = run_xhci_rebind
        self._run_adb_reconnect = run_adb_reconnect
        self._probe_usb_summary = probe_usb_summary
        self._probe_adb_devices = probe_adb_devices
        self._sleep = sleep_fn
        self._wall_clock = wall_clock
        self._mono_clock = mono_clock
        self._cooldown_seconds = float(cooldown_seconds)
        self._background = background

        self._lock = threading.Lock()
        self._xhci_fuse: RebindFuse = self._xhci_store.load(now=self._wall_clock())
        self._adb_fuse: RebindFuse = self._adb_store.load(now=self._wall_clock())
        self._empty_tree_ticks = 0
        self._mass_offline_ticks = 0
        self._last_adb_reconnect_mono: float = -1e9
        self._healing_kind: Optional[str] = None
        self._xhci_last_action_at: Optional[str] = None
        self._xhci_last_marker: Optional[str] = None
        self._xhci_last_recovered = False
        self._adb_last_action_at: Optional[str] = None
        self._adb_last_marker: Optional[str] = None
        self._adb_last_recovered = False
        self._worker: Optional[threading.Thread] = None

        # 来自控制面心跳响应的只读门控线索（默认安全保守：0 行台账 / 不放行）
        self._host_ledger_device_rows: int = 0
        self._in_maintenance: bool = False

    def update_control_plane_hints(
        self,
        *,
        host_ledger_device_rows: Optional[int] = None,
        in_maintenance: Optional[bool] = None,
    ) -> None:
        with self._lock:
            if isinstance(host_ledger_device_rows, int) and not isinstance(host_ledger_device_rows, bool):
                self._host_ledger_device_rows = max(0, host_ledger_device_rows)
            if isinstance(in_maintenance, bool):
                self._in_maintenance = in_maintenance

    def _reverify_idle_and_fresh(
        self,
        *,
        snapshot_monotonic: float,
        max_snapshot_age: float,
    ) -> Tuple[bool, Optional[str]]:
        """持暂停门闸后二次复核：occupancy、local_db、快照鲜度、维护窗（D9.5）。"""
        age = self._mono_clock() - snapshot_monotonic
        if age > max(0.0, max_snapshot_age):
            return False, "snapshot_stale"
        with self._lock:
            if self._in_maintenance:
                return False, "in_maintenance"
        try:
            active_jobs, active_devices = self._get_occupancy_counts()
        except Exception:
            return False, "occupancy_read_failed"
        if active_jobs > 0:
            return False, "active_jobs"
        if active_devices > 0:
            return False, "active_devices"
        try:
            persisted_active = int(self._get_local_db_active_count())
        except Exception:
            return False, "local_db_read_failed"
        if persisted_active > 0:
            return False, "local_db_active_jobs"
        return True, None

    def _launch_worker(self, kind: str, target: Callable[[], None]) -> bool:
        with self._lock:
            if self._healing_kind is not None:
                return False
            self._healing_kind = kind
        if not self._background:
            try:
                target()
            finally:
                with self._lock:
                    self._healing_kind = None
            return True

        def _wrapped() -> None:
            try:
                target()
            except Exception:
                logger.exception("self_heal_worker_failed kind=%s", kind)
            finally:
                with self._lock:
                    self._healing_kind = None

        thread = threading.Thread(
            target=_wrapped,
            name=f"self-heal-{kind}",
            daemon=True,
        )
        self._worker = thread
        thread.start()
        return True

    def maybe_trigger_xhci_rebind(
        self,
        *,
        enabled: bool,
        whitelist: frozenset[str],
        usb_device_count: Optional[int],
        usb_root_hub_count: Optional[int],
        other_usb_nodes: Optional[int],
        discovered_devices: int,
        active_jobs: int,
        active_devices: int,
        snapshot_monotonic: float,
        poll_interval: float,
    ) -> Tuple[Action, Optional[str]]:
        """每拍评估 xHCI 自动恢复门控与熔断；满足时异步（或同步测试模式）触发。"""
        now_wall = self._wall_clock()
        with self._lock:
            other_nodes = 0 if other_usb_nodes is None else int(other_usb_nodes)
            if other_usb_nodes is None:
                # lsusb 采集失败时不可证空
                verifiably_empty = False
            else:
                verifiably_empty = is_usb_tree_verifiably_empty(
                    usb_device_count, usb_root_hub_count, other_nodes, discovered_devices,
                )
            if verifiably_empty:
                self._empty_tree_ticks += 1
            else:
                self._empty_tree_ticks = 0
                if self._xhci_fuse.episode_failures > 0 and self._xhci_fuse.open_reason is None:
                    self._xhci_fuse.record_recovered()
                    self._xhci_store.save(self._xhci_fuse, now=now_wall)
            ticks = self._empty_tree_ticks
            ledger_rows = self._host_ledger_device_rows
            in_maintenance = self._in_maintenance
            fuse = self._xhci_fuse

        inp = GateInput(
            host_id=self._host_id,
            enabled=enabled,
            whitelist=whitelist,
            usb_device_count=usb_device_count,
            usb_root_hub_count=usb_root_hub_count,
            other_usb_nodes=other_nodes if other_usb_nodes is not None else 1,
            host_ledger_device_rows=ledger_rows,
            discovered_devices=discovered_devices,
            empty_tree_ticks=ticks,
            active_jobs=active_jobs,
            active_devices=active_devices,
            in_maintenance=in_maintenance,
        )
        gate = evaluate_gate(inp)
        action, reason = decide(gate, fuse, now=now_wall)
        if action != Action.REBIND:
            return action, reason
        if not self._wrapper_has_xhci():
            return Action.NOOP, REASON_WRAPPER_UNAVAILABLE

        launched = self._launch_worker(
            "xhci",
            lambda: self._execute_xhci_rebind(
                snapshot_monotonic=snapshot_monotonic,
                max_snapshot_age=max(float(poll_interval), 1.0),
            ),
        )
        if not launched:
            return Action.NOOP, "healing_in_progress"
        return Action.REBIND, None

    def _execute_xhci_rebind(
        self,
        *,
        snapshot_monotonic: float,
        max_snapshot_age: float,
    ) -> None:
        if not self._claim_pause_gate.acquire_pause(timeout=5.0):
            logger.info("xhci_self_heal_skipped reason=claim_pause_timeout")
            return
        try:
            ok, block = self._reverify_idle_and_fresh(
                snapshot_monotonic=snapshot_monotonic,
                max_snapshot_age=max_snapshot_age,
            )
            if not ok:
                logger.info("xhci_self_heal_aborted_after_recheck reason=%s", block)
                return

            now_wall = self._wall_clock()
            with self._lock:
                # 重读本地持久化状态防止并发/损坏态被绕过
                self._xhci_fuse = self._xhci_store.load(now=now_wall)
                check = self._xhci_fuse.check(now=now_wall)
                if not check.allowed:
                    logger.warning("xhci_self_heal_blocked_by_fuse reason=%s", check.block_reason)
                    return
                # D9.6：调用 wrapper 之前先记账并原子落盘
                self._xhci_fuse.record_attempt(now=now_wall)
                saved = self._xhci_store.save(self._xhci_fuse, now=now_wall)
                self._xhci_last_action_at = datetime.fromtimestamp(
                    now_wall, tz=timezone.utc
                ).isoformat()
                self._xhci_last_recovered = False
                if not saved:
                    self._xhci_fuse.open_reason = REASON_FUSE_CORRUPT
                    logger.error("xhci_self_heal_aborted_fuse_save_failed")
                    return

            rc, marker, detail = self._run_xhci_rebind()
            with self._lock:
                self._xhci_last_marker = marker or ("OK" if rc == 0 else f"exit_{rc}")

            if marker == MARKER_PROBE_FAILED:
                with self._lock:
                    self._xhci_fuse.record_probe_failed()
                    self._xhci_store.save(self._xhci_fuse, now=self._wall_clock())
                logger.error(
                    "xhci_self_heal_probe_failed host=%s detail=%s — tripped hardware_suspect",
                    self._host_id, detail,
                )
                return

            if rc != 0:
                with self._lock:
                    self._xhci_fuse.record_still_empty()
                    self._xhci_store.save(self._xhci_fuse, now=self._wall_clock())
                logger.warning(
                    "xhci_self_heal_wrapper_failed host=%s rc=%s marker=%s detail=%s",
                    self._host_id, rc, marker, detail,
                )
                return

            # D9.5：保持 claim_loop 暂停直到重枚举冷却（15s）结束
            if self._cooldown_seconds > 0:
                self._sleep(self._cooldown_seconds)

            usb_cnt, root_hubs, other_nodes = self._probe_usb_summary()
            recovered = (
                (usb_cnt is not None and usb_cnt > 0)
                or (other_nodes is not None and other_nodes > 0)
            )
            with self._lock:
                if recovered:
                    self._xhci_fuse.record_recovered()
                    self._empty_tree_ticks = 0
                    self._xhci_last_recovered = True
                    logger.info(
                        "xhci_self_heal_recovered host=%s usb=%s root_hubs=%s",
                        self._host_id, usb_cnt, root_hubs,
                    )
                else:
                    self._xhci_fuse.record_still_empty()
                    logger.warning(
                        "xhci_self_heal_still_empty host=%s usb=%s root_hubs=%s",
                        self._host_id, usb_cnt, root_hubs,
                    )
                self._xhci_store.save(self._xhci_fuse, now=self._wall_clock())
        finally:
            self._claim_pause_gate.release_pause()

    def maybe_trigger_adb_reconnect_offline(
        self,
        *,
        enabled: bool,
        devices_list: List[Dict[str, Any]],
        cooldown_seconds: float,
        active_jobs: int,
        active_devices: int,
        snapshot_monotonic: float,
        poll_interval: float,
    ) -> Tuple[Action, Optional[str]]:
        """L3 批量 ``adb offline`` 非提权自愈（D9.7 同构空闲窗互斥 + 持久化熔断）。"""
        if not enabled:
            return Action.NOOP, "disabled"
        offline_count = sum(1 for d in devices_list if d.get("adb_state") == "offline")
        total = len(devices_list)
        mass_offline = total > 0 and offline_count >= 2 and offline_count * 2 >= total
        now_wall = self._wall_clock()
        now_mono = self._mono_clock()

        with self._lock:
            if not mass_offline:
                self._mass_offline_ticks = 0
                if self._adb_fuse.episode_failures > 0 and self._adb_fuse.open_reason is None:
                    self._adb_fuse.record_recovered()
                    self._adb_store.save(self._adb_fuse, now=now_wall)
                return Action.NOOP, "not_mass_offline"
            self._mass_offline_ticks += 1
            if self._mass_offline_ticks < 2:
                return Action.NOOP, "ticks_short"
            if self._in_maintenance:
                return Action.NOOP, "in_maintenance"
            if active_jobs > 0:
                return Action.NOOP, "active_jobs"
            if active_devices > 0:
                return Action.NOOP, "active_devices"
            if now_mono - self._last_adb_reconnect_mono < cooldown_seconds:
                return Action.NOOP, "cooldown"
            fuse_gate = self._adb_fuse.check(now=now_wall)
            if not fuse_gate.allowed:
                reason = fuse_gate.block_reason
                if reason in _FUSE_HARDWARE_SUSPECT_REASONS:
                    return Action.STOP_HARDWARE_SUSPECT, reason
                return Action.STOP_NEED_HUMAN, reason

        launched = self._launch_worker(
            "adb_reconnect",
            lambda: self._execute_adb_reconnect(
                snapshot_monotonic=snapshot_monotonic,
                max_snapshot_age=max(float(poll_interval), 1.0),
            ),
        )
        if not launched:
            return Action.NOOP, "healing_in_progress"
        return Action.REBIND, None

    def _execute_adb_reconnect(
        self,
        *,
        snapshot_monotonic: float,
        max_snapshot_age: float,
    ) -> None:
        if not self._claim_pause_gate.acquire_pause(timeout=5.0):
            logger.info("adb_reconnect_self_heal_skipped reason=claim_pause_timeout")
            return
        try:
            ok, block = self._reverify_idle_and_fresh(
                snapshot_monotonic=snapshot_monotonic,
                max_snapshot_age=max_snapshot_age,
            )
            if not ok:
                logger.info("adb_reconnect_self_heal_aborted_after_recheck reason=%s", block)
                return

            now_wall = self._wall_clock()
            with self._lock:
                self._adb_fuse = self._adb_store.load(now=now_wall)
                check = self._adb_fuse.check(now=now_wall)
                if not check.allowed:
                    return
                self._adb_fuse.record_attempt(now=now_wall)
                saved = self._adb_store.save(self._adb_fuse, now=now_wall)
                self._last_adb_reconnect_mono = self._mono_clock()
                self._mass_offline_ticks = 0
                self._adb_last_action_at = datetime.fromtimestamp(
                    now_wall, tz=timezone.utc
                ).isoformat()
                self._adb_last_recovered = False
                if not saved:
                    self._adb_fuse.open_reason = REASON_FUSE_CORRUPT
                    return

            ok_cmd = False
            try:
                ok_cmd = bool(self._run_adb_reconnect(self._adb_path))
            except Exception as exc:
                logger.warning("adb_reconnect_self_heal_exec_error err=%s", exc)
                ok_cmd = False

            if self._cooldown_seconds > 0:
                self._sleep(self._cooldown_seconds)

            recovered = False
            if ok_cmd:
                try:
                    refreshed = self._probe_adb_devices(self._adb_path)
                    off = sum(1 for d in refreshed if d.get("adb_state") == "offline")
                    tot = len(refreshed)
                    recovered = tot > 0 and not (off >= 2 and off * 2 >= tot)
                except Exception:
                    recovered = False

            with self._lock:
                self._adb_last_marker = "OK" if recovered else ("CMD_FAILED" if not ok_cmd else "STILL_OFFLINE")
                if recovered:
                    self._adb_fuse.record_recovered()
                    self._adb_last_recovered = True
                else:
                    self._adb_fuse.record_still_empty()
                self._adb_store.save(self._adb_fuse, now=self._wall_clock())
        finally:
            self._claim_pause_gate.release_pause()

    def snapshot(self) -> Dict[str, Any]:
        """心跳 ``self_heal`` 摘要快照（供控制面展示于 ``/hosts`` 与熔断告警）。"""
        now_wall = self._wall_clock()
        with self._lock:
            self._xhci_fuse.prune(now_wall)
            self._adb_fuse.prune(now_wall)
            xhci_state, xhci_open, xhci_reason = _fuse_state_label(
                self._xhci_fuse,
                healing=self._healing_kind == "xhci",
                last_recovered=self._xhci_last_recovered,
            )
            adb_state, adb_open, adb_reason = _fuse_state_label(
                self._adb_fuse,
                healing=self._healing_kind == "adb_reconnect",
                last_recovered=self._adb_last_recovered,
            )

            # 顶层状态：hardware_suspect > need_human > healing > recovered > idle
            priority = {
                "hardware_suspect": 4,
                "need_human": 3,
                "healing": 2,
                "recovered": 1,
                "idle": 0,
            }
            if priority[xhci_state] >= priority[adb_state]:
                top_state, top_reason = xhci_state, xhci_reason
            else:
                top_state, top_reason = adb_state, adb_reason

            return {
                "state": top_state,
                "fuse_open": bool(xhci_open or adb_open),
                "fuse_reason": top_reason,
                "xhci": {
                    "state": xhci_state,
                    "fuse_open": xhci_open,
                    "fuse_reason": xhci_reason,
                    "boot_attempts": self._xhci_fuse.boot_attempts,
                    "window_attempts": len(self._xhci_fuse.window_attempts),
                    "episode_failures": self._xhci_fuse.episode_failures,
                    "last_action_at": self._xhci_last_action_at,
                    "last_marker": self._xhci_last_marker,
                },
                "adb_reconnect": {
                    "state": adb_state,
                    "fuse_open": adb_open,
                    "fuse_reason": adb_reason,
                    "boot_attempts": self._adb_fuse.boot_attempts,
                    "window_attempts": len(self._adb_fuse.window_attempts),
                    "episode_failures": self._adb_fuse.episode_failures,
                    "last_action_at": self._adb_last_action_at,
                    "last_marker": self._adb_last_marker,
                },
            }
