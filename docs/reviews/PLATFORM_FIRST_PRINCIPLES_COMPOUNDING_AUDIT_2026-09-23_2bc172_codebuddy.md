# 平台第一性原理与长期复利审计（跨链静态）

> 日期：2026-09-23（Asia/Shanghai）。产出方：CodeBuddy（会话 `2bc172`）。
> 性质：**跨链静态审计 + 规模验收设计**，不是 150 host / 3750 device 已验收证明，
> 也不替代 R01–R15 全区复审。未读取生产库、凭据或主机清单；未修改业务代码 / ADR / 配置。
>
> **取证窗口**：主体取证于 `main@512e61a8`。落稿复核时 `main` 已前进到 `8bc6bc1e`，
> 期间 **ADR-0051 Phase 3 落地**（#3208：210 个版本目录删除、每族一棵源码树、
> agent-code tarball 排除 `scripts/`、`check-script-version-immutability` 门禁退役；
> ADR-0051 v1.2 记录 fleet 48/48 已 `strict`）。经 `git diff 512e61a8..8bc6bc1e`
> 复核，F-01～F-07、F-10～F-13 的引用锚点未变动（唯一差异：`admission_pump.py` +4 行，
> 为 Phase 3 的补推跳过 `package_active`）；**F-08 按新基线改写为「已收口 + 残留」**，
> **F-09 为 Phase 3 当日暴露、待真机复核**。
>
> **同日并行稿**：`PLATFORM_FIRST_PRINCIPLES_CAPACITY_2026-09-23_ae232a3_codex.md`
> （codex 会话；落稿时尚未入仓，故不设链接）。按「会话×模型」为独立单位，
> §5 给一致/互补/分歧对照。

---

## 0. 执行摘要与边界

**核心判断**：平台已经把「事实单一来源」「昂贵资源可租」「外部副作用可查」三条
立成了机械契约，这是单人维护到今天的复利基础；但新目标（150 host × 25 device =
3750 device 同时挂测、6 类工具族、日志/去重/Jira 集成、客户刷机工具）会先把
**三个不可绕过的问题**推到面前，其余都是可排期的成本项：

1. **全量 run 的准入事务与全部 host 的心跳写入在同一行集合上互锁**（F-01）——
   这是"发起一个 3750 台 run"时第一个会断的机制，且与 2026-09-11
   "1 台设备阻塞 368"同源。
2. **DB 连接总预算算术不成立（180 > 97）且失败语义未裁决**（F-02）——
   ADR-0047 仍 Proposed，`#2959` 的 `TooManyConnectionsError` 一天 1211 次是
   已写进代码的现场记录（`backend/core/database.py:156`）。
3. **产物投递的丢弃在控制面不可见**（F-03）——闭环可信度问题：
   3750 台 × 崩溃洪峰下"材料丢了、丢了多少"无法回答。

**新工具/新专项的复利出口是本轮最值得投资的改造面**：当前"入口被堵、出口未建"
（F-04）——D0 门禁拦下 `external-tool` 新族，但 Tool Contract 验证器只能验 Python、
包路径未接 `script:<name>`；测试资产（APK/JAR/固件）仍走四条互不相认的通道（F-05）；
平台词表在两处并列硬编码（F-06）。这三条决定了"每加一个专项/项目/工具"是 O(1)
还是 O(N)。

**容量侧**：除 F-01/F-02/F-03 外，其余是**先测再调的固定税**（F-10～F-12），
不建议在测量前扩容或重写架构。ADR-0026 的规模目标是「60+ host、1000+ device」
（`docs/adr/ADR-0026-plan-execution-scaling.md:12`），新目标是它的 2.5×/3.75×，
按新基线重算验收即可，不把旧数字当缺陷。

**边界**：未做全量安全渗透、每种专项/客户工具的源码审计、数据库执行计划、
整站浏览器测试或生产容量压测。未运行任何测试（落稿前仅运行 `check:quick`，
结果见 §6）。所有"模型推导"均标注前提，不构成吞吐承诺。

