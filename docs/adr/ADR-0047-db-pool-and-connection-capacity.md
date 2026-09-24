# ADR-0047：控制面 DB 连接池与 PG 上限的容量取向——预算归属与不变量

- 状态：**Accepted** v1.1（2026-09-23 owner 裁决 D1/D2/D5/D6；D3/D4 保留开放并写明复评条件）
- 优先级：P1（它已经造成过一次控制面不可用并被踢回登录，且当时配置在算术上不可能同时成立）
- 目标里程碑：M7
- 日期：2026-09-18（起草）；2026-09-23（裁决）
- 决策者：owner（2026-09-23）；起草：平台研发组
- 标签：连接池, QueuePool, PostgreSQL, 过载, 可观测性, #703, #2959
- 关联：[#703](https://github.com/DUElost/stability-test-platform/issues/703)（本稿源自其收口评论列出的第 ② 面）
  / [#2959](https://github.com/DUElost/stability-test-platform/issues/2959)（本裁决的**主实施单**：双池实测合计峰值 100 对 PG 可用 97）
  / [ADR-0027](./ADR-0027-control-plane-horizontal-scaling.md)（多实例与无状态化：每加一个控制面实例就多两份池）
  / [ADR-0026](./ADR-0026-plan-execution-scaling.md)（准入与规模化灰度）
  / [#2365/#2485 风险 gauge 口径](../notes/bug-fix/2026-09-17-risk-gauge-zeroing-and-scope-2365.md)（同类「观测语义归谁」判读先例）
  / [`docs/development/environment-variables.md`](../development/environment-variables.md)（池容量 env 的登记面）
- 版本记录：v1.0（2026-09-18）首次提出，D1–D6 全部开放；v1.1（2026-09-23）owner 裁决 D1/D2/D5/D6，D3/D4 保留开放，落地 pgbouncer 之外的首轮参数与启动门禁

## 1. 背景：一组在算术上不可能同时成立的默认值

现状全部来自读码与配置，无推测：

| 事实 | 值 | 出处 |
|---|---|---|
| 同步引擎池 | `pool_size=30` + `max_overflow=60` → 峰值 **90** | `backend/core/database.py:254-260`（`_pool_capacity_kwargs`，`STP_DB_POOL_SIZE` / `STP_DB_MAX_OVERFLOW`） |
| 异步引擎池 | 同上，峰值 **90**（**同源 env、独立两池**） | 同函数被 `get_async_engine_kwargs` 与 `get_sync_engine_kwargs` 各调一次 |
| 进程拓扑 | 单进程（uvicorn 无 `--workers`；SAQ worker 在 lifespan 内 `asyncio.create_task` 起，非独立进程） | `deploy/control-plane/systemd/stability-backend.service`、`backend/main.py` |
| 故应用侧峰值 | **2 × 90 = 180** 条后端连接 | 上三行相加 |
| PG 允许 | `max_connections=100`；`superuser_reserved_connections=3`；`reserved_connections=0`（PG 17 起存在该 GUC，本机为 0） | 本机 `SHOW`（2026-09-23 实测） |
| 排队超时 | **生产引擎未设** → SQLAlchemy `QueuePool` 默认 **30s** | v1.0 时 `_pool_capacity_kwargs()` 只产出 size/overflow/recycle；v1.1 起显式设 `pool_timeout` |

即：**默认配置允许应用在自己触顶之前，先把 PostgreSQL 的连接数顶满**。
2026-09-13 的事故（#703）与 2026-09-23 的 R523 事故（#2959 观测面）都是这个形态：

- **R523（2026-09-23 20:17:18，plan_run 523 / plan 55，37 host / 494 job）**：手动 abort 让 490 个
  RUNNING job 同时回传终态 —— `/complete` 在该窗口 1644 次请求（490 成功 + 1053 个 500 + 101 个
  客户端弃等）。PG 侧 20:17:27–20:18:09 共 **1401 条** `53300`（`remaining connection slots are
  reserved for roles with the SUPERUSER attribute`）；应用侧 `checkout_failures{kind="slots_exhausted"}`
  **1551** 次；池采样峰值 **async 86（pool_size 30 + overflow 56）+ sync 12 = 98 ≥ 97**。
- **同日 09:52（plan_run 518）** 同型：1349 条 `53300`。⇒ 不是偶发，是「手动 abort 大 run」的结构代价。
- **对照（同日 19:43 plan_run 522 的自然终态波，441 COMPLETED）**：同样过 plan_run 行锁，但
  `/complete` 只有 9–21 req/s，async 池 `max_over_time[1m]` **从未超过 4**、零 `53300`。
  差别 = abort 的同步突发（峰值 55 req/s）+ ABORTED 专属的逐 Job `acknowledged_job_ids` 读改写
  + Agent 线程内 3 次重试（P1 要拆的放大回路，另立 ADR 处理）。

这带来三个彼此纠缠的问题，本 ADR 把它们拆成可分别裁决的点（D1–D6）。

## 2. 裁决（2026-09-23 owner 批准）

### D1（池总量不变量）— **接受，并进门禁**

写成硬不变量：

> **所有控制面实例的所有连接池上限之和**
> **≤ `max_connections` − `superuser_reserved_connections` − `reserved_connections`**
>   **− 非应用连接预算 − 运维恢复预留**

- 落点 = **启动前硬校验**（`ExecStartPre`，与 `check_alembic_at_head.py` 同类；不设减号），
  **预算不成立即拒绝启动**。理由：本仓对「配置误配比回退默认更危险」已有先例判读
  （`_pool_env_int` 注释：`pool_size=0` 会让每次借连接直接抛 `QueuePool limit ... reached`）；
  运行期静默降级会把「配置非法」伪装成「负载高」。
- 校验器读 PG 的 `max_connections` / `superuser_reserved_connections` / `reserved_connections`
  **现算**，不硬编码 97；PG 不可达时判失败（与前一行的 alembic 硬门禁同口径：那一行本就需要 PG）。
- 「非应用连接预算 + 运维恢复预留」合成**一个** env（`STP_DB_CONNECTION_RESERVE`，首轮 8），
  不设两个无人有数据的旋钮；将来按 §4 的实测分布再拆。
- 首轮值（单实例、本机 100−3−0=97）：

| 池 | `pool_size` | `max_overflow` | 上限 |
|---|---|---|---|
| sync | 20 | 20 | 40 |
| async | 20 | 20 | 40 |
| **应用合计** | | | **80** |
| 剩余普通连接槽（97−80） | | | **17**（其中 8 计入 reserve，9 为headroom） |

- 这组值是**首轮候选**：必须在隔离压测中校准（§4），校准只改数字不改不变量。

### D2（`pool_timeout` 取值）— **接受 2s，超时对外 503**

- 显式设 `pool_timeout=2s`（env `STP_DB_POOL_TIMEOUT`），判据是「API 延迟预算 + 网关超时」，
  **不是**「最长的那条迁移/报表查询」——后者应走独立连接，不该由共享池兜。
- 对外形态：排队超时（SQLAlchemy `TimeoutError`）与槽耗尽（SQLSTATE `53300`）**统一**返回
  `503` + `Retry-After` + `{"code": "DB_OVERLOADED", "retryable": true}`，**不再**是 500。
  500 会被调用方按「无差别失败」重试，既放大尖峰又污染业务错误统计（R523 现场：1053 个 500
  触发了 Agent 的线程内重试回路）。

### D5（告警与 SLO）— **接受事件侧立即触发**

- 落在**事件侧**而非水位侧；水位（`checked_out/(size+overflow)`）只作趋势面板。
- `slots_exhausted`（SQLSTATE 53300）：**critical**，无 `for` 或 ≤30s —— 槽耗尽意味着硬不变量
  已被突破，不是「趋势」。
- `pool_timeout`：warning，立即触发。
- 新增终态舱壁拒绝率告警（`/complete` 舱壁的 503 计数），与两条取连接失败告警并列。
- 阈值数字必须来自真实分布（§4）；不得用本稿猜测值替换现行阈值。

### D6（多实例下的重算）— **接受现在就把 `n_instances` 写进公式**

- 校验器的乘数是显式 env（`STP_DB_POOL_INSTANCES`，默认 1）；ADR-0027 推进时改这个数即可，
  不重开一次同样的讨论。**默认 1 不是「永远单实例」的声明**，而是「当前拓扑事实 + 多实例必须显式声明」。

### D3（两份池是否合并）— **本次不裁（保留开放）**

sync/async 双池的合一是**代码结构决策**（84+ 处同步调用点与异步侧混用是既成事实），
与容量裁决互相独立；D1 的不变量已覆盖「两份池加起来」的总量，不依赖是否合并。

### D4（是否引入连接池代理）— **本轮不选丙（pgbouncer），保留复评条件**

拒绝理由是可见性与连接身份：外部池会把 `stability_db_pool_*` 三条序列的语义拿走
（transaction pooling 与 `SET`/会话级 advisory lock 的兼容性未验证；本仓 `leader_election`
依赖**会话级** advisory lock 归属，#2519 已实测 `Session.commit()` 归还会把锁留在池里另一条连接上）。
复评条件（任一）：ADR-0027 多实例推进；或「三条池序列的语义改写方案」与实现同 PR 就绪。

## 3. 备选方向与各自代价

| 方向 | 内容 | 代价 / 风险 | 本轮结论 |
|---|---|---|---|
| 甲：收预算 + 快失败 | 两侧合计 ≤ 可用连接（20+20 / 20+20），`pool_timeout` 2s，超时对外 503 | 排队变短意味着高峰期更多请求**明确失败**；需要调用方具备退避（Agent 侧退避是本 ADR 的配套单） | **选甲**（D1/D2） |
| 乙：放宽数据库侧 | 调大 `max_connections` | PG 每连接一个进程，内存与锁开销真实；且**没有解决「没有总量不变量」这个根因**，只是把墙外移 | 不选：移动容量墙 |
| 丙：外部池代理 | pgbouncer 池化 | 引入新生产依赖 + 现有池指标语义失效（D4） | 本轮不选：见 D4 复评条件 |
| 丁：只补观测不动参数 | 消费 #2571 三条序列先看一周 | 默认配置仍处在算术不成立状态 | 不选：v1.0 已判定「必须带出口的过渡」；出口=甲落地（本 PR） |

## 4. 校准所需的证据（首轮值不是终值）

1. **分布基线**：`checkout_seconds` 的 p50/p99（按 `engine`）、`checkout_failures{kind}` 的日增量
   ——已具备 R523 / R518 / R522 三个现场样本（§1），仍需**一次受控压测**（490 RUNNING 同时 abort +
   并发 `/complete` 回流）确认 20/20/2s 不产生新的 `kind="timeout"` 尖峰。
2. **真实并发来源归因**：APScheduler 周期任务、SAQ 并发 10、`SessionLocal()` 调用点、
   `/complete` 舱壁前后的到达率。
3. **非应用连接**：备份、迁移、`check_schema_sync`、人工 psql 的常驻占用分布（拆 reserve 用）。
4. 校准结果回填本节与 D1 表格；**只改数字，不改不变量与门禁形态**。

## 5. 影响面（本裁决落地）

- 参数：`_pool_capacity_kwargs()` 默认 30/60 → 20/20，新增 `pool_timeout`（env `STP_DB_POOL_TIMEOUT`）；
  两侧同源，一处生效。
- 门禁：新增 `tools/dev/check_db_pool_budget.py` 并挂 `ExecStartPre`（硬）；新增 env
  `STP_DB_POOL_INSTANCES` / `STP_DB_CONNECTION_RESERVE` / `STP_DB_POOL_TIMEOUT`，
  同步 `docs/development/environment-variables.md`（ADR-0042 配置读取收敛判据）。
- 对外语义：`backend/core/exception_log.py` 增 `is_db_overload`，`backend/main.py` 全局 handler
  对该类返回 503 + `Retry-After`（D2）；日志分档（#3042）不变。
- 指标与告警：新增终态舱壁指标与告警（D5）；`StabilityDbConnectionSlotsExhausted` 改 critical/去 `for`。
- 文档：`docs/production-minimum-deployment-checklist.md` §3.5 补「连接预算」前置说明
  （容量属运维事实，不只属代码）。
- **不改**：D3（双池结构）、D4（不引入代理）、ADR-0026 §6 的终态聚合语义（P1 另立 ADR 处理）。
