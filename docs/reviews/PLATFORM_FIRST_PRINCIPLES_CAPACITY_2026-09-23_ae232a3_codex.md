# 稳定性测试平台：第一性原理与长期复利定向审计

> 日期：2026-09-23（Asia/Shanghai）。性质：跨链静态审计与规模验收方案，**不是**
> 150 host / 3750 device 已验收证明，也不代替 R01–R15 全区复审。
>
> **代码基线：`main@ae232a308ee6a46890accec810448c22f1fbdfc5`**（本报告所有
> 行号与结论只对此 SHA 负责）。收尾时远端 `main` 已前进至
> `8bc6bc1e`（PR #3208）。`ae232a30..8bc6bc1e` 共 636 个文件变更，逐条比对
> 本报告引用面后：**心跳 / 连接池 / 认领 / permit / 设备列表 / 产物上传 /
> 存储保留**等锚点全部未动；**三处变了**——ADR-0051 升到 v1.2（Phase 3
> 落地：210 个版本目录删除、`AGENTS.md` 与 `script-versioning.md` 改口径、
> 不可变门禁退役），以及 `backend/api/routes/scripts.py` /
> `scripts/run_gates.py` 行号位移（语义未变，已在 F06 复核）。
> 因此 F06 需按 F06.1 的时效补充一起读，其余结论不受影响。
> 收尾前主检出被并行 Execution 切到其 docs 分支（`8752aaf6`，本仓已知现象），
> 故取证时的树与收尾时的树不同；不得把本报告的行号与结论无条件套用未来主干。

## 0. 执行摘要与边界

**目标**：平台将持续接入稳定性专项、Shell/Python/APK/JUnit 插桩 APK/
PowerShell/Jar 工具、日志收集/导出/去重汇总/Jira 闭环、客户刷机工具，最终需要
承载**同时运行 150 台 host × 每台理论最多 25 台 device = 3750 台 device**。
这是本次评估的未来目标，不是已有文档规模数字应立即相等的事实；旧文档的
44→60→100 host、60+/1000+ device 数字是历史验收/设计包线，不因新目标而自动
变成代码缺陷。后续修订目标、验收标准、ADR 与运维容量基线时，应区分
“新规划”与“已实测”。

**核心判断**：已有设备租约与 fencing、host 内操作 permit、心跳降频、列表翻页、
material-only 设备推送、工具分层及包校验等正确方向；但**不能从这些机制的存在推出
3750 台长跑已可用**。先办两件不可绕过的事：把连接数预算的不变量与失败语义裁下来
（F01，已有 #2959 / Proposed ADR-0047），以及让崩溃产物投递的丢弃变得可观测（F08）
——后者破坏的是「报告 + 工单材料」闭环可信度，不是延迟。之后才为单 host 25 台真机
与 fleet 150 台各建可复验的运行证据；不要先把扩容理解为「增加 worker /
调大单页 limit / 上更多控制面实例」。

**取证等级**：`代码确定` = 本基线源代码/测试/配置可证；`历史记录` = ADR、运维
Note 或 GitHub Issue 的当时数据，本轮未重测；`模型推导` = 明示前提的算术，
不是吞吐承诺；`待验证` = 需要隔离负载、真机或现场只读观测。关键路径抽样覆盖
R01/R03/R04/R06–R12/R14/R15 的部分链路及工具接入决策（R09–R10 只查上传与保留
两跳，未追每种 SoC 的采集实现）；未做全量安全
渗透、每种专项/客户工具的源码审计、数据库执行计划、整站浏览器测试或生产容量
压测。不读取生产库、凭据、主机清单；不更改配置、服务、设备或 ADR。

## 1. 从不可删去的需求建模

1. **被管理的对象不是执行吞吐**：区分注册 host、在线健康 host、已发现设备、
   可租设备、RUNNING job、同时占用 ADB/USB/刷机通道的昂贵操作。150/3750
   规定最大在场/挂测规模；测试内容、重试、产物字节数与操作并发另定工作负载。
   `backend/agent/claim_loop.py:43`–`:61` 和
   `backend/agent/operation_scheduler.py:28` 证明 RUNNING 与瞬时操作分层，不能
   把 “5 permit/host” 误当作 “只能管理 5 device/host”，也不能承诺 3750
   个脚本同时进行。
2. **事实必须单一、可追溯、可恢复**：PostgreSQL 中的 job/lease/版本记录
   是持久事实，Redis 只作队列与瞬时通信；版本与内容摘要冻结执行语义，产物
   与 Jira 的外部副作用要求可审计、幂等与重试。同步/异步两份连接池、发布
   清单与 DB script catalog 不能各自成为第二个不受约束的权威。