---

## 1. 从不可删的需求建模

### 1.1 本质问题

平台本质上只做三件事，删掉任何一件它就不再是"测试管理平台"：

1. **把测试意图翻译成设备上的受限操作**：昂贵资源（设备、USB/ADB 通道、提权动作）
   必须可租、可 fencing、可回收（ADR-0003/0019/0037）。
2. **把事实做成单一、可追溯、可恢复的来源**：终态、租约、版本、产物落在 PostgreSQL；
   Redis 只承载队列与瞬时通信（AGENTS.md 硬不变量）。
3. **把外部副作用做成幂等、可查**：上传 / 合并 / Jira / 通知必须有失败类别、
   重试 owner、去重键与落库事实（ADR-0036 是这一条的成文契约）。

**成本量纲**由此派生：成本 ∝ **设备×时间**（心跳行写、逐设备日志、产物字节）
+ **昂贵操作的瞬时并发** + **单人注意力**。host 数是低阶因子——一次心跳里
host 只提交一次，device 是每台一次行更新（`backend/api/routes/heartbeat.py:535`）。

### 1.2 复利判据（本稿用）

1. **一次定义、处处复用？**（还是每加一项就复制/改 N 处）
2. **O(1) 还是 O(N)？**（新增一个专项 / 工具 / 项目 / SoC 的触点数量）
3. **随时间自我放大还是自我收敛？**（无界增长、日志/行写放大、文档税是典型）

### 1.3 规模算术（仅作压测输入，不是承诺）

- 150 × 25 = **3750** 最大在场 device。按服务端建议间隔
  `suggested_heartbeat_interval(25) = 20 + 25 // 10 = 22s`
  （`backend/services/agent_host_heartbeat.py:31-42`，钳制 [15,60]），
  稳态约 **6.8 host 心跳/s、170 device 快照/s**。实际周期 = tick 采集/HTTP 耗时
  + wait（agent 侧钳制 [10,120]s，`backend/agent/settings.py:209-210`）。
- 每拍每台设备至少一次行更新 + 一次 commit/主机 → **~170 UPDATE/s** 稳态；
  长跑叠加续租（~62 UPDATE/s）与协调者心跳（~125 UPDATE/s）。
- `GET /devices` 单响应上限 **1200**（`backend/api/routes/devices.py:32-38`），
  3750 台需 4 次请求；这是护栏不是上限。
- **配置允许的连接峰值 180 vs PG `max_connections=100`**（见 F-02）。

---

## 2. 复利判定：资产与负债

| 复利资产（勿被"顺手优化"掉） | 复利负债（随新增面/时间放大） |
|---|---|
| 单一事实源收口：`backend/storage_families.py`（族/分桶一处定义 + 测试不写死族名）、`core/dedup_platform.py` 的"控制面唯一来源"声明、`artifact_digest` 内容寻址 | 平台词表**双份**硬编码（控制面 + agent，"改动时需同时检查"，`core/dedup_platform.py:5-6`）→ 第 3 个 SoC 是 O(N) 触点 |
| 机械门禁：34 gate / 4 profile；tool-manifest append-only + 族树等效；D0 新族拦截；`admission_pump` 的锁序注释（防死锁设计意图写在代码里） | 新工具族"入口被堵、出口未建"：D0 红 + Tool Contract/包路径 Python-only |
| ADR-0051 Phase 3 收口：**不可变性移到包**（`tool_manifest.json` + `packages/<name>/<version>.tar.gz` 只增不改），族树可演进、改树不发版本即红；新增"待激活/重指"两道对账 | 测试资产（APK/JAR/固件）无统一分发与版本绑定 → 每个专项自造定位方式 |
| capabilities.json + 能力前置判据；心跳建议间隔与降采样；material-only 设备推送；1200 护栏 + 翻页（"抬护栏"被显式拒绝） | 无界增长面：`mtbf/` 无 purge、notification 无保留期、`ai_work` registry 无裁剪 |
| 单人+AI 可维持的测试网（779 文件 / ~7.9k 用例）与文档分层（ADR/design/prd/acceptance/notes） | 注意力税：153k 行文档；时效字段 19/1536 覆盖且抽查多已失真（F-13） |

