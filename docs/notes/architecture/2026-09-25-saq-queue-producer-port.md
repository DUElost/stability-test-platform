# SAQ 入队端口下沉到 core/task_queue，解开 services ↔ tasks 的 20 模块 import 环

Status: implemented
Class: architecture

## Decision

`backend/tasks/saq_worker.py` 原来同时承担两件事：入队 API（`get_queue` / `enqueue_sync` /
`is_saq_ready` / 只读探针）和进程内 worker 生命周期。worker 要 import `saq_tasks`（→ services），
services 入队又要 import 它，于是 services ↔ tasks 形成 20 个模块的 import 环，只能靠函数体内
局部 import 维持可加载。

- 新增 `backend/core/task_queue.py`：队列单例、连接 / 断开、`enqueue_sync`、`EnqueueSyncError`、
  `is_saq_ready` / `is_saq_producer_ready`、`verify_redis_connectivity`、只读探针。只依赖
  saq / redis / `core.redis`，位于 services 之下；函数体逐字搬迁，行为不变；
- `saq_worker.py` 只保留 worker：`ControlPlaneWorker`、指标 hook、`start_saq_worker` /
  `stop_saq_worker`。worker 存活由 `register_worker_alive_probe(_worker_running)` 登记给
  `task_queue`，生产者不 import worker；
- 断开队列统一为 `task_queue.disconnect_queue(swallow_errors=...)`：worker 崩溃重启前和
  producer-only 停机吞异常（原语义），worker 正常停机照常抛出（原 `stop_saq_worker` 语义）；
- 生产代码 11 处调用方（services 5、tasks 1、scheduler 3、routes 1、main 1）改指 `task_queue`；
  测试中的 patch 目标同步改为 `backend.core.task_queue.*`，**不在 `saq_worker` 保留重导出**：
  重导出会让 patch 旧路径的测试不再作用于调用方而不一定失败（假绿）；
- `.importlinter` C1 基线删除 5 行 `services → tasks.saq_worker`（61 → 56）。

放在 `core` 而不是 `backend/tasks/queue.py`：它是 Redis 队列基础设施；而且 C1 把 `backend.tasks`
整包放在入口层，同一个包不能拆成两层。

## Alternatives

- **只把 `saq_tasks` 的 import 挪进 `start_saq_worker` 函数体**：弃——环仍在，只是藏得更深，
  正是 #738 要消除的形态；grimp 也照样能看见。
- **保留 `saq_worker` 重导出以减小测试改动**：弃，理由见上（patch 目标分叉导致假绿）。
- **顺手把 services 的函数内 import 提到模块顶层**：本 PR 不做——测试按模块属性打桩依赖
  调用时查找；改为 `from backend.core import task_queue` + `task_queue.enqueue_sync(...)`
  可以兼顾，留作后续，届时同步下调 `check_inner_imports` 基线。

## Verification

- 依赖图重算：20 模块的强连通分量拆成 5（PlanRun 生命周期）+ 3（ai_assistant）两个小环；
- `lint-imports`：5 条合约 KEPT，C1 忽略项 9 → 4；
- 受影响测试文件（tasks / scheduler / admission / notification / health / lifespan /
  ai_assistant / abort / dedup 等）对隔离 PostgreSQL 16：503 passed；
  新增 `test_is_saq_ready_follows_registered_worker_probe`；
- `TestReaperCompetition::test_second_reaper_skips_row_already_requeued` 间歇失败，
  **与本改动无关**：不含本改动的基线上连跑 10 次失败 4 次，本分支 10 次失败 1 次；
- 后端全量套件结果见 PR 描述。

## Revisit

- PlanRun 生命周期 5 模块环（`plan_dispatcher_sync` / `plan_run_abort` / `plan_run_aggregation` /
  `post_completion` / `plan_chain_trigger`）是拆掉队列后显露的真实领域环，需要一个明确的
  生命周期编排者，单独立项；
- 外部 worker 模式（`STP_ENABLE_INPROCESS_SAQ=0`）行为不变：探针不登记，`is_saq_ready`
  只看生产者连接。
