"""DeviceLogWatcher — per-device 编排器。

把 InotifydSource (上游事件源) + EventBatcher (聚合) + SignalEmitter (持久化)
组装成一个完整的"单设备 watcher"。是 LogWatcherManager 在阶段 5 接入的真实
Worker（替换 manager.py 的 stub capability="stub"）。

职责（KISS）：
    - start()：启动 SignalEmitter（无 IO）→ EventBatcher 后台线程 → InotifydSource Popen
    - stop(drain, timeout)：先停 source（断上游）→ batcher.stop(drain) → 收尾 stats
    - stats：暴露 batcher.stats 与 capability，供 Manager.WatcherHandle 持续抓取

不在本类职责（明确边界）：
    - 文件 pull / artifact 上传（阶段 5 LogPuller 负责，本期 emit 时 artifact_uri=None）
    - 网络 outbox 上送（OutboxDrainer 进程级单例处理）
    - 重连：source 掉线由 DeviceLogWatcher 内部 watchdog 监控？—— 留待阶段 5 决策；
      当前阶段 source 死了即视为 watcher 不可用，由 Manager 阶段 5 决定如何处理

线程模型：
    - inotifyd 读线程（InotifydSource 内部）→ 调用 batcher.add_event
    - flusher 线程（EventBatcher 内部）→ 调用 _on_batch_emit / _on_immediate_emit
    - on_emit_* 内部 SignalEmitter.emit() 是线程安全的（持锁分配 seq_no）

容错：
    - SignalEmitter.emit() 抛 ContractViolation：记日志丢弃，不打断本批后续条目
    - 其它异常向上冒泡到 EventBatcher，由 batcher 的 try/except 兜底（已实现）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .batcher  import DEFAULT_IMMEDIATE_CATEGORIES, BatcherStats, EventBatcher
from .contracts import ContractViolation
from .emitter  import SignalEmitter
from .exceptions import WatcherStartError
from .policy   import WatcherPolicy
from .puller   import LogPuller, PullerStats
from .sources  import InotifydSource, ProbeResult, WatcherCapability, WatcherEvent

logger = logging.getLogger(__name__)


@dataclass
class WatcherStats:
    """per-DeviceLogWatcher 累计统计。

    覆盖 manager.WatcherHandle.stats 期望的 5 个字段，并补充 batcher / puller 内部细分。
    """

    events_total: int = 0
    events_dropped: int = 0          # 队列满 / contract 违规 / emit 异常
    pulls_ok: int = 0                # LogPuller 成功 pull 的次数
    pulls_failed: int = 0            # LogPuller 失败（含 oversized）的次数
    signals_emitted: int = 0
    immediate_emits: int = 0
    batch_emits: int = 0

    @classmethod
    def from_batcher(
        cls,
        b: BatcherStats,
        *,
        dropped_extra: int = 0,
        puller: Optional[PullerStats] = None,
    ) -> "WatcherStats":
        pulls_ok = puller.pulls_ok if puller else 0
        pulls_failed = (
            (puller.pulls_failed + puller.pulls_oversized + puller.submits_dropped)
            if puller else 0
        )
        return cls(
            events_total=b.events_total,
            events_dropped=b.events_deduped + dropped_extra,
            pulls_ok=pulls_ok,
            pulls_failed=pulls_failed,
            signals_emitted=b.signals_total,
            immediate_emits=b.immediate_emits,
            batch_emits=b.batch_emits,
        )

    def to_dict(self) -> Dict[str, int]:
        return {
            "events_total":    self.events_total,
            "events_dropped":  self.events_dropped,
            "pulls_ok":        self.pulls_ok,
            "pulls_failed":    self.pulls_failed,
            "signals_emitted": self.signals_emitted,
            "immediate_emits": self.immediate_emits,
            "batch_emits":     self.batch_emits,
        }


class DeviceLogWatcher:
    """单设备 Watcher Worker。一个 Job 一个实例，由 LogWatcherManager 创建/销毁。"""

    def __init__(
        self,
        *,
        adb_path: str,
        local_db,
        host_id: str,
        serial: str,
        job_id: int,
        policy: WatcherPolicy,
        capability: WatcherCapability,
        probe_result: Optional[ProbeResult] = None,
        puller: Optional[LogPuller] = None,
    ) -> None:
        self._adb_path = str(adb_path)
        self._host_id = str(host_id)
        self._serial  = str(serial)
        self._job_id  = int(job_id)
        self._policy  = policy
        self._capability = capability
        self._probe_result = probe_result
        self._puller = puller   # 5B1: immediate 路径富化，None = 直接 emit

        # SignalEmitter：持久化 → outbox（OutboxDrainer 异步上送）
        self._emitter = SignalEmitter(
            local_db=local_db,
            job_id=self._job_id,
            host_id=self._host_id,
            device_serial=self._serial,
        )

        # EventBatcher：聚合 + 路由
        self._batcher = EventBatcher(
            on_emit_immediate=self._on_immediate,
            on_emit_batch=self._on_batch,
            batch_interval_seconds=policy.batch_interval_seconds,
            batch_max_events=policy.batch_max_events,
            queue_maxsize=policy.event_queue_maxsize,
            immediate_categories=set(DEFAULT_IMMEDIATE_CATEGORIES),
        )

        # InotifydSource：上游订阅（capability=polling 时不启动）
        self._source: Optional[InotifydSource] = None
        if capability in (WatcherCapability.INOTIFYD_ROOT, WatcherCapability.INOTIFYD_SHELL):
            # 仅订阅 probe 出可访问的分类，避免 inotifyd 因不可读目录直接退出
            paths_subset = self._build_subscribed_paths()
            if paths_subset:
                self._source = InotifydSource(
                    adb_path=self._adb_path,
                    serial=self._serial,
                    paths_by_category=paths_subset,
                    on_event=self._batcher.add_event,
                )

        # 内部状态
        self._started = False
        self._stopped = False
        self._extra_dropped = 0     # SignalEmitter contract 违规等导致的额外丢弃

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def job_id(self) -> int:
        return self._job_id

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def capability(self) -> WatcherCapability:
        return self._capability

    def attach_puller(self, puller: LogPuller) -> None:
        """由 LogWatcherManager 在 start() 之前注入 puller。

        不能在 start() 之后调用（启动顺序依赖：puller 必须先于 source 启动）。
        单独暴露此方法是为了解构循环依赖：LogPuller.on_pull_done 指向 watcher._on_pull_done，
        所以必须先构造 watcher，再构造 puller，最后把 puller 装回 watcher。
        """
        if self._started:
            raise RuntimeError(
                f"attach_puller after start() is not allowed (serial={self._serial})"
            )
        self._puller = puller

    @property
    def stats(self) -> WatcherStats:
        return WatcherStats.from_batcher(
            self._batcher.stats,
            dropped_extra=self._extra_dropped,
            puller=self._puller.stats if self._puller is not None else None,
        )

    @property
    def signals_count(self) -> int:
        """SignalEmitter 已分配的 seq_no 总数（≈ 已落 outbox 的 signal 总数）。

        与 stats.signals_emitted 的差别：
          - signals_emitted 来自 batcher；只统计 batcher 调到了 _on_immediate / _on_batch 的次数
          - signals_count 来自 emitter；ContractViolation 时 batcher 已计数但 emitter 未分配 seq_no
        """
        # SignalEmitter.next_seq_preview 是"下一个将分配"的 seq_no；当前已分配 = preview - 1
        return max(0, self._emitter.next_seq_preview - 1)

    def start(self) -> None:
        """启动 puller（若有）→ batcher → source。重复 start 为 no-op。

        失败语义（硬失败）：
            source.start() 抛异常时：
              1. 回滚 batcher（stop drain=False），避免留下孤儿后台线程
              2. 回滚 puller（stop drain=False），避免 worker 孤儿
              3. _started 保持 False（允许上层决策后再重试 / 切降级）
              4. 抛 WatcherStartError(code="source_start_failed") 让 LogWatcherManager
                 统一按 policy.on_unavailable 决策；**绝不出现"manager 认为活跃但 source
                 实际没起来"的假活跃状态**（见阶段 5 风险收口）
        """
        if self._started:
            return

        if self._puller is not None:
            self._puller.start()
        self._batcher.start()
        try:
            if self._source is not None:
                self._source.start()
        except Exception as exc:
            # 回滚已启动的 batcher + puller，避免孤儿线程
            try:
                self._batcher.stop(drain=False, timeout=1.0)
            except Exception:
                logger.exception(
                    "device_log_watcher_rollback_batcher_failed serial=%s job=%d",
                    self._serial, self._job_id,
                )
            if self._puller is not None:
                try:
                    self._puller.stop(drain=False, timeout=1.0)
                except Exception:
                    logger.exception(
                        "device_log_watcher_rollback_puller_failed serial=%s job=%d",
                        self._serial, self._job_id,
                    )
            logger.exception(
                "device_log_watcher_source_start_failed serial=%s job=%d",
                self._serial, self._job_id,
            )
            raise WatcherStartError(
                f"source_start_failed serial={self._serial} job={self._job_id}: {exc}",
                code="source_start_failed",
                context={"serial": self._serial, "job_id": self._job_id, "cause": str(exc)[:200]},
            ) from exc

        self._started = True
        logger.info(
            "device_log_watcher_started serial=%s job=%d capability=%s source=%s puller=%s",
            self._serial, self._job_id, self._capability.value,
            "inotifyd" if self._source is not None else "none",
            "on" if self._puller is not None else "off",
        )

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> WatcherStats:
        """先停 source（断上游 inotifyd Popen）→ 停 batcher（drain 残余）→ 停 puller（drain pull）。

        顺序重要：
            1. source 停掉 → 不再有新 WatcherEvent 进入 batcher
            2. batcher drain 把残余事件分发完：AEE/VENDOR_AEE → puller.submit，ANR → _safe_emit
            3. puller drain 把 pull 队列排空 → 每个 event 最终 emit 到 outbox
               未完成的 pull 在 puller.stop timeout 后降级为空 enrichment，保证信号不丢

        返回最终 stats（供 Manager.WatcherHandle 回填）。
        """
        if self._stopped:
            return self.stats
        self._stopped = True

        # Phase 1: 断上游
        if self._source is not None:
            try:
                self._source.stop(timeout=timeout)
            except Exception:
                logger.exception(
                    "device_log_watcher_source_stop_failed serial=%s job=%d",
                    self._serial, self._job_id,
                )

        # Phase 2: drain batcher（同步把残余 pending flush 出去）
        try:
            self._batcher.stop(drain=drain, timeout=timeout)
        except Exception:
            logger.exception(
                "device_log_watcher_batcher_stop_failed serial=%s job=%d",
                self._serial, self._job_id,
            )

        # Phase 3: drain puller（把 pull 队列排空 → emit）
        if self._puller is not None:
            try:
                self._puller.stop(drain=drain, timeout=timeout)
            except Exception:
                logger.exception(
                    "device_log_watcher_puller_stop_failed serial=%s job=%d",
                    self._serial, self._job_id,
                )

        final = self.stats
        logger.info(
            "device_log_watcher_stopped serial=%s job=%d stats=%s",
            self._serial, self._job_id, final.to_dict(),
        )
        return final

    # ------------------------------------------------------------------
    # 内部回调（由 EventBatcher 在 flusher 线程中调用）
    # ------------------------------------------------------------------

    def _on_immediate(self, event: WatcherEvent) -> None:
        """AEE / VENDOR_AEE 类直通：若注入 puller 则走异步 pull + 富化；否则直接 emit。

        5B1：关键分叉点。puller 成功/失败都会回调 _on_pull_done → _safe_emit，
        保证 log_signal 最终落 outbox，不会因 pull 失败而丢信号。
        """
        if self._puller is not None:
            self._puller.submit(event)
        else:
            self._safe_emit(event)

    def _on_batch(self, events: List[WatcherEvent]) -> None:
        """ANR / MOBILELOG 聚合后批量 emit。

        5B1 明确：batch 路径 **不走 puller**，只记元数据（KISS）。
        ANR 事件量大、文件短、bugreport 通常独立覆盖，没必要逐条拉。
        """
        for ev in events:
            self._safe_emit(ev)

    def _on_pull_done(self, event: WatcherEvent, enrichment: Dict[str, Any]) -> None:
        """LogPuller 回调：pull 完成（成功或失败）→ emit 带 enrichment 的信封。

        enrichment 来自 LogPuller._do_pull：
            成功   →  { artifact_uri, sha256, size_bytes, first_lines }
            失败   →  {} （emit 时不写入这些字段）
            超大   →  { size_bytes } （artifact_uri=None，本地文件已删）

        5B2：pull 成功（artifact_uri 非空）且为 AEE/VENDOR_AEE → 额外异步提交到
        ArtifactUploader。uploader 是 fire-and-forget 单例，失败不影响主 emit 链路。
        """
        self._safe_emit(event, enrichment=enrichment)
        self._maybe_submit_artifact(event, enrichment)

    def _maybe_submit_artifact(
        self, event: WatcherEvent, enrichment: Dict[str, Any],
    ) -> None:
        """仅 AEE / VENDOR_AEE 且 pull 成功（有 artifact_uri）时转发到 ArtifactUploader。

        严格边界（5B2）：
            - ANR / MOBILELOG 不走（快路径只记元数据）
            - 超大文件（artifact_uri=None，仅 size_bytes）不走
            - 任何异常必须被吞：log_signal 已 emit 成功，artifact 入库失败不能回退
        """
        artifact_uri = enrichment.get("artifact_uri")
        if not artifact_uri:
            return
        cat_to_type = {
            "AEE": "aee_crash",
            "VENDOR_AEE": "vendor_aee_crash",
        }
        artifact_type = cat_to_type.get(event.category)
        if artifact_type is None:
            return
        try:
            from backend.agent.artifact_uploader import ArtifactUploader
            ArtifactUploader.instance().submit(
                job_id=self._job_id,
                artifact_type=artifact_type,
                storage_uri=str(artifact_uri),
                size_bytes=enrichment.get("size_bytes"),
                checksum=enrichment.get("sha256"),
                source_category=event.category,
                source_path_on_device=event.full_path,
            )
        except Exception:
            logger.exception(
                "device_log_watcher_artifact_submit_failed serial=%s job=%d uri=%s",
                self._serial, self._job_id, artifact_uri,
            )

    def _safe_emit(
        self,
        event: WatcherEvent,
        *,
        enrichment: Optional[Dict[str, Any]] = None,
    ) -> None:
        """统一 emit 出口：吞 ContractViolation 不打断后续；其它异常上冒。

        enrichment 可选；若提供则透传 artifact_uri / sha256 / size_bytes / first_lines。
        """
        enrichment = enrichment or {}
        try:
            self._emitter.emit(
                category=event.category,
                source="inotifyd",
                path_on_device=event.full_path,
                detected_at=event.detected_at,
                artifact_uri=enrichment.get("artifact_uri"),
                sha256=enrichment.get("sha256"),
                size_bytes=enrichment.get("size_bytes"),
                first_lines=enrichment.get("first_lines"),
            )
        except ContractViolation as exc:
            self._extra_dropped += 1
            logger.warning(
                "device_log_watcher_contract_violation serial=%s job=%d cat=%s file=%s err=%s",
                self._serial, self._job_id, event.category, event.filename, exc,
            )
        except Exception:
            self._extra_dropped += 1
            logger.exception(
                "device_log_watcher_emit_failed serial=%s job=%d cat=%s file=%s",
                self._serial, self._job_id, event.category, event.filename,
            )

    def _build_subscribed_paths(self) -> Dict[str, List[str]]:
        """根据 probe_result 过滤出可订阅的分类 → 路径列表。

        若 probe_result 为 None，回退为 policy.paths（不裁剪；可能 inotifyd 因部分目录
        不可读而退出，但保留行为简单：上层（Manager）应根据 capability 决定是否启用）。
        """
        if self._probe_result is None:
            return dict(self._policy.paths)
        accessible = set(self._probe_result.accessible_categories)
        return {
            cat: list(paths) for cat, paths in self._policy.paths.items()
            if cat in accessible
        }


__all__ = [
    "DeviceLogWatcher",
    "WatcherStats",
]