**判读**：Phase 3 是本季度最大的一次"负债转资产"（复制冗余 85.6% 在源码面消解，
`tool_manifest.json` 成为 Git 侧唯一事实源）。**剩下的复利风险集中在"新增面"
（工具/资产/SoC/专项）而不是"存量面"**——这正是本轮审计与容量视角互补的原因。

---

## 3. 发现（按优先级）

### F-01 【容量·P0】全量 run 的准入事务与全部 host 的心跳写入互锁

**证据**：`backend/services/admission_pump.py:82-105`——准入事务对全部 Host、全部目标
Device 取 `FOR UPDATE`（锁序 Host→Device 稳定 id 序，注释明说"避免 Host↔Device
死锁"）；`:9` 记录 Phase B 是"ONE short tx"：锁 PlanRun + 全部 PlanRunHost →
终检 → WiFi 分配 → 物化全部 job。心跳按同一锁序在 `backend/api/routes/heartbeat.py:535`
每拍重写每台设备 `last_seen`、`:249-317` 重写 host 行。

**机制/第一性**：锁持有时间 ∝ 目标设备数（3750 行锁 + 3750 插入 + 分配），
而心跳频率也 ∝ 设备数。两个 O(N) 在同一行集合上相撞：**一次全量 run 发起时，
150 台 host 的心跳写全部排队**；agent 侧 POST 有 5s 超时，超时即丢拍；
任何冲突使整个准入回滚重排。已发生过的同类事故：1 台设备阻塞 368 条心跳。

**出口**（方向级，触碰 ADR-0026 不变量，需先测后裁）：分片准入（按 host 分批提交，
保留"QUEUED 不建 job + 幂等"）、校验/分配移出锁窗、或先物化后校验。
**判据**：准入期间心跳超时率与 P99（阈值先测后定）；无重复 job；单次全量准入
的锁窗时长与目标设备数的关系曲线。

### F-02 【容量·P0】DB 连接总预算算术不成立，且 ADR-0047 未裁决

**证据**：`backend/core/database.py:257-258`（默认 `pool_size=30`、`max_overflow=60`，
sync/async 各一份 → 峰值 180）；`deploy/postgres/docker-compose.yml:31`
（`max_connections=100`，一般角色可用 ≤97）；ADR-0047 状态 **Proposed**；
`backend/core/database.py:156` 记录了 `#2959` 现场：`TooManyConnectionsError`
一天 1211 次。

**机制/第一性**：池上限是"许可"不是"用量"。许可总和 > 数据库硬上限时，
过载没有可用的失败语义——所有调用排队到 `pool_timeout`（未设 = SQLAlchemy
默认 30s）一起超时；`to_thread` 上的心跳、WS 鉴权、日志写入会同批阻塞。

**出口**：owner 裁决 ADR-0047（总预算不变量 / 失败语义 / 是否 pgbouncer）。
过渡 = 只加观测（已有 `slots_exhausted`/`timeout` 分列告警）；终态 = 可机械校验的
`n_instances × n_engines × (pool+overflow) ≤ 可用连接 − 预留`。不要只调大
`max_connections` 把总量约束藏起来。

### F-03 【集成·P0】产物投递的"丢弃"在控制面不可见（闭环可信度）

**证据**：`backend/agent/artifact_uploader.py:277-285`（队列满 → `submits_dropped += 1`
+ 一条 WARNING；fire-and-forget 是 ADR-0018 5B2 的**有意**设计）；
`backend/agent/watcher/puller.py:65`（同名计数，仓库内**零读者**）；
`backend/agent/heartbeat_bindings.py:111-121`（心跳只带 terminal/log_signal outbox、
两类死信、scan 分片失败——**没有任何 drop 计数**）。