3. **共享机制只抽象不变的部分**：专项在 Plan/Suite/Step/Lease/终态/产物
   接缝复用；工具按平台/host/device 宿主与外部资产归类。APK 是资产或
   instrumentation 被调用物，Jar/PowerShell 是特定宿主上的可执行适配器，
   不是四种新的顶层 pipeline action。客户方言只能存在薄适配器，不进入
   核心状态机。参见 `docs/design/2026-semantic-ownership.md:64`–`:78`、
   `docs/adr/ADR-0033-tool-kit-ecosystem-integration.md:99`–`:142`。
4. **优先把已知失败变成有界失败**：OOM、连接槽耗尽、ADB 假死、USB 失联、
   终态回传失败、产物/工具缺失、Jira 重复提交，均应有显式失败类别、
   足够的诊断信息和终态出口；在线指标与“用户有意下线/搬迁/清空”不能混为一谈。

### 1.1 规模算术——仅作压测输入

- 150 × 25 = **3750** 最大在场 device。若每 host 的 25 台设备均健康、
  使用默认建议且整轮心跳耗时可忽略，服务端
  `suggested_heartbeat_interval(25) = 20 + 25 // 10 = 22s`
  （`backend/services/agent_host_heartbeat.py:30`–`:42`）。则约
  `150/22 = 6.82` 个 host 心跳/s、`3750/22 = 170.45` 个 device 快照/s。
  **实际周期 = 整轮采集/HTTP/回调耗时 + wait(建议间隔)**，且环境钳制、
  非健康设备数会改建议值（`backend/agent/heartbeat_thread.py:207`–`:216`,
  `:730`–`:747`）；因此 6.82/170.45 是条件性模型，不是现场速率或上界承诺。
- `backend/api/routes/heartbeat.py:372`–`:400` 将 device/lease **批量读取**，
  `:534`–`:535` 每个被见到的 device 更新 `last_seen` 后在 `:639` 提交。
  在上述理想化 22s 场景，稳态约 **170 次 device 刷新/s**，不是
  170 条 SQL/s（ORM flush、事务、WAL、行大小和争用需隔离库测量）。
  硬件字段另有降采样：`:65`–`:72`、`:500`–`:528`。
