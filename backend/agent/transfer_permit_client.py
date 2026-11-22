"""Agent 传输并发许可客户端（ADR-0064 D3 / B10 U3）。

独立落位本模块，供 U2 ``firmware_cache.py`` 注入集成；不修改
``backend/agent/settings.py``。读取继承的 ``API_URL`` / ``HOST_ID`` /
``AGENT_SECRET``；等待与不可达超时由构造参数配置。

超时契约（fail-closed）：
- 正常排队（``acquired=false``）与异常重试累计计入总等待上限；
- 连续不可达累计不得超过不可达上限；
- 超限默认显式失败；仅不可达超限且 ``allow_permit_bypass=True`` 才放行并标
  ``permit_degraded=True``；正常排队超限即使 bypass 也必须
  ``transfer_permit_wait_timeout``。
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

DEFAULT_POOL = "firmware_nfs"
CODE_WAIT_TIMEOUT = "transfer_permit_wait_timeout"
CODE_UNAVAILABLE = "transfer_permit_unavailable"

ProgressCallback = Callable[..., None]


class TransferPermitClientError(Exception):
    """许可客户端失败（携带结构化 ``code``）。"""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass
class TransferPermitHold:
    """已获批（或 degraded 放行）的许可持有者。"""

    lease_token: str
    host_id: str
    pool: str
    permit_degraded: bool = False
    _client: Optional["TransferPermitClient"] = None
    _stop_renew: Optional[threading.Event] = None
    _renew_thread: Optional[threading.Thread] = None
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._stop_renew is not None:
            self._stop_renew.set()
        if self._renew_thread is not None and self._renew_thread.is_alive():
            self._renew_thread.join(timeout=2.0)
        if self.permit_degraded or self._client is None:
            return
        try:
            self._client._release(self.lease_token, self.pool)
        except Exception:
            logger.warning(
                "transfer_permit_release_failed host_id=%s token=%s",
                self.host_id,
                self.lease_token[:8],
                exc_info=True,
            )

    def __enter__(self) -> "TransferPermitHold":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.release()


class TransferPermitClient:
    """控制面 transfer-permits 客户端。"""

    def __init__(
        self,
        *,
        api_url: Optional[str] = None,
        host_id: Optional[str] = None,
        agent_secret: Optional[str] = None,
        pool: str = DEFAULT_POOL,
        wait_timeout_seconds: float = 3600.0,
        unreachable_timeout_seconds: float = 300.0,
        request_timeout_seconds: float = 10.0,
        ttl_seconds: int = 60,
        allow_permit_bypass: bool = False,
        progress_callback: Optional[ProgressCallback] = None,
        session: Optional[requests.Session] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        uuid_fn: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self.api_url = (api_url if api_url is not None else os.getenv("API_URL", "")).rstrip(
            "/"
        )
        self.host_id = host_id if host_id is not None else os.getenv("HOST_ID", "")
        self.agent_secret = (
            agent_secret if agent_secret is not None else os.getenv("AGENT_SECRET", "")
        )
        self.pool = pool or DEFAULT_POOL
        self.wait_timeout_seconds = float(wait_timeout_seconds)
        self.unreachable_timeout_seconds = float(unreachable_timeout_seconds)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.ttl_seconds = int(ttl_seconds)
        self.allow_permit_bypass = bool(allow_permit_bypass)
        self.progress_callback = progress_callback
        self._session = session or requests.Session()
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._uuid = uuid_fn

    def _endpoint(self, action: str) -> str:
        return urljoin(self.api_url + "/", f"api/v1/agent/transfer-permits/{action}")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Agent-Secret": self.agent_secret,
        }

    def _emit_progress(self, **kwargs: Any) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(**kwargs)
        except Exception:
            logger.debug("transfer_permit_progress_callback_failed", exc_info=True)

    def _post(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._session.post(
            self._endpoint(action),
            json=body,
            headers=self._headers(),
            timeout=self.request_timeout_seconds,
        )
        if resp.status_code >= 500:
            raise requests.HTTPError(
                f"server error {resp.status_code}",
                response=resp,
            )
        if resp.status_code >= 400:
            detail = None
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise TransferPermitClientError(
                "transfer_permit_http_error",
                f"{action} failed status={resp.status_code} detail={detail}",
            )
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise TransferPermitClientError(
                "transfer_permit_bad_response",
                f"{action} missing data payload",
            )
        return data

    def _release(self, lease_token: str, pool: str) -> None:
        self._post(
            "release",
            {
                "host_id": self.host_id,
                "lease_token": lease_token,
                "pool": pool,
            },
        )

    def _renew(self, lease_token: str, pool: str) -> None:
        self._post(
            "renew",
            {
                "host_id": self.host_id,
                "lease_token": lease_token,
                "pool": pool,
                "ttl_seconds": self.ttl_seconds,
            },
        )

    def _start_renew_thread(self, hold: TransferPermitHold) -> None:
        stop = threading.Event()
        hold._stop_renew = stop
        interval = max(1.0, float(self.ttl_seconds) / 3.0)

        def _loop() -> None:
            while not stop.wait(interval):
                try:
                    self._renew(hold.lease_token, hold.pool)
                except Exception:
                    logger.warning(
                        "transfer_permit_renew_failed host_id=%s",
                        self.host_id,
                        exc_info=True,
                    )

        thread = threading.Thread(
            target=_loop,
            name=f"transfer-permit-renew-{self.host_id}",
            daemon=True,
        )
        hold._renew_thread = thread
        thread.start()

    def acquire_permit(
        self,
        *,
        allow_permit_bypass: Optional[bool] = None,
        lease_token: Optional[str] = None,
    ) -> TransferPermitHold:
        """阻塞等待获批并返回可 ``with`` 的持有者；超限按契约失败或 degraded。"""
        if not self.api_url:
            raise TransferPermitClientError(CODE_UNAVAILABLE, "API_URL is empty")
        if not self.host_id:
            raise TransferPermitClientError(CODE_UNAVAILABLE, "HOST_ID is empty")

        bypass = (
            self.allow_permit_bypass
            if allow_permit_bypass is None
            else bool(allow_permit_bypass)
        )
        token = lease_token or self._uuid()
        deadline = self._monotonic() + self.wait_timeout_seconds
        unreachable_spent = 0.0
        attempt = 0
        poll_base = 1.0

        while True:
            now = self._monotonic()
            if now >= deadline:
                raise TransferPermitClientError(
                    CODE_WAIT_TIMEOUT,
                    f"waited >= {self.wait_timeout_seconds}s without acquiring permit",
                )

            attempt += 1
            started = self._monotonic()
            try:
                data = self._post(
                    "acquire",
                    {
                        "host_id": self.host_id,
                        "lease_token": token,
                        "pool": self.pool,
                        "ttl_seconds": self.ttl_seconds,
                    },
                )
                unreachable_spent = 0.0
                if data.get("acquired"):
                    hold = TransferPermitHold(
                        lease_token=token,
                        host_id=self.host_id,
                        pool=self.pool,
                        permit_degraded=False,
                        _client=self,
                    )
                    self._start_renew_thread(hold)
                    return hold

                self._emit_progress(
                    stage="firmware-stage-wait",
                    attempt=attempt,
                    active_count=data.get("active_count"),
                    max_hosts=data.get("max_hosts"),
                    acquired=False,
                )
                remaining = deadline - self._monotonic()
                sleep_for = min(poll_base + random.uniform(0, 0.5), max(0.05, remaining))
                self._sleep(sleep_for)
                poll_base = min(poll_base * 1.5, 5.0)
                continue

            except TransferPermitClientError:
                # 4xx 结构化错误：不可恢复，直接抛出（不计入不可达逃生）。
                raise
            except (requests.RequestException, OSError, ValueError) as exc:
                elapsed = max(0.0, self._monotonic() - started)
                unreachable_spent += elapsed
                # 单次请求超时/连接失败本身也计入不可达墙钟；退避睡眠也计入。
                if unreachable_spent >= self.unreachable_timeout_seconds:
                    if bypass:
                        logger.warning(
                            "transfer_permit_bypass host_id=%s unreachable_spent=%.1fs",
                            self.host_id,
                            unreachable_spent,
                        )
                        return TransferPermitHold(
                            lease_token=token,
                            host_id=self.host_id,
                            pool=self.pool,
                            permit_degraded=True,
                            _client=None,
                        )
                    raise TransferPermitClientError(
                        CODE_UNAVAILABLE,
                        f"permit API unreachable for >= "
                        f"{self.unreachable_timeout_seconds}s: {exc}",
                    ) from exc

                self._emit_progress(
                    stage="firmware-stage-wait",
                    attempt=attempt,
                    unreachable=True,
                    error=str(exc),
                )
                backoff = min(2 ** min(attempt, 6), 30.0) + random.uniform(0, 0.5)
                remaining_wait = deadline - self._monotonic()
                remaining_unreach = self.unreachable_timeout_seconds - unreachable_spent
                sleep_for = min(backoff, max(0.05, remaining_wait), max(0.05, remaining_unreach))
                sleep_started = self._monotonic()
                self._sleep(sleep_for)
                unreachable_spent += max(0.0, self._monotonic() - sleep_started)
                continue

    def __call__(
        self,
        *,
        allow_permit_bypass: Optional[bool] = None,
        lease_token: Optional[str] = None,
    ) -> TransferPermitHold:
        """便捷：``with client(...) as hold:``。"""
        return self.acquire_permit(
            allow_permit_bypass=allow_permit_bypass,
            lease_token=lease_token,
        )
