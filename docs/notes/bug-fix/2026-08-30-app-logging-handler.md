# 应用层 INFO 日志补上 handler（#563）

- **日期**：2026-08-30
- **关联**：Issue [#563](https://github.com/DUElost/stability-test-platform/issues/563)；发现于 #556 的生产验证

## 决定了什么

新增 `backend/core/logging_setup.py::configure_logging()`，在 `backend/main.py`
导入时调用，给 **`backend` logger 单独**挂一个 stdout `StreamHandler`：

- 级别取 `STP_LOG_LEVEL`（默认 `INFO`，非法值回落 `INFO`）；
- `propagate=False` —— 记录不再冒泡到 root；
- 幂等（按 handler name `stp_app_stdout` 判重，重复调用不叠加）。

原状：`backend/main.py` 只给 uvicorn 的三个 logger 换了 formatter，应用 logger
没有 handler，`logger.info()` 走 `logging.lastResort`（stderr、WARNING+）被丢弃。
生产后果是所有周期任务的运行证据为零 —— `schedule_registered id=*`、
`counter_reconcile_done`、`signal_link_reconcile_done`、`watchdog_pass` 一条都看不到，
「这个 sweep 到底跑没跑」只能靠改库副作用去旁证。

## 放弃的备选

- **挂到 root logger / 用 `basicConfig()`**：会把 uvicorn 记录也接进来，要么访问日志
  双写，要么得额外给 uvicorn 关 propagate。改动面更大，收益为零。
- **`dictConfig` 全量接管**：一次性重配所有 logger 需要复刻现在 uvicorn 的
  formatter patch，风险高于收益。当前缺的只是「应用日志有个去处」这一件事。
- **只提级别不挂 handler**：`setLevel(INFO)` 对没有 handler 的 logger 无效，
  lastResort 仍按 WARNING 截断。

## 如何验证

```bash
JWT_SECRET_KEY=test-secret python -m pytest backend/agent/tests/test_logging_setup.py -q
```

8 条：handler 就位 / `propagate=False` / 幂等 / 子 logger 的 INFO 出现在 stdout /
WARNING 不重复进 stderr / `STP_LOG_LEVEL` 解析（含空串与非法值回落）。

导入级自检（本次实测输出）：

```
backend logger handlers: ['stp_app_stdout'] level= INFO propagate= False
2026-08-30 01:04:38 INFO     backend.scheduler.app_scheduler selftest: app INFO visible
```

上线后判据：`logs/backend.log` 出现
`... INFO backend.scheduler.app_scheduler schedule_registered id=signal_link_reconcile interval=300s`。

## 何时重议

- 需要按模块分级（如把 `backend.realtime` 压到 WARNING 降噪）时，扩展
  `logging_setup.py` 的 logger 白名单，不要在业务模块里各自 `setLevel`。
- 引入结构化日志 / 送外部收集器时，改 handler 即可，调用点无需动。
- 若 stdout 体积成为问题（访问日志本就占大头，本改动只增加应用 INFO），
  先降 `STP_LOG_LEVEL`，不要直接删 handler。
