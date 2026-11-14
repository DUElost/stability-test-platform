"""Agent 侧 S8 代表证据上送（ADR-0062 D8/D9，B8 G6）。

控制面在 S7 选完代表后写入独立发布记录；本模块按 host 拉取待办，把采集时
固化的本地事件目录复制到证据区固定目的路径，校验后回执。它与 EventUploader
共用同一份复制/哈希契约、五次尝试上限与持久尝试计数（重启不清零），但走
独立的回执端点与目的路径：DLE.remote_path 保持原语义，供溢出补扫读取。

不在中心校验回执前删除本地源（D8 第 4 条 / I4）：本模块从不 prune。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

import requests

from .event_uploader import _state_store as _agent_state_store

try:
    from .aee.paths import (
        PathOutsideRootError,
        get_aee_nfs_root,
        resolve_path_under_aee_local,
    )
    from .contracts.evidence_layout import (
        EvidenceLayoutError,
        assert_no_symlinks,
        copytree_no_symlinks,
        dir_sha256_and_size,
        parse_evidence_rel_path,
    )
except ImportError:  # pragma: no cover - 兼容脚本式导入
    from agent.aee.paths import (
        PathOutsideRootError,
        get_aee_nfs_root,
        resolve_path_under_aee_local,
    )
    from agent.contracts.evidence_layout import (
        EvidenceLayoutError,
        assert_no_symlinks,
        copytree_no_symlinks,
        dir_sha256_and_size,
        parse_evidence_rel_path,
    )

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5
_MAX_CONCURRENT = 2
_POLL_INTERVAL = 30.0
_RETRY_BACKOFF_SECONDS = (60.0, 120.0, 240.0, 480.0)
_ATTEMPTS_STATE_PREFIX = "evidence_upload_attempts:"


def _enabled() -> bool:
    raw = os.getenv("STP_DEVICE_LOG_EVENT_ENABLED")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() in ("1", "true", "yes")


def _attempts_key(publication_id: int) -> str:
    return f"{_ATTEMPTS_STATE_PREFIX}{int(publication_id)}"


def _durable_get(publication_id: int) -> Optional[int]:
    try:
        store = _agent_state_store()
        if store is None:
            return None
        raw = store.get_state(_attempts_key(publication_id), "")
    except Exception:
        logger.warning(
            "evidence_attempts_state_read_failed publication=%d", publication_id,
            exc_info=True,
        )
        return None
    if not raw.strip():
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def _durable_set(publication_id: int, attempts: int) -> None:
    try:
        store = _agent_state_store()
        if store is None:
            return
        store.set_state(_attempts_key(publication_id), str(int(attempts)))
    except Exception:
        logger.debug(
            "evidence_attempts_state_write_failed publication=%d", publication_id,
            exc_info=True,
        )


class EvidenceUploader:
    """进程级单例：轮询发布待办、复制、校验、回执。"""

    _instance: Optional["EvidenceUploader"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._api_url = ""
        self._agent_secret = ""
        self._host_id = ""
        self._nfs_root = ""
        self._configured = False
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._active: set[int] = set()
        self._attempts: Dict[int, int] = {}
        self._retry_after: Dict[int, float] = {}
        self._slot = threading.Semaphore(_MAX_CONCURRENT)

    @classmethod
    def instance(cls) -> "EvidenceUploader":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def _reset_for_tests(cls) -> None:
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            inst.stop(timeout=0.5)

    def configure(
        self,
        *,
        api_url: str,
        agent_secret: str,
        host_id: str,
        nfs_root: str = "",
        force: bool = False,
    ) -> None:
        if self._configured and not force:
            return
        self._api_url = (api_url or "").rstrip("/")
        self._agent_secret = agent_secret or ""
        self._host_id = str(host_id or "")
        if not nfs_root:
            try:
                nfs_root = str(get_aee_nfs_root())
            except RuntimeError:
                nfs_root = ""
        self._nfs_root = nfs_root or ""
        self._configured = bool(
            self._api_url and self._agent_secret and self._host_id and self._nfs_root
        )
        logger.info(
            "evidence_uploader_configured enabled=%s host=%s root=%s",
            _enabled(), self._host_id, self._nfs_root,
        )

    def start(self) -> None:
        if not self._configured or not _enabled():
            logger.info(
                "evidence_uploader_start_skipped enabled=%s configured=%s",
                _enabled(), self._configured,
            )
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="evidence-uploader", daemon=True,
        )
        self._thread.start()
        logger.info("evidence_uploader_started")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self._stop_evt.wait(_POLL_INTERVAL):
            try:
                self._poll_once()
            except Exception:
                logger.exception("evidence_uploader_poll_failed")

    def _load_attempts(self, publication_id: int, server_attempts: int) -> int:
        with self._lock:
            local = self._attempts.get(publication_id)
        durable = _durable_get(publication_id)
        candidates = [value for value in (local, durable, int(server_attempts)) if value is not None]
        return max([0, *[int(value) for value in candidates]])

    def _store_attempts(self, publication_id: int, attempts: int) -> None:
        with self._lock:
            self._attempts[publication_id] = int(attempts)
        _durable_set(publication_id, attempts)

    def _clear_attempts(self, publication_id: int) -> None:
        with self._lock:
            self._attempts.pop(publication_id, None)
        _durable_set(publication_id, 0)

    def _poll_once(self) -> None:
        if not self._configured:
            return
        response = requests.get(
            f"{self._api_url}/api/v1/agent/evidence-publications",
            params={"host_id": self._host_id, "limit": 50},
            headers={"X-Agent-Secret": self._agent_secret},
            timeout=15.0,
        )
        if response.status_code >= 400:
            logger.warning(
                "evidence_uploader_poll_http_failed status=%s", response.status_code,
            )
            return
        tasks = response.json().get("data", {}).get("publications", [])
        now = _monotonic()
        for task in tasks:
            publication_id = int(task["publication_id"])
            with self._lock:
                if publication_id in self._active:
                    continue
                if now < self._retry_after.get(publication_id, 0.0):
                    continue
                self._active.add(publication_id)
            threading.Thread(
                target=self._upload_holding_slot,
                args=(task,),
                name=f"evidence-upload-{publication_id}",
                daemon=True,
            ).start()

    def _upload_holding_slot(self, task: dict) -> None:
        publication_id = int(task["publication_id"])
        while not self._slot.acquire(timeout=1.0):
            if self._stop_evt.is_set():
                with self._lock:
                    self._active.discard(publication_id)
                return
        try:
            self._upload(task)
        except Exception:
            logger.exception("evidence_upload_failed publication=%d", publication_id)
            self._report_failure(task, reason="agent_exception", terminal=False)
        finally:
            self._slot.release()
            with self._lock:
                self._active.discard(publication_id)

    def _copy_to_destination(self, src: Path, dest: Path) -> tuple[str, int]:
        root = Path(self._nfs_root)
        temp = dest.parent / f".stp-evidence-tmp-{uuid.uuid4().hex}"
        try:
            copytree_no_symlinks(src, temp, dest_root=root)
            src_checksum, src_size = dir_sha256_and_size(src)
            temp_checksum, temp_size = dir_sha256_and_size(temp)
            if (src_checksum, src_size) != (temp_checksum, temp_size):
                raise EvidenceLayoutError("copy verification failed")
            if os.path.lexists(dest):
                if dest.is_symlink() or not dest.is_dir():
                    raise FileExistsError(str(dest))
                dest_checksum, dest_size = dir_sha256_and_size(dest)
                if (dest_checksum, dest_size) != (src_checksum, src_size):
                    raise FileExistsError(f"destination conflict: {dest}")
                return dest_checksum, dest_size
            os.rename(temp, dest)
            return src_checksum, src_size
        finally:
            if temp.exists():
                shutil.rmtree(temp, ignore_errors=True)

    def _upload(self, task: dict) -> None:
        publication_id = int(task["publication_id"])
        attempts = self._load_attempts(publication_id, task.get("attempts") or 0)
        if attempts >= _MAX_ATTEMPTS:
            self._recover_exhausted(task, attempts)
            return
        try:
            src = resolve_path_under_aee_local(str(task["local_path"]))
        except PathOutsideRootError:
            self._report_failure(task, reason="source_mismatch", terminal=True, attempts=attempts + 1)
            return
        if src.is_symlink() or not src.is_dir():
            self._report_failure(task, reason="source_missing", terminal=False, attempts=attempts + 1)
            return
        if src.name != str(task.get("event_dir") or ""):
            self._report_failure(task, reason="source_mismatch", terminal=True, attempts=attempts + 1)
            return
        try:
            parse_evidence_rel_path(
                str(task["destination_rel_path"]),
                root_plan_run_id=int(task["root_plan_run_id"]),
                serial=str(task["serial"]),
                event_dir=str(task["event_dir"]),
            )
        except (EvidenceLayoutError, KeyError, ValueError):
            self._report_failure(task, reason="destination_conflict", terminal=True, attempts=attempts + 1)
            return
        dest = Path(self._nfs_root) / str(task["destination_rel_path"])
        try:
            checksum, size = self._copy_to_destination(src, dest)
        except FileExistsError:
            self._report_failure(task, reason="destination_conflict", terminal=True, attempts=attempts + 1)
            return
        except (EvidenceLayoutError, OSError):
            logger.warning("evidence_upload_copy_failed publication=%d", publication_id, exc_info=True)
            self._report_failure(task, reason="copy_failed", terminal=False, attempts=attempts + 1)
            return
        self._post(
            task,
            state="VERIFIED",
            checksum=checksum,
            size_bytes=size,
            attempts=max(attempts + 1, 1),
            reason=None,
        )

    def _existing_destination(self, rel_path: str) -> Path:
        """只读定位既有目的目录；任一父分量缺失或是 symlink 都拒绝。"""
        root = Path(self._nfs_root)
        if root.is_symlink() or not root.is_dir():
            raise EvidenceLayoutError(f"evidence root is not a plain directory: {root}")
        current = root
        for part in Path(rel_path).parts:
            current = current / part
            if current.is_symlink():
                raise EvidenceLayoutError(f"symlink not allowed: {current}")
            if not current.exists():
                raise FileNotFoundError(str(current))
            if not current.is_dir():
                raise EvidenceLayoutError(f"not a directory: {current}")
        return current

    def _recover_exhausted(self, task: dict, attempts: int) -> None:
        """预算耗尽后的恢复：只核验同一发布身份的既有目标，绝不复制或覆盖。

        第五次复制可能已经落盘而 VERIFIED 回执在落库前失败（例如 503）。
        重启后先比对本地源与既有目标的目录哈希；一致才重发 VERIFIED/attempts=5，
        由真实回执路由落库。目标缺失、哈希不符、路径含 symlink 或本地源不可读时
        一律按终态失败收敛，不发起第六次复制。
        """
        try:
            src = resolve_path_under_aee_local(str(task["local_path"]))
        except PathOutsideRootError:
            self._report_failure(
                task, reason="source_mismatch", terminal=True, attempts=attempts,
            )
            return
        if src.is_symlink() or not src.is_dir():
            self._report_failure(task, reason="source_missing", terminal=True, attempts=attempts)
            return
        if src.name != str(task.get("event_dir") or ""):
            self._report_failure(task, reason="source_mismatch", terminal=True, attempts=attempts)
            return
        try:
            parse_evidence_rel_path(
                str(task["destination_rel_path"]),
                root_plan_run_id=int(task["root_plan_run_id"]),
                serial=str(task["serial"]),
                event_dir=str(task["event_dir"]),
            )
            assert_no_symlinks(src)
            dest = self._existing_destination(str(task["destination_rel_path"]))
            assert_no_symlinks(dest)
            src_checksum, src_size = dir_sha256_and_size(src)
            dest_checksum, dest_size = dir_sha256_and_size(dest)
            if (src_checksum, src_size) != (dest_checksum, dest_size):
                raise EvidenceLayoutError("destination hash mismatch")
        except FileNotFoundError:
            self._report_failure(task, reason="copy_failed", terminal=True, attempts=attempts)
            return
        except (EvidenceLayoutError, KeyError, ValueError, OSError):
            self._report_failure(
                task, reason="destination_conflict", terminal=True, attempts=attempts,
            )
            return
        self._post(
            task,
            state="VERIFIED",
            checksum=src_checksum,
            size_bytes=src_size,
            attempts=attempts,
            reason=None,
        )

    def _report_failure(
        self, task: dict, *, reason: str, terminal: bool, attempts: Optional[int] = None,
    ) -> None:
        publication_id = int(task["publication_id"])
        attempts = int(attempts if attempts is not None else self._load_attempts(
            publication_id, task.get("attempts") or 0,
        ))
        self._store_attempts(publication_id, attempts)
        if terminal or attempts >= _MAX_ATTEMPTS:
            self._post(
                task, state="FAILED", checksum=None, size_bytes=None,
                attempts=attempts, reason=reason,
            )
            return
        self._post(
            task, state="RETRYING", checksum=None, size_bytes=None,
            attempts=attempts, reason=reason,
        )
        with self._lock:
            index = min(max(attempts - 1, 0), len(_RETRY_BACKOFF_SECONDS) - 1)
            self._retry_after[publication_id] = _monotonic() + _RETRY_BACKOFF_SECONDS[index]

    def _post(
        self,
        task: dict,
        *,
        state: str,
        checksum: Optional[str],
        size_bytes: Optional[int],
        attempts: int,
        reason: Optional[str],
    ) -> bool:
        publication_id = int(task["publication_id"])
        # 回执可能在落库前失败：先持久化已消耗次数，重启后仍按同一发布记录继续。
        self._store_attempts(publication_id, attempts)
        payload = {
            "host_id": self._host_id,
            "dle_id": str(task["dle_id"]),
            "destination_rel_path": str(task["destination_rel_path"]),
            "state": state,
            "checksum": checksum,
            "size_bytes": size_bytes,
            "attempts": attempts,
            "reason": reason,
        }
        try:
            response = requests.post(
                f"{self._api_url}/api/v1/agent/evidence-publications/{publication_id}/receipt",
                json=payload,
                headers={"X-Agent-Secret": self._agent_secret},
                timeout=120.0,
            )
        except Exception:
            logger.exception("evidence_receipt_post_failed publication=%d", publication_id)
            return False
        if response.status_code >= 400:
            logger.warning(
                "evidence_receipt_rejected publication=%d state=%s status=%s body=%s",
                publication_id, state, response.status_code, response.text[:200],
            )
            return False
        if state == "VERIFIED":
            data = response.json().get("data", {})
            if data.get("error"):
                logger.warning(
                    "evidence_receipt_not_verified publication=%d error=%s",
                    publication_id, data.get("error"),
                )
                return False
            self._clear_attempts(publication_id)
        elif state == "FAILED":
            self._store_attempts(publication_id, attempts)
        return True


def _monotonic() -> float:
    return time.monotonic()


__all__ = ["EvidenceUploader"]