**机制/第一性**：3750 台 × 崩溃洪峰（开关机/Monkey/刷机后首轮）必然填满
puller(256) 与 uploader(256) 两道有界队列。设计允许丢，但"丢过"必须成为可告警事件；
否则"报告完整 + 工单材料完整"不可判定——**完整性破坏比延迟贵**。

**出口**（低成本高杠杆）：把 3 个计数（puller / artifact 的 `submits_dropped`、
EventUploader 的 queue-full）接入现有心跳观测面（或 Prometheus 序列）。
**判据**：注入已知数量产物，端到端能回答"收到多少 / 丢了几条"。
**不要**先抬 256——无界队列只是把丢弃换成内存增长与 OOM。

### F-04 【扩展·P1】新工具族的"入口被堵、出口未建"

**证据**：`tools/dev/check_new_script_family.py:18-24`（声明 `external-tool` 的新族 → **红**，
"外部工具无合法 in-tree 出口，须走 D0 分级准入或显式登记 legacy 例外"）；
`tools/dev/verify_tool_contract.py:47`（`cmd = [sys.executable, ...]`，**只能验 Python**，
且只跑仓库内 fixture）；`backend/agent/tool_cache.py:199-205`（`python: null` → agent
解释器；外部工具包只被 `scan_runner` 消费，**未接 `script:<name>`**）；
`backend/agent/pipeline_engine.py:1766-1776`（`runners = {python, shell}` 是唯一执行分支）。

**机制/第一性**：平台核心动作只有 `script:<name>`。要让 jar / ps1 / 插桩 APK /
刷机二进制成为可用件，要么扩 action 语义（大改且违背"不造第二套执行权威"），
要么承认"所有外部工具必须有一个平台书写的入口适配器"。当前是把后者当默认事实，
但没有把它写成契约与工具链。**这就是新增工具族时的实际堵点：门禁说"不行"，
却没给出合法路径长什么样。**

**出口**（二选一，方向级需 owner 裁决）：A) 显式化"适配器模式"——Tool Contract
允许声明 interpreter/argv，`verify_tool_contract` 与 agent 一致支持；
B) 保持 Python-only 适配器，把打包/分发/校验做成可复制样板。
无论哪条，先拿一个真实工具（建议一个 jar + 一个 ps1）走通 mini end-to-end：
构建 → digest → 下发 → 执行 → 产物 → Jira 幂等。**判据 = 该样板全绿**，
而不是脚手架 fixture 全绿。

### F-05 【扩展·P1】测试资产（APK/JAR/固件）无统一下发与版本绑定

**证据**：包/族树只覆盖族内脚本文件（`docs/development/script-versioning.md`
「族树、清单与包」）；大资产走**四条互不相认的通道**：`backend/agent/resources/`
（gitignored，`git show origin/main:.gitignore:93`，经 host-resources digest 通道）、
中心存储（`firmware/`、`apk-repo/`）、包内成员、env/参数路径。APK 名硬编码在脚本
（`backend/agent/scripts/gpu_setup/gpu_setup.py:34` 的绝对默认路径、
`_lib.py:59-67` 资产表；`mtbf_setup/mtbf_setup.py:58-59`）。

**机制/第一性**：测试资产是专项的**输入事实**，应与脚本版本一样可追溯——给定历史
run 必须能回答"这一窗用的哪个 APK/固件"。四通道各自为政 = 每加一个专项重新发明一次
定位与校验方式。

**出口**：先"绑定"再"搬家"——在 step/run 快照记录资产 digest（物理仍可走中心存储）；
或把资源包纳入 manifest（ADR-0033 D1 的原承诺）。**判据**：任取一个历史 run
能回答其资产 digest。

### F-06 【集成·P1】平台词表双份硬编码 → 第 3 个 SoC 是 O(N) 触点

