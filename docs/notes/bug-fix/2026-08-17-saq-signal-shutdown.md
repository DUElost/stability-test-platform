# SAQ worker 接管 SIGTERM 导致 uvicorn 优雅停机失效（#283）

Status: implemented
Class: bug-fix

systemd 停机卡满 90s 被 SIGKILL（8/15、8/17 共 3 次）的根因定位与修复。

## Decision

- **现象**：`systemctl restart` 时 `stop-sigterm` 卡满默认 90s 超时被 SIGKILL；
  停机窗口内应用日志**从未出现 "Shutting down"**，agent 心跳仍持续返回 200
  ——uvicorn 根本没进入 shutdown，lifespan 收尾完全不执行（在途 SAQ 任务被
  硬杀）。
- **根因**：`saq.Worker` 启动时对 `SIGINT/SIGTERM` 执行
  `loop.add_signal_handler(signum, self.event.set)`（saq/worker.py:191-192），
  **覆盖同进程 uvicorn 用 `signal.signal` 注册的处理器**（asyncio 信号处理器
  优先）。生产 `STP_ENABLE_INPROCESS_SAQ=1` 下 systemd 的 SIGTERM 只触发
  SAQ 停止事件，uvicorn 的 `should_exit` 永远不被设置 → 无限服务 → SIGKILL。
- **修复**：`backend/tasks/saq_worker.py` 新增 `ControlPlaneWorker(Worker)`
  子类，`SIGNALS = []`——SAQ 不再接管停机信号，信号所有权归 uvicorn；
  SAQ 的优雅停止由 lifespan 收尾的 `stop_saq_worker()`（自带 10s 超时保护）
  负责。`test_saq_tasks.py` 两处幂等测试的 monkeypatch 目标同步改为
  `ControlPlaneWorker`（否则会启动真实 worker）。

## Alternatives

- uvicorn `timeout_graceful_shutdown`：无效——uvicorn 从未进入 shutdown，
  超时参数只作用于 shutdown 内部的排空等待。
- 缩短 systemd `TimeoutStopSec`：仍 SIGKILL，lifespan 收尾不执行，在途
  SAQ 任务被硬杀；只是把等待从 90s 缩短。
- 启动后重新注册 uvicorn 的 SIGTERM 处理器：脆弱，依赖 uvicorn 内部实现。

## Verification

- 本地隔离复现（独立 PG + Redis db15 + `STP_ENABLE_INPROCESS_SAQ=1` +
  10 个 websocket agent + 心跳流量）：
  - 修复前：SIGTERM 后 40s+ 卡死（SigCgt 掩码无 SIGTERM 位、"Shutting
    down" 不出现）——与生产现象一致；polling 传输或关 SAQ 时正常停机，
    据此锁定 SAQ 信号接管为差异点；
  - 修复后：**SIGTERM → 1.0s 优雅停机**（Shutting down → Application
    shutdown complete，端口即时释放）。
- 生产实测：合入 #283 后第二次重启（首次重启停的是旧进程，仍 90s；新
  进程带修复）**总耗时 2s，无 SIGKILL**，`/health` healthy、SAQ ready。
- `pytest backend/tests/tasks/test_saq_tasks.py`：17 passed；
  `backend/tests/api/test_health_saq.py backend/tests/test_phase0_closure.py`：
  19 passed。

## Revisit

- 排查方法沉淀：同进程多组件各自注册信号处理器时，`/proc/<pid>/status`
  的 SigCgt 掩码 + 停机窗口内日志（"Shutting down" 是否出现）是快速定位
  信号所有权冲突的两个抓手。
- 若未来 SAQ 升级改变 `SIGNALS` 语义或引入新的信号注册，需重验停机路径。
