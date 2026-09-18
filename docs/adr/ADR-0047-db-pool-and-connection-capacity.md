# ADR-0047：控制面 DB 连接池与 PG 上限的容量取向——预算归属与不变量

- 状态：**Proposed** v1.0（2026-09-18 起草，**待 owner 裁决**；本稿不声明 Accepted，也不改任何参数或代码）
- 优先级：P1（它已经造成过一次控制面不可用并被踢回登录，且当前配置在算术上不可能同时成立）
- 目标里程碑：M7
- 日期：2026-09-18
- 决策者：待裁决（起草：平台研发组）
- 标签：连接池, QueuePool, PostgreSQL, 过载, 可观测性, #703
- 关联：[#703](https://github.com/DUElost/stability-test-platform/issues/703)（本稿源自其收口评论列出的第 ② 面）
  / [ADR-0027](./ADR-0027-control-plane-horizontal-scaling.md)（多实例与无状态化：每加一个控制面实例就多两份池）
  / [ADR-0026](./ADR-0026-plan-execution-scaling.md)（准入与规模化灰度）
  / [#2365/#2485 风险 gauge 口径](../notes/bug-fix/2026-09-17-risk-gauge-zeroing-and-scope-2365.md)（同类「观测语义归谁」判读先例）
  / [`docs/development/environment-variables.md`](../development/environment-variables.md)（三个池容量 env 的登记面）
- 版本记录：v1.0（2026-09-18）首次提出，D1–D6 全部开放

## 1. 背景：一组在算术上不可能同时成立的默认值

现状全部来自读码与配置，无推测：

| 事实 | 值 | 出处 |
|---|---|---|
| 同步引擎池 | `pool_size=30` + `max_overflow=60` → 峰值 **90** | `backend/core/database.py:200-231`（`_pool_capacity_kwargs`，`STP_DB_POOL_SIZE` / `STP_DB_MAX_OVERFLOW`） |
| 异步引擎池 | 同上，峰值 **90**（**同源 env、独立两池**） | 同函数被 `get_async_engine_kwargs` 与 `get_sync_engine_kwargs` 各调一次 |
| 进程拓扑 | 单进程（uvicorn 无 `--workers`；SAQ worker 在 lifespan 内 `asyncio.create_task` 起，非独立进程） | `deploy/control-plane/systemd/stability-backend.service`、`backend/main.py` |
| 故应用侧峰值 | **2 × 90 = 180** 条后端连接 | 上三行相加 |
| PG 允许 | `max_connections=100`；`superuser_reserved_connections` 未在部署配置中设置（PG 默认 3） | `deploy/postgres/docker-compose.yml:30` |
| 排队超时 | **生产引擎未设** → SQLAlchemy `QueuePool` 默认 **30s** | `_pool_capacity_kwargs()` 只产出 size/overflow/recycle；仓内出现 `pool_timeout` 的位置只有注释与探针用例（`backend/tests/test_database_config.py`），无一处作用于生产构造 |

即：**默认配置允许应用在自己触顶之前，先把 PostgreSQL 的连接数顶满**——而 09-13 的事故正是这个形态
（`ss` 见 106 条 established、`pg_stat_activity` 98 会话 / 85 阻塞，连超级用户都拿不到连接，
排查工具本身失效；最终靠**停服**恢复，非自愈。数据引自 #703 事故记录，属当时结论性摘要）。

这带来三个彼此纠缠的问题，本 ADR 把它们拆成可分别裁决的点：

1. **预算归属**：两份池加起来归谁管？今天的语义是"各管各的"，于是没有任何一层保证总量 ≤ 数据库上限。
2. **失败形态**：排队 30s 后抛 `TimeoutError`，与"1 秒内快速失败并告诉调用方稍后再试"，
   哪一个对用户更坏？#703 的原始症状（UI 卡顿并被踢回登录）正是前者的表现面之一
   （前端「超时/5xx 不误登出」已在 `frontend/src/utils/api/client.ts` 落地，但那只是止血，不是容量答案）。
3. **可见性**：过载前兆现在能看见——`stability_db_pool_checkout_seconds{engine}` 与
   `stability_db_pool_checkout_failures_total{engine,kind}`（PR #2571 / #703 第 ③ 面）——
   但**阈值与 SLO 仍未定**，本 ADR 要定的正是这条。

## 2. 需要裁决的点（本稿不裁决）

- **D1（池总量不变量）**：是否把「`n_engines × (pool_size + max_overflow) ≤ max_connections − 预留 − 非应用连接`」
  写成**硬不变量并进门禁**（配置期校验，启动即拒），还是继续只做文档约定？
  现状是两侧各自合法、合起来非法，且 env 允许运维单方面改一侧而无人复核。
- **D2（`pool_timeout` 取值）**：显式设还是沿用 30s 默认？取值判据是"API 延迟预算 + 网关超时"，
  而**不是**"最长的那条迁移/报表查询"——后者应走独立连接/只读副本，不该由共享池兜。
  配套问题：超时抛给调用方时对外形态是什么（503 + `Retry-After`？还是现在的 500 面孔）。
- **D3（两份池是否合并）**：sync/async 双池是历史成因（#1516 只是把两侧对齐到同源 env，未合并）。
  是否值得收敛为单一预算？代价：`AsyncSession` 与同步 `SessionLocal` 的 84+ 处调用点混用是既成事实，
  合并池意味着其中一侧要改访问方式——本 ADR 判断这是**代码结构决策**，不该在容量单里顺手做。
- **D4（是否引入连接池代理）**：pgbouncer 一类外部池是"总量控制"的另一条路，但它把可见性从
  SQLAlchemy 手里拿走（我们的 checkout 指标与 `pool_pre_ping` 语义都会变形），且新增一个生产依赖。
  需要明确：**若选它，`stability_db_pool_*` 三条序列的含义必须在同一 PR 里改写**，否则指标会静默说谎
  （这正是 #2485/#2365 那轮"指标语义归谁"的同型风险）。
- **D5（告警与 SLO）**：拿到基线后，告警条件建议落在**事件侧**而不是水位侧：
  `increase(checkout_failures{kind="timeout"}[5m]) > 0` 作 P1；水位（`checked_out/(size+overflow)`）
  只作趋势面板。阈值数字必须来自真实分布，不来自本稿猜测。
- **D6（多实例下的重算）**：ADR-0027 一旦推进到多控制面实例，本稿所有算术按实例数倍增；
  需要现在就把"每实例两份池"写进 D1 的不变量里（`n_instances` 进公式），避免将来重开一次同样的讨论。

## 3. 备选方向与各自代价（供裁决，不排序）

| 方向 | 内容 | 代价 / 风险 |
|---|---|---|
| 甲：收预算 + 快失败 | 两侧合计 ≤ 可用连接（如各 20+10），`pool_timeout` 设 2–5s，超时对外 503 | 排队变短意味着高峰期更多请求**明确失败**；需要前端与调用方先具备退避（部分已具备） |
| 乙：放宽数据库侧 | 调大 `max_connections` | PG 每连接一个进程，内存与锁开销真实；且**没有解决"没有总量不变量"这个根因**，只是把墙外移 |
| 丙：外部池代理 | pgbouncer 池化 | 引入新生产依赖 + 现有池指标语义失效（见 D4）；transaction pooling 与 `SET`/advisory lock 的兼容性需专项验证（本仓 `leader_election` 依赖**会话级** advisory lock 归属，#2519 已实测证明 `Session.commit()` 归还会把锁留在池里另一条连接上——同类"连接身份"假设会被代理再打断一次） |
| 丁：只补观测不动参数 | 消费 #2571 三条序列，先看一周分布 | 最短路径，但默认配置仍处在"算术不成立"状态；**只能作为过渡**，须写明终态出口指向甲/乙/丙之一 |

**本 ADR 的取向（不构成裁决）**：丁 作为"下一步动作"是对的（数据还没采），但它必须是**带出口的过渡**——
出口就是 D1 那条不变量进门禁的那一刻。不标注出口的过渡会沉淀成默认答案，这是本仓反复记过的账。

## 4. 裁决前仍缺的证据（明确列出，避免拍数）

1. **分布基线**：`checkout_seconds` 的 p50/p99 按 `engine` 分开、`checkout_failures{kind="timeout"}` 的日增量
   ——至少覆盖一次规模 abort 与一次批量 hot-update（在窗 Execution 正在做 #703 ①的 500 job / 30 host 压测，
   其判据即引用这两条序列）。
2. **真实并发来源**：谁在占池——APScheduler 周期任务、SAQ 并发 10、`SessionLocal()` 调用点是历史判读，
   需要一份当前拓扑的**按入口归因**（否则 D3 的"合并池"无从评估）。
3. **非应用连接**：备份、`check_schema_sync`、人工 psql 会话占多少预留（D1 公式里的减项）。

## 5. 影响面（裁决后才会发生）

- 参数变更集中在 `_pool_capacity_kwargs()`（一处、两侧同源）+ 可能新增 `STP_DB_POOL_TIMEOUT`
  ——新增 env 必须同步 `docs/development/environment-variables.md`，并按 ADR-0042 的配置读取收敛判据准入。
- 门禁：D1 若成立，落点是启动前校验（`tools/dev/check_alembic_at_head.py` 同类的 `ExecStartPre` 硬守卫），
  而不是运行时静默降级——本仓对"配置误配比回退默认更危险"已有先例判读（`_pool_env_int` 的注释：
  `pool_size=0` 会让每次借连接直接抛 `QueuePool limit ... reached`）。
- 指标：D4 选丙则三条池序列的 HELP/语义必须同 PR 改写。
- 文档：`docs/operations/control-plane-db-maintenance.md` 需补"连接预算"一节（容量属运维事实，不只属代码）。