**证据**：`backend/core/dedup_platform.py:11-18`（`DEDUP_PLATFORMS=("mtk","unisoc")`、
`_COLLECTION_IMPLEMENTED` 不含 QCOM），该文件 docstring `:5-6` 自述"Agent 侧对应判定在
`device_platform.py`，**两者不是同一份代码，改动时需同时检查**"；同类字面量另见
`backend/services/dedup_scan.py:246`、`backend/services/dedup_extract.py:36-38`、
`backend/agent/job_session.py:374-395`。ADR-0032 D9 的 b2（agent 能力上报）此前被否决。

**机制/第一性**：平台支持面本是一个可声明事实，现在被编码成两处并列词表 + 若干字面量；
每加一个平台触点跨 agent/控制面/外部工具/ADR 约十处。目标恰是"持续承载更多项目"。

**出口**：能力面收敛为单一注册表（SoC → {collect, scan, merge, archive}），
agent 上报或配置声明、控制面只读表；过渡期保留现有函数签名、内部改查表。
因 ADR-0032 D9 已裁决过 b2，此处属**条件已变后的复议**，需 ADR 修订。

### F-07 【扩展·P1】family 名硬编码进控制面（应按 capabilities 协商）

**证据**：`backend/services/plan_dispatcher_core.py:176-192`（`mtbf_` 前缀强制 suite 绑定）、
`:365`（WiFi consumer = `{connect_wifi, monkey_setup}` 字面量）；
`backend/agent/pipeline_engine.py:802-808`（按名字给 `flash_firmware` 8s 终止宽限）。
`capabilities.json` 机制已存在且进包，但当前只服务 progress 面。

**机制/第一性**：按名字分支的每一条都要求新专项改核心代码；按能力声明则新专项自描述、
核心不动。这是把扩展点从代码搬到数据的最短路径。

**出口**：三处改读 capabilities（如 `suite_required` / `wifi_consumer` /
`term_grace_seconds`），门禁校验声明完整性。

### F-08 【扩展·P1 → 已收口（2026-09-23 Phase 3）】版本复制模型

**取证时（`512e61a8`）**：35 族 210 个版本目录；最新版本合计 18.7k 行 vs 全量 130k 行 →
**85.6% 是历史复制**；独立核出 **8 对字节完全相同的版本目录**；`_lib.py` 93 份/36 种内容、
`_adb.py` 80 份/13 种内容。

**落稿复核（`8bc6bc1e`）**：Phase 3（#3208）已删除 210 个版本目录（497 文件 / 111,681 行），
每族一棵源码树；不可变性移到包（`tool_manifest.json` append-only + `packages/` 只增不改），
`check_script_packages.py` 新增"重建 sha == 最新未退役条目 / 残留 v 目录红 /
改树未发版本红"。**源码面复制冗余消解，本条收口。**

**残留项（新形态下仍成立的复利问题）**：

1. **跨族共享仍是复制**：35 族各自的 `_adb.py` / `_lib.py` 仍是独立副本，横切修复
   = O(族数)。量级已从 O(版本数) 降到 O(族数)，且共享库（旧 P2）是被显式否决的设计——
   **建议**：采一次"横切修复实际改了几族"的分布数据（最近 N 次跨族修复），
   长期 ≥ 阈值再复议，否则接受现状并写进 ADR 的显式代价。
2. **口径回扫缺口**：`backend/agent/script_packages.py` 模块 docstring 仍写
   "回退到 `nfs_path`（**Phase 3 前**主机树上的版本目录仍在）"、把 `off` 描述为默认
   逃生阀。Phase 3 后 tarball 排除 `scripts/`、fleet 已 `strict`，`off` 实际等价于
   "解析到不存在的树路径"。属"口径修订后未回扫机械载体"一类（参见 #3120 的同类教训）。
3. **发布流变为**：改族树 → `--register` → 合入 → `--publish` → scan；
   两道对账（`--pending-activation` / `--plan-step-drift`）仍**账本非门禁**（exit 0/2），
   上线后必须按作业级判据验证（族内 head 追平 ≠ 下一窗跑到新行为）。

### F-09 【扩展·P1｜Phase 3 当日暴露，待真机复核】族树扁平化把相对资源路径改错位

