# 稳定性测试平台：150 host / 3750 device 第一性原理与长期复利复审

> 日期：2026-09-25（Asia/Shanghai）
>
> 代码基线：`origin/main@1fafd0528c667b0f6ddcda590b30effa6a0d60d9`，本轮已 fetch 核对。
>
> 目标：150 台 host 同时运行，每台理论挂测 25 台，合计 3750 台 device 同时运行。
>
> 方法：代码与测试交叉审查、当前 GitHub issue/CI 读取、隔离的局部反例实验。非生产容量认证。
>
> **同日独立复核订正见 [§9](#9-复核订正2026-09-25独立复核)**：A03 的生产生效与 A06 的保留期均已只读核实（结论改变处置），
> A01 归因补充、A02 定级调整、补入遗漏的 #3232。

## 1. 判断

**现有架构值得继续演进；首要投入应转向验收可信度、证据与恢复闭环，再扩展工具生态和规模。**
目前的证据不足以宣布达到 150/3750，也不足以断言必须重写或拆微服务。
控制面编排、Agent 执行、PostgreSQL 事实、Redis 瞬时传输、不可变发布包这些边界，与需求相符。
真正限制长期收益的，是「运行了」能否稳定转化为「结果可信、证据齐全、故障能恢复、下次接入更便宜」。

本轮识别了两个可以局部重现的具体问题：**容量探针的成功判据可空过；工具包解压可经符号链接越界**。
另外，当前完整 CI 确实有四项失败，历史局部压测的绿色记录不能替代当前回归状态。
产物完整性、满规模长跑、脱机恢复与长期统计则分别存在观测、验收或设计缺口；它们不应被混称为已发生的生产故障。

旧文档容量数字与本目标不一致是正常需求演进，**不作为缺陷**。25 台/host 是待物理验证的理论上限，
也不等于 25 台同时刷机或同时执行重负载 ADB 操作。本文未读取生产凭据、主机清单、生产库或生产接口，
没有运行全规模压测、触发 Jira、更新 Agent 或改变生产配置。生产状态引用均标为既有记录，未作本轮现场确认。

## 2. 从本质推导：什么值得长期投资

平台的产出应是**可追溯的有效测试时长、故障事实与可复现证据**。3750 台设备满负荷一天的名义上限是
90,000 device-hours；离线、排队、刷机、初始化、基础设施故障造成的空转，应与实际测试暴露时间区分。
否则更大的机队可能只增加日志和操作量，没有同比增加有效可靠性信息。

单人维护下，可用以下非财务评分公式判断一项建设是否值得做：

> 长期收益 ≈ 未来重复次数 × 每次节省的人力/错误损失 − 首次建设成本 − 持续维护成本。

优先积累四类资产：

1. **可信事实。** 意图、实际执行、租约代次、终态、工具版本和原始证据能对账；重试不改变已经发生的事实。
2. **可复用接入边界。** 专项差异留在工具适配器和声明中；编排、取消、产物、归档和问题提交链可复用。
3. **可重复交付与恢复。** 包摘要、空库自举、灰度、回滚和异机恢复能由脚本及记录复现。
4. **越来越便宜的维护。** 同类错误进入一个有效守卫；临时分支有退出条件；问题结论能在一个入口持续更新。

现有正向资产已实存：

- [`device_lease.py`](../../backend/models/device_lease.py) 的 ACTIVE 唯一索引与 fencing，
  [`lease_renewer.py`](../../backend/agent/lease_renewer.py) 的 host 批量续租，
  [`api_client.py`](../../backend/agent/api_client.py) 的先落本地 outbox 再上报终态。
- [`operation_scheduler.py`](../../backend/agent/operation_scheduler.py):1–16,28 将每 host 的运行设备数与操作并发分开，
  默认 5 个操作 permit；这是用有限宿主资源承载更多长跑设备的合理边界。
- [`script_packages.py`](../../backend/agent/script_packages.py):51–89 的强制包解析、manifest 的不可变身份，
  以及 ADR-0051 已落地的源码/发布物分离。
- Suite/逐用例结果、DLE、scan/upload/merge、Jira 厂商适配边界都已存在；不能把这些能力说成「从零未建」。

## 3. 本次更新了哪些旧结论

以下对照[9 月 23 日 Codex 审计](STP_FIRST_PRINCIPLES_COMPOUNDING_AUDIT_2026-09-23_codex.md)
及既有[综合处置台账 #3230](https://github.com/DUElost/stability-test-platform/issues/3230)，
不另造一套竞争的实施台账。

- **连接预算已修到代码层。** [ADR-0047](../adr/ADR-0047-db-pool-and-connection-capacity.md) 已 Accepted；
  默认双池由总上限 180 收到 80，显式 2 秒超时、结构化 503、启动预算检查、终态限流均已入主干。
  「仍为 Proposed、默认 180」已不是当前事实；运行闭环见 A03。
- **平台脚本的源码回退已删除。** `package_mode()` 对缺省及旧 off/on 值均返回 strict；
  [PR #3258](https://github.com/DUElost/stability-test-platform/pull/3258) 同时修复首扫 seed 回填。
  旧「漏配就静默跑可变树」不适用于此基线。
  [#3222](https://github.com/DUElost/stability-test-platform/issues/3222) 的 fleet 验证结果不落账问题仍在，
  但其缺省 off 的论据须更新；旧 Agent 载荷与新代码也不能混算。
  **【§10 订正：本句前半已失效——#3222 已由 PR #3263 + #3265 关闭。】**
- **部分终态削峰已完成。** Agent 终态首发单次、host 并发上限和 abort 抖动已存在，
  不能再笼统声称「全链路零 jitter」。这也不证明每条连接/claim 重试路径都已治理。
- **设备页已能翻页取全量。** 1200 是响应体护栏，不是机队总量限制；当前代价变成全量轮询与渲染成本（A08）。
- **过渡退出机制已落地。** [`transitions.json`](../governance/transitions.json) 有 6 条记录（4 active、2 done），
  `check_transitions.py` 已进 quick gate。下一步是按实际出口关闭过渡，不能继续建议「先建台账」。
- **七天自动续跑有新记录，但验收定义未最终收口。** [#107 的 9 月 25 日记录](https://github.com/DUElost/stability-test-platform/issues/107)
  称链自动续跑，并保留 force 热更中断及人工 run 归属问题；issue 仍 OPEN。
  这份记录不能直接升级成「零人工干预已验收」，也不证明 150/3750。

## 4. 审计发现与最小出口

优先级为本报告的处置顺序建议，不修改既有 issue 标签。P1 是扩规模或扩信任面前应处理的高后果问题；
P2 是应按测量或设计推进的工作。代码事实、局部反例、历史测量、待测项分别标明。

### A01｜P1：容量验收判据和当前 CI 不能支撑扩大承诺

**本轮实证。** [`test_plan_run_abort_backflow_scale_3243.py`](../../backend/tests/services/test_plan_run_abort_backflow_scale_3243.py)
:518–523 仅取状态 200 的探针延迟，没有成功样本时替换成 `[0.0]`；:582 只断言 p99 < 1s。
heartbeat 与 health 混为一组，未对每端点的成功数量、错误比例单独设断言。
本轮直接用 AST 提取这几行真实代码，喂入三种输入，均通过这一**局部断言**：

- 零探针样本 → p99 = 0ms；
- 全部 503 且每次 5 秒 → p99 = 0ms；
- heartbeat 500、health 200/10ms → p99 = 10ms。

这证明可响应性判据会漏报失败，不证明整个测试在这些输入下一定通过，也不否定历史成功样本。
**最小修复**：按端点统计 attempted/succeeded/failed，要求样本量和成功率，零样本失败；
延迟仅作为成功率之外的另一条断言。必须加入「全失败/单端点失效」的负向验证。

**本轮读取的 CI 事实。** 同基线的 [CI run 36058517011](https://github.com/DUElost/stability-test-platform/actions/runs/36058517011)
在 2026-09-25 05:36 CST 完成，后端 **4 failed / 3675 passed**，前端通过：

1. dispatcher→Agent 契约夹具无 package SHA，strict 下无法执行；
2. 迁移测试仍找已删除的 `gpu_setup/v1.0.2/` 目录；
3. 回流测试 `/complete(200)` p99=2.892s 超过 2s 断言；打印摘要还有 92 个未 ACK、Run 仍 RUNNING；
4. 终态限流测试期待累计计数为 0，实际为 602。

前两项直接指向发布模型升级后的测试适配；第四项需检查进程共享指标隔离；第三项需区分运行器负载、
重放预算和真实热点。不能把四项统归“只是 flaky”，也不能据 CI 耗时直接断言生产延迟。
[backstop #3247](https://github.com/DUElost/stability-test-platform/issues/3247) 仍 OPEN，记录重跑仍红。

**历史证据边界。** [#3243 Note](../notes/bug-fix/2026-09-24-abort-backflow-scale-3243.md)
曾记录 37 host / 490 RUNNING 回流下 1.14 倍请求、5.4s 收敛、探针 p99=51.8ms；本轮未重跑该 PG 场景。
其 ASGI 进程内驱动不启完整 lifespan，post_completion 使用队列替身，重放间隔从生产 15s 压缩为 0.5s。
它验证了一个局部场景，不能证明真实 Redis/worker/Jira 副作用完整执行，更不能外推为 3750 设备容量。

**归属与验收**：CI 修复归 #3247；新探针反例建议并入容量测试修正，再与 #105/#3244 共用。
同提交完整 CI 通过、负向判据有效、真实队列与网络场景通过，各自独立留证。

### A02｜P1：包摘要通过，不等于解压边界安全

**代码与本轮临时目录实证。** [`tool_cache.py`](../../backend/agent/tool_cache.py):85–103 放行绝对目标软链，
:137–143 逐成员检查后直接 `extractall`。先放一个指向解压根外的目录软链，再放其子文件，可以通过字面路径检查。
Python 3.13.5 下，本轮调用真实 `ensure_package()`：包摘要匹配、返回成功、文件出现在解压根外。
全部文件均位于本轮临时目录，未覆盖任何现存文件。

这复核了已有 [#3169](https://github.com/DUElost/stability-test-platform/issues/3169)，
并非新的匿名远程攻击结论：前提是问题包能够进入受信任发布链，并获得认可的摘要。
摘要校验解决内容身份，不解决恶意/误制包的路径或权限边界。

**最小出口**：明确支持的链接形态；解压时验证解析后的目标及后续成员路径，禁止借目录软链跨根写入；
解释器系统软链如确需保留，应使用窄白名单并禁止成员继续写入其目标。入口路径同样做 containment 校验。
在支持的 Python 版本上复测目录软链组合、绝对入口、`..`、设备/FIFO/硬链和正常 venv。
隔离运行、刷机提权继续沿 [ADR-0037](../adr/ADR-0037-agent-host-privilege-boundary.md)，不能把签名/摘要当成执行沙箱。

### A03｜P1：连接预算代码已治理，整批事务与父级热行仍需证明

**代码事实。** [`database.py`](../../backend/core/database.py):259–292 当前单进程双池允许上限为
`2 × (20 + 20) = 80`；[`check_db_pool_budget.py`](../../tools/dev/check_db_pool_budget.py)
按实例数、数据库可用槽、运维预留检查预算；unit 模板已挂硬检查。
这使扩容具备明确约束，但实际部署是否加载该 unit、实例数是否正确、告警是否加载，仍需运行证据。

两个随规模上升的串行窗口仍在：

- [`admission_pump.py`](../../backend/services/admission_pump.py):664–849 在单事务内锁 Run、host、目标 device，
  校验并物化整批 Job；3750 设备准入与心跳重叠的持锁时间需实测（[#3231](https://github.com/DUElost/stability-test-platform/issues/3231)）。
- [`job_terminalization.py`](../../backend/services/job_terminalization.py):146–183 每个终态锁同一父 Run 并更新计数。
  单次计算 O(1) 不等于并发无争用；同一 Run 的提交仍在该行串行化。

**最小出口**：先完成 #2959 的生效核验和真实分布回填 [#3261](https://github.com/DUElost/stability-test-platform/issues/3261)，
在准入/自然结束/批量 abort/重连四种波次分别测锁等待、池排队、ACK 收敛及 API 成功率。
[ADR-0052](../adr/ADR-0052-terminal-fact-parent-aggregation-decoupling.md) 当前 Proposed，
其持久 pending 与批量父级聚合方案是可选结构出口；应按既定真机门槛裁决，不能由本审计直接实施。
不建议先放宽池或增加 worker；实例数变化必须重新核算总预算。

### A04｜P1：不同证据通道的可靠性保证不一致，中心缺少完整率

**代码事实与现有测试。** [`artifact_uploader.py`](../../backend/agent/artifact_uploader.py):94,243–285
使用上限 256 的内存队列，满队列、未配置或停服会累计 `submits_dropped`。
其本地行为有测试，不能把测试通过解释为中心已能观测；本轮核对 heartbeat/指标消费面，未见该计数出口。
[#3217](https://github.com/DUElost/stability-test-platform/issues/3217) 仍 OPEN。
**【§10 订正：本句已失效——#3217 由 PR #3270 关闭；但 A04 的「中心完整率」部分仍开放。】**

DLE/EventUploader 与终态 outbox 有不同的持久化和重试机制，不能把它们的保证自动赋予 crash JobArtifact。
队列丢弃是既有可用性取舍；首先要修的是「丢了多少、影响哪些结论」不可见。

**最小出口**：按 run/host 对齐发现→提交→归档→登记→可下载，各阶段有明确终态和重试状态。
先接入丢弃、失败、积压和进程重启标识的指标与告警；以已知数量的产物加慢存储/断线验证对账。
关键证据是否要求持久 outbox、保留多久，应由允许的丢失目标决定。
若关键证据缺失，结果须能表达“不完整”，不能被统计层默认为无故障。

### A05｜P1：备份脚本存在，恢复闭环仍无本轮验收证据

**代码事实。** [`pg_backup.sh`](../../scripts/pg_backup.sh):22–27 默认在本机仓库下保存、保留 7 天；
[`pg_restore_test.sh`](../../scripts/pg_restore_test.sh):64–107 有恢复与计数检查，不能说“没有恢复工具”。
但表数和 host/plan 行数只能证明基础恢复可读，不证明包资产、证据目录、调度状态和新站点均能恢复工作。

[#3233](https://github.com/DUElost/stability-test-platform/issues/3233) 登记了带外告警、离机备份和演练缺口。
其中实际 Alertmanager receiver、磁盘用量和备份落点是旧现场记录，本轮未验证；不据此断言现网现在没有离机备份。

**最小出口**：约定 RPO/RTO；数据库、发布包、证据目录及必要站点配置分别有恢复来源；
在独立环境恢复到可读、可查证据、可启动一条无副作用任务的状态，测量实际耗时和允许丢失窗口。
平台不可达时仍能收到告警；只验证平台自身 webhook 不足以覆盖整机故障。
这比立即引入多活更适合当前单人维护成本。

### A06｜P1 设计：短期运行事实还没有被证明能沉淀为长期可靠性资产

**代码事实。** [`scheduler.py`](../../backend/core/settings/scheduler.py):110 的 PlanRun 默认保留 3 天；
[`cron_scheduler.py`](../../backend/scheduler/cron_scheduler.py):414–452 按特定终态、起始时间与引用关系选择候选；
[`case_result.py`](../../backend/models/case_result.py):23–24 的结果对 Run/Job 外键为 CASCADE。
不能简单说“一切证据三天必删”：链引用、其它保留策略与生产覆盖值都会影响实际结果。
但逐用例表确实不是天然独立于 Run 保留策略的长期层。

平台已有逐用例结果和 `build_version` 快照（`plan_dispatcher_sync.py`:773–811），所以“数据资产为零”过强。
本轮未找到能证明以下长期闭环的验收：原始 Run 删除后仍能按构建/专项/设备群重算有效测试时长、故障率，
并追溯故障签名、证据与 Jira。

**最小出口**：在 #3230 G4 / [#718](https://github.com/DUElost/stability-test-platform/issues/718) 方向上裁决分层保留：
短期详细日志、可复核故障摘要、长期暴露时长与构建归属。聚合前保存可再计算的必要事实，处理去重、补报与版本修订。
MTBF/故障率必须给出有效暴露时间、失败定义和观测不完整口径；零故障样本不能写成“无限可靠”。
验收样例应跨越至少一次真实保留清理，再重算并核对。

### A07｜P1/P2：多种工具已有局部支持，通用准入能力仍未闭合

**代码事实。** [`pipeline_engine.py`](../../backend/agent/pipeline_engine.py):1766–1775 的直接 runner 为 Python/Shell。
这不等于没有 APK 能力：`install_apk` 已通过 ADB 安装，`mtbf_setup` 已处理测试 APK 对与摘要并启动测试管理组件。
缺口是各载体的宿主能力、失败分类、取消和结果摄入是否形成统一、可验证的接入边界。

[`verify_tool_contract.py`](../../tools/dev/verify_tool_contract.py):33–35,160–189 默认验证 fixture；
CI 与 quick 调用未传实际 entrypoint，且环境开关可使其 SKIP/exit 0。
因此当前 Tool Contract 绿灯主要证明脚手架有效，不能证明新工具符合契约（[#3094](https://github.com/DUElost/stability-test-platform/issues/3094)）。

**最小出口**：先把新工具清单接到真实适配器契约测试；存量按 ADR-0033 的按族采用边界推进。
再选两个高差异试点：一个 JUnit/UIAutomator 插桩工具、一个客户刷机工具。JAR/PowerShell 按明确宿主支持矩阵接入，
不能仅凭 shell 可以调用就宣称完成跨平台支持。具体接入形态见第 6 节。

### A08｜P2：持续全量读取和证据体积会放大维护成本

**代码形状，未测吞吐。** [`DevicesPage.tsx`](../../frontend/src/pages/devices/DevicesPage.tsx):55–68
每 10 秒翻页取全量设备及主机；`devices.ts` 每页 1200，3750 台需要至少 4 页。
[`devices.py`](../../backend/api/routes/devices.py):434–442 的读请求还可能校正状态并提交。
因此一名持续查看者名义上每秒重复传输约 375 条设备记录，多查看者线性放大；
这不是 SQL 写入数，也不是浏览器性能实测。

**最小出口**：先用 3750 设备数据和 1/5/10 查看者测 API、浏览器渲染、载荷、查询与状态写入量。
超出 SLO 后再选择服务端过滤分页、列表虚拟化、事件增量刷新；不提高单响应护栏来掩盖问题。
浏览器布局与命中必须真实浏览器验证，jsdom 通过不能覆盖这一维。

## 5. 容量模型与可证伪的验收

### 5.1 先区分六个负载量

host 在线数、device 在线数、持租约执行数、瞬时操作并发数、单位时间状态变更数、产物字节率各不相同。
必须记录具体专项组合、作业周期、失败率、同时结束比例、查看者数、host 硬件/USB 拓扑和网络存储条件。
容量声明应是“在某负载包络内满足某 SLO”，不能只有 3750 一个数字。

根据本基线默认值，可以得到以下**条件模型，而非实测**：

- 150×25=3750；满负荷 24h=90,000 device-hours/day。
- host 心跳建议 `20 + 25 // 10 = 22s`：150/22≈6.82 请求/s，3750/22≈170.45 设备快照/s。
  实际周期还包括设备探测和网络耗时，慢轮次可能降低请求率同时恶化新鲜度。
- coordinator 默认 30s、批量续租默认 60s：分别约 5 和 2.5 请求/s；不是全部控制面流量。
- 每设备平均作业长 6h，且一直运行：15,000 Job/day，约 450,000 Job/30day；
  即使全天平均仅约 0.174 个终态/s，同步轮次仍可瞬间回流 3750 个终态。
- 若每 device-hour 最终需保存 1 MB，3750 台每天约 90 GB、30 天约 2.7 TB；
  10 MB 则约 900 GB/day、27 TB/30day。十进制单位，未计副本、索引、WAL、包、归档中间副本与空间余量。
  产物率须实测；高频大日志专项会远超示例。

队列稳定的必要条件是长期到达率低于处理率，且峰值有足够缓冲与恢复时间。
若断线 t 秒、每秒新增 λ 条待送事实，积压约 λt；恢复后处理率 μ 只略高于 λ，排空仍会很慢。
同理，对同一 Run 的父行持锁临界段平均耗时 s，单行串行吞吐约受 1/s 约束；不能靠加请求并发消除。

### 5.2 顺序与验收出口

1. **先起一套隔离的 dev 环境。** 独立 DB、Redis、存储、端口和合成身份；夹具不得注册到生产。
   包和数据库都用隔离样本，限制 CPU/内存，记录运行器资源。外部 Jira 使用替身项目/服务，禁止真实提交。
2. **校准测试自身。** 修 A01，断言错误率、样本量及探针每端点成功；消除已知回归，保留完整失败摘要。
   queue 替身、加速时间、关闭的调度器和 mock 外呼必须列进每份结果。
3. **B1 控制面阶梯。** 5 实例冒烟→44→60→100→150 host，每 host 最多 25 合成设备。
   测准入、claim、批量续租、聚合、连接/行锁、调度器、真实 worker、浏览器与长跑增长。
   现有 [#105](https://github.com/DUElost/stability-test-platform/issues/105) 已可排期，不能继续归为采购阻塞。
4. **B2 物理阶梯。** 单 host 1→5→10→25 真机，验证 USB/供电/ADB/刷机/日志/permit 公平性与心跳整轮 p99，
   再按实际可用 host 分档。关联 [#3219](https://github.com/DUElost/stability-test-platform/issues/3219)、
   [#106](https://github.com/DUElost/stability-test-platform/issues/106)；后者最新 owner 评论已改为先排 ≥44 host，
   不能照抄其标题“采购阻塞”。25 台拓扑不成立时，先调整物理布局或每 host 密度。
5. **跨域故障与恢复。** 同步 abort、150 host 重连、控制面重启、Redis 不可用、PG 过载、
   中心盘变慢/写满、坏包、Agent 重启、Jira 响应丢失；对拍意图、唯一租约、终态、产物和外部副作用。
   强杀刷机等有破坏性动作必须在专用设备与明确窗口下进行。
6. **长跑与保留清理。** 目标档位覆盖 ≥7 天（建议目标，最终以验收约定为准）、多个自然轮次、一次清理/备份周期，
   记录内存/磁盘趋势、人工介入和每类基础设施失败。较小真机档只能认证该档，B1 不能代验 B2。

每档至少交付：负载配置、代码/包摘要、资源限制、所有探针成功率与 p95/p99、准入与 ACK 收敛时间、
租约误回收数、计数对拍、outbox/worker 积压曲线、产物完整率和故障恢复记录。
**硬正确性目标**建议为重复 ACTIVE 租约=0、终态事实丢失=0、未解释计数差=0；
“测试用例本身失败”和“平台执行/采证失败”分别统计，不能通过忽略失败来制造绿色。
具体延迟、证据丢失预算与 RPO/RTO须明确签收；现有 490-job 的 120 秒收敛线不自动成为所有专项的统一标准。

## 6. 扩展工具生态的最小架构

沿 [ADR-0033](../adr/ADR-0033-tool-kit-ecosystem-integration.md) 的 Platform/Host/Device 分层，
保持 `lifecycle` 与 `script:<name>` 的既有入口。**文件后缀不宜成为调度状态机的新分支。**

- **Shell/Python**：复用现有 host runner，补齐环境检查、结构化参数、取消/超时和有界输出。
- **普通 APK**：host 适配器负责安装、启动、检查版本、采集结果；APK 身份单独追溯，成功安装不等于测试成功。
- **JUnit/UIAutomator 插桩 APK**：明确 app-under-test/test APK、包名、runner、用例清单与参数；
  适配器调用 instrumentation 并解析结果。区分断言失败、runner 崩溃、ADB 断开和取消；归档每用例结果。
- **JAR**：按实际运行处区分 host JVM 与 device 侧运行；声明 JRE/API/架构依赖，不假设所有 jar 都是 `java -jar`。
- **PowerShell**：明确 PowerShell Core 跨平台脚本或 Windows 专属依赖。后者必须有对应 Windows 执行宿主与验收，
  不能从当前 Linux Agent 的 Python/Shell runner 推定支持。
- **客户刷机工具**：封装厂商命令、驱动、固件与设备匹配；前置检查、供电/USB 锁、升级窗口、取消安全边界独立定义。
  执行权限与高后果操作沿现有治理，不允许任意工具获得通用 root 能力。
- **日志、导出、去重与 Jira 集成**：复用产物登记、归档与厂商适配层，输出保留来源与原始证据 URI；
  Jira 等外部写操作持久记录操作身份、请求摘要、返回 key 和未知结果，响应丢失后先对账再重试。
  后一点是接入验收要求，本轮没有证明现有 Jira 链发生重复提交。

统一契约至少包括：工具/包身份、输入 schema、执行宿主能力、资源互斥与超时、进度/活性、结果分类、
产物清单与校验、取消/重试策略、权限和副作用边界。已有 script capabilities 并不自动等同通用 host 能力协商。
先用真实工具证明这些字段被派发和验收消费，避免一次建成无人使用的通用插件框架。

试点成功标准：新工具主要新增适配器/声明/测试，核心状态机不因载体变化而修改；
能完成缺依赖、坏包、设备离线、取消、重复结果与回滚的验证，且记录接入和发布所需时间。

## 7. 长期复利路线：按依赖推进，而不是按 issue 数量推进

**第一阶段：恢复可相信的交付基线。** #3247 与 A01，A02/#3169，A04/#3217（计数可见性已闭，
见 §10；完整率表达仍开放），A05/#3233；
完成 #2959/#3261 的运行证据核对。开发修复、生产生效、真实验收分别结案。
这些问题分别保护变更判断、宿主边界、证据价值和项目存续。

**第二阶段：取得目标容量的因果证据。** #3231 + #3219 + #105/#106 的阶梯，覆盖终态峰值和共享存储。
#3244/ADR-0052 以门槛决定是否实施；调参或拆服务只能针对已测出的瓶颈。

**第三阶段：让数据与工具开始复利。** 在保留清理前形成长期事实设计，完成上述两个工具试点，接通真实 Tool Contract 门禁。
长周期数据定义和工具试点设计可提前推进，规模化铺开依赖第一、二阶段证据。

**持续维护：** 沿 #3230 一处收口，保留本报告为有日期/基线的证据快照；
依据 transitions 的退出条件删除兼容分支。新审计需提供新增反例、过期结论修正或验收证据，不能靠报告数量证明健康。

用以下四个实际指标评估复利，不建议给项目打缺乏标尺的“成熟度分数”：

- 每 1000 有效 device-hours 的人工救火次数与分钟数；
- 新专项首次接入工时、触及核心模块数、第二个同类工具的边际工时；
- 从修复合入到 fleet 生效、真实验收完成的时间；
- 关键产物可复核率、同族缺陷复发率与恢复演练成功率。

本轮没有提出直接扩 worker、全仓重构、引入 Kubernetes/微服务、无界队列、无限日志保留或后缀驱动的 runner 大扩建。
若后续测量证明单进程资源隔离、调度可用性或队列吞吐不满足 SLO，再按 ADR-0027/0052 拆出必要边界。

## 8. 本轮验证与可复现范围

独立 worktree：`.wt/stp-audit-20260925`；没有改运行代码或测试实现。
Python 为 3.13.5，使用主仓 `.venv/bin/python -m ...`，清空继承环境，无生产 env 链接。
所有局部测试套 systemd scope 内存上限 2 GiB、swap=0；反例实验上限 1 GiB。

已执行：

- `python -m pytest backend/agent/tests/test_tool_cache.py backend/agent/tests/test_script_packages.py backend/agent/tests/test_terminal_upload_shaving_3242.py backend/agent/tests/test_artifact_uploader.py -q`
  → **58 passed，4.89s**。
- `python -m pytest tests/test_check_db_pool_budget.py tests/test_pg_restore_drill.py -q`
  → **19 passed，0.56s**。备份测试是隔离测试替身，不是实际恢复演练。
- 本轮 AST 反例：执行原测试中 `probe_latencies`、`p99_probe` 的赋值与 p99 断言，三类失效样本均被放行。
- 本轮真实解包反例：`TemporaryDirectory` 内创建匹配 SHA 的 tar，包含绝对目标目录软链及 `linked-dir/proof.txt`，
  调用 `ensure_package` 后返回非空、受控解压根外出现文件，临时目录自动清理。
- 当前 GitHub CI 日志读取；远端测试结果引用见 A01，**不是本轮本地执行**。
- `python -m scripts.run_gates check:quick` → **16 gates 总流程通过**；
  `schema-at-head` 因隔离 worktree 无 DATABASE_URL 明确跳过，不能计为数据库对齐已验证。
  Tool Contract 此次同样只验 fixture，保证范围见 A07。门禁 cgroup 内存上限 3 GiB、swap=0。
- 三份涉及文档的 **49 个本地链接解析通过**；`git diff --check` 无空白问题。

未执行：本轮全量后端 PG 套件、3750 负载、真实浏览器规模测试、真机验收、生产生效核对及异机恢复。
本报告是对上述关键路径的聚焦审计，**不声称 R01–R15 全部区域或所有安全入口已覆盖**。

局部反例的复现要点：

```python
# 容量探针：与当前测试统计逻辑相同；本轮实际通过 AST 提取原语句执行。
driver = {"probe": [{"status": 503, "elapsed": 5.0}]}
probe_latencies = sorted(p["elapsed"] for p in driver["probe"] if p["status"] == 200) or [0.0]
p99_probe = probe_latencies[int(len(probe_latencies) * 0.99) - 1]
assert p99_probe < 1.0  # 全失败也通过：这就是待修判据，不是正确验收。
```

解包反例只在临时目录验证：先创建 `base/outside/`，tar 成员一 `linked-dir` 为指向该目录的软链，
成员二为普通文件 `linked-dir/proof.txt`；包放 `base/packages/audit-tool/v1.tar.gz`，
摘要取实际字节 SHA256，cache 指向 `base/cache/`。Python 3.13.5 上调用
`ensure_package('audit-tool', 'v1', sha, package_root, cache_root)` 后检查 `base/outside/proof.txt`。
其它 Python 版本未在本轮执行，不外推其默认解压策略。

复审触发：CI 恢复、包边界修复、容量阶梯或恢复演练取得结果、新专项改变负载包络、
实例数/宿主 OS/工具权限或保留策略变化时，仅重审受影响结论。

## 9. 复核订正（2026-09-25，独立复核）

> 独立性：不同会话、不同模型（Claude）。代码面对照 `origin/main@fca8f466`（本稿基线之后只多 #3262，与结论无交集）；
> 生产侧只读核对 unit 文件、已加载告警规则与 env 中单键，未读凭据、未连生产库。以上 §1–§8 保留原文作为快照，订正只在此追加。

| # | 本稿原判断 | 复核事实 | 对处置的影响 |
|---|---|---|---|
| C1 | A03：部署是否加载该 unit、告警是否加载「仍需运行证据」 | **已核实未生效**：生产 `stability-backend.service` 无 `check_db_pool_budget.py` 的 `ExecStartPre`、无 `StartLimit*`（仍是 `Restart=always` + `RestartSec=5`）；`/etc/prometheus/rules/alerts-stability-platform.yml` 仍为 09-23 10:35 副本（35 条，仓库 36 条，缺 `StabilityTerminalBulkheadRejected`）。代码层（池 20/20、2s、503、舱壁）已随 `1fafd05` 生效 | #2959 手册 Step 1–2 属「待执行」而非「待核对」；手册锚点已逐项复核可用 |
| C2 | A06：PlanRun 默认保留 3 天（提示生产覆盖值会影响） | 生产 `PLAN_RUN_RETENTION_DAYS=36500`（仓库零记载）→ 保留清理**实际停用**；owner 2026-09-25 确认为有意：保留全部历史 | 生产风险是**无界增长**（3750 device 约 15k job/天），不是「3 天即焚」；默认值 3 是新站点陷阱；「跨越一次真实保留清理后重算」在现配置下不会发生。已登记过渡台账 `plan-run-retention-disabled`（出口 #3230 G4）；库体量增长监测随 #3233 同批补 |
| C3 | A01：CI 第 4 红「需检查进程共享指标隔离」 | 602 恰等于同进程 3243 用例的 `shed_503=602`——断言绝对值被污染，舱壁无缺陷。3243 用例合入时 PR 阶段 `backend-test` 为 SKIPPED，**从未在 CI 绿过**；固定 3 轮重放让收敛依赖 runner 速度；CI 的 `pool_peak_async=0.0`（本地 17）源于 `Engine.dispose()` 换池后池观测失明（生产代码缺陷，运行期不触发） | 由 PR #3266 修复。另有实证：heartbeat 探针缺必填 `status`，**每次 422**，09-24 的「探针 p99 51.8ms」只含 `/health`——本稿「单端点失效被混算」的反例在真实用例里发生过 |
| C4 | A02 定 P1 | 能进受信发布链的包本身就会被执行，越界写不扩大攻击面，#3169 的 P2 更准确；价值在于防误制包写坏宿主，且 strict 缺省后 `ensure_package` 是全 fleet 每个脚本的必经路径 | 阶段 1 顺手做、不作门槛。#3169 建议的 `filter="data"` 会拒 venv 的绝对软链，需自定义 filter，并以「全部已发布包零误拒回放」为合入前置 |
| C5 | 未提及 | **#3232**（P1）刷机资源默认路径错位：U1 真机 48/48 坐实下一次刷机窗必败；已有 #3234 env 注入（台账 `flash-tool-dir-env-injection`，12-31 到期）与 preflight v1.0.5 | 列入阶段 1 运维项：确认 fleet 生效 → 重指 7 个在库刷机计划 → 真机 preflight + 受控刷机；根治并入 A07 的客户刷机工具试点（显式资源锚） |

复核后的处置顺序（owner 2026-09-25 确认）：

1. **代码 PR**：#3247 + A01（PR #3266）→ #3169 → #3217。**运维窗（owner 择时）**：#2959 Step 1–2 →
   Agent 分发与 #3251 合为一批 → #3232 → #3233。
2. **容量因果证据**：固定资源的容量环境、#3231、#105、#3219/#106、ADR-0052 门槛。延迟 SLO 只在该环境判定——
   3243 的 `/complete(200)` p99 已改为只记录、不断言（owner 裁决）。
3. **复利**：长期事实层 ADR（以「生产不删」为起点，恢复有界保留为出口）、#3094、两个工具试点。

## 10. 复核订正（2026-09-25 第二轮，基线 `a3ad5fca`）

本轮为只读复核（`gh` + 读码 + 已合并 PR 对账），未跑真机、未触生产。基线由本稿的
`1fafd052` 前进到 `a3ad5fca`（#3263/#3265/#3270/#3262 等已合入），因此文中三句判据须按
下表更新；**原句保留不删**，以免把快照改写成「一直如此」。

| 本稿原句 | 现态 | 证据 |
|---|---|---|
| §3「#3222 的 fleet 验证结果不落账问题仍在」 | **已闭** | PR #3263（`host.script_packages_mode` + sweep 推导 + 指标与 summary 视图）与 **#3265 的追加修复**：真实 ack **不回传** `package_sha256`，首采 48/48 全落 `unknown`，判据改用 expected 侧 `sha_keys`。⇒ 档位可见性成立，但**「48/48 首采全 unknown」本身就是 ack 契约与推导假设不同源的实证**，新站/旧载荷接入时须重跑同一判据 |
| A04「#3217 仍 OPEN」 | **计数可见性已闭** | PR #3270：`ArtifactUploader.heartbeat_counts()` 进心跳 extras → 控制面 `record_agent_artifact_upload()` **显式接指标**（代码注释直接引用「只落 `host.extra` 就是 #1257 型有账面无告警」）→ 两条按成因拆分的告警（`stage="submit"` 提交即丢 / `stage=~"promote|post"` 投递失败）+ 四数对拍用例 + 重启回落不误报 + payload 预算用例 |
| A04 整体 | **仍开放的部分** | #3270 让「丢了多少」可见，但 A04 要的另一半未做：按 run/host 贯通「发现→提交→归档→登记→可下载」的**中心完整率**，以及关键证据缺失时结果须能表达「不完整」而非被统计层默认为无故障。⇒ A04 不应被记为已结 |
| §7 第一阶段与处置顺序 1 的「→ #3217」 | 该步已完成 | 顺序剩余：#3169 与 A05/#3233 的运维批（#2959 Step 1–2、#3232、#3233），及 §9-C1 已核实未生效的两项（`check_db_pool_budget.py` 的 `ExecStartPre`、`/etc/prometheus/rules` 仍缺 `StabilityTerminalBulkheadRejected`） |

**与 9 月 23 日 Codex 容量审计的关系**（此前 §3 只对照了同日的 compounding 稿，漏链）：
[150 host / 3750 device 容量审计（`ae232a3` 基线）](PLATFORM_FIRST_PRINCIPLES_CAPACITY_2026-09-23_ae232a3_codex.md)
的 F01/F06.1/F08 三条即本表 #3263/#3265/#3270 与 ADR-0047 裁决的上游来源；
其 F02（#3219 整轮心跳时间预算）与 F04/F07 仍是**未验收集**，处置台账统一归
[#3230](https://github.com/DUElost/stability-test-platform/issues/3230)，不另造竞争台账。

**同主题文档现状（防重复投资）**：主干现有 5 份同题审查稿（codex 3、claude 1、codebuddy 1），
本稿是其中最新且带自我订正的主稿。**后续复核请续写本稿，不要再新增平行文档。**
