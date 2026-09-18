# #703 ①：500 job / 30 host 规模的 abort 回归（CI 判规模不变性 + dev 栈现场压测腿）

Status: implemented
Class: bug-fix

- 日期：2026-09-17
- 关联：`#703`（本单 ①，②容量 ADR 仍待裁决）、
  [`#703 第 3 面：池与扇出的可观测信号`](./2026-09-17-db-pool-and-abort-fanout-signals-703.md)
  （本单用它的三条序列当判据）、[`abort host 扇出`](./2026-09-13-abort-host-fanout-703.md)、
  [`emit 折叠`](./2026-09-13-abort-emit-collapse-703.md)、
  [`持锁时长`](./2026-09-13-abort-lock-shorten-703.md)、`#492/#988`（批量终态化）、
  `#1880`（作用域错位）

## Decision

#703 ① 的原文是「用接近 #327 规模（~500 job / ~30 host）的 abort 回归，确认无
QueuePool TimeoutError 风暴」。**回归的判据不能是耗时**——机器相关、还会随时间漂移，
写死一个秒数只会让人下次把它调大。本单把它拆成两层，各自只在能给出确定性的地方断言：

### 1. CI 回归（`backend/tests/services/test_plan_run_abort_scale.py`，4s）

30 host × 17 job（每 host 6 RUNNING + 11 PENDING）= **510 job**，跑真实
`abort_plan_run`，断言四条**规模不变性**：

| 判据 | 断言 |
|---|---|
| DB 往返不随 job 数增长 | 30 host 不变下，60 job 与 510 job 的 SQL 语句数差 ≤ 20（实测 **48 : 48，差 0**） |
| 控制扇出合并 | `schedule_agent_control_fanout` 恰好 **1** 次、含全部 30 台 host 的 control items |
| 进度推送汇总 | `job_status` 恰好 **1** 条（不是每 job 一条） |
| 扇出样本如实 | 恰好一条 `("run", 510)` |

加一条终态语义不变式：330 个 PENDING → ABORTED、180 个 RUNNING **保持 RUNNING**
（等 Agent ack + reaper 收口，abort 不等 agent）。

### 2. 现场压测腿（`tools/dev/abort_scale_probe.py`，dev 栈）

CI 里量不出 p99 与真实并发，所以另有一条可复跑的腿：`seed`（造同规模数据）→
`run`（8/24 并发读 + abort，前后读 `/metrics`）→ `cleanup`。判据用埋点 note 给出的三条
现成序列：池超时增量 = 0、借连接 p99 ≤ 1s、并发读全 200；外加扇出样本 = 造数总量且
`scope="host"` 无样本。

**为什么不把这条腿也塞进 CI**：它要造数、起控制面、并发打 HTTP，耗时与机器相关；而且
「无池超时」在 CI 里因为池从未被真正压过而恒真——那是假绿。分层是刻意的：CI 管
**形状**（形状一旦回归，p99 迟早跟着坏），现场管**读数**。

## Alternatives

- **断言墙钟阈值（如「abort < 1s」）**：机器相关、随负载漂移，且 #327 的现场是
  **偶发**的池耗尽——单请求耗时正常也不能说明没有回归。否。
- **只在 dev 栈做压测、不写 CI 用例**：那就没有回归网——下次谁把 per-job UPDATE 加回来，
  要等到某次大 abort 又把人踢下线才发现。否。
- **把压测腿做成带 `@pytest.mark.scale` 的门禁用例**：门禁环境里既起不了控制面也造不了
  并发，最后一定会被 `-k` 掉，等价于没有。否。
- **用固定语句数上界（如 < 100）代替「两次规模的差值」**：绝对上界会把无关的实现变化
  （多一条 precheck 查询）判红，而差值是**真正要钉的性质**。否。
- **把「并发读 p99 ≤ N ms」也写进 CI**：CI 的进程内调用没有 HTTP/连接池那一层，
  量到的不是同一件事。放进现场腿的判据里（本单报了读数，未设 CI 阈值）。否。

## Verification

**CI 回归**（工作树 `.wt/stp-703-scale`，基线 `origin/main`）：