**证据**：族树化后 `backend/agent/scripts/flash_firmware/flash_firmware.py:867-870` 的
`_DEFAULT_REL_FLASH_TOOL = ("..","..","..","resources","flashtool",...)` 仍按旧布局
（`<name>/v<version>/<entry>.py` 多一层）写：

- 源码态解析到 `<repo>/backend/resources/flashtool`（实际资源在
  `<repo>/backend/agent/resources/`）；
- 包模式解析到 `<install>/resources/flashtool`（安装链落位
  `<install>/agent/resources/flashtool`，`backend/agent/install_agent.sh:250`）。

同类相对锚点另见 `backend/agent/scripts/flash_preflight/flash_preflight.py:95`、
`backend/agent/scripts/gpu_setup/_lib.py:245`（`parents[3]`）。**GPU 幸免**因为
`gpu_setup.py:34` 的 DB `default_params` 带绝对路径 `/opt/stability-test-agent/agent/resources/gpu`；
**flash 链没有这层兜底**：`flash_tool_dir` 参数在仓库内无任何模板/runbook/前端引用
（`git grep` 0 命中），`STP_FLASH_TOOL_DIR` 不在 `backend/services/agent_env_sync.py`
的 fleet 下发键表内 → **默认路径就是实际路径**。

**机制/第一性**：族树扁平化改变了 `__file__` 深度，所有"相对 `__file__` 推导资源根"
的隐含前提被同时打断；开发态与安装态的层级差不同，属"本地绿、生产红"类。

**出口**：单主机 `packages=strict` 下跑 `flash_preflight` + 一次 `flash_firmware` 复核；
若确认，把相对层级补回一级（或显式登记 env/参数），并把"资源路径解析仿真"
加进族树/包改造的验收清单。**判据**：真机 flash 成功且 `metrics.route` 与工具路径
落在登记位置。（`flash_firmware` 的 DB `default_params` 是否带绝对路径未核——
只读仓储无法判定。）

### F-10 【容量·P2】无界增长面

- `mtbf/` **无 purge**：`backend/storage_families.py:32-39` 的 `RUN_FAMILIES` 只含
  `devices|dedup|jira|_meta`，`backend/scheduler/cron_scheduler.py` 无 `mtbf` 引用；
  MTBF 结果 JSON 只增不减（DB 侧 `test_case_result` 随 run CASCADE）。
- `notification_logs` / `notification_delivery` **无保留期**（scheduler 无引用）。
- `ai_work` registry 1,162 条、每次操作全量解析、无裁剪。

**出口**：沿用 ADR-0049 分层保留先例给前两者定 TTL；registry 加归档。
**判据**：三类对象的字节/行数随时间的斜率有界。

### F-11 【容量·P2】前端 3750 的成本排序

1. **devices 页每 10s 拉全量**（4 次串行请求）+ 每个 material 变更 invalidate
   整个 `['devices']` 前缀（2s 节流 → 每 2s 一轮全量重取）；
2. 一次 host 重启 = 25 帧、整队重启 = 3750 帧，每帧触发全量重取（每个开着的标签页都付）；
3. `DeviceMultiSelect` **无虚拟化、无防抖**：展开即 ~3750 行 DOM，每次按键全量扫描；
4. `GET /plan-runs/{id}/devices` 是**唯一无 limit 的 fleet 级端点**
   （`backend/api/routes/plan_runs.py:315-334` 无 limit 参数），详情页 10s/3s 轮询 +
   默认网格每设备一个 DOM。

**出口**：后端过滤/字段投影（而不是抬 1200）、MultiSelect 虚拟化 + 打开时再拉、
devices 端点加护栏。**判据**：多查看者下初屏/筛选 p95 与浏览器内存。

### F-12 【容量·P2】固定 O(fleet) 税 + 一个一致性不变量

- dashboard summary ≤1Hz 读全表（host + device 全行进 Python）；
  `/metrics` 每次 scrape 4–5 个全 fleet 聚合；host health probe 全 fleet SSH
  （默认 600s 周期、并发 4、每台 2×10s ≈ 750s 最坏 > tick，需确认自续/重叠语义）。
