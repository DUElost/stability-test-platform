"""SignalEmitter + OutboxDrainer — Agent 侧 log_signal 上送闭环。

职责划分（KISS）：
    SignalEmitter    per-Job 实例，调用方（DeviceLogWatcher）同步 emit()
                     → 只做 envelope 组装 + 幂等 seq_no 分配 + 写 LocalDB outbox。
                     不做网络 I/O，保证 emit 路径快速不阻塞事件循环。

    OutboxDrainer    进程级单例，后台线程周期性批量 POST /agent/log-signals。
                     成功 ack，失败 bump_attempts 不阻断后续批次。
                     后端 (job_id, seq_no) ON CONFLICT DO NOTHING 兜底幂等。

契约：
    envelope 字段形态见 backend/agent/watcher/contracts.py::LogSignalEnvelope
    后端端点 shape 见 backend/api/routes/agent_api.py::LogSignalBatchIn
    幂等键 (job_id, seq_no) 在 LocalDB UNIQUE 约束 + 后端 DB 约束双重保证。

生命周期：
    Agent 启动     →  OutboxDrainer.instance().configure(...).start()
    Job 开始       →  per-Job SignalEmitter(local_db, job_id, host_id, serial)
    异常事件       →  emitter.emit(category, source, path, ...)
    Agent 退出     →  OutboxDrainer.instance().stop(timeout=5)
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from .contracts import validate_log_signal

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# SignalEmitter — per-Job
# ----------------------------------------------------------------------

class SignalEmitter:
    """per-Job log_signal 发射器。同步写 LocalDB outbox；不做网络 I/O。

    seq_no 策略（用户决策，per-job 单调）：
        - 构造时从 LocalDB 读取 MAX(seq_no)+1 作为起点（Agent 重启后恢复）
        - emit() 持锁自增并写库
        - 幂等键 (job_id, seq_no) 与后端一致，冲突由 LocalDB UNIQUE 兜底

    线程安全：emit() 可被多线程并发调用（inotifyd/polling 多路来源场景）。
    """

    def __init__(
        self,
        *,
        local_db,
        job_id: int,
        host_id: str,
        device_serial: str,
    ) -> None:
        self._db = local_db
        self._job_id = int(job_id)
        self._host_id = str(host_id)
        self._device_serial = str(device_serial)
        self._lock = threading.Lock()
        # 恢复 seq_no 起点：Agent 重启后继续单调递增，避免与已持久化条目冲突
        self._next_seq = self._db.next_log_signal_seq_no(self._job_id)
        logger.debug(
            "signal_emitter_init job_id=%d serial=%s next_seq=%d",
            self._job_id, self._device_serial, self._next_seq,
        )

    def emit(
        self,
        *,
        category: str,
        source: str,
        path_on_device: str,
        detected_at: Optional[datetime] = None,
        artifact_uri: Optional[str] = None,
        sha256: Optional[str] = None,
        size_bytes: Optional[int] = None,
        first_lines: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        """同步写 outbox，返回分配的 seq_no。

        契约违规（非法 category / source / 缺字段）会抛 ContractViolation，
        由调用方捕获并记日志；不应进入 outbox 污染后续批次。
        """
        with self._lock:
            seq_no = self._next_seq
            self._next_seq += 1

        ts = detected_at or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        envelope: Dict[str, Any] = {
            "job_id":         self._job_id,
            "seq_no":         seq_no,
            "host_id":        self._host_id,
            "device_serial":  self._device_serial,
            "category":       category,
            "source":         source,
            "path_on_device": path_on_device,
            "detected_at":    ts.isoformat(),
        }
        if artifact_uri is not None:
            envelope["artifact_uri"] = artifact_uri
        if sha256 is not None:
            envelope["sha256"] = sha256
        if size_bytes is not None:
            envelope["size_bytes"] = size_bytes
        if first_lines is not None:
            envelope["first_lines"] = first_lines
        if extra is not None:
            envelope["extra"] = extra

        # Fail-fast：非法 envelope 不入库（避免脏数据被 drainer 反复重试失败）
        validate_log_signal(envelope)

        row_id = self._db.enqueue_log_signal(self._job_id, seq_no, envelope)
        if row_id is None:
            # UNIQUE 冲突（极端并发下的重复分配）— 仅日志警告，seq_no 已递增
            logger.warning(
                "signal_emitter_seq_conflict job_id=%d seq_no=%d (duplicate ignored)",
                self._job_id, seq_no,
            )
        return seq_no

    @property
    def job_id(self) -> int:
        return self._job_id

    @property
    def next_seq_preview(self) -> int:
        """仅供调试/测试观察：当前将要分配的 seq_no。"""
        with self._lock:
            return self._next_seq


# ----------------------------------------------------------------------
# OutboxDrainer — 进程级单例
# ----------------------------------------------------------------------

class OutboxDrainer:
    """进程级单例：周期性批量推送 outbox 中 acked=0 的 log_signal。

    运行模式：
        - 后台 daemon 线程；stop() 发停止信号 + join(timeout)
        - tick_once() 暴露单次刷出接口，便于单元测试直接驱动

    失败策略：
        - 整批 POST 失败 → 逐条 bump_attempts，留给下一轮重试
        - 不在此处做指数退避：靠 interval_seconds 节流已足够
        - 超高 attempts 的条目以后可接入死信（当前阶段 YAGNI）
    """

    _instance: Optional["OutboxDrainer"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._db = None
        self._api_url: str = ""
        self._agent_secret: str = ""
        self._interval: float = 5.0
        self._batch_size: int = 50
        self._request_timeout: float = 10.0
        # Prune 策略：避免 log_signal_outbox 无限增长（Agent 本地空间约束）
        # 默认每 16 次成功 tick 后 prune 一次，保留最近 1000 条已 ack 记录
        self._prune_every_n_ticks: int = 16
        self._prune_keep_recent: int = 1000
        self._ticks_since_prune: int = 0
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._configured: bool = False
        # 可注入的 HTTP session（方便测试替换）
        self._session: Optional[requests.Session] = None

    # ------------------------------------------------------------------
    # 单例 + 依赖注入
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "OutboxDrainer":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        """仅供测试：销毁单例，允许下个 case 重新 configure。"""
        with cls._instance_lock:
            if cls._instance is not None:
                try:
                    cls._instance.stop(timeout=1.0)
                except Exception:
                    pass
            cls._instance = None

    def configure(
        self,
        *,
        local_db,
        api_url: str,
        agent_secret: str = "",
        interval_seconds: float = 5.0,
        batch_size: int = 50,
        request_timeout: float = 10.0,
        session: Optional[requests.Session] = None,
        prune_every_n_ticks: int = 16,
        prune_keep_recent: int = 1000,
    ) -> "OutboxDrainer":
        self._db = local_db
        self._api_url = api_url.rstrip("/")
        self._agent_secret = agent_secret
        self._interval = float(interval_seconds)
        self._batch_size = int(batch_size)
        self._request_timeout = float(request_timeout)
        self._session = session
        self._prune_every_n_ticks = max(1, int(prune_every_n_ticks))
        self._prune_keep_recent = max(0, int(prune_keep_recent))
        self._configured = True
        logger.info(
            "outbox_drainer_configured api=%s interval=%.1fs batch=%d prune_every=%d",
            self._api_url, self._interval, self._batch_size, self._prune_every_n_ticks,
        )
        return self

    def is_configured(self) -> bool:
        return self._configured

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        if not self._configured:
            raise RuntimeError(
                "OutboxDrainer not configured — call configure(local_db, api_url, ...) first"
            )
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="outbox-drainer",
            daemon=True,
        )
        self._thread.start()
        logger.info("outbox_drainer_started")

    def stop(self, timeout: float = 5.0) -> None:
        """请求停止；不强杀。超时后线程仍可能在最后一次 tick 中，daemon 退出时自死。"""
        self._stop_evt.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("outbox_drainer_stopped")

    # ------------------------------------------------------------------
    # 主循环 + 单次 tick（测试可直接调用）
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                flushed = self.tick_once()
                # 有负载：短间隔继续消费；无负载：按 interval 等待
                if flushed > 0:
                    self._stop_evt.wait(min(0.5, self._interval))
                else:
                    self._stop_evt.wait(self._interval)
            except Exception:
                logger.exception("outbox_drainer_tick_unhandled")
                self._stop_evt.wait(self._interval)

    def tick_once(self) -> int:
        """单次批量刷出。返回本轮成功 ack 的条目数（0 表示空或失败）。"""
        if not self._configured or self._db is None:
            return 0
        batch = self._db.get_pending_log_signals(limit=self._batch_size)
        if not batch:
            return 0

        signals: List[Dict[str, Any]] = [row["envelope"] for row in batch]
        url = f"{self._api_url}/api/v1/agent/log-signals"
        headers = {"Content-Type": "application/json"}
        if self._agent_secret:
            headers["X-Agent-Secret"] = self._agent_secret

        try:
            session = self._session or requests
            resp = session.post(
                url,
                data=json.dumps({"signals": signals}),
                headers=headers,
                timeout=self._request_timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            err = str(exc)[:500]
            for row in batch:
                self._db.bump_log_signal_attempt(row["id"], err)
            logger.warning(
                "outbox_drainer_post_failed count=%d err=%s",
                len(batch), err,
            )
            return 0

        # 成功：批量 ack（后端 ON CONFLICT DO NOTHING 已保证幂等）
        for row in batch:
            self._db.ack_log_signal(row["id"])
        logger.debug(
            "outbox_drainer_flushed count=%d url=%s", len(batch), url,
        )

        # Prune 闭环：定期清理已 ack 的旧条目，防止 SQLite 无限增长
        self._ticks_since_prune += 1
        if self._ticks_since_prune >= self._prune_every_n_ticks:
            self._ticks_since_prune = 0
            try:
                pruned = self._db.prune_acked_log_signals(
                    keep_recent=self._prune_keep_recent,
                )
                if pruned:
                    logger.info(
                        "outbox_drainer_pruned deleted=%d kept=%d",
                        pruned, self._prune_keep_recent,
                    )
            except Exception:
                # prune 失败不影响主流程
                logger.exception("outbox_drainer_prune_failed")

        return len(batch)