- `python -m pytest backend/tests/services/test_plan_run_abort_scale.py -q` → **3 passed（4.2s）**；
- 与相邻套件合跑（`..._fanout_metric.py` / `..._abort.py` / `api/test_plan_run_abort_api.py`）
  → **36 passed**；
- **反向验证（4 处定向变异，逐条放回历史形态，实测均红）**：

  | 变异（放回的老形态） | 期望红 | 实测 |
  |---|---|---|
  | 进度推送退回逐 job（#327） | 汇总 emit 用例 | **1 failed** |
  | 控制扇出退回逐 host 投递（#703 之前） | 扇出合并用例 | **1 failed** |
  | PENDING 终态化退回逐 job UPDATE（#492 之前） | 语句数差值用例 | **1 failed** |
  | 扇出数量只算已终态化（漏 RUNNING） | 样本如实用例 | **1 failed** |

- `ruff check`（本单两个新文件）通过；`python scripts/run_gates.py check:quick` → **11 gates 绿**。

**现场压测腿**（隔离实例：临时库 `stp_probe703` + redis db3 + 端口 18099，跑的是
`origin/main` + 本单代码；不是共享 dev 栈）：

| 并发读 | abort | 并发读结果 | 池 | 扇出 |
|---|---|---|---|---|
| 8 | 200 / **0.202s** | 23 次全 200，p50 **36.7ms**、p99 **45.0ms** | 超时 **0**、错误 0、借连接 p99 **0.05s** | 1 条样本、`_sum=510`、桶 `le=1000`、无 host 样本 |
| 24 | 200 / **0.642s** | 57 次全 200，p50 **131ms**、p99 **210ms** | 超时 **0**、错误 0、借连接 p99 **0.05s** | 同上 |

即：**#327 规模下控制面保持可响应**——没有 5xx、没有池超时，只看到尾部延迟随并发上升
（45ms → 210ms），与「有界但非零代价」的预期一致。

**两条现场事实（都值得留给下一次跑这条腿的人）**：

1. **共享 dev 栈量到的是旧世界**：compose 的 `server` 挂的不是主检出，而是独立检出
   `/home/debian13/stp-dev`（`docker inspect` 实测），当时**落后 origin/main 280 个提交**
   ——第一次压测因此拿到 `fanout_samples_delta = 0`（那时的镜像里还没有 #2571 的埋点）。
   这正是「陈旧检出给出的现场是假的」的又一形态；本工具把它判成 **FAIL**（而不是当作
   「没有扇出」静默通过），所以这条腿自带这个哨兵。
2. **UI 限流桶先于池触顶**：默认 `STP_UI_RATE_LIMIT_REQUESTS=300/60s`，8 并发读几十秒即
   429；工具如实报「并发读非 200」，不会被误读成池问题。压测要在**专用实例**上把桶调大，
   不要为此改共享 dev 栈。

## Revisit

- **②容量取向仍待 ADR**（`pool_timeout` 未显式设 = SQLAlchemy 默认 30s，`30+60` 与 PG
  `max_connections=100` 的关系）：本单给出的基线（24 并发读下借连接 p99 = 0.05s、超时 0）
  是那份 ADR 该用的证据之一；但那要在**真实流量形态**下采，不是本单的合成负载。
- **p99 阈值未写死任何地方**：现场腿默认 `--p99-budget 1s`（量级判据）。若将来要把它当
  SLO，需要连续几次真实大 abort 的分布，而不是拿本单的合成值拍。
- **共享 dev 栈的陈旧检出**：它同时影响所有「在 dev 栈上验证」的工作（本单只是撞上了）。
  修法属 #1987/ADR-0046 的检出角色议题（dev 检出与部署源的分离/推进权），本单不碰。
- **CI 用例的规模常量（30 host / 17 job）**：调大只会拉长门禁时间，调小会削弱「与 job 数
  无关」的判别力（对照组的 job 数也会跟着小）。若将来出现 per-job 但**低常数**的回归
  （如每 10 个 job 一次往返），当前预算（差值 ≤ 20）可能漏；届时把差值预算收紧到个位数。
