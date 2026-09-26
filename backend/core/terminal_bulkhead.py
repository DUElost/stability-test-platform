"""终态请求（`/agent/jobs/{id}/complete`）的独立并发舱壁（ADR-0047 D2 / #2959）。

为什么需要它：R523（2026-09-23）现场 490 个 RUNNING job 同时回传终态——峰值
55 req/s、窗口内 1644 次请求。每个请求在**第一次 DB 查询**时就占一条连接，并且
（ABORTED 分支）还要争同一条 `plan_run` 行做 JSON 读改写 ⇒ 异步池从 1 涨到 86，
把 PG 的非超级用户槽（97）打满，波内 1551 次取连接失败、1530 个请求 500。

舱壁要解决的不是「量」，而是「量落在哪一层排队」：

- **排队必须在连接池之外**。若只是把池调小，等待会从 PG 侧搬进 QueuePool，
  用户看到的仍是卡顿——换名字不解决问题（ADR-0047 §3 备选「甲」的代价说明）；
- 等不到就**快失败**：`503` + `Retry-After`（`DB_OVERLOADED`），让 Agent 把终态事实
  交回本地 outbox（事实先落 SQLite，不丢）而不是占着连接空等；
- 名额有限，留给 heartbeat / steps / claim / recovery / UI：默认 8 并发对
  async 池上限 40，余 32 给其余通道；
- 名额同时是**单事件循环上**的实效并发（#3403）：ADR-0052 D1 之前父行锁让同一 run
  的终态在 PG 里串行（名义 16、实效约 1）；锁移除后 16 个名额真并发，就绪批次放大，
  heartbeat / `/health` p99 升 3–8×。默认值因此由 16 重校为 8。

口径（均可 env 覆盖）：

- `STP_TERMINAL_BULKHEAD_CONCURRENCY`（默认 8）：同刻持有的终态名额；
- `STP_TERMINAL_BULKHEAD_WAIT_MS`（默认 500）：愿意等多久；超过即拒绝。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import AsyncIterator, Optional

from backend.core.exception_log import DB_OVERLOAD_RETRY_AFTER_SECONDS
from backend.core.metrics import (
    terminal_bulkhead_inflight,
    terminal_bulkhead_rejected_total,
    terminal_bulkhead_wait_seconds,
    terminal_bulkhead_waiting,
)

logger = logging.getLogger(__name__)

#: 拒绝时给调用方的建议退避（秒）——与过载 503 同一常量，避免两处退避口径漂移。
RETRY_AFTER_SECONDS = DB_OVERLOAD_RETRY_AFTER_SECONDS


class TerminalBulkheadFull(RuntimeError):
    """等待超过预算仍未拿到名额。调用方（main.py 的 handler）转 503 + Retry-After。"""


_semaphore: Optional[asyncio.Semaphore] = None
_semaphore_size: Optional[int] = None

#: 同一轮削峰只记**一条**日志（逐条记会把现场可查时长再压一次，#3042 同源纪律）；
#: 静默超过这个秒数算新一轮 burst——早期实现是「进程生命周期一次」，那会让**第二次**
#: 事故完全没有起点日志（低优先修正，2026-09-23 裁决）。指标不受此影响。
_REJECTION_LOG_QUIET_SECONDS = 60.0
_last_rejection_log_at: Optional[float] = None


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def limits() -> tuple[int, float]:
    """(并发名额, 等待预算秒)。非法/非正 env 回退默认——容量类误配不得退化成零闸门。"""
    concurrency = _int_env("STP_TERMINAL_BULKHEAD_CONCURRENCY", 8)
    wait_ms = _int_env("STP_TERMINAL_BULKHEAD_WAIT_MS", 500)
    return concurrency, wait_ms / 1000.0


def _get_semaphore() -> asyncio.Semaphore:
    """惰性建闸（按 env 容量）；容量变化时重建（测试与运行期调参都靠这条）。"""
    global _semaphore, _semaphore_size
    concurrency, _ = limits()
    if _semaphore is None or _semaphore_size != concurrency:
        _semaphore = asyncio.Semaphore(concurrency)
        _semaphore_size = concurrency
    return _semaphore


def _reset_for_tests() -> None:
    """清进程内状态（闸门 + 「本 burst 已记日志」标记）。测试专用，生产不调。"""
    global _semaphore, _semaphore_size, _last_rejection_log_at
    _semaphore = None
    _semaphore_size = None
    _last_rejection_log_at = None


@contextlib.asynccontextmanager
async def terminal_slot() -> AsyncIterator[None]:
    """占用一个终态名额；等不到预算就抛 `TerminalBulkheadFull`。

    进入时**不碰数据库**：这是「池外排队」的落点，调用方必须在本上下文内才发起
    第一次 DB 查询（`/complete` 路由里 `get_async_db` 的会话是惰性的，首次使用才取连接）。
    """
    global _last_rejection_log_at

    semaphore = _get_semaphore()
    concurrency, wait_budget = limits()

    terminal_bulkhead_waiting.inc()
    started = time.perf_counter()
    try:
        # `wait_for` 超时会取消内部的 acquire；asyncio.Semaphore 在取消路径上会把
        # 已减掉的计数还回去（3.13 语义），不会漏名额——由测试钉住。
        # 3.11 起 `asyncio.TimeoutError is TimeoutError`，只捕后者即可。
        await asyncio.wait_for(semaphore.acquire(), timeout=wait_budget)
    except TimeoutError:
        terminal_bulkhead_rejected_total.inc()
        now = time.monotonic()
        if (
            _last_rejection_log_at is None
            or (now - _last_rejection_log_at) >= _REJECTION_LOG_QUIET_SECONDS
        ):
            # 每个 burst 的**起点**留一行；burst 内后续拒绝只进指标。
            _last_rejection_log_at = now
            logger.warning(
                "terminal_bulkhead_rejected burst_start wait_ms=%d concurrency=%d",
                int(wait_budget * 1000),
                concurrency,
            )
        raise TerminalBulkheadFull(
            f"terminal bulkhead wait budget {int(wait_budget * 1000)}ms exceeded"
        ) from None
    finally:
        terminal_bulkhead_waiting.dec()
        terminal_bulkhead_wait_seconds.observe(time.perf_counter() - started)

    terminal_bulkhead_inflight.inc()
    try:
        yield
    finally:
        terminal_bulkhead_inflight.dec()
        semaphore.release()
