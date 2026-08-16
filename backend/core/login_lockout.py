"""每账户登录失败锁定（进程内）。

与全局 IP 限流（``backend/core/limiter.py``，300 req/min/IP）互补：
IP 限流是横向防护，本模块是纵向防护——同一账户在失败窗口内连续失败
``STP_LOGIN_MAX_FAILURES`` 次后，锁定 ``STP_LOGIN_LOCKOUT_SECONDS`` 秒，
防定向口令爆破（针对单账户慢速试密可绕过 IP 限流）。

已知局限（与 limiter.py 相同的进程内局限，见 #91）：单进程 systemd 部署下
准确；一旦加多 worker，锁定量 = 配置值 × 副本数，需挪 Redis。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


STP_LOGIN_MAX_FAILURES = _env_int("STP_LOGIN_MAX_FAILURES", 5)
STP_LOGIN_FAILURE_WINDOW_SECONDS = _env_int("STP_LOGIN_FAILURE_WINDOW_SECONDS", 300)
STP_LOGIN_LOCKOUT_SECONDS = _env_int("STP_LOGIN_LOCKOUT_SECONDS", 900)

# 同时跟踪的账户上限(与 limiter.MAX_TRACKED_IPS 同理):防止攻击者轮换海量
# 用户名把字典撑爆;超出按 LRU 淘汰最久未活动账户(#281 CR 意见)。
MAX_TRACKED_ACCOUNTS = 10_000


class LoginLockout:
    """Per-account failed-login lockout tracker (in-process).

    线程安全(#281 CR 意见):内部一把互斥锁串行化「检查/记录/清零」,
    并发请求不会出现双双越过锁定检查再双双计数导致的计数失真。
    """

    def __init__(
        self,
        max_failures: int = STP_LOGIN_MAX_FAILURES,
        failure_window_seconds: int = STP_LOGIN_FAILURE_WINDOW_SECONDS,
        lockout_seconds: int = STP_LOGIN_LOCKOUT_SECONDS,
        max_tracked_accounts: int = MAX_TRACKED_ACCOUNTS,
    ) -> None:
        self.max_failures = max_failures
        self.failure_window_seconds = failure_window_seconds
        self.lockout_seconds = lockout_seconds
        self.max_tracked_accounts = max_tracked_accounts
        # username(lower) -> {"failures": [ts, ...], "locked_until": float | None}
        # OrderedDict LRU 序:最近活动的在末尾,淘汰从头部 popitem — O(1)。
        self._state: "OrderedDict[str, Dict[str, object]]" = OrderedDict()
        self._lock = threading.Lock()

    def _evict_if_needed(self) -> None:
        while len(self._state) >= self.max_tracked_accounts:
            self._state.popitem(last=False)

    def _prune(self, username: str, now: float) -> None:
        entry = self._state.get(username)
        if entry is None:
            return
        cutoff = now - self.failure_window_seconds
        failures: List[float] = entry["failures"]  # type: ignore[assignment]
        entry["failures"] = [ts for ts in failures if ts > cutoff]
        locked_until: Optional[float] = entry["locked_until"]  # type: ignore[assignment]
        if locked_until is not None and locked_until <= now:
            entry["locked_until"] = None
            entry["failures"] = []
        if not entry["failures"] and entry["locked_until"] is None:
            self._state.pop(username, None)

    def locked_remaining(self, username: str) -> int:
        """>0 = 该账户还需等待的秒数（0 = 未锁定）。"""
        with self._lock:
            now = time.time()
            self._prune(username.lower(), now)
            entry = self._state.get(username.lower())
            if entry and entry.get("locked_until") is not None:
                locked_until: float = entry["locked_until"]  # type: ignore[assignment]
                return max(0, int(locked_until - now))
            return 0

    def record_failure(self, username: str) -> int:
        """记录一次失败；返回本次触发的锁定时长（0 = 未触发锁定）。"""
        with self._lock:
            now = time.time()
            key = username.lower()
            self._prune(key, now)
            if key not in self._state:
                self._evict_if_needed()
            entry = self._state.setdefault(key, {"failures": [], "locked_until": None})
            self._state.move_to_end(key)  # 标记最近活动,淘汰才是 LRU 而非 FIFO
            failures: List[float] = entry["failures"]  # type: ignore[assignment]
            failures.append(now)
            if len(failures) >= self.max_failures:
                entry["locked_until"] = now + self.lockout_seconds
                entry["failures"] = []
                logger.warning(
                    "login_lockout: account=%s locked for %ds after %d failures",
                    username, self.lockout_seconds, self.max_failures,
                )
                return self.lockout_seconds
            return 0

    def record_success(self, username: str) -> None:
        """登录成功后清零该账户的失败记录与锁定。"""
        with self._lock:
            self._state.pop(username.lower(), None)


_default = LoginLockout()


def locked_remaining(username: str) -> int:
    return _default.locked_remaining(username)


def record_failure(username: str) -> int:
    return _default.record_failure(username)


def record_success(username: str) -> None:
    _default.record_success(username)
