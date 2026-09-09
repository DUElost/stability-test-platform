# /health readiness 的 Redis ping 加超时（#1177）

Status: implemented
Class: bug-fix

## Decision

`backend/main.py` `/health`（readiness）的 Redis 检查从裸 `await redis_client.ping()`
改为 `asyncio.wait_for(..., timeout=_HEALTH_REDIS_PING_TIMEOUT)`，超时与 SAQ
worker 同源同参（`REDIS_PING_TIMEOUT` env，缺省 3.0s）。`redis_client` 由
`aioredis.from_url` 创建、未设 socket 超时——黑洞分区（SYN 丢弃）下 ping 可
无限悬挂，超过 Docker HEALTHCHECK 时限且每次探针累积一个 asyncio 任务。

超时以**按调用包裹**实现而非给共享 `redis_client` 设全局 socket 超时：客户端
同时被 agent_sid_registry / socketio adapter 复用，全局超时会改动所有使用方
语义，风险面过大；探针自身 fail-fast 已闭合缺陷。

涉及：`backend/main.py`（`import asyncio`、模块常量、ping 调用）；测试见
`backend/tests/api/test_health_saq.py::test_redis_ping_hang_times_out_returns_503`。

## Alternatives

- 建连时设 `socket_timeout/socket_connect_timeout`：影响共享客户端全部语义，
  放弃。
- 提升为 503 REDIS_UNREACHABLE 同码区分 message：超时与不可达同判 503
  （readiness 视角一致），仅日志区分 `health_redis_ping_timeout`。

## Verification

- `pytest backend/tests/api/test_health_saq.py`：13 passed（含新增悬挂超时
  用例：hung ping 在 50ms 内被截断并返回 503 REDIS_UNREACHABLE）。

## Revisit

若日后把 SAQ 之外对 `redis_client` 的操作也纳入探活面，可统一收敛到客户端
级超时并复核所有使用方的命令时长。
