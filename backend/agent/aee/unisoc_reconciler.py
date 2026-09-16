"""UnisocUniviewReconciler — per-Job uniview watcher (ADR-0032 D8 w1)."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
from uuid import uuid4

from ..watcher.contracts import ContractViolation
from .collector import CollectorError
from .collectors.unisoc import UNIVIEW_INFO_FILENAME, UNIVIEW_ROOT
from .emit_intent import MAX_REPLAY_ATTEMPTS, load_intents, save_intents
from .mobilelog import make_adb_pull_fn
from .extraction_slot import host_extraction_slot
from .paths import get_aee_local_root
from .reconciler import (
    ReconcilerStats,
    _env_float,
    _env_int,
    _make_interruptible_adb_shell_fn,
    _parse_iso_dt,
    is_reconciler_enabled,
)

logger = logging.getLogger(__name__)

_UNISOC_STATE_PREFIX = "watcher:unisoc"
_PROCESSED_SUFFIX = "processed_event_dirs"
# 事件根以 collector 常量为单一真源（真机 Z2581/Z2582 确认：/data/ylog/uniview_exception）。
# 旧值 ("/data/uniview", "/data/vendor/uniview") 是**框架侧**目录，真机从未在其下出现
# 事件目录 → 采集恒空（#73）。
_DEVICE_UNIVIEW_ROOTS = (UNIVIEW_ROOT,)

#: #2010：``_processed`` 里"从未处理过"的哨兵（与「签名未知(None)」区分开）。
_SIGNATURE_UNKNOWN = object()
_STP_RC_MARKER = "__STP_RC__:"

#: #2083：``_emit_event`` 三态结果——决定调用方是否落签名。
#: ``NOT_REPORTABLE`` 是**确定性**结论（``CollectorError``：normalboot-only /
#: 空文件 / 截断）：不产生信号，但必须落签名，否则该目录每拍重拉。
_EMIT_RESULT_EMITTED = "emitted"
_EMIT_RESULT_NOT_REPORTABLE = "not_reportable"
_EMIT_RESULT_FAILED = "failed"

#: #2272：单目录**连续失败上限**。确定性失败（远端目录已消失、内容截断到
#: unievent_info 永不出现等）会让该目录每拍重拉整目录且不收敛——既耗 host 提取
#: 信号量，也不计入 ``signals_dropped``，操作侧无从察觉。达上限后放弃该目录并
#: 计入 ``signals_dropped``（与 MTK 侧 ``emit_intent.MAX_REPLAY_ATTEMPTS`` 同向；
#: 阈值取同值以保持两平台一致）。
MAX_DIR_ATTEMPTS = MAX_REPLAY_ATTEMPTS


def _unisoc_watcher_root(local_root: Path, run_date_stamp: Optional[str], serial: str) -> Path:
    stamp = run_date_stamp or "unknown"
    return local_root / "uniview_watcher" / stamp / serial


class UnisocUniviewReconciler:
    """Pull device uniview dirs → local tree → emit UNIVIEW reconciler signals."""

    def __init__(
        self,
        *,
        signal_emitter,
        state_store: Any,
        serial: str,
        job_id: int,
        host_id: str,
        adb_path: str = "adb",
        local_root: Optional[Path] = None,
        run_date_stamp: Optional[str] = None,
        baseline_interval_seconds: Optional[float] = None,
        plan_run_id: Optional[int] = None,
        platform: str = "UNISOC",
        device_log_client: Any = None,
        platform_collector: Any = None,
        shell_fn: Optional[Callable[[str, int], Optional[str]]] = None,
        pull_fn: Optional[Callable[[str, str, int], bool]] = None,
        on_self_shutdown: Optional[Callable[[], None]] = None,
        **_: Any,
    ) -> None:
        self._emitter = signal_emitter
        # #806：自关闭通知（与 MTK 路径同语义，见 reconciler._notify_self_shutdown）。
        self._on_self_shutdown = on_self_shutdown
        self._state_store = state_store
        self._serial = str(serial)
        self._job_id = int(job_id)
        self._host_id = str(host_id)
        self._adb_path = str(adb_path)
        self._local_root = Path(local_root) if local_root else get_aee_local_root()
        self._run_date_stamp = run_date_stamp
        self._plan_run_id = int(plan_run_id) if plan_run_id is not None else None
        self._platform = str(platform or "UNISOC")
        self._device_log_client = device_log_client
        self._platform_collector = platform_collector
        self._baseline = (
            baseline_interval_seconds
            if baseline_interval_seconds is not None
            else _env_float("STP_WATCHER_UNISOC_RECONCILE_INTERVAL_SECONDS", 180.0)
        )
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self.stats = ReconcilerStats()
        self._shell_fn = shell_fn or _make_interruptible_adb_shell_fn(
            self._serial, self._adb_path, self._stop_evt,
        )
        self._pull_fn = pull_fn or make_adb_pull_fn(self._serial, self._adb_path)
        # #2010：展锐会**复用同一个 event_id 目录**（新异常追加进目录，目录名不变），
        # 所以去重键必须是 (目录名 → 内容签名) 而不是仅目录名——否则同名目录被更新后
        # 永不重拉，重复异常在平台侧全部丢失（已用真机证据确认：同一 JE.103000004 里
        # 追加了 003-…tar.gz，event_count 1→3，平台却毫无记录）。
        # 签名取远端 `ls -l` 行中「名字之前」的部分（size+mtime），一次列举即可覆盖
        # 全部目录，不额外增加 shell 调用。旧状态是 list[str]（只有名字），加载时归一
        # 为 {name: None}，首个 tick 会对这些目录重新确认并补发一次。
        self._processed: Dict[str, Optional[str]] = {}
        #: 本拍远端列举到的签名（供发射侧判定；每拍重置）
        self._pending_signatures: Dict[str, str] = {}
        #: #2079：本拍**未能确认**「本地内容 == 远端签名」的名字（拉取失败）——
        #: 发射循环必须跳过，否则会拿陈旧本地内容当新异常上报。每拍重置。
        self._unconfirmed_local: Set[str] = set()
        #: #2272：``{name: 连续失败拍数}``。达 ``MAX_DIR_ATTEMPTS`` 即放弃该目录
        #: （计入 ``signals_dropped``），避免确定性失败项每拍重拉整目录。
        #: 成功一拍即清零（保留在 map 里的 0 值可被裁剪逻辑清理）。
        self._dir_attempts: Dict[str, int] = {}
        #: #2272：已放弃的目录名。**必须与 ``_processed`` 分开**——放弃语义是
        #: 「不再尝试拉取」，而 ``_processed[name] = ""`` 这种写法会被拉取循环的
        #: `prev == signature` 判据穿透（`"" != S` → 仍会重拉），等于没止损。
        self._abandoned_dirs: Set[str] = set()
        # #767：_processed 只增不减 + 整集重写会让状态存储按设备历史事件总量
        # 线性膨胀。去重语义要求保留的名字只有两类——仍在设备列表上（会被
        # 重拉）、仍在当前 stamp 本地树（会被重扫）；两者皆非的名字不可能再
        # 触发处理，可安全裁剪。裁剪按「连续 N 拍未见」滞回执行，设备列表
        # 失败（adb 抖动）当拍不裁剪且清零计数；体积硬上限仅作最后防线
        # （优先驱逐最久未见的）。state key 与 JSON 格式不变，存量全量集随
        # 列表恢复后自然收敛，无需迁移。
        self._absent_streak: Dict[str, int] = {}
        self._last_listed: Optional[Set[str]] = None
        self._prune_after_ticks = max(
            1, _env_int("STP_WATCHER_UNISOC_PROCESSED_PRUNE_AFTER_TICKS", 3),
        )
        self._max_processed_entries = _env_int(
            "STP_WATCHER_UNISOC_PROCESSED_MAX_ENTRIES", 20000,
        )
        self._state_lock = threading.Lock()
        self._max_consecutive_tick_errors = _env_int(
            "STP_WATCHER_UNISOC_RECONCILE_MAX_TICK_ERRORS", 5,
        )
        self._consecutive_tick_errors = 0

    def start(self) -> bool:
        if self._started:
            return True
        self._load_processed_state()
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"unisoc-reconciler-{self._serial}-{self._job_id}",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info(
            "unisoc_reconciler_started serial=%s job=%d interval=%.1fs",
            self._serial, self._job_id, self._baseline,
        )
        return True

    def stop(self, timeout: float = 5.0) -> ReconcilerStats:
        if not self._started:
            return self.stats
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._started = False
        logger.info(
            "unisoc_reconciler_stopped serial=%s job=%d stats=%s",
            self._serial, self._job_id, self.stats.to_dict(),
        )
        return self.stats

    def _state_key(self) -> str:
        return f"{_UNISOC_STATE_PREFIX}:{self._serial}:{_PROCESSED_SUFFIX}"

    def _load_processed_state(self) -> None:
        if self._state_store is None:
            return
        try:
            # LocalDb / StateStore API is get_state/set_state (#806/#1043).
            raw = self._state_store.get_state(self._state_key(), "")
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    # #2010 新格式：{name: signature}
                    self._processed = {
                        str(name): (None if sig is None else str(sig))
                        for name, sig in loaded.items()
                    }
                else:
                    # 旧格式 list[str]：签名未知 → 本拍重新确认一次（并补发漏掉的更新）
                    self._processed = {str(name): None for name in loaded}
        except Exception:
            logger.debug("unisoc_reconciler_state_load_failed", exc_info=True)

    def _save_processed_state(self) -> bool:
        """写回 processed 集；返回是否**确实落盘**（#2040 据此回收意图记录）。"""
        if self._state_store is None:
            return True
        try:
            self._state_store.set_state(
                self._state_key(),
                json.dumps(self._processed, sort_keys=True),
            )
            return True
        except Exception:
            logger.debug("unisoc_reconciler_state_save_failed", exc_info=True)
            return False

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.tick_once()
                self._consecutive_tick_errors = 0
            except Exception:
                self.stats.tick_errors += 1
                self._consecutive_tick_errors += 1
                logger.exception(
                    "unisoc_reconciler_tick_error serial=%s job=%d",
                    self._serial, self._job_id,
                )
                if self._consecutive_tick_errors >= self._max_consecutive_tick_errors:
                    # #806：自关闭同样要复位 watcher 抑制位（与 MTK 路径一致）。
                    self._notify_self_shutdown()
                    break
            if self._stop_evt.wait(self._baseline):
                break

    def _notify_self_shutdown(self) -> None:
        """#806：连续错误自关闭后通知外部；失败只记日志（自关闭必须完成）。"""
        callback = getattr(self, "_on_self_shutdown", None)
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.exception(
                "unisoc_reconciler_self_shutdown_notify_failed serial=%s job=%d",
                self._serial, self._job_id,
            )

    def tick_once(self) -> int:
        self.stats.ticks_total += 1
        root = _unisoc_watcher_root(self._local_root, self._run_date_stamp, self._serial)
        root.mkdir(parents=True, exist_ok=True)
        # #1043: produce local tree from device before emit loop.
        self._sync_device_events_to_local(root)
        emitted = 0
        recorded = 0
        recorded_names: List[str] = []
        local_names: Set[str] = set()
        for event_dir in sorted(root.iterdir()):
            if not event_dir.is_dir():
                continue
            local_names.add(event_dir.name)
            if event_dir.name.startswith("."):
                continue
            key = event_dir.name
            with self._state_lock:
                # #2010：仅「从未处理」或「远端签名变化」才发射；同签名不重复发
                prev = self._processed.get(key, _SIGNATURE_UNKNOWN)
                signature = self._pending_signatures.get(key)
                unconfirmed = key in self._unconfirmed_local
            if unconfirmed:
                # #2079：本拍拉取失败 → 本地内容不代表远端签名，本拍不发射
                # （pending 已回退，下一拍重试；不发陈旧内容，也不吞掉新内容）。
                continue
            if signature is None:
                # #2272：**本拍未能确认远端签名**的两种情况必须分开——
                #
                #   A) 远端**列到了**该目录，但 unievent_info 探测/拉取失败
                #      （`_pending_signatures` 被 pop）→ 本地内容不代表远端当前
                #      状态，本拍**不发射**。修复前只有 `prev` 已知时才跳过，
                #      `prev` 未知（首次见到）时会带 `None` 发射，随后把
                #      `signature or ""`（**空串**）写进 `_processed`：下一拍拿到
                #      真签名 `S` 时 `"" != S` → 既整目录重拉，又因 emit 意图簿按
                #      签名建键（`sig_key = signature or ""`）而**重新分配 seq_no
                #      与 DLE id**，同一条物理事件在平台侧落成**第二条记录**——
                #      即 #2040 想关掉的重复事实类在「签名不可得」这一拍仍敞开。
                #
                #   B) 本拍**远端列举整体失败**（`_last_listed is None`，如离线/
                #      adb 抖动）→ 该目录根本不在 `_pending_signatures` 里，历来
                #      会按「首次/本地预置」发射。**保持原行为**：这是本地预置与
                #      离线补发的既有通道，收紧它会误伤（实测 6 例既有用例）。
                #
                # 判据用 `_last_listed is not None`（本拍列举成功）而非直接看
                # `signature is None`——后者把 A、B 混为一谈。
                if self._last_listed is not None and key in self._last_listed:
                    continue
            if prev is not _SIGNATURE_UNKNOWN and (
                signature is None or prev == signature
            ):
                # `signature is None` 保留原语义（本拍签名仍不可得 → 不重复发）；
                # `prev == signature` 覆盖同签名不重发（#2010）。
                continue
            if not (event_dir / UNIVIEW_INFO_FILENAME).is_file():
                continue
            result = self._emit_event(event_dir, signature)
            if result == _EMIT_RESULT_FAILED:
                # 瞬时失败（其它 parse 异常 / emit 阶段）→ 不落签名，下一拍重试
                continue
            # #2083：确定性不可上报（normalboot-only 等）同样落签名，
            # 否则同步侧「已是最新」短路永不成立 → 每拍重拉。
            with self._state_lock:
                self._processed[key] = signature or ""
            recorded += 1
            recorded_names.append(key)
            if result == _EMIT_RESULT_EMITTED:
                emitted += 1
        if emitted:
            self.stats.ticks_with_new += 1
            self.stats.new_entries_total += emitted
        pruned = self._prune_processed(local_names)
        if recorded or pruned:
            if self._save_processed_state() and recorded_names:
                # #2040：processed 已持久化 → 这批 keys 不再需要（留着会被同名
                # 目录的下一次「新内容」误当重放复用，见 _drop_emit_intents）。
                self._drop_emit_intents(recorded_names)
        return emitted

    def _prune_processed(self, local_names: Set[str]) -> int:
        """#767：裁剪不可能再触发处理的名字，返回本拍移除数。

        判据：名字不在本拍设备列表（``_last_listed``，任一 root 传输失败
        则为 None）且不在当前 stamp 本地树，连续 ``_prune_after_ticks`` 拍
        如此才移除——设备仍存留的事件不会被重拉重发，本地树内的事件仍被
        重扫去重。传输失败当拍清零滞回计数（防 adb 抖动误删）；root 缺失
        （``ls`` rc≠0）视为该 root 权威空集，不阻塞整拍裁剪（#1820）。
        """
        listed = self._last_listed
        pruned_intent_names: List[str] = []
        with self._state_lock:
            if listed is None:
                if self._absent_streak:
                    self._absent_streak.clear()
                return 0
            pruned = 0
            for name in sorted(self._processed):
                if name in listed or name in local_names:
                    self._absent_streak.pop(name, None)
                    continue
                streak = self._absent_streak.get(name, 0) + 1
                if streak >= self._prune_after_ticks:
                    self._processed.pop(name, None)
                    self._absent_streak.pop(name, None)
                    pruned_intent_names.append(name)
                    pruned += 1
                else:
                    self._absent_streak[name] = streak
            if (
                self._max_processed_entries > 0
                and len(self._processed) > self._max_processed_entries
            ):
                overflow = len(self._processed) - self._max_processed_entries
                # #2060：victims 必须排除**仍在场**的名字（本拍设备列表或本地树）。
                # 驱逐在场条目会让下一拍以新 seq_no 重发——emit 循环唯一的去重就是
                # 本集合成员判定，而 _sync_device_events_to_local 对已同步目录不再重拉、
                # 也不会把名字加回来 → 稳态下每拍重复 emit 约 overflow 条 log_signal/DLE。
                absent_pool = [
                    name for name in self._processed
                    if name not in listed and name not in local_names
                ]
                # 最后防线：在**不在场**的名字里优先驱逐滞回计数最大（最久未见）的
                victims = sorted(
                    absent_pool,
                    key=lambda n: self._absent_streak.get(n, 0),
                    reverse=True,
                )[:overflow]
                for name in victims:
                    self._processed.pop(name, None)
                    self._absent_streak.pop(name, None)
                    pruned_intent_names.append(name)
                pruned += len(victims)
                if len(victims) < overflow:
                    # 上限是防膨胀的最后防线，不是必须精确命中：不足时宁可少驱逐，
                    # 也不能把在场条目踢出去（否则重复 emit）。
                    logger.warning(
                        "unisoc_reconciler_processed_cap_partial serial=%s job=%d "
                        "wanted=%d evicted=%d kept=%d present=%d",
                        self._serial, self._job_id, overflow, len(victims),
                        len(self._processed), len(absent_pool),
                    )
                else:
                    logger.warning(
                        "unisoc_reconciler_processed_cap_evicted serial=%s job=%d "
                        "evicted=%d kept=%d",
                        self._serial, self._job_id, len(victims), len(self._processed),
                    )
        if pruned:
            logger.info(
                "unisoc_reconciler_pruned serial=%s job=%d removed=%d kept=%d",
                self._serial, self._job_id, pruned, len(self._processed),
            )
        # #2040：名字已被裁剪 → 其意图记录一并回收（留着会被同名目录的下一次
        # 「新内容」误当重放复用旧 keys）。锁外做 I/O。
        if pruned_intent_names:
            self._drop_emit_intents(pruned_intent_names)
        return pruned

    def _list_remote_uniview_root(self, remote_root: str) -> Optional[Dict[str, str]]:
        """List event dirs under one device uniview root as ``{name: signature}``.

        #2010：签名 = ``ls -l`` 行里「名字之前」的字段（size + mtime），用来识别
        **同名目录的内容变化**——展锐复用同一 ``event_id`` 目录追加新异常，
        仅按名字去重会把这些新异常全部丢掉。

        Returns ``None`` on transport failure (``shell_fn`` returned None).
        Returns an empty dict when the root is missing or unreadable (``ls`` rc≠0).
        """
        listing = self._shell_fn(
            f"ls -l {remote_root} 2>/dev/null; echo {_STP_RC_MARKER}$?",
            30,
        )
        if listing is None:
            return None
        entries: Dict[str, str] = {}
        rc: Optional[int] = None
        for raw in listing.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(_STP_RC_MARKER):
                try:
                    rc = int(line[len(_STP_RC_MARKER):])
                except ValueError:
                    rc = None
                continue
            if line.startswith("total"):
                continue
            if line[0] not in "d-l":          # 只认目录/文件/链接行
                continue
            parts = line.split()
            if len(parts) < 6:
                continue
            name = parts[-1]
            if name in {".", ".."} or "/" in name:
                continue
            entries[name] = " ".join(parts[:-1])
        if rc is None:
            return None
        if rc != 0:
            return {}
        return entries

    def _sync_device_events_to_local(self, root: Path) -> int:
        """adb-list + pull new uniview event dirs into ``uniview_watcher`` (#1043).

        #767：顺带记录本拍设备列表（``_last_listed``）供裁剪判据使用。
        #1820：区分传输失败与 root 缺失——``shell_fn`` 返回 None 时整拍
        记为「未知」（None），当拍不做裁剪；``ls`` rc≠0 视为该 root 权威
        空集，其余 root 仍参与列表与裁剪；rc==0 按行解析名字。
        """
        pulled = 0
        listed: Set[str] = set()
        unconfirmed: Set[str] = set()
        listing_complete = True
        self._last_listed = None
        self._pending_signatures = {}
        for remote_root in _DEVICE_UNIVIEW_ROOTS:
            if self._stop_evt.is_set():
                listing_complete = False
                break
            root_entries = self._list_remote_uniview_root(remote_root)
            if root_entries is None:
                listing_complete = False
                continue
            listed.update(root_entries)
            for name, signature in root_entries.items():
                local_dir = root / name
                local_ready = (local_dir / UNIVIEW_INFO_FILENAME).is_file()
                with self._state_lock:
                    prev = self._processed.get(name, _SIGNATURE_UNKNOWN)
                    abandoned = name in self._abandoned_dirs
                if abandoned:
                    # #2272：已达连续失败上限 → 不再尝试拉取（否则每拍白拉整目录
                    # 且不收敛）。记账已在放弃时完成（signals_dropped + error 日志）。
                    continue
                # #2010：名字未变但签名变了（同目录被追加了新异常）→ 必须重拉
                self._pending_signatures[name] = signature
                if (
                    local_ready
                    and prev is not _SIGNATURE_UNKNOWN
                    and prev == signature
                ):
                    continue
                remote_dir = f"{remote_root}/{name}"
                info = self._shell_fn(
                    f"ls {remote_dir}/{UNIVIEW_INFO_FILENAME} 2>/dev/null", 10,
                )
                if not info:
                    # #2272：远端 unievent_info 探测失败 → 签名不可得。除回退 pending
                    # 外**同时记入 unconfirmed**，使本路径与 #2079 的拉取失败路径
                    # 在发射守卫处**同判**（此前只 pop 不记，靠发射循环的
                    # `signature is None` 兜底；两处判据不对称是 #2272 的成因之一）。
                    self._pending_signatures.pop(name, None)
                    unconfirmed.add(name)
                    self._note_dir_failure(name)
                    continue
                if self._pull_event_dir(remote_dir, local_dir):
                    pulled += 1
                    # 本地内容已刷新为该签名 → pending 保持不动（发射循环据此判断）
                    self._clear_dir_failure(name)
                    continue
                # #2079：拉取失败时本地目录仍是**旧签名**的内容，不得把 pending
                # 推进到远端新签名——否则发射循环会拿陈旧 unievent_info 上报为
                # 「新异常」，并在成功后记下新签名：真实新内容此后既不重拉、
                # 也不再发射（该 key 的签名不再变化）。
                self._pending_signatures.pop(name, None)
                unconfirmed.add(name)
                self._note_dir_failure(name)
        self._unconfirmed_local = unconfirmed
        if pulled:
            logger.info(
                "unisoc_reconciler_pulled serial=%s job=%d count=%d",
                self._serial, self._job_id, pulled,
            )
        self._last_listed = listed if listing_complete else None
        return pulled

    def _note_dir_failure(self, name: str) -> bool:
        """记录一次目录失败；达 ``MAX_DIR_ATTEMPTS`` → 放弃并返回 ``True``（#2272）。

        放弃语义：把该名字加入 ``_processed`` 并记一个**空签名**，使其此后
        同签名（含 `None`）不再触发重拉——即「确定性失败不要再无限重试」。
        同时计入 ``signals_dropped``，让操作侧可从指标看见（修复前是静默不收敛）。
        """
        with self._state_lock:
            attempts = self._dir_attempts.get(name, 0) + 1
            self._dir_attempts[name] = attempts
            if attempts < MAX_DIR_ATTEMPTS:
                return False
            # 达上限：放弃该目录（与其每拍白拉整目录，不如显式止损并可见）
            self._dir_attempts.pop(name, None)
            self._abandoned_dirs.add(name)
        self.stats.signals_dropped += 1
        logger.error(
            "unisoc_reconciler_dir_abandoned serial=%s job=%d dir=%s attempts=%d",
            self._serial, self._job_id, name, attempts,
        )
        return True

    def _clear_dir_failure(self, name: str) -> None:
        """成功一拍即清零连续失败计数（#2272）。"""
        with self._state_lock:
            self._dir_attempts.pop(name, None)

    def _pull_event_dir(self, remote_dir: str, local_dir: Path) -> bool:
        """Pull remote event directory; flatten ``adb pull`` nested basename if needed."""
        # #740: share host extraction budget with MTK processor pulls
        with host_extraction_slot(purpose=f"unisoc:{self._serial}"):
            return self._pull_event_dir_unlocked(remote_dir, local_dir)

    def _pull_event_dir_unlocked(self, remote_dir: str, local_dir: Path) -> bool:
        local_dir.mkdir(parents=True, exist_ok=True)
        staging = local_dir.parent / f".pulling_{local_dir.name}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            if not self._pull_fn(remote_dir, str(staging), 180):
                return False
            # adb pull dir → staging/<basename>/… or staging files
            nested = staging / local_dir.name
            source = nested if nested.is_dir() else staging
            if not (source / UNIVIEW_INFO_FILENAME).is_file():
                # one more nesting level sometimes
                candidates = [
                    p for p in staging.rglob(UNIVIEW_INFO_FILENAME) if p.is_file()
                ]
                if not candidates:
                    return False
                source = candidates[0].parent
            if local_dir.exists():
                shutil.rmtree(local_dir, ignore_errors=True)
            shutil.copytree(source, local_dir)
            return (local_dir / UNIVIEW_INFO_FILENAME).is_file()
        except Exception:
            logger.debug(
                "unisoc_reconciler_pull_failed remote=%s", remote_dir, exc_info=True,
            )
            return False
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _emit_event(self, event_dir: Path, signature: Optional[str]) -> str:
        """发射一条事件；返回 ``_EMIT_RESULT_*`` 三态（#2083）。

        ``NOT_REPORTABLE`` = ``parse_metadata`` 抛 ``CollectorError``（normalboot-only /
        空文件 / 截断）：确定性不可上报，调用方落签名避免每拍重拉；其它 parse 异常
        与 emit 阶段失败属瞬时问题（``FAILED``），不落签名、保留重试。

        #2040：效果之前**先持久化幂等 keys**（意图记录，与 MTK 路 #1719 同语义）。
        崩溃落在「已 emit、未落 processed」之间时，重启重扫同一目录会复用同一
        ``(job_id, seq_no)`` 与同一 DLE ``event_id``；否则重发拿到**新 seq_no**，
        控制面按 ``(job_id, seq_no)`` 去重拦不住 → 重复 log_signal + 重复 DLE。
        """
        if self._platform_collector is None:
            return _EMIT_RESULT_FAILED
        try:
            meta = self._platform_collector.parse_metadata(event_dir)
        except CollectorError:
            logger.debug("unisoc_reconciler_not_reportable dir=%s", event_dir)
            return _EMIT_RESULT_NOT_REPORTABLE
        except Exception:
            logger.debug("unisoc_reconciler_metadata_failed dir=%s", event_dir, exc_info=True)
            return _EMIT_RESULT_FAILED

        extra: Dict[str, Any] = {
            "schema_version": 2,
            "event_type": "UNIVIEW",
            "event_subtype": meta.event_subtype,
            "package_name": meta.package_name,
            # #785: aee_ts = 设备时钟原文（非 event_subtype）；漂移排查靠 aee_ts vs detected_at
            "aee_ts": meta.device_timestamp_raw or (
                meta.device_timestamp.isoformat() if meta.device_timestamp else None
            ),
            "aee_ts_utc": meta.device_timestamp.isoformat() if meta.device_timestamp else None,
            "nfs_path": str(event_dir),
            "pull_source": "reconciler",
            "entry_origin": "runtime",
        }
        dle_params: Dict[str, Any] = {
            "serial": self._serial,
            "platform": self._platform,
            "event_type": "UNIVIEW",
            "event_subtype": meta.event_subtype,
            "device_timestamp": (
                meta.device_timestamp.isoformat() if meta.device_timestamp else None
            ),
            "plan_run_id": self._plan_run_id,
            "job_id": self._job_id,
        }
        try:
            record = self._acquire_emit_intent(event_dir, signature, extra, dle_params)
            self._deliver_emit_intent(event_dir, record)
            return _EMIT_RESULT_EMITTED
        except ContractViolation as exc:
            self.stats.signals_dropped += 1
            logger.warning(
                "unisoc_reconciler_contract_violation serial=%s job=%d err=%s",
                self._serial, self._job_id, exc,
            )
            return _EMIT_RESULT_FAILED
        except Exception:
            self.stats.signals_dropped += 1
            logger.exception("unisoc_reconciler_emit_failed serial=%s job=%d", self._serial, self._job_id)
            return _EMIT_RESULT_FAILED

    def _acquire_emit_intent(
        self,
        event_dir: Path,
        signature: Optional[str],
        extra: Dict[str, Any],
        dle_params: Dict[str, Any],
    ) -> dict:
        """取该目录**当前内容签名**对应的幂等 keys；没有则分配并**先持久化**。

        签名不匹配的旧记录一律弃用（同一目录被追加新异常时必须是新 keys，否则
        新异常会被平台当作重复丢掉）；无 state_store（测试桩）时不落盘、每次新分配。
        """
        name = event_dir.name
        sig_key = signature or ""
        intents: Optional[Dict[str, dict]] = None
        if self._state_store is not None:
            intents = load_intents(self._state_store, self._state_key())
            existing = intents.get(name)
            if (
                isinstance(existing, dict)
                and existing.get("seq_no") is not None
                and existing.get("signature") == sig_key
            ):
                logger.debug(
                    "unisoc_reconciler_emit_intent_replay serial=%s job=%d dir=%s seq=%s",
                    self._serial, self._job_id, name, existing.get("seq_no"),
                )
                return existing
        # 只有确实要产生新效果时才分配 keys（重放路径不得白烧 seq_no）
        record = self._new_emit_intent(event_dir, sig_key, extra, dle_params)
        if intents is None:
            return record
        intents[name] = record
        # 先持久化 keys，后做效果——崩溃落在两者之间时重放复用同一幂等键
        save_intents(self._state_store, self._state_key(), intents)
        return record

    def _new_emit_intent(
        self,
        event_dir: Path,
        signature: str,
        extra: Dict[str, Any],
        dle_params: Dict[str, Any],
    ) -> dict:
        detected_at = datetime.now(timezone.utc)
        seq_no, envelope = self._emitter.prepare(
            category="UNIVIEW",
            source="reconciler",
            path_on_device=event_dir.name,
            detected_at=detected_at,
            artifact_uri=str(event_dir),
            extra=extra,
        )
        return {
            "signature": signature,
            "seq_no": int(seq_no),
            "envelope": envelope,
            # 首次观测时刻固化：重放不得产生第二个 detected_at（设备时钟不可信）
            "detected_at": detected_at.isoformat(),
            "dle_event_id": str(uuid4()),
            "dle_params": dict(dle_params),
        }

    def _deliver_emit_intent(self, event_dir: Path, record: dict) -> None:
        """按记录里的 keys 做效果：log_signal 入 outbox + DLE 预分配 UUID。

        两者都由 keys 保证幂等（outbox ``(job_id, seq_no)`` UNIQUE / 后端
        ``ON CONFLICT DO NOTHING``；DLE 按 ``id`` upsert，#1042/#1051），
        因此重放同一记录不会在平台侧产生第二条事实。
        """
        seq_no = int(record["seq_no"])
        self._emitter.enqueue(seq_no, dict(record["envelope"]))
        self.stats.signals_emitted += 1
        if self._device_log_client is None:
            return
        params = dict(record.get("dle_params") or {})
        device_ts = params.get("device_timestamp")
        self._device_log_client.create_local_event(
            serial=str(params.get("serial") or self._serial),
            platform=str(params.get("platform") or self._platform),
            event_type=str(params.get("event_type") or "UNIVIEW"),
            event_subtype=params.get("event_subtype"),
            detected_at=_parse_iso_dt(record.get("detected_at")) or datetime.now(timezone.utc),
            device_timestamp=_parse_iso_dt(device_ts),
            local_path=event_dir,
            plan_run_id=params.get("plan_run_id"),
            job_id=params.get("job_id"),
            link_signal_seq_no=seq_no,
            size_bytes=self._device_log_client.dir_size_bytes(event_dir),
            event_id=record.get("dle_event_id"),
        )

    def _drop_emit_intents(self, names: List[str]) -> None:
        """#2040：回收已无用的意图记录（processed 已持久化 / 名字已被裁剪）。

        必须与 processed 同步回收：记录留着会被同名目录的下一次「新内容」误认成
        重放而复用旧 keys，把新异常在平台侧当作重复丢掉。
        """
        if self._state_store is None or not names:
            return
        try:
            intents = load_intents(self._state_store, self._state_key())
            changed = False
            for name in names:
                if intents.pop(name, None) is not None:
                    changed = True
            if changed:
                save_intents(self._state_store, self._state_key(), intents)
        except Exception:
            logger.debug("unisoc_reconciler_intent_drop_failed", exc_info=True)


def resolve_unisoc_reconciler_enabled(host_id: Optional[str]) -> bool:
    return is_reconciler_enabled(host_id)
