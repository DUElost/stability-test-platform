# SMTP 网络 deadline + 后台池待提交队列有界（#1122）

Status: implemented
Class: bug-fix

## Decision

#1122（R11-F15，设计风险）：`smtplib.SMTP(...)` 无显式超时——网络停滞时 send
会无限挂在 connect/read 上；后台池（`core/thread_pool.py`）只限线程数（8），
ThreadPoolExecutor 自带的**待提交队列无界**——几轮挂死就把 worker 全部拖住，
任务无限积压且不可观测。

修复：

- **SMTP deadline**：`SMTP(host, port, timeout=SMTP_TIMEOUT_SECONDS)`，
  `STP_SMTP_TIMEOUT_SECONDS` 可调（默认 15s）。超时异常沿既有通道错误路径
  （`NotificationDeliveryError` → SAQ 重试）走，不新增分支。
- **队列有界**：`submit` 改为经 `BoundedSemaphore(BACKGROUND_POOL_MAX_QUEUE,
  默认 200)` 限流——满了抛 `PoolQueueFullError`（绝不静默积压），配额在任务
  结束（含异常）后 finally 归还。信号量独立于 pool 实例存活：shutdown 重建
  pool 不清空配额（在途任务结束后仍要释放）。
- **拒绝策略**：唯一调用方 `dispatch_notification_async`（fire-and-forget，无
  SAQ 重试）捕获拒绝 → 记 warning 丢弃；需要可靠投递的通知走 SAQ 的
  `send_notification_task`（独立重试路径，不经此池）。
- **可观测**：`queue_depth()/snapshot()`（容量/深度/累计提交与拒绝）+
  Prometheus `stability_background_pool_queue_depth`（Gauge，提交/完成时同步）
  与 `stability_background_pool_rejected_total`（Counter，拒绝时递增）。

## Alternatives

- 有界阻塞队列（满了阻塞提交者）：调用方多在请求/worker 线程上，阻塞会把
  停滞传染给上游——本池语义是 fire-and-forget，拒绝比阻塞诚实；
- 给 SMTP 挂 watchdog 线程强杀：Python 线程杀不掉（同 #1123 结论），deadline
  是唯一正解；
- 换 aiosmtp/异步化：通知量级不需要，收益不抵改动面。

## Verification

- `pytest backend/tests/core/test_thread_pool.py`：3 passed——提交执行并归还
  配额 / fn 抛异常也归还 / **填满 200 配额后第 201 个提交抛 PoolQueueFullError
  且 rejected_total 递增**、释放后可再次提交；
- `pytest backend/tests/services/test_notification_service.py`：8 passed——新增
  `_send_email` 必须携带 `timeout=SMTP_TIMEOUT_SECONDS`、
  `dispatch_notification_async` 吞掉队列满拒绝不外溢；
- 合并回归：线程池 + 聚合/chain-trigger/terminalization/heartbeat 通知相关
  合计 81 passed；ruff 干净。

## Revisit

- 队列满=丢弃的策略只适用于 fire-and-forget 通知；若未来把关键路径（如
  post-completion）也迁进该池，必须先给它接 SAQ 重试或改阻塞语义；
- `queue_depth` Gauge 在提交/完成时同步，长时间无事件时不刷新——对告警足够；
  若要精确曲线可挂进 app_scheduler 的周期采样（与 saq_queue_depth 同法）；
- SMTP 超时 15s 是单次网络操作上限（smtplib 语义为 per-socket op），一条邮件
  最坏 ~4 次操作 × 15s；如需整链 deadline 再立单。