- **不变量冲突**：设备 OFFLINE 判据 60s（`backend/api/routes/heartbeat.py:135`）
  < agent 允许的最大心跳周期 120s（`backend/agent/settings.py:209-210`）。
  只要有效周期被 tick 时长或误配置推到 60s 以上，就会出现"心跳正常但设备被判离线
  + 逐台通知"。**25 台真机验收应把「有效周期 < 60s 且留抖动余量」列为硬判据。**

### F-13 【治理·P2】时效字段与口径回扫

`docs/DOC-MAP.md` 的「最后更新」被定义为"读者最廉价的时效判据"，但全库仅
19/1536 份文档带该字段，抽查 5/9 与 git 最后修改不符（最长差 2 个月）。
DOC-MAP 自述"做不到就删掉该字段"——建议二选一：门禁化（比对 git log）或撤销。
（同类回扫缺口见 F-08 残留项 2。）

**有意取舍，非缺陷**（记录以免误读）：`ai-drift` 仅 advisory；`ai-work` 不在
`check:pr`；PR 路径不跑 backend/frontend 全量（靠 nightly backstop）。

---

## 4. 落地顺序（建议）

1. **不可绕过（先办）**：F-02 裁决 ADR-0047 → F-01 测准入-心跳互锁（先测后改）→
   F-03 drop 计数上报（低成本高杠杆）。
2. **规模化验收设计**：单 host 25 真机（含 F-12 的有效周期硬判据）→ 合成 host 阶梯 →
   真机分档；每档对拍"意图-事实-租约-终态-产物"，互不代验。
3. **新工具/专项的复利出口**：F-04 真实 jar/ps1 走通一遍 → F-05 资产 digest 绑定 →
   F-07 capabilities 化 → F-06 平台能力注册表（需 ADR 复议）；F-09 真机复核
   并入族树/包改造验收清单。
4. **长跑成本**：F-10 TTL、F-11 前端、F-12 固定税（先测再调）。
5. **治理**：F-13 时效字段门禁化或撤销；registry 归档；F-08 的横切修复分布采数。

---

## 5. 与同日并行稿的对照

**独立一致**（两稿分别取证、结论相同）：连接预算 180>97 且 ADR-0047 Proposed；
25 台真机的 tick 时间预算未证；产物丢弃不可观测；多实例不是现成扩容答案；
工具契约/包路径缺口；1200 是单响应护栏而非 fleet 上限；ADR-0026 目标是 60+/1000+。

**本稿补充**：F-01 准入事务与心跳的行锁互锁；F-06 平台词表双份硬编码；
F-04 "堵入口未开出口"；F-05 资产分发分裂；F-09 族树扁平化的相对路径错位；
F-10 `mtbf/` 与 notification 无 TTL；F-11 前端成本排序；F-13 时效字段量化。

**分歧**：无发现与并行稿对立的证据。两稿在"先测后调、不为扩容先重写架构"的取向上一致。

---

## 6. 取证与验证记录

- **取证方式**：只读代码/测试/ADR/配置 + `git show`/`git diff` 直读；
  关键结论均给出 `file:line`。
- **落稿前实际运行**：`python scripts/run_gates.py check:quick`（结果见 PR 描述与
  Agent Note；未运行全量 backend/agent/前端测试，未连生产库）。
- **基线与复核**：主体 `512e61a8`，落稿复核 `8bc6bc1e`；
  受影响条目（F-08/F-09）已按新基线改写，其余锚点经 diff 复核未变动。
- **未验证 / UNKNOWN**：fleet 当前 `STP_SCRIPT_PACKAGES` / `STP_FLASH_TOOL_DIR` 的实际值；
  生产是否单实例；`flash_firmware` DB `default_params` 是否带绝对路径；
  任何 3750 / 25 台的真实运行数据；多查看者浏览器成本；
  `host_health_probe` 是否与自身 tick 重叠；CodeQL 与分支保护配置（本地无）。
