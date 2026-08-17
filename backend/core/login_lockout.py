"""每账户登录失败锁定（进程内）。

与全局 IP 限流（``backend/core/limiter.py``，300 req/min/IP）互补：
IP 限流是横向防护，本模块是纵向防护——同一账户在失败窗口内连续失败
``STP_LOGIN_MAX_FAILURES`` 次后，锁定 ``STP_LOGIN_LOCKOUT_SECONDS`` 秒，
防定向口令爆破（针对单账户慢速试密可绕过 IP 限流）。

已知局限（与 limiter.py 相同的进程内局限，见 #91）：单进程 systemd 部署下
准确；一旦加多 worker，锁定量 = 配置值 × 副本数，需挪 Redis。

#281 评审修复（对应 CodeRabbit Major 线程）：
- **原子性**：``LoginLockout.guarded_attempt`` 在账户分片锁（固定 64 片，
  内存有界）内完成「检查锁定 → 密码验证 → 失败计数/成功清零」。路由不再
  分三步调用，并发请求无法同时越过锁定检查、在同一批次越阈值试密；不同
  账户落在不同分片时可并行执行 bcrypt，不再全局串行化所有登录。
- **淘汰保护**：``_evict_if_needed`` 只淘汰未锁定条目，锁定中的账户不会
  被用户名洪泛挤出锁定桶；表满且全部在锁定中时不再插入新键（容量上限
  真正生效）。
- **身份键**：键由调用方提供（路由用数据库用户 ID）；未注册用户名一律
  归入共享的 ``UNKNOWN_ACCOUNT_KEY`` 桶——``unknown:<用户名>`` 的键空间
  由攻击者控制，逐名跟踪会被「占满跟踪表」的洪泛打穿。模块本身不归一
  大小写，大小写不同的两个数据库账户不共享桶。
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional

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

# 未注册用户名共用的锁定键(#281 CR Major):路由把一切查无此人的登录尝试
# 归入这一个桶。若按 "unknown:<原样用户名>" 逐名跟踪,键空间完全由攻击者
# 控制——先对 MAX_TRACKED_ACCOUNTS 个不同未注册名各触发锁定即可占满跟踪
# 表,让真实账户失去锁定保护。共享桶让未知名洪泛只影响未知名,且自带
# 枚举限速;真实账户使用各自的 user.id 桶,互不影响。
UNKNOWN_ACCOUNT_KEY = "unknown"

_STRIPE_COUNT = 64


def _remaining_seconds(locked_until: float, now: float) -> int:
    """锁定剩余秒数（向上取整，锁定中至少报 1 秒）。

    #281 二轮：``int()`` 截断会让剩余 <1s 的锁定误报 0（看起来已解锁），
    Retry-After 语义应向上取整；调用方仅在锁定中（locked_until > now）使用。
    """
    return max(1, math.ceil(locked_until - now))


class AccountLocked(Exception):
    """账户仍在锁定中;``remaining`` 为还需等待的秒数。"""

    def __init__(self, remaining: int) -> None:
        self.remaining = remaining
        super().__init__(f"account locked, retry in {remaining}s")


class InvalidCredentials(Exception):
    """用户名或密码错误(guarded_attempt 失败分支)。"""


class LoginLockout:
    """Per-account failed-login lockout tracker (in-process).

    线程安全(#281 CR 意见):``guarded_attempt`` 用内部一把互斥锁串行化
    「检查/验证/记录/清零」,并发请求不会双双越过锁定检查再双双计数。
    锁定键为不透明字符串,由调用方决定(数据库用户 ID 等)。
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
        # key -> {"failures": [ts, ...], "locked_until": float | None}
        # OrderedDict LRU 序:最近活动的在末尾,淘汰从头部 popitem — O(1)。
        self._state: "OrderedDict[str, Dict[str, object]]" = OrderedDict()
        self._lock = threading.Lock()  # 保护 _state(短临界区)
        # guarded_attempt 的分片锁(#281 CR Major):固定 64 片,内存有界;
        # 同 key 恒落同片,保证「检查—验证—计数」原子;不同片可并行 bcrypt。
        self._stripes = [threading.Lock() for _ in range(_STRIPE_COUNT)]

    def _evict_if_needed(self, now: float) -> bool:
        """只淘汰「未锁定」的最旧条目(#281 CR Major)。

        锁定中的账户必须保留:攻击者用 ~MAX_TRACKED_ACCOUNTS 个不同用户名
        洪泛即可把仍处于锁定期的目标账户挤出锁定桶。返回 True 表示有空位
        或已腾出空位;False 表示表已满且全部在锁定中——调用方必须放弃
        跟踪新账户(#281 二轮 P2:此时继续插入新键会让容量上限形同虚设)。
        """
        if len(self._state) < self.max_tracked_accounts:
            return True
        while len(self._state) >= self.max_tracked_accounts:
            victim: Optional[str] = None
            for key, entry in self._state.items():
                locked_until: Optional[float] = entry.get("locked_until")  # type: ignore[assignment]
                if locked_until is None or locked_until <= now:
                    victim = key
                    break
            if victim is None:
                return False
            self._state.pop(victim)
        return True

    def _prune(self, key: str, now: float) -> None:
        entry = self._state.get(key)
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
            self._state.pop(key, None)

    def _record_failure_locked(self, key: str, now: float) -> int:
        """锁内:计数一次失败并可能触发锁定(返回锁定时长,0 = 未触发)。"""
        if key not in self._state:
            if not self._evict_if_needed(now):
                # 表已满且全部在锁定中:放弃跟踪新账户,不再插入新键
                # (#281 二轮 P2,容量上限真正生效)。该账户本次失败不计数,
                # 但登录本身仍会 401。
                return 0
        entry = self._state.setdefault(key, {"failures": [], "locked_until": None})
        self._state.move_to_end(key)  # 标记最近活动,淘汰才是 LRU 而非 FIFO
        failures: List[float] = entry["failures"]  # type: ignore[assignment]
        failures.append(now)
        if len(failures) >= self.max_failures:
            entry["locked_until"] = now + self.lockout_seconds
            entry["failures"] = []
            logger.warning(
                "login_lockout: account=%s locked for %ds after %d failures",
                key, self.lockout_seconds, self.max_failures,
            )
            return self.lockout_seconds
        return 0

    def guarded_attempt(self, key: str, verify: Callable[[], bool]) -> None:
        """原子执行「检查锁定 → 密码验证 → 失败计数/成功清零」。

        同账户的「检查—验证—计数」在同一把**分片锁**内完成(#281 CR Major:
        原实现里锁定检查、密码验证、record_failure 是路由侧三段式调用,存在
        TOCTOU——并发请求可同时通过初始检查,再在同一批次越阈值试密;
        且全局单锁会把所有账户的登录(含 bcrypt)串行化)。``key`` 哈希到
        64 个分片之一,同账户并发登录在分片上串行化,不同账户(不同分片)
        可并行执行 bcrypt。分片数固定,内存有界。

        成功:清零失败记录;凭据错误:计数并可能触发锁定后抛
        ``InvalidCredentials``;锁定中:抛 ``AccountLocked(remaining)``。
        """
        with self._stripes[hash(key) % _STRIPE_COUNT]:
            with self._lock:
                now = time.time()
                self._prune(key, now)
                entry = self._state.get(key)
                if entry is not None and entry.get("locked_until") is not None:
                    raise AccountLocked(_remaining_seconds(entry["locked_until"], now))
            ok = verify()  # bcrypt 在分片锁内、状态锁外执行
            with self._lock:
                if not ok:
                    self._record_failure_locked(key, now)
                    raise InvalidCredentials()
                self._state.pop(key, None)

    def locked_remaining(self, key: str) -> int:
        """>0 = 该账户还需等待的秒数（0 = 未锁定）。"""
        with self._lock:
            now = time.time()
            self._prune(key, now)
            entry = self._state.get(key)
            if entry and entry.get("locked_until") is not None:
                return _remaining_seconds(entry["locked_until"], now)
            return 0

    def record_failure(self, key: str) -> int:
        """记录一次失败；返回本次触发的锁定时长（0 = 未触发锁定）。

        供纯逻辑测试与模块级兼容使用;路由侧必须走 ``guarded_attempt``
        才能保证「检查—验证—计数」整体原子。
        """
        with self._lock:
            now = time.time()
            self._prune(key, now)
            return self._record_failure_locked(key, now)

    def record_success(self, key: str) -> None:
        """登录成功后清零该账户的失败记录与锁定。"""
        with self._lock:
            self._state.pop(key, None)


_default = LoginLockout()


def guarded_attempt(key: str, verify: Callable[[], bool]) -> None:
    """模块级原子登录尝试(见 :meth:`LoginLockout.guarded_attempt`)。"""
    _default.guarded_attempt(key, verify)


def locked_remaining(key: str) -> int:
    return _default.locked_remaining(key)


def record_failure(key: str) -> int:
    return _default.record_failure(key)


def record_success(key: str) -> None:
    _default.record_success(key)
