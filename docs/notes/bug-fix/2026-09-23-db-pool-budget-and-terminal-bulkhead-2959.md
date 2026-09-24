# 连接预算进门禁 + 过载语义 503 + 终态舱壁（ADR-0047 D1/D2/D5/D6 裁决落地，#2959 第一段）

Status: implemented
Class: bug-fix

关联：[#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（主实施单）、
[ADR-0047](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（v1.1：本单把 D1/D2/D5/D6 从
Proposed 裁为 Accepted 并落地）、
[#703](https://github.com/DUElost/stability-test-platform/issues/703)（同族历史事故；关闭 ≠ 容量闭环）、
[#3042](./2026-09-21-unhandled-exc-log-shape-3042.md)（日志分档：本单只改状态码分流，不动日志体积口径）、
[#1257](./2026-09-10-prometheus-alert-contract-1257.md)（「引用不存在的标签/指标」形态：本单新增规则带场景用例）。

## Decision

**起因**：2026-09-23 20:17 `stp-admin` 中止 plan_run 523（plan 55，37 host / 494 job）——490 个
RUNNING 作业同时回传终态，`/complete` 在 20:17–20:19 共 1644 次请求（490 成功 + 1053 个 500 +
101 个客户端弃等），异步池从 1 涨到 **86**（pool_size 30 + overflow 56），叠加 sync 池 12 ⇒
**98 ≥ PG 非超级用户可用槽 97**；PG 侧 20:17:27–20:18:09 打出 **1401 条 53300**，应用侧 1551 次
`kind="slots_exhausted"`，UI/回调平均耗时冲到 10s 级。同型现场同日 09:52（r518，1349 条 53300）；
对照 r522 的自然终态波（441 COMPLETED、同样过 plan_run 行锁）池峰只有 4、零 53300——差别在
abort 的同步突发（峰值 55 req/s）+ ABORTED 专属的逐 Job `acknowledged_job_ids` 读改写 +
Agent 线程内 3 次重试。本条按 owner 批准的方向落 **P0 的第一段**：

1. **ADR-0047 裁决 D1/D2/D5/D6**（`docs/adr/ADR-0047-*.md` v1.1，Proposed → Accepted）：
   D1 总量不变量进门禁、D2 `pool_timeout=2s` + 过载对外 503、D5 告警事件侧立即触发、
   D6 `n_instances` 现在就进公式；**D3（双池合并）与 D4（pgbouncer）保留开放**并写明复评条件
   （前者是代码结构决策，后者动的是连接身份与指标语义——`#2519` 已实测会话级 advisory lock
   会被 `Session.commit()` 归还到池里另一条连接）。
2. **池默认 30/60 → 20/20、`pool_timeout=2s`**（`backend/core/database.py::_pool_capacity_kwargs`）：
   两侧合计 `2×(20+20)=80`，落在 97 以内（余 17，其中 8 计为 reserve、9 为 headroom）。
   新增 `pool_capacity()` 作为**单一读数口**——门禁与引擎共用一份算术，避免「校验器自己另算一份」。
3. **启动期硬门禁**（`tools/dev/check_db_pool_budget.py` + unit 模板 `ExecStartPre` 无减号）：
   读 PG 的 `max_connections` / `superuser_reserved_connections` / `reserved_connections`
   **现算**（不硬编码 97；PG<17 无 `reserved_connections` 按 0），比对
   `n_instances × n_engines × (size+overflow) + reserve ≤ 可用槽`；不成立即**拒绝启动**。
4. **过载语义统一为 503**（`backend/core/exception_log.py::is_db_overload` + `backend/main.py`）：
   `SQLSTATE 53300` 与 SQLAlchemy 池排队超时 → `503` + `Retry-After: 1` +
   `{"code":"DB_OVERLOADED","retryable":true}`；**死锁 40P01 / 连接中断 08xxx / 真 bug 仍 500**
   （判定面刻意收窄：不同故障的处置不同，不能借「过载」一个词一起改语义）。
5. **`/complete` 独立并发舱壁**（`backend/core/terminal_bulkhead.py`）：默认 16 并发、
   等待预算 500ms，**排队发生在连接池之外**，超预算抛 `TerminalBulkheadFull` → 同一个 503 语义；
   指标 `inflight` / `waiting` / `rejected_total` / `wait_seconds`（D5 告警的生产者）。
6. **告警口径**（`deploy/prometheus/alerts-stability-platform.yml`）：
   `StabilityDbConnectionSlotsExhausted` → **critical 且去 `for`**（R523 现场 5m 的 `for` 让告警
   20:23 才 firing，而事故 20:18 就结束了）；`StabilityDbPoolCheckoutTimeout` 去 `for` 且描述
   改为「2s 快失败」口径；新增 `StabilityTerminalBulkheadRejected`（持续削峰 ≥10m）。

## Alternatives

- **乙：调大 `max_connections`**——不选：PG 每连接一个进程，且不解决「没有总量不变量」这个根因，
  只是把墙外移（ADR-0047 §3）。
- **丙：pgbouncer**——本轮不选：会拿走 `stability_db_pool_*` 三条序列的可见性，且 transaction
  pooling 与 `leader_election` 依赖的**会话级 advisory lock 身份**冲突（`#2519` 同族）。
- **丁：只补观测不动参数**——不选：v1.0 已判定它「必须是带出口的过渡」，出口就是本单的 D1 门禁。
- **池容量只改 env 不改默认值**：不选。默认值就是「新部署的第一天」，只改 env 会让下一台新机
  重新踩同一个坑；env 仍是覆盖入口（`STP_DB_POOL_*`）。
- **门禁 fail-open（PG 不可达就跳过）**：不选。生产 unit 里它紧跟在 `alembic upgrade head` 之后，
  那一行本就需要 PG；fail-open 会让「配置非法」伪装成「数据库暂时不可用」。开发机形态
  （无 `DATABASE_URL` / SQLite）仍 WARN + exit 0。
- **舱壁用中间件或调小池来替代**：不选。中间件会波及所有路由（heartbeat/claim 也要留通道）；
  调小池只是把排队从 PG 搬进 QueuePool——用户看到的仍是卡顿，换名字不解决问题。
- **给 `ErrorDetail` 加 `retryable` 字段（Pydantic/TS 同步）**：未做。现有 500 处理器本就返回
  裸 dict，本单沿用同一形状并只在过载响应里多一个键，避免动 OpenAPI/前端类型注册面；
  前端按 `code` 映射「系统繁忙，正在恢复」（UI 文案属后续前端单）。
- **在 handler 里逐条记拒绝日志**：不选。波内会拒绝成百上千次，逐条会把现场可查时长再压一次
  （#3042 同族纪律）；改为**首次告警一行 + 指标**。

## Verification

- `python -m pytest backend/tests/test_database_config.py tests/test_check_db_pool_budget.py
  backend/tests/test_terminal_bulkhead_2959.py backend/tests/test_db_overload_response_2959.py
  backend/tests/test_unhandled_exception_log_3042.py` → **全绿**（池默认/pool_timeout、门禁判定
  与 CLI、舱壁名额/预算/不漏名额/指标、503 契约与 500 未回归、#3042 日志形态）。
- 舱壁测试含两条关键不变式：**超预算拒绝后名额不泄漏**（`wait_for` 取消路径）与
  **接线守卫**（`/complete` 必须在第一次 DB 调用之前持名额——写好了没接线等于没有）。
- `promtool test rules deploy/prometheus/alerts-stability-platform.test.yml` → **SUCCESS**
  （两条改动的期望值逐字更新 + 新增规则的场景用例；新增规则必须带场景，#2151 棘轮）。
- `python tools/dev/env_inventory.py --check` → **OK（265 个读取名）**；两个新舱壁键登记进
  `backend/.env.example` 的「过载保护」组。
- `python tools/dev/check_governance_surface.py` → **OK**（S1–S15：ADR 头部状态与
  adr/README 主表、DOC-MAP、M7 看板四处一致）。
- 真实 PG（只读 `SHOW`）：`check_db_pool_budget.py --env-file <生产 .env.backend>` →
  `[OK] app_total=80（每引擎 40 × 2） instances=1 budget=80 available=97 reserve=8 headroom=17`。
- `python scripts/run_gates.py check:quick` → 见 PR 描述（最终结果）。
- **未验证/未做**（刻意留痕，避免「跑过一半当全绿」）：真机/真流量的压测（R523 型 490 job
  同时 abort + 并发 /complete 回流）**尚未跑**，本单所有容量数字属「首轮候选」，须按
  ADR-0047 §4 在隔离压测中校准；生产部署（unit 换行 + env + 重启 + 告警副本同步 + 机队分发）
  **本次未执行**——重启才生效，须走部署窗。

## Revisit

1. **P0-3（Agent 侧削峰）单独一单**：首次终态 HTTP 只打一次、429/503 立即交 outbox（不线程内
   重试）、outbox 对 503 也吃 `Retry-After`、指数退避 + full jitter、每机终态上传 1–2 并发、
   abort 终态 0–5s 抖动。现状：`backend/agent/api_client.py:38-43`（RETRIES=3 / base 1s）与
   `backend/agent/outbox_drainer.py:29`（只有 408/429 读 `Retry-After`，5xx 走 else 无限重试）。
   **未改这一条之前，舱壁的 503 仍会被 Agent 线程内重试 3 次**——两条必须同批上线才算闭环。
2. **P1（终态事实与父 Run 聚合解耦）另立 ADR**：修订 ADR-0026 §6「终态事务内自增 + 单一
   terminalization 入口」的执行语义；删逐 Job `acknowledged_job_ids` 读改写
   （`backend/services/agent_completion.py:438-453`，全仓无消费方）；post_completion 走独立
   低并发队列（SAQ `priority` 仅 postgres broker 可用，Redis 下只能落成独立队列 + 独立 Worker）。
   顺带核对 ADR-0012 关于 post_completion「与终态同事务生成」的措辞与现行 `commit` 后入队的差异。
3. **压测场景**：`backend/tests/services/test_plan_run_abort_scale.py` 是 30 host × 17 job
   （其中 180 RUNNING）且断言「RUNNING 保持等待 ack」——覆盖不了回流。新场景要同时制造
   490 RUNNING + abort + 并发 `/complete` + heartbeat/steps/claim 背景流量，验收线 = 0×53300、
   0×500（背压只能是结构化 503）、p99 < 1s、池不越预算、490 条事实全 ACK、计数一致、120s 收敛。
4. **阈值校准**：`STP_DB_CONNECTION_RESERVE=8`、舱壁 16/500ms、告警窗口与 severity 均为首轮值，
   按 ADR-0047 §4 用压测分布回填。
5. **部署动作**（需窗口）：① 生产 unit 补 `ExecStartPre` 预算门禁（`daemon-reload`）；
   ② `.env.backend` 无需改动（默认值即新值）；③ 重启后端；④ `/etc/prometheus/rules` 副本同步
   + `POST /-/reload`；⑤ 若要让 Agent 侧削峰生效还需机队分发（另一单）。

## 后续修正（2026-09-23 晚，owner 裁决）

1. **门禁 fail-closed 收口**（`tools/dev/check_db_pool_budget.py`）：
   - 每条 `SHOW` 由宽口径 `except Exception` 收窄为**只容忍**「旧 PG 没有
     `reserved_connections`」（SQLSTATE `42704` / `psycopg.errors.UndefinedObject`，且仅该键）；
     连接中途断开、权限不足、其它读失败一律非零退出——宽口径会在「读一半掉线」时退回
     默认值，那是事实上的 fail-open；
   - `--env-file` 除 `DATABASE_URL` 外还注入预算键（`STP_DB_POOL_SIZE` /
     `STP_DB_MAX_OVERFLOW` / `STP_DB_POOL_TIMEOUT` / `STP_DB_POOL_INSTANCES` /
     `STP_DB_CONNECTION_RESERVE`，ambient 优先、文件补缺项），输出新增 `config_source=`；
     否则手工检查可能验的是默认值而不是文件里写的值；
   - 显式传入的 `--env-file` 不存在/不可读 ⇒ 非零退出（静默退回默认 = 一次假绿）。
2. **舱壁告警判据**（`deploy/prometheus/alerts-stability-platform.yml`）：
   `increase(...[5m]) > 0` + `for: 5m` **不能证明持续拒绝**——一笔孤立 counter 增量会让
   表达式保持约 5 分钟为真，边界条件下可进 firing。改为
   `count_over_time((increase(stability_terminal_bulkhead_rejected_total[1m]) > 0)[10m:1m]) >= 8`
   （最近 10 分钟里 ≥8 个一分钟窗口有拒绝），去掉 `for:`；定位为**非 paging 诊断 warning**
   （Alertmanager 路由里只有 critical 走抢修 receiver），paging 条件待 #3243 的量级分布，
   并应叠加 terminal outbox backlog / 终态收敛延迟 / 父 Run 长时间不终态。
   场景补两条：单次拒绝后长期静默**不得** firing（含原式会假阳的 `eval 6m` 边界）、
   10 分钟持续拒绝**必须** firing。
3. **拒绝日志粒度**：`_rejection_logged` 由「进程生命周期一次」改为「每个 burst 一次」
   （静默窗 `_REJECTION_LOG_QUIET_SECONDS=60s` 划分 burst），第二次事故不再没有起点日志；
   指标语义不变。

验证：`pytest`（门禁 13 / 舱壁 9 / 过载 11，全绿）、`promtool test rules` **SUCCESS**、
真 PG 只读三条——默认值 `[OK] ... config_source=default`；把旧池参数 30/60 写进 env-file 时
`[FAIL] app_total=180 ... config_source=env-file`（证明文件真的参与判定）；PG 不可达 `rc=1`。