- 一个控制面进程里 sync 和 async **独立**池，各允许 `30 + 60 = 90`
  条连接，配置峰值 **180**（`backend/core/database.py:254`–`:303`,
  `:344`–`:368`）。部署文件设置 PG `max_connections=100`
  （`deploy/postgres/docker-compose.yml:28`）；在 PostgreSQL 17 默认保留 3 个
  superuser 槽、未另设 `reserved_connections` 的条件下，一般角色可用至多
  **97**，还要减备份、人工诊断、其他应用连接。`180 > 97` 是**配置允许峰值
  无法同时成立**，不是当前连接数。多进程/实例的预算按各实例两池相加重算。
  SQLAlchemy 的 `pool_size + max_overflow` 为每池并发连接容量，参见
  [官方 2.0 说明](https://docs.sqlalchemy.org/en/20/errors.html#queuepool-limit-of-size-x-overflow-y-reached-connection-timed-out-timeout-z)；
  PG 保留槽语义参见 [官方 17 文档](https://www.postgresql.org/docs/17/runtime-config-connection.html)。
- `GET /devices` 的 **1200 是单响应护栏**，`frontend/src/utils/api/devices.ts:52`
  –`:66` 已按 `total` 翻页。3750 台在“无额外筛选、全量成功”的场景需要
  `ceil(3750/1200)=4` 次设备请求/查看者/轮；设备页每 10s refetch，
  另外还有 host 列表与 material WS 触发的失效（
  `frontend/src/pages/devices/DevicesPage.tsx:48`–`:69`，
  `frontend/src/hooks/useFleetDeviceUpdates.ts:8`–`:55`）。不是 1200 台的
  fleet 硬上限，也不是所有浏览器始终只有 4 次请求。

## 2. 按优先级排序的发现

### F01 · 连接总预算缺不变量；真实过载已有历史证据｜P1

**证据**：两份同源配置、相互独立的池在本基线各可达 90，部署 PG
`max_connections=100`；`backend/tests/test_database_config.py:47`–`:100`
测试单侧配置与默认回退，不能代替整机预算约束。`ADR-0047` 仍为
**Proposed**（`docs/adr/ADR-0047-db-pool-and-connection-capacity.md:3`,
`:43`–`:69`）；#703 为 CLOSED，**不是** D1/D2 已裁决。
[开放的 #2959](https://github.com/DUElost/stability-test-platform/issues/2959)
记录 2026-09-20 发生过 `TooManyConnectionsError` 与终态回传失败；这是
**历史现场记录**，本轮未读生产库或日志，不能称现在仍在复现。

**已有防护**：`backend/core/metrics.py:457`–`:470` 对取连接耗时、失败原因
计数；`deploy/prometheus/alerts-stability-platform.yml:279`–`:307` 已将
`slots_exhausted` 和 `timeout` 分开告警。告警发现事故但不限制连接峰值。

**触发/影响**：批量 abort/终态化、后台任务与互动查询叠加；增加一个控制面实例
会再增加两池容量，不能把更多实例当无代价修复。结论只确认配置容量不变量
不成立；峰值占用、事务持续时间与事故因果须另测。

**出口**：由 owner 裁决 ADR-0047 的应用总预算、PG 预留、失败超时及对外
错误语义；先在隔离/只读监测里按 sync/async 取 p99、`slots_exhausted`/`timeout`
增量、PG 连接来源与突发相关性，再决定减池、增加 PG 上限、隔离负载或代理。
不要直接改 `.env.backend`，也不要只调大 `max_connections` 来隐藏总量约束。

### F02 · 25 台真机心跳时间预算尚未证明｜P1 验收风险

**证据**：Agent 一轮 `_tick()` 结束才等待下一间隔
（`backend/agent/heartbeat_thread.py:207`–`:216`）；每拍并发探测至多 8 个
（`:284`–`:337`），ADB echo 5s，due 时电池 10s、版本 5s 和网络探测
（`backend/agent/device_discovery.py:656`–`:718`）；磁盘探测低频另起最多
8 worker（`backend/agent/heartbeat_thread.py:339`–`:380`）。慢指标缓存和
状态翻转强采已在场（`:228`–`:280`）。设置读取异常时，心跳不中断，
但慢指标回退每拍采（`:449`–`:492`）。现有
`backend/agent/tests/test_heartbeat_parallel_probe.py:41`–`:82` 是 **10 台模拟
设备**的并发/异常隔离测试，不是 25 台真实 ADB/USB/power 条件下的 p99。

**触发/影响**：同机 25 台的慢 ADB、设备回线、低频磁盘探测或集中热更新会
叠加到 `_tick` 时间，建议间隔不再等于实际发包间隔；监控中的假掉线与
设备认领节奏都可能受影响。不能仅凭 8 线程推断最坏时延为一个设备超时。

**出口**：隔离环境采集 discover、fast/slow、disk、HTTP、回调各阶段耗时
与实际心跳间距的分布；分别做稳态/首次接入/25 台同时恢复/多台超时。
用已确认的设备在线新鲜度与恢复 SLO 判定，不拍脑袋把周期数字改大。

### F03 · 稳态写入、日志与聚合的放大需要实测｜P2 容量风险

**证据**：F01/F02 公式下每拍 device `last_seen` 都刷新（F01 前的算术）；
Agent 稳态会在 `backend/agent/device_discovery.py:667`、
`backend/agent/heartbeat_thread.py:523`、`backend/agent/heartbeat.py:58`
按设备记 INFO，而 Agent 默认日志级别即 INFO
（`backend/agent/main.py:22`–`:23`）。三条同时启用时，22s 模型对应约
`3 × 3750 / 22 ≈ 511` 条**设备级**日志/s（≈4.4×10^7 条/日的量级外推），
尚未计错误、步骤或后处理日志。

**同类放大已发生过一次（历史记录）**：
`backend/services/dashboard_summary.py:73`–`:80` 记录了修复前控制面侧的实测
量级——626 台 ONLINE 设备 × 每 5s 一拍 = 单日 **392 万行、占 `backend.log`
的 68%**；#2960 通过把判据收敛为单一
`device_connectivity_changed` 并只在翻转记 INFO 收口（本基线已落地）。
这条先例说明：**设备数 × 每拍常数的日志放大是本项目已经付过学费的形态**，
而 Agent 侧那三条逐设备 INFO 尚无同等门控。历史 392 万行不是本轮实测、
也不能直接乘到 3750 台。
控制面设备状态日志已对稳态降为 DEBUG
（`backend/api/routes/heartbeat.py:47`–`:62`）；device WS 仅 material 更新才发
（`:592`–`:618`），且只入 `fleet:devices` 房间
（`backend/realtime/socketio_server.py:537`–`:548`）。Dashboard 汇总合流
默认 ≤1Hz，但每次汇总仍读取 host/device 全量字段
（`backend/services/dashboard_summary_publisher.py:1`–`:6`,
`backend/services/dashboard_summary.py:103`–`:116`）。

**触发/影响**：长跑时心跳稳态日志、WAL/行更新及全量摘要扫描合计可能
放大存储与诊断成本，影响单人排查真正异常。**不是**“每拍 3750 条 WS 广播”
或“每秒 3750 次 Dashboard 重算”。

**出口**：用隔离 3750 行基数、同时接入/重连/批量终态三种负载测 DB
写入、锁等待、WAL、摘要耗时、日志 byte/s 与异常信噪比。若证实昂贵，
先按状态变化保留 INFO 与故障证据、稳态采样/降级，再评估增量汇总；
避免为省日志丢失 USB/ADB 故障轨迹。

### F04 · RUNNING 作业、认领节奏与操作并发不可混算｜P1 验收风险

**证据**：健康设备仍可逐 tick 认领（`backend/agent/claim_loop.py:43`–`:61`），
每拍 `effective_slots` 默认 ≤5（`backend/agent/capacity_reporter.py:100`
–`:135`），每 host 全局昂贵操作 permit 默认 5
（`backend/agent/operation_scheduler.py:28`–`:48`, `:91`–`:105`），job
worker 池上限默认 50（`backend/agent/job_runtime.py:108`–`:115`；池**懒建线程**，
不是启动就起 50 个）。这些限制分别保护 claim 突发、脚本/ADB 操作和排队
容纳能力，不是相同“并发”。一台 host 的多台设备可各有 RUNNING Job，但每 host
默认同时持有 permit 的操作不超过 5；fleet 默认许可上限的算术是
`150 × 5 = 750` 瞬时操作，**不等于**真实吞吐、所有步骤都必持 permit，
也不意味着 3750 台不能同时挂测。

**触发/影响**：刷机/安装 APK/JUnit instrumentation、重启、采日志与
普通稳定性脚本的资源画像不同；25 台同 host 长跑可能先撞到 USB
电流/带宽、ADB server、磁盘、网络或内存，而不是控制面 device 数。

**出口**：为每一专项定义每 host 设备数、步骤分布、产物速率与同机并发，
分别核对租约/fencing、队列延迟、permit 等待与取消、worker 内存、
异常回收（掉电/重连/agent 重启/abort）；安全优先于“最大同时执行脚本数”。

### F05 · 全量列表正确性已修，重复全量读取成本未验｜P2

**证据**：服务端上限 1200 是单响应体积保护
（`backend/api/routes/devices.py:32`–`:38`, `:355`, `:434`–`:452`）。
前端 `fetchAllDevicePages` 按总数取完，#3131/#3152 已 CLOSED，
`ExpandableDeviceTable` **客户端过滤再按 50 条展示**
（`frontend/src/components/device/ExpandableDeviceTable.tsx:195`–`:230`），
设备页每 10s 拉 device 与 host 全量；material 推送也能触发失效
（`frontend/src/hooks/useFleetDeviceUpdates.ts:29`–`:55`）。

**触发/影响**：多个操作员同时开页，四页设备请求 × 查看者 × 刷新频率，
产生序列化、重复 DB 查询、浏览器内存和重绘成本；静态逻辑不能推断
真实延迟，也不能把翻页正确性修复说成“3750 条会被截断”。

**出口**：隔离基数下用多查看者测试初屏、筛选、分页竞态、材料推送下
的请求数/p95 和浏览器内存；达到设定预算再考虑后端过滤、字段投影或
列表聚合，而非抬高 1200 上限。

### F06 · 新专项最有价值的是宿主/契约与发布收敛｜P2 集成风险

**证据**：`backend/agent/pipeline_engine.py:1733`–`:1775` 仅处理
`script:<name>` 并以 `python`/`shell` 直接运行；
`backend/api/routes/scripts.py:35`–`:36` 注册类型同为两种。
这不是所有 APK/Jar/PowerShell 无法接入：它们可作为包内资源或由兼容宿主上
的薄 Python/Shell 适配器调用；**直接作为 script_type 运行**目前不支持。
ADR-0030 对 Suite/Case 配置层已有 Accepted 与实施记录
（`docs/adr/ADR-0030-multi-case-suite-management.md:1`–`:19`）；
日志链/客户 Jira 的归属见 `docs/design/2026-semantic-ownership.md:64`–`:73`，
ADR-0012 的 Jira 第 1 层已实现、第 2–3 层仍 Proposed
（`docs/adr/ADR-0012-post-completion-pipeline-jira-automation.md:1`–`:23`）。

**已知的验证缺口**：`tools/dev/verify_tool_contract.py:160`–`:164` 默认校验
fixture；`scripts/run_gates.py:130`–`:135` 没传新工具的真实 entrypoint。
已有开放的 [#3094](https://github.com/DUElost/stability-test-platform/issues/3094)
跟踪；新族自声明的验证力度由
[#3093](https://github.com/DUElost/stability-test-platform/issues/3093) 跟踪。
本审计**不重复立单、不以脚手架绿灯宣称所有新工具符合 D2**。

**发布边界**：ADR-0051 Accepted v1.1；代码面包执行已落，
`backend/agent/script_packages.py:49`–`:89` 默认 `off`，可 `on/strict`；
`backend/agent/tool_cache.py:106`–`:148` 有包 SHA 校验。**fleet 全 strict、
整轮 package_active 未被本轮证明**，Phase 3 前不得删版本目录。
现有 [#735](https://github.com/DUElost/stability-test-platform/issues/735) /
[#3075](https://github.com/DUElost/stability-test-platform/issues/3075)
跟踪脚本膨胀与包分发；同日另一 Execution 的 ADR-0033–0051 定向复审
（`docs/reviews/REVIEW_ADR0033_TO_0051_ISSUE2546_2026-09-23_ae232a3.md`，
已随 `ca38d079` 进入 main）与本报告 F06 有重叠，此处只讨论对扩展成本的影响，
不代其复核结论、也不重复立单。

**出口**：每个新专项/客户工具先回答宿主（平台/host/device）、授权、
输入/输出/退出码与超时、环境自检、单机并发和失败恢复；脚本版本与外部
资产 digest 分开追踪，以小型端到端样板验证真实入口的 Tool Contract、
构建/分发/回滚及产物/Jira 幂等。控制面只积累稳定状态机和通用接缝，不
为每个后缀造一个核心执行分支，也不把客户 token 送至设备端。

### F06.1 · 时效补充：Phase 3 已在收尾当天落地，验证缺口的影响面上升

**为什么单列**：本报告的取证基线是 `ae232a30`，其 F06 写的是「包执行代码已落、
开关默认 off、fleet 全 strict 未验、Phase 3 前不得删版本目录」。**该判断对
基线成立，但在同一天的 `8bc6bc1e`（PR #3208）之后已被推进**，不改就会误导读者。

**新事实（对 `origin/main@8bc6bc1e` 复核，2026-09-23 收尾）**：

- `backend/agent/scripts/` 下 `v*.*.*/` 版本目录计数 **210 → 0**，改为每族一棵
  源码树；`tools/dev/check-script-version-immutability.py`、`AGENTS.md` 与
  `docs/development/script-versioning.md` 同步改口径（ADR-0051 v1.2）。
- ADR-0051 头部现记「**Phase 2b ✅ 且 fleet 48/48 已 `strict`**」「Phase 3 ✅」。
  这是 **ADR 自述的运行记录**，本轮**未**逐台复核 `verify_scripts` 与
  `package_active`，不据此宣称包化生产验收通过——证据等级仍是 `历史记录`。
- F06 的两条核心事实在新树上**依旧成立**：`scripts.py` 仍是
  `_VALID_SCRIPT_TYPES = {"python", "shell"}`（现 `:36`），`run_gates.py` 的
  `tool-contract` 仍不带 `--entrypoint`（现 `:126`–`:128`）——只验 fixture。

**为什么这反而让 #3093/#3094 更要紧（第一性原理）**：版本目录此前既是发布单元
也是**兜底路径**——包拉取失败可以退回 `nfs_path` 树继续跑。目录退役后，
「DB 权威 → 包身份 → `tools_cache`」成为唯一通路，于是**准入侧的验证强度**
（真实 entrypoint 是否满足 `--check-env` / `summary.json` / 退出码命名空间、
新族归类声明是否可核）从「绿灯偏软」升级为「**没有兜底的单点**」。
`STP_VERIFY_TOOL_CONTRACT=0` 静默 SKIP 与 fixture-only CI 的原有缺口（#3094）、
自声明归类可绕过（#3093）因此权重上升，且新增专项越多越贵。

**据此调整的动作**：不动 ADR-0051 的裁决（方向级决策归 owner，且 Phase 3 已合入）；
而是把 #3093/#3094 的优先级在包化终态下**重评一次**——判据是「真实 entrypoint
能否被自动验证」，不是「脚手架能跑」；并在每次接入新外部工具时用一次
真实 `--check-env` + 一次端到端产物校验留痕。若发现包通路成为故障单点，
那是新的容量/可用性事实，须按 §3.A 的 SLO 判据立案，而非临时恢复目录。

### F07 · 多实例是隔离/容量选项，不是当前已验的扩容答案｜P2

**证据**：ADR-0027 多实例检查清单为 opt-in
（`docs/adr/ADR-0027-control-plane-horizontal-scaling.md:94`–`:118`）；
同文 `:118`–`:123` 登记同 run 手动 API × SAQ merge 跨实例缺互斥。
2026-07-23 的运行 Note 将当时生产多实例标记“未开启”
（`docs/operations/adr-0026-admission-and-scale-gray-rollout.md:205`–`:217`），
本轮**未重新核对部署拓扑**。加实例会放大 F01 的 PG 连接预算，还要验证
Redis fan-out、会话路由、单例调度、merge 原语与部署版本一致性。

**出口**：先证明单实例/单库在目标负载下的具体瓶颈；若 SLO 要求多实例，
把连接总预算与 ADR-0027 清单作为前置，在隔离环境做跨实例重复提交、
断线接管和 merge 互斥演练。不要用一个新基础设施掩盖原预算问题。

### F08 · 崩溃产物投递是有界即丢；日志链与它不是同一种保证｜P1 验收风险

**为什么重要**：「日志采集 / 导出 / 去重汇总 / Jira 提单」是新目标里最容易被
规模打穿的一段，因为它同时吃 device 数、崩溃率与产物字节数。

**证据（两条路径的强度不同，不能混为一谈）**：

- **DeviceLogEvent（DLE）= 持久事实驱动**：控制面在 PlanRun 终态后标
  `UPLOAD_PENDING`，Agent 以 30s `_recover_pending` 轮询入队
  （`backend/agent/event_uploader.py:3`–`:4`, `:47`, `:647`–`:650`），
  重试次数落 SQLite 持久面（`:62`–`:105`），本地目录缺失显式落终态
  以免 `UPLOAD_PENDING` 永久卡死（`:442`）。队列满时
  `enqueue_local_event` 返回 `False`（`:371`–`:377`），调用方仍可感知。
  这条链符合「DB 承载业务事实、Redis 只作队列」的硬不变量。
- **JobArtifact（watcher 拉到的 crash artifact）= 显式 fire-and-forget**：
  `backend/agent/artifact_uploader.py:12` 声明「队列满立即丢」，
  容量 `DEFAULT_QUEUE_MAXSIZE = 256`（`:94`），满即
  `stats.submits_dropped += 1` + 一条 WARNING（`:277`–`:285`）；
  未 configure / 非 drain 停服的残余也计入同一计数（`:214`–`:220`,
  `:243`–`:245`）。按 ADR-0018 5B2 边界这是**故意的**（注释明写
  `log_signal unaffected`），不是缺陷；问题是**丢弃只在 Agent 本机可见**。

**可观测性缺口（确定的）**：心跳只上报两个**持久 outbox** 的深度
（`backend/agent/heartbeat_bindings.py:112`–`:113` →
`terminal_outbox_pending` / `log_signal_outbox_pending`，落到
`stability_agent_outbox_pending` gauge，`backend/core/metrics.py:670`–`:671`）；
`submits_dropped` **没有**进入心跳 payload、没有对应 Prometheus 序列，
只在 `stats.to_dict()` 与停服日志里（`backend/agent/artifact_uploader.py:52`–`:59`,
`:220`）。因此在 3750 台 × 崩溃洪峰下，「产物注册被丢了、丢多少」无法
从控制面回答；报告完整性只能事后靠人读日志得知。

**触发/影响**：同一时刻大量设备 crash/ANR（开关机、Monkey、刷机后
首轮正是这种形态）时，puller 与 uploader 两道有界队列先后填满；
结果是**测试结论完整但工单材料缺失**，或去重汇总少一台 host 的产物，
而平台侧无信号。这比「慢」更贵：它破坏的是闭环可信度。

**出口（先测后调）**：先在隔离环境以已知产物数做洪峰注入，量出
队列深度、丢弃计数与端到端完整率的真实关系；再把
`submits_dropped`（连同 `puller` 的同名计数）接入心跳观测面，
使「丢过」成为可告警事件。**不要**在未测量前直接抬大 256 或改成
无界队列：无界队列只是把丢弃换成内存增长与 OOM，且会掩盖下游
背压。若验收要求 artifact 零丢失，那是新的持久 outbox 设计（同 DLE
形态），属方向级变更、须另立 ADR，不能在本轮审计里顺带拍板。

### 复利资产（正向清单，勿退回）

审计同时确认了几处**不靠加机器、而靠收窄事实源**的改动。它们是单人
项目能把长期成本压住的原因，记录以免日后被「顺手优化」掉：

- **中心存储族单一事实源**：`backend/storage_families.py:1`–`:49` 把
  「哪些族、按 run 还是按 job 分桶」从三处各自字面量收成一份，且
  `backend/tests/scheduler/test_retention_cleanup.py:362`–`:365` 刻意
  **不写死族名**——新增一族时夹具自动覆盖，漏桶立刻变红。这修掉了
  2026-09-13 链 B 审查记的「`jira/` 永不清理、成为无索引孤儿」
  （F-B1，见 `docs/reviews/REVIEW_CROSS_REGION_CHAIN_B_2026-09-13.md:281`）：
  本基线 `purge_run_storage_dirs` 已覆盖 `devices|dedup|jira|_meta` +
  `jobs/{job_id}` + `devices/unassigned/{event_id}`
  （`backend/scheduler/cron_scheduler.py:252`–`:310`），并保留
  「先文件后行、失败整批推迟重试」的自愈语义
  （`test_retention_cleanup.py:401`–`:419`, `:470`–`:494`）。
  判读：该缺口**已在代码面关闭**（本轮读码 + 既有用例，未运行）；
  历史审查文件不改，其结论以新基线复审为准。
- **越界不删**：`_within_shared_root` 拒绝跟随 symlink 删到共享根之外
  （`backend/scheduler/cron_scheduler.py:236`–`:249`, `:285`–`:286`），
  配套 `test_purge_refuses_target_outside_shared_root`。
- **持锁窗口是判据而非性能旋钮**：retention 批量上限被明确记录为
  「purge（NFS）与行删除同事务，窗口长度 ∝ 本 tick run 数」，正确动作
  是调小批量而不是移除 purge（`test_retention_cleanup.py:67`–`:73`）。
  这类注释本身就是防「顺手优化」的安全带。
- **中心存储双份是已知事实且有只读测量器**：`MERGE_REPORT_FAMILIES`
  显式建模「同一份 merge 报表在 `dedup/` 与 `jira/` 各存一份」
  （`backend/storage_families.py:41`–`:44`）；
  `backend/scripts/measure_center_storage.py:8`–`:25` 只读量 E-1 / E-1b /
  E-3，并把**不覆盖**的 E-2（retention 残留）、E-4（merge 端到端耗时）、
  E-5（Jira 外链有效率）写在脚本头部。扩容前这三项需按目标基数重采；
  本轮**未运行**（需挂载点与授权）。

## 3. 验收路线：把目标变成可证伪的门槛

### A. 定义工作负载与 SLO（先于调参）

- 约定 **150 在线 host / 3750 已发现 device** 与健康、可租、RUNNING、
  permit-in-use 等分母；定义占比、真正同时跑的重操作峰值、计划生命周期，
  按专项分开常规、刷机/OTA、APK instrumentation、日志洪峰与 Jira 后处理。
- 约定 heartbeat freshness、device 首次发现和故障恢复、queue/admission
  p95/p99、步骤完成与正确终态、作业 UNKNOWN/误杀、报告完整性、页面
  初屏及故障时可诊断时长；阈值经现有真实基线 + 用户需求裁定，本稿
  **不发明毫秒值、TPS 或成功率**。
- 设不可妥协的正确性信号：设备租约/fencing 无双占；abort 后无永久
  RUNNING；外部产物/Jira 侧效果幂等可查；列表总数/已加载数可对拍；
  打开 `strict` 时包摘要失败必须 fail-closed，不能执行不明字节。
  另加一条规模专属信号：**注入已知数量的崩溃产物，端到端必须能报出
  「收到多少 / 丢了几条」**（F08）；「拿不到但不报错」不算通过。

### B. 分开验 host 控制面与 device 真执行

- **B1：host 维度**：按既有 ADR-0026 方法，先隔离单机多 Agent 实例冒烟，
  然后以明确标记的**合成设备**走历史 44→60→100、延展到 150 host；
  记录心跳/准入/续租/终态、DB 两池、内存、后台聚合、批量 abort、热更新
  与断连恢复。`docs/operations/adr-0026-admission-and-scale-gray-rollout.md:221`
  –`:235` 明确 B1 不等于真机通过、观测窗不得热更新共享代码树。
- **B2：device 维度**：先 **1 台 host × 25 真机**覆盖 USB/供电/ADB/磁盘、
  5 permit/25 Job、同步故障与长跑；再在有真机库存时分档增加 host 和
  总 device 数，至 150 × 25。按专项资源画像分别测，不以 B1 的模拟
  serial 数或历史 87 台 device 替代。上述阶梯是**建议验收设计**，不是
  宣称库存可达或修改原 ADR 阶梯。
- **B3：跨域故障**：在隔离环境将控制面短暂不可达、PG 连接拒绝、NFS
  工具包不可用/校验失败、Agent 重启、同 host 多台 ADB 超时、USB 掉树、
  崩溃/ANR 洪峰（同时压 puller 与 artifact 两道有界队列）、批量 abort、
  Jira 重试/重复响应组合入测试。每次对拍 “用户意图—设备
  在线事实—租约—Job 终态—产物/外部副作用”；触发回退或暂停的判据应
  是事先约定的 SLO/正确性破坏，而非只看 CPU 平均值。
- **B4：单人可维护性**：每新增一种专项只新增必要 Adapter/版本/样板
  契约测试与文档，验收新 host 装机/版本对齐/包校验/回滚、真实工具
  `--check-env` 与端到端结果；不要凭平台脚手架 fixture 通过签收客户工具。

### C. 改造顺序与终态出口

1. **先保底**：推进 ADR-0047 owner 决策与 #2959；把“实例数 × 双池 +
   非应用连接 + PG 预留”定为统一预算，观测取连接的事件侧失败；若暂时只
   加监控，明确这是**过渡**，终态为可机械校验的跨池/实例不变量。
   同批把 F08 的丢弃计数接入既有观测面（它是低成本、高杠杆的一步）。
2. **再测 host 25**：补 tick 分段、每 host 资源/日志/permit 证据；只对
   已证实的热点减法优化（如稳态 INFO 或不必要的重复全量拉取），并保留
   故障证据。必要时为同步启动负载评估错峰，不凭直觉改全 fleet 周期。
3. **再扩 fleet**：B1/B2 分档，先复用现有准入/监控/灰度与回退；
   若测到单实例边界，再裁决 ADR-0027 多实例前置，勿反过来先堆服务。
4. **最后规模化接入**：固化 Tier 分类 → 真实 Tool Contract → 包版本与
   SHA → 小流量 canary → 结果/故障归因 → 回滚的单一流程。#735/#3075 的
   发布膨胀面已随 Phase 3 收敛（见 F06.1），**剩下的正是绿灯可信度**：
   #3093/#3094 在目录兜底退役后权重大幅上升，宜排在「再新增 N 个专项」
   之前处理，否则每次接入都是在验证不足的单点上加倍。

## 4. 已有证据与未完成项

- `backend/agent/tests/test_heartbeat_parallel_probe.py` 验证并发/异常隔离；
  `frontend/src/utils/api/devices.test.ts:92`–`:106` 验证跨 1200 翻页；
  `backend/tests/test_database_config.py` 验证池参数和事件指标；均为**局部
  代码/测试证据**，本轮没有重跑。
- `docs/operations/adr-0026-admission-and-scale-gray-rollout.md:205`–`:235`
  记录了历史真机 87-device 成功、44→60→100 host 未验、B1/B2 拆分。
  2026-09-20 的 #2959 记录过去的连接槽拒绝；不得外推为 3750 台运行结论
  或断言此刻的生产连接数。
- 本轮 GitHub 只读核对：#703/#3131/#3152 CLOSED；#2959/#3093/#3094/
  #735/#3075 OPEN（**截至 2026-09-23**，以后需重新核对）。Issue 状态
  是追踪线索，不替代合并代码与运行事实。
- 基线时效复核：`git diff --name-only ae232a30 origin/main` = 636 文件，
  与本报告引用面求交后仅 3 处位移/变更（见页首与 F06.1）；其余锚点
  在 `8bc6bc1e` 上仍与 `ae232a30` 一致。
- 定向验证（本轮实际运行，均在隔离入口、未指向生产库）：
  `python -m pytest backend/agent/tests/test_heartbeat_parallel_probe.py -q`
  → **2 passed**（证明并发探测与异常隔离，不证明 25 真机 p99）；
  `vitest run src/utils/api/devices.test.ts` → **7 passed**（证明跨 1200
  翻页与 short-read 出口，不证明多查看者成本）。两份文档的相对链接
  检查 0 缺失；空白检查通过；Canvas 以
  `frontend/node_modules/typescript` 的 `tsc --noEmit` 检查通过。
- Pending：隔离库执行计划与基数压测、真实 25 台/host ADB/USB 长跑、
  B1 150 host、B2 3750 真机、浏览器多查看者、控制面多实例演练、
  真实外部工具按 D2 验收、崩溃产物洪峰的完整率测量、
  `measure_center_storage` 的 E-2/E-4/E-5 目标基数重采、ADR-0051 自述
  「fleet 48/48 `strict`」的逐台 `package_active` 复核、生产只读容量复核；
  没有一项被本次静态审计宣称“通过”。

**结论**：最省总成本的路线不是先重写架构，而是先锁定无法同时成立的资源
预算、用真实 25 台设备的运行分布校准 host 层，再在“合成 host vs 真机 device”
互不代验的阶梯中扩到 150/3750；每种新专项只在稳定契约与可验证发布物上
增加一次适配。未来如变更容量目标，以新验收基线重算上述模型即可，不把旧
数字当 defect，也不把一次局部绿灯当 fleet 承诺。
