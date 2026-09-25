# 夜间全量 CI 四红 + 容量验收判据可空过（#3247）

Status: implemented
Class: bug-fix

关联：[#3247](https://github.com/DUElost/stability-test-platform/issues/3247)（main 全量 CI backstop，09-23 起连续红）、
[#3243 Note](./2026-09-24-abort-backflow-scale-3243.md)（回流压测原单）、
[#2959](https://github.com/DUElost/stability-test-platform/issues/2959) / [ADR-0047](../../adr/ADR-0047-db-pool-and-connection-capacity.md)（池观测与告警的生产者）、
[#3244](https://github.com/DUElost/stability-test-platform/issues/3244) / [ADR-0052](../../adr/ADR-0052-terminal-fact-parent-aggregation-decoupling.md)（被接纳 `/complete` p99 的热点归属）、
[#3230](https://github.com/DUElost/stability-test-platform/issues/3230)（审计台账；2026-09-25 复审 A01「容量验收判据可空过」）。

## Decision

### 四红逐条归因

| # | 用例 | 根因 | 修法 |
|---|---|---|---|
| 1 | `integration/test_dispatch_agent_pipeline_contract.py` | ADR-0051 strict 成为缺省（#3258）后，假注册表条目没有 `package_sha256` → `PackageUnavailable: no_package_sha` | 夹具发布真 tar 包并给出整包 sha，`STP_PACKAGES_ROOT` / `STP_TOOLS_CACHE_ROOT` 指向 tmp——验的就是生产现行路径，**不关 strict** |
| 2 | `migration/test_e7f8_passthrough_seed_entry_sha_751.py` | Phase 3 删了 `scripts/<name>/v<ver>/`，用例现场读文件算 sha | 入口 sha 与既有 `LIB_SHAS` 同形**钉常量**，三源出处写进 docstring；另加 `tool_manifest.json` 的 `script` 字段一致性（仓内仍可复核的那一条） |
| 3 | `services/test_plan_run_abort_backflow_scale_3243.py` | 被接纳 `/complete(200)` p99 2.892s > 临时上界 2s；固定 3 轮重放后仍剩 92 条未 ACK | 判据改造，见下 |
| 4 | `test_terminal_bulkhead_2959.py::test_admits_at_most_concurrency_at_a_time` | 对进程级 Counter 断言**绝对值** 0；同进程先跑的 #3243 被拒 602 次（恰等于其 `shed_503=602`） | 改断言增量（与同文件其余用例一致） |

第 3、4 条同源：#3243 用例随 PR #3253 合入时 PR 阶段 `backend-test` 为 SKIPPED，它在 CI 上的**第一次**运行就是 09-24 夜间——从未绿过，并顺带污染了第 4 条。

### #3243 判据：每条线先证明「量到了」，再判「多快/多少」

- **探针分端点**（验收线 3）：每端点 ≥5 次尝试、**全部** 200、200 样本 p99 < 1s，缺一即红。
  旧判据两端点混算、只取 200 的延迟、零样本回退 `[0.0]`——零样本 / 全 503 / 单端点全挂都能通过。
- **心跳按真实 Agent 形状上报**：补必填 `status` 与本机设备清单。首版缺 `status`，**每次都 422**，
  旧判据只取 200，于是 heartbeat 一次都没量到也照样「p99 通过」——新判据首跑即抓到
  `/api/v1/heartbeat 非 200：{'422': 24}`。空设备清单则会触发 `_mark_missing_devices_offline`，改写被测场景。
- **观测在线**（验收线 1、4）：async 取连接观测计数必须增长、async 池峰必须 > 0，否则判红而非判绿。
- **重放至收敛**：真机 outbox 每个 drain 周期重放到 ACK 为止，没有轮数上限；固定 3 轮让收敛与否取决于
  runner 快慢。改为重放到 ACK 或耗尽 120s（与验收线 7 同一预算），另设 400 轮迭代上界。
- **被接纳 `/complete(200)` p99 只进摘要、不在本用例断言**（owner 2026-09-25 裁决）：它量的是父 Run
  行锁串行段（#3244），且随 runner 负载漂移（同一提交本地 1.1s、共享 runner 2.9s）。延迟 SLO 在固定资源的
  容量环境（#105 阶梯）与 ADR-0052 §5 真机门槛判定；共享 CI 只守不变量。

### 池观测在 `Engine.dispose()` 后失明（`backend/core/database.py`）

`Engine.dispose()` 以 `pool.recreate()` 换一只新池：

- Gauge 回调读的是**闭包里的旧池** → 此后 `stability_db_pool_checked_out` 恒 0；
- `connect` 的计时/失败包装装在**旧池实例**上，新池没有 → 取连接超时、槽耗尽从此不计数。

生产只在关停时 dispose（`backend/main.py` lifespan 末尾），运行期不受影响；但全量测试进程里前序用例
dispose 过 async 引擎后，#3243 的验收线 ①④ 读的就是一只拔掉的表——这正是 CI 里 async 池峰恒为 0.0
（本地单跑读 17）的根因。修法：回调改读 `engine.pool`（当下的池）；`engine_disposed` 事件里重装
`connect` 包装（池事件本就经 `recreate()` 的 `_dispatch` 继承，无需重挂）。

## Alternatives

- **契约夹具关 strict / 走源码树回退**：生产已没有这条路径，测了等于没测。
- **e7f8 从 git 历史或站点包现场读**：CI 浅克隆不保证有历史，CI 也没有站点包；**整文件删除**（Phase 3
  对 19 个历史守卫的做法）会丢掉 #751 的不变量，而钉值只多几行常量。
- **舱壁用例清零注册表**：`prometheus_client` 不支持重置单条 Counter，全局清会误伤其它用例；取增量即可。
- **3243 上界放宽到 5s**：数字是拍的，runner 一变还会红；**保持 2s**：夜间持续红，其它回归被这条红灯掩盖；
  **引入容量开关环境变量**：容量环境尚不存在，开关先于环境就是无人使用的机制，还要连带改 env 清单门禁。
  选「只记录不断言」，由将来的容量环境定义判定入口。
- **池观测只在测试侧绕过**（采样器直接读 `async_engine.sync_engine.pool.checkedout()`）：测试能绿，但生产告警
  依赖的那条观测继续带着「dispose 后失明」的缺陷——任何将来引入的运行期 dispose 都会静默关掉 #2959 的池告警。
  改生产侧，并用回归用例钉住。
- **探针最小样本取更大值（如 10）**：本地回流窗口约 4.5s、每端点约 22 个样本；5 已足以排除「没量到」，
  更大的值只会在回流变快时制造假红。延迟分布的判定在容量环境做。

## Verification

本机（生产控制面宿主）全部经 `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`，
`env -i` + `scripts/run_pytest.sh`（testcontainers PG，未设 `TEST_DATABASE_URL`，未触生产库）。

- **复现**：契约用例 `no_package_sha` 红；舱壁用例按污染顺序（先 `test_wait_budget…` 再 `test_admits…`）
  `assert 1.0 == 0.0` 红。修后两者均绿（舱壁全文件 9 passed）。
- **e7f8 钉值出处**：`git show 64d0ef93^:…/v<ver>/{<name>.py,_lib.py}`、站点包 `packages/<name>/<ver>.tar.gz`
  解包后同名文件、迁移 seed 的 `VERSIONS[*].sha`——三源逐字节一致（三个版本、入口与 `_lib` 共 6 个 sha）。
  变异：seed 改记 `_lib.py` 的 sha → 红。
- **池观测回归用例** `test_attach_pool_metrics_survives_engine_dispose`：旧代码红（Gauge `0.0 == 1.0`）；
  只修 Gauge、不重装包装 → 第二条断言红（超时计数 `0.0 == 1`）；全修绿。
- **探针判据反例**（6 反 1 正，纯函数）：新判据 7 passed；把 `_probe_violations` 换回旧判据 → 6 个反例全红。
- **端到端复现 CI 形态**：同一进程先跑 `backend/tests/api/test_agent_routes.py`（多处 dispose async 引擎）
  再跑 3243——旧 `database.py`：`pool_peak_async 0.0`、232 次采样、async 取连接观测 0（与 CI 的 0.0 同形），
  新断言判红；新代码：`pool_peak_async 17.0`、取连接观测 513，全绿。
- **3243 本地校准**（修后，同进程前置 dispose）：572 次请求（1.167×）、82×503、0×500；2 轮收敛 5.72s；
  heartbeat 22/22 p99 47.6ms、`/health` 22/22 p99 12.6ms；被接纳 `/complete` p99 1.20s（只记录）。
- 全量 `backend/tests/`：见 PR 描述（本 Note 提交时运行中）。
- `python scripts/run_gates.py check:quick`：见 PR 描述。

## Revisit

- 容量环境（#105 阶梯）建成时：由它定义 `/complete(200)` p99 的判定入口，从 `CALIBRATION_3243` 取数；
  ADR-0052 裁决（父行热点移出）后重估该口径。
- 若将来引入运行期重建引擎（故障转移、fork 后重建等），本修复保证池观测不失明；新增引擎一律经
  `_attach_pool_metrics` 接线，别手工挂监听。
- 同类「量了什么都没量到也算过」的判据：新增验收/容量用例时，先写「零样本 / 全失败 / 单点失效」三个反例。
- **#1525 前移评估的输入**：四红里两条的结构性成因相同——PR 阶段不跑 `backend-test`，于是 PR 新增或
  改动的 backend 测试文件合入前**一次都没执行过**（#3253 新增的 3243 用例首跑即红；Phase 3 删目录让 e7f8
  连红两夜）。候选的最小前移是「PR 只跑本 PR 触及的 `backend/tests/**` 文件」，而不是整套前移；是否采纳按
  #1525 规则另行裁决，本 PR 不动 CI。
- 重放至收敛后总请求数不再有 3 轮的天然上限：用例里 37 台 host 共用一个客户端 IP，共享 Agent 桶
  （2000/min）。若慢 runner 上削峰率长期很高、总请求逼近该桶，`/complete` 会出现 429 并按「非预期状态码」
  判红——届时按 host 分源 IP 还原真机形态，而不是把 429 加进允许集或放宽判据。探针两条路径均豁免限流，
  不受此影响。
