"""HTTP client for control-plane DeviceLogEvent API (ADR-0028 D1)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import requests

logger = logging.getLogger(__name__)

# 与 log_signal/step_trace outbox 对齐（watcher/emitter._MAX_ATTEMPTS=10）：
# 持续失败的 create 意图达阈值转死信，避免队首饿死（#1204）。
_MAX_REGISTER_ATTEMPTS = 10

# Bound from Agent main after LocalDB.initialize (#1042 durable create intents).
_bound_local_db: Any = None


def bind_local_db(local_db: Any) -> None:
    """Attach Agent LocalDB for DLE create-intent outbox (#1042)."""
    global _bound_local_db
    _bound_local_db = local_db


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class DeviceLogEventClient:
    api_url: str
    agent_secret: str
    host_id: str
    timeout: float = 15.0
    local_db: Any = field(default=None, repr=False)

    @classmethod
    def from_env(cls, *, api_url: str, agent_secret: str, host_id: str) -> Optional["DeviceLogEventClient"]:
        # #287：默认开启；未配置连接参数时仍返回 None（自然 no-op）。
        if not _env_truthy("STP_DEVICE_LOG_EVENT_ENABLED", default=True):
            return None
        if not api_url or not agent_secret or not host_id:
            return None
        return cls(
            api_url=api_url.rstrip("/"),
            agent_secret=agent_secret,
            host_id=host_id,
            local_db=_bound_local_db,
        )

    def _headers(self) -> Dict[str, str]:
        return {"X-Agent-Secret": self.agent_secret, "Content-Type": "application/json"}

    def _db(self) -> Any:
        return self.local_db if self.local_db is not None else _bound_local_db

    def _enqueue_register_intent(self, event_id: str, payload: Dict[str, Any]) -> bool:
        db = self._db()
        if db is None:
            return False
        try:
            db.enqueue_dle_register(event_id, payload)
            logger.info(
                "device_log_event_register_intent_enqueued event_id=%s path=%s",
                event_id, payload.get("local_path"),
            )
            return True
        except Exception:
            logger.exception(
                "device_log_event_register_intent_enqueue_failed event_id=%s",
                event_id,
            )
            return False

    def create_local_event(
        self,
        *,
        serial: str,
        platform: str,
        event_type: str,
        event_subtype: Optional[str],
        detected_at: datetime,
        device_timestamp: Optional[datetime],
        local_path: Path,
        plan_run_id: Optional[int],
        job_id: Optional[int],
        link_signal_seq_no: Optional[int] = None,
        size_bytes: Optional[int] = None,
        event_id: Optional[str] = None,
    ) -> Optional[str]:
        """POST 新事件 state=LOCAL；返回 event id 字符串。

        #1042 / #1051: 预分配 UUID 作为幂等键；HTTP 失败时写入 LocalDB
        ``dle_register_outbox``，由 :meth:`drain_register_outbox` 重试补建。
        """
        chosen_id = event_id or str(uuid4())
        payload: Dict[str, Any] = {
            "id": chosen_id,
            "serial": serial,
            "platform": platform,
            "event_type": event_type,
            "event_subtype": event_subtype,
            "detected_at": detected_at.isoformat(),
            "device_timestamp": device_timestamp.isoformat() if device_timestamp else None,
            "state": "LOCAL",
            "local_path": str(local_path),
            "host_id": self.host_id,
            "job_id": job_id,
            "plan_run_id": plan_run_id,
            "size_bytes": size_bytes,
            "link_signal_seq_no": link_signal_seq_no,
        }
        try:
            resp = requests.post(
                f"{self.api_url}/api/v1/agent/device-log-events",
                json={"events": [payload]},
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "device_log_event_create_failed status=%s body=%s",
                    resp.status_code, resp.text[:200],
                )
                self._enqueue_register_intent(chosen_id, payload)
                return None
            ids = resp.json().get("data", {}).get("event_ids") or []
            return str(ids[0]) if ids else chosen_id
        except Exception:
            logger.exception("device_log_event_create_error path=%s", local_path)
            self._enqueue_register_intent(chosen_id, payload)
            return None

    def drain_register_outbox(self, *, limit: int = 20) -> int:
        """Replay pending create intents; returns number newly ACKed (#1042)."""
        db = self._db()
        if db is None:
            return 0
        pending = db.get_pending_dle_registers(limit=limit)
        acked = 0
        for row in pending:
            event_id = row["event_id"]
            payload = dict(row.get("payload") or {})
            payload.setdefault("id", event_id)
            try:
                resp = requests.post(
                    f"{self.api_url}/api/v1/agent/device-log-events",
                    json={"events": [payload]},
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                if resp.status_code >= 400:
                    self._bump_or_dead_letter(
                        db, event_id, error=f"HTTP {resp.status_code}",
                    )
                    logger.warning(
                        "device_log_event_register_retry_failed event_id=%s status=%s",
                        event_id, resp.status_code,
                    )
                    continue
                db.ack_dle_register(event_id)
                acked += 1
                logger.info("device_log_event_register_retry_ok event_id=%s", event_id)
            except Exception as exc:
                self._bump_or_dead_letter(db, event_id, error=str(exc)[:200])
                logger.exception(
                    "device_log_event_register_retry_error event_id=%s", event_id,
                )
        if acked:
            try:
                db.prune_acked_dle_registers()
            except Exception:
                logger.exception("device_log_event_register_prune_failed")
        return acked

    def _bump_or_dead_letter(self, db: Any, event_id: str, *, error: str) -> None:
        """累计 attempts；达 _MAX_REGISTER_ATTEMPTS 转死信（#1204）。

        与 log_signal/step_trace outbox 同语义：持续失败（403/422 等永久拒绝、
        长窗口网络故障）的意图若一直占着 LIMIT 20 的队首，会饿死后续可恢复
        意图。死信行不再被取出（排除了队首饿死），保留供审计与
        replay_dle_register_dead_letter 手动回放。
        """
        new_attempts = db.bump_dle_register_attempts(event_id, error=error)
        if new_attempts >= _MAX_REGISTER_ATTEMPTS:
            db.mark_dle_register_dead_letter(event_id, error)
            logger.warning(
                "device_log_event_register_dead_letter event_id=%s attempts=%d error=%s",
                event_id, new_attempts, error[:200],
            )

    def create_pull_failed_event(
        self,
        *,
        serial: str,
        platform: str,
        event_type: str,
        event_subtype: Optional[str],
        detected_at: datetime,
        device_timestamp: Optional[datetime],
        plan_run_id: Optional[int],
        job_id: Optional[int],
        link_signal_seq_no: Optional[int] = None,
        local_path: str = "",
    ) -> Optional[str]:
        """POST state=PULL_FAILED（检测成功但本地落盘失败）。"""
        payload: Dict[str, Any] = {
            "serial": serial,
            "platform": platform,
            "event_type": event_type,
            "event_subtype": event_subtype,
            "detected_at": detected_at.isoformat(),
            "device_timestamp": device_timestamp.isoformat() if device_timestamp else None,
            "state": "PULL_FAILED",
            "local_path": local_path,
            "host_id": self.host_id,
            "job_id": job_id,
            "plan_run_id": plan_run_id,
            "link_signal_seq_no": link_signal_seq_no,
        }
        try:
            resp = requests.post(
                f"{self.api_url}/api/v1/agent/device-log-events",
                json={"events": [payload]},
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "device_log_event_pull_failed_create status=%s body=%s",
                    resp.status_code, resp.text[:200],
                )
                return None
            ids = resp.json().get("data", {}).get("event_ids") or []
            return str(ids[0]) if ids else None
        except Exception:
            logger.exception("device_log_event_pull_failed_create_error serial=%s", serial)
            return None

    def patch_event_state(
        self,
        *,
        event_id: str,
        state: str,
        serial: str,
        platform: str,
        event_type: str,
        detected_at: str,
        local_path: str,
        plan_run_id: Optional[int] = None,
        job_id: Optional[int] = None,
        remote_path: Optional[str] = None,
        checksum: Optional[str] = None,
    ) -> bool:
        """PATCH 已有事件 state（EventUploader / spill prune 用）。"""
        payload: Dict[str, Any] = {
            "id": event_id,
            "serial": serial,
            "platform": platform,
            "event_type": event_type,
            "detected_at": detected_at,
            "state": state,
            "local_path": local_path,
            "host_id": self.host_id,
            "plan_run_id": plan_run_id,
            "job_id": job_id,
            "remote_path": remote_path,
            "checksum": checksum,
        }
        try:
            resp = requests.post(
                f"{self.api_url}/api/v1/agent/device-log-events",
                json={"events": [payload]},
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                logger.warning(
                    "device_log_event_patch_failed event_id=%s status=%s",
                    event_id, resp.status_code,
                )
                return False
            return True
        except Exception:
            logger.exception("device_log_event_patch_error event_id=%s", event_id)
            return False

    def list_events(self, *, state: str, limit: int | None = 1) -> List[Dict[str, Any]]:
        try:
            params: Dict[str, Any] = {"host_id": self.host_id, "state": state}
            if limit is not None:
                params["limit"] = limit
            resp = requests.get(
                f"{self.api_url}/api/v1/agent/device-log-events",
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                return []
            return list(resp.json().get("data", {}).get("events") or [])
        except Exception:
            logger.exception("device_log_event_list_error state=%s", state)
            return []

    @staticmethod
    def dir_size_bytes(path: Path) -> int:
        total = 0
        if not path.is_dir():
            return 0
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
        return total
