# #703 第 3 面：池的等待/超时 与 abort 扇出规模（把「过载前兆」变成可查序列）

Status: implemented
Class: bug-fix

关联：[#703 的长事务半边（PR #2519）](./2026-09-17-leader-election-txn-boundary-703.md)、
[#703 abort 持锁时长](./2026-09-13-abort-lock-shorten-703.md)、
[#703 host 扇出](./2026-09-13-abort-host-fanout-703.md)、
[#703 emit 折叠](./2026-09-13-abort-emit-collapse-703.md)、
[指标生产者全表面判据（#2287）](../testing/2026-09-16-metric-producer-full-surface-2287.md)。

## Decision

#703 的 09-17 收口评论把剩余三面写清了：① 500 job / 30 host 规模 abort 的压测回归；
② 池与超时的容量取向（**需 ADR**）；③ 扇出规模 / 池等待 / ASGI 异常率的可观测信号「规模侧仍不完整」。
本单只做 ③，而且只做其中**确实没有序列**的两条。判断依据是读码得到的现状，不是推测：

| 想问的问题 | 本单之前 | 本单之后 |
|---|---|---|
| 池当下借出多少？ | ✅ `stability_db_pool_checked_out` / `_overflow`（gauge） | 不变 |
| 一次 abort 持锁多久？ | ✅ `stability_plan_run_abort_lock_seconds{phase}` | 不变 |
| 5xx / 请求延迟？ | ✅ `stability_api_requests_total{method,endpoint,status_code}` + 延迟直方图（#2286 已核实有生产者） | 不变 |
| **借一条连接等了多久 / 有没有等不到？** | ❌ 零痕迹——`QueuePool limit …` 只进日志；gauge 在「忙但正常」与「满且排队」之间**同形** | `stability_db_pool_checkout_seconds{engine}` + `stability_db_pool_checkout_failures_total{engine,kind}` |
| **一次 abort 到底牵动多少 job、是不是越界？** | ❌ 零痕迹——#1880 的「单台 host 升级终态化整轮 run」当时只能逐条数日志 | `stability_plan_run_abort_fanout_jobs{scope}`（`run` / `host`） |

「ASGI 异常率」那条**没做**：`api_requests` 已带 `status_code`，错误率在查询侧可算，缺的只是
面板（属 #1258/#2286 已记录的读法判读，不是埋点缺失）。把它塞进本单只会多一条重复序列。

### 1. 池侧：包 `Pool.connect()`，而不是加 pool 事件

`checkout` 事件在**已经拿到**连接之后才触发——排队等了多久、有没有超时它都不知道；
`QueuePool` 也没有公开的 waiters 计数（`_cond` 是私有实现，跟版本走）。
`Pool.connect()` 是公开方法，且 `Engine.connect()` / `Session` / async 侧
（`AsyncAdaptedQueuePool` 继承同一入口）的每条取连接路径都经过它：**一处包住即全覆盖**，
不必给 `create_engine` 注入 `poolclass`（那要为 async 单独子类化，改动面更大且更容易与方言默认池走偏）。

三个刻意留痕：

- **计时口径写进 HELP**：它是「排队等待 + 建连 + `pool_pre_ping` 往返」，**不是纯排队时间**。
  把它读成排队时间会高估池竞争；把它读成"含建连"才是它真正回答的问题（拿一条可用连接要多久）。
- **超时按 `sqlalchemy.exc.TimeoutError` 判类**，其余 `error`。两类成因的处置完全不同
  （池容量 vs 数据库/网络），混成一条就等于没分。它与内置 `TimeoutError` 无继承关系（实测核过），
  不存在误判成别的超时。也不与 #1958 的死锁计数重叠——那条发生在 checkout **之后**。
- **成功与失败都计时**（`finally` 里观测）：只记成功次数就看不见「等了 30s 才失败」这一形态，
  而这恰是 #703 的现场。
- **幂等**：`_stp_checkout_instrumented` 标记防重复包装——叠加计时会让 p99 与超时数同时失真。
- **SQLite 不装**：沿用 `_attach_pool_metrics` 既有守卫。给一个不存在「池耗尽」语义的后端
  埋恒零序列，是假绿的一种标准形态。

### 2. 扇出侧：一个调用点、按 scope 分桶

埋在 `abort_plan_run` 主返回前，数量取 `len(aborted_jobs) + len(abort_requested_jobs)`——
即「已终态化（PENDING→ABORTED）+ 已下发控制信号等回收器收口（RUNNING）」两类之和：
对「这次扇出多大」而言，两类都是这次 abort 造成的工作量，只记前者会在有 RUNNING job 时系统性低估。

`scope` 由 `host_id is None` 直接决定（不看审计文案），这是判据本身：**`scope="host"` 的样本
落进 run 量级的桶，就是 #1880 那类作用域错位复发**。日志行同时补了 `scope=` 与两个计数，
让「指标报警 → 日志定位」这一跳不用再靠猜。

早退路径（run 已终态、precheck 无 job）**不记**：它们没有扇出，记 0 会把直方图低桶灌满噪声。

### 3. label 值域纪律（#1927）

`engine` 只可能来自两个调用点（`"sync"` / `"async"`），`kind` 与 `scope` 在 helper 里过白名单，
越界值归一到 `unknown`/`error`——Python client 的子序列**永不回收**，label 失控等于观测面自己
变成泄漏面。负数与非数值输入直接丢弃（不产生"看起来正常"的 0 样本）。

## Alternatives

- **给 `create_engine` 注入自定义 `poolclass`（子类化 `QueuePool` / `AsyncAdaptedQueuePool`）**：
  否决。要在同步/异步两侧各建一个子类并改两处 kwargs 构造，而 async 侧一旦与方言默认池类不一致
  就是运行期报错；实例上包 `connect` 是同一处覆盖，改动面小一个数量级。
- **轮询 `pool.status()` / 读 `QueuePool._cond._waiters` 做"等待者数"**：否决。私有实现，
  跟版本走，且是采样而非事件——恰好在过载那一刻最不可靠。
- **只在 `get_db` 依赖里 `except TimeoutError` 计数**：否决。它会漏掉服务层/Scheduler 里
  被宽 `except Exception` 吞掉的那批（#1958 的教训就是这类错误在服务端日志里躲了四周）。
  埋在池入口是"吞不掉"的位置。
- **把 ①（规模压测回归）与 ②（容量取向 ADR）一起做**：否决。②是方向级取舍（`pool_timeout` 默认 30s
  该不该设、与 PG `max_connections=100` 的容量关系），必须由 ADR 裁决后另行落地；①要 dev 栈与
  500 job/30 host 的造数，是验证不是埋点，混进来会让本 PR 的判据从「序列在场」变成「现场结论」。
- **给新指标直接配告警/面板**：否决（本轮）。告警阈值需要基线数据，而基线正是这三条序列要采的；
  先埋点、拿到分布再定阈值，避免拍一个数当 SLO。消费方与再评估条件记在 Revisit。

## Verification

实跑（工作树 `.wt/stp-703b`，基线 `origin/main`）：

- `pytest backend/tests/test_database_config.py backend/tests/services/test_plan_run_abort_fanout_metric.py
  backend/tests/services/test_plan_run_abort.py backend/tests/services/test_abort_lock_order_1985.py
  backend/tests/api/test_plan_run_abort_api.py tests/ -q`
  → rebase 前 **1478 passed / 1 failed**，rebase 到 `origin/main`（含 `af71d0d8`）后重跑
  → **1486 passed / 0 failed**。
  那 1 条失败与本单无关：`test_abort_running_job_releases_lease_only_after_agent_ack` patch 的
  `backend.api.routes.agent_api.broadcast_run_job_update` 已随 #1520 的切片搬走。
  当时用 `git stash` 证明「去掉本单改动同样红」（不是推断），据此立了 #2568；
  随后 `af71d0d8`（#1520 plan_run_catalog 切片）把目标改到
  `backend.services.agent_completion.broadcast_*`，#2568 已带证据关闭。
  **一条自纠**：#2568 里我写「`backend/tests/` 由 PR 门禁外、只能等夜间」是从 `ci.yml` 注释
  推出来的，当时代码里没实测过——推断要标成推断，这次侥幸与事实一致，但方法不对。
- `pytest tests/test_alert_metric_producers.py tests/test_grafana_dashboard_contract.py -q` → **10 passed**
  （#2287 的全指标面生产者判据：三条新序列各自「定义 → helper → 异文件调用点」一跳可追）。
- `ruff check`（本单 5 个文件）通过；`python scripts/run_gates.py check:quick` 见 PR 表格。

**红绿自证**（6 处定向变异；每处都先断言变异后源码仍可解析——否则红的是语法错，不算证据）：

| 变异 | 期望 | 实测 |
|---|---|---|
| 摘掉 `_attach_pool_metrics` 里的 `_instrument_pool_connect(...)` 一行 | 池用例红 | **1 failed** / 13 passed |
| 观测从 `finally` 挪成只在成功路径（失败不计时） | 超时计时用例红 | **3 failed** / 11 passed |
| `kind` 判类退化（超时也算 `error`） | 分类用例红 | **1 failed** / 13 passed |
| 去掉 `abort_plan_run` 的 `record_plan_run_abort_fanout(...)` | 两条 PG 用例红 | **2 failed** / 5 passed |
| `scope` 判反（host ↔ run） | host 用例红 | **2 failed** / 5 passed |
| 数量只算 `aborted_jobs`（漏 RUNNING 那一类） | run 用例红 | **2 failed** / 5 passed |

全部恢复后：池 **14 passed**、扇出 **7 passed**。

> 过程中一条自我纠正，值得留在记录里：第 4 项变异最初以
> `"host" if host_id is not None else "run"` 为锚点，而它在 `record_*` 与 `logger.info`
> 里各出现一次 → `count == 1` 断言当场拒了这次变异，那一轮跑出的是「绿」，
> 差点被当成「判据没牙」。锚点收紧到整条调用之后才拿到预期的红。
> **变异的锚点本身必须也有判别力**，否则测的是脚本，不是代码。

**离线可跑**：池的 4 条用例不需要 PG（`creator` 给 sqlite3 连接，排队/超时与方言无关）——
用真库反而会让 `pool_timeout=0.05` 与容器往返争时间，测到的可能是网络而不是排队。

**未做/未覆盖**：

- 未新增告警与面板（见 Revisit）；
- 未测「async 引擎的池也装了探针」：`AsyncAdaptedQueuePool` 走同一 `Pool.connect`，
  但本仓的 async 引擎实例在测试进程里不建连接，装了也采不到样本；留作现场验证；
- 池等待的**分位数**取决于生产流量形态，本地只能证明「样本进得去、类别分得对」。

## Revisit

- **消费方**（拿到基线分布后二选一）：`increase(checkout_failures{kind="timeout"}[5m]) > 0`
  作为「池耗尽」的 P1 前置告警；或在 Grafana 加「借连接 p99 + 超时计数」面板。
  阈值必须来自真实分布，不来自本 PR 的猜测。
- **②容量取向仍待 ADR**：`pool_timeout` 现在没显式设（SQLAlchemy 默认 30s），
  而 `STP_DB_POOL_SIZE=30 + STP_DB_MAX_OVERFLOW=60` 与 PG `max_connections=100` 在 #703 那次
  同时见顶。这三者的关系是方向级取舍，**本单刻意不碰**——三条新序列正是那份 ADR 该用的证据。
- **①规模压测回归仍缺**：约 500 job / 30 host 的 abort 下「控制面保持可响应」目前只有代码级依据。
  有了 `fanout_jobs{scope}` 的分布，压测的判据可以写成「样本落在哪个桶 + p99 借连接耗时」，
  而不是只看有没有报错。
  **→ 已于同日交付**：[#703 ① 规模压测回归](./2026-09-17-abort-scale-regression-703.md)
  （CI 判规模不变性 + dev 栈现场压测腿；本条列出的两个读数都用上了）。
- **一次 host 热更新的总影响面**：`abort_jobs_for_host` 会按 run 逐个调用 `abort_plan_run`，
  所以 host 级事件产生**多条**样本而非一条聚合值（避免双计数）。直方图不能按事件求和；
  真需要「这次热更新总共动了多少 job」，得在 `abort_jobs_for_host` 单独埋一条按事件聚合的序列。
- 若将来 `Pool.connect()` 之外出现别的取连接入口（自建 pool、第三方扩展），该处需同样接线；
  本单的可断言锚点是 `engine.pool` 实例上的 `_stp_checkout_instrumented` 标记。
