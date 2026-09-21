"""未捕获异常的日志形态（#3042）。

问题不是「日志太吵」，而是**一次失败的体积与失败族无关**：`backend/main.py` 的
`global_exception_handler` 一律 `logger.exception`，而 Python 3.13 的 fine-grained
error locations 会把 chained traceback 的每个帧再配上 `^^^` 续行。2026-09-21 21:17
起 PG 连接槽耗尽（#2959）在实时发生，本机 `logs/backend.log` 尾部 8 MB 内实测
**228 条记录、每条 367 行**（终端异常 227 条 `TooManyConnectionsError`、3 条
`DeadlockDetectedError`）——约 96% 的字节由这一条语句产生，现场可查时长被压到 1/8。

全栈不是噪声：某一种失败**第一次**出现时，它是唯一能定位根因的东西。所以取舍既不是
「压成一行」（会把婴儿连同洗澡水倒掉）也不是「永远保留全栈」，而是：

    每种失败族全栈一次，之后每种只记一行；一行里必须留得住定位维度。

一行保留的维度（对应 #3042 判据 1）：

- 异常类链 `DBAPIError<...>TooManyConnectionsError`：谁抛的、根因是谁；
- **SQLSTATE**：`53300` 槽耗尽（asyncpg `TooManyConnectionsError`）/ `40P01` 死锁 /
  `08xxx` 连接中断，一眼分类；
- 端点**模板**：复用 `request_metrics.endpoint_label` 的有界口径，不写原始 path
  （`/api/v1/agent/jobs/41981/complete` 这种带 ID 的路径会把日志键撑成无界）；
- 客户端地址：今天只有 uvicorn access 行带它，#3020 若关掉 access log 就彻底没了。

判据来源写死在此：匹配的是**异常链上出现 DBAPI 层异常类型**，不是消息文本匹配——
文本匹配会在下一次改文案时静默失效。键的有界性同 #1927 的指标基数纪律。
"""
from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import exc as sqlalchemy_exc

# 链上最多走这么远（防御自引用与恶意深链；实测 PG 失败链是 3~4 层）
_MAX_CHAIN_DEPTH = 8

# 记录过的「失败族」上限：超出的最老族让位（让位后重现将再得一次全栈，
# 这是可接受的代价——无界字典会把内存变成新的事故面）
_MAX_TRACKED_FAMILIES = 128

# fingerprint -> 该族在本进程内出现的次数（dict 保持插入序，用于 FIFO 让位）
_seen_families: dict[str, int] = {}


class DbFailureLog(NamedTuple):
    """一条数据库侧失败的日志决策（由调用方落 `logger.error`）。"""

    fingerprint: str   # 有界，可作族键
    message: str       # 一行式，含全部定位维度
    first_of_family: bool  # True ⇒ 调用方应带 exc_info 记这一次全栈


def exception_chain(exc: BaseException | None) -> list[BaseException]:
    """异常链（`__cause__` 优先、`__context__` 兜底），带深度与自引用防护。"""
    nodes: list[BaseException] = []
    seen: set[int] = set()
    node = exc
    while node is not None and len(nodes) < _MAX_CHAIN_DEPTH and id(node) not in seen:
        seen.add(id(node))
        nodes.append(node)
        node = node.__cause__ or node.__context__
    return nodes


def db_failure_fingerprint(exc: BaseException) -> str | None:
    """已知「数据库侧失败族」→ 一行指纹；不是这类失败 → ``None``。

    判定面刻意收窄：
    - `sqlalchemy.exc.DBAPIError` —— PG **服务端**返回的错误经方言翻译成它的子类
      （`OperationalError`/`InterfaceError`…），槽耗尽与死锁都落在这里；
    - `sqlalchemy.exc.TimeoutError` —— 连接池排队超时（同一失败面的另一半）。

    `StatementError`/`InvalidRequestError` 等 SQLAlchemy 编程性错误**不在此列**：
    那些是真 bug，必须继续看到全栈。
    """
    nodes = exception_chain(exc)
    is_dbapi = any(
        isinstance(node, (sqlalchemy_exc.DBAPIError, sqlalchemy_exc.TimeoutError))
        for node in nodes
    )
    if not is_dbapi:
        return None
    sqlstate = ""
    for node in nodes:
        # asyncpg 的异常带 `sqlstate`，psycopg 系带 `pgcode`；两边都认，取链上第一个
        state = getattr(node, "sqlstate", None) or getattr(node, "pgcode", None)
        if isinstance(state, str) and state:
            sqlstate = state
            break
    names = "<".join(type(node).__name__ for node in nodes)
    return f"{names} sqlstate={sqlstate or '-'}"


def _note_family(fingerprint: str) -> int:
    """族计数 +1，返回该族本进程内的出现序号（1 = 首次）。"""
    count = _seen_families.get(fingerprint, 0) + 1
    if fingerprint not in _seen_families and len(_seen_families) >= _MAX_TRACKED_FAMILIES:
        _seen_families.pop(next(iter(_seen_families)))
    _seen_families[fingerprint] = count
    return count


def describe_db_failure(
    exc: BaseException, *, method: str, endpoint: str, client: str,
) -> DbFailureLog | None:
    """构造一行式日志决策；不是数据库侧失败时返回 ``None``（调用方留全栈）。"""
    fingerprint = db_failure_fingerprint(exc)
    if fingerprint is None:
        return None
    ordinal = _note_family(fingerprint)
    message = (
        f"unhandled_db_failure method={method} endpoint={endpoint} "
        f"client={client} chain={fingerprint}"
    )
    return DbFailureLog(
        fingerprint=fingerprint,
        message=message,
        first_of_family=ordinal == 1,
    )
