# 平台整体只读审查报告（四维度并行 · 风险台账 R-01~R-09 复核）

- **状态**：Living
- **日期**：2026-09-11
- **审查基线**：HEAD `ca0665c4`（main 分支，工作区干净）
- **合入前复检基线**：`dfbeb2ef`（main @ 2026-09-12；本分支合入该 326 提交后**逐条重验**，见 §十）
- **范围**：backend 控制面 / Agent（`backend/agent/`）/ frontend / 测试·CI / 需求·文档（含风险台账 R-01~R-09 复核）
- **性质**：全只读静态审查。四维度并行审查（架构 / 代码质量 / 测试与 CI / 需求与文档）后，主线程对每维度最高优先级断言**逐条回源码复核**（见 §九）；未运行测试、未连库写操作、未修改任何被审文件
- **对应产出**：本文档登记于 DOC-MAP；issue 批次见 `audit-2026-09-11` label 与 📌 总表 [#1515](https://github.com/DUElost/stability-test-platform/issues/1515)。代码与文档**均不代改**，修订项列于 §七/§八 供作者采纳
- **前序基线**：`reviews/PLATFORM_HEALTH_REVIEW_2026-09-03.md`（本轮为其后 815 个提交的复检，构成连续快照）

**代码规模**：后端 947 个 .py / 246,313 行；前端 321 个 ts/tsx / 57,077 行；文档 648 篇 md / 79,250 行；37 篇 ADR。

---

## 一、TL;DR

**这是一个工程质量显著高于行业均值、文档纪律罕见的成熟平台**——主链契约（状态机、终态唯一入口、CAS、幂等、锁顺序）真实落地且有注释固化，测试反模式近乎为零，核心文档索引零死链。

**但它存在两类系统性问题**：

1. **基础容量参数停留在默认值**：同步数据库连接池未配（全平台共享 15 连接，影响 32 个模块）、NFS 原始日志零 TTL、心跳超时默认值三处不一致。这些不是"写错"，而是"没配"——却位于失效链的咽喉。
2. **声称的能力与实际可达状态存在落差**：ADR-0027 宣称的横向扩展三原语虽已落地，但默认全关，且 `RunConsole` 进程内单例是**未被任何 ADR 登记的硬阻塞点**；分层架构被 `services → api.routes` 私有函数的反向依赖穿透。

**总分：6.8 / 10**（主链契约 A-，基础层与扩展性 C+）

---

## 二、交付概览

| 维度 | 评分 | 核心结论 |
|------|:----:|---------|
| 架构 | **6/10** | 主链契约落地优秀；扩展性与分层是短板；发现台账未收录的 C-02 |
| 代码质量 | **7.5/10** | 并发/幂等设计学术级严谨；资源安全（5/10）是唯一拖累 |
| 测试与 CI | **8/10** | 测试质量 ~9，门禁覆盖 ~6；缺陷可先合入 main 再被夜间拦截 |
| 需求与文档 | — | 文档治理健康；风险台账 9 条**无一条完整修复**；新增 H-09（死代码 + 文档双向漂移） |

**问题统计**：Critical 2 · High 9 · Medium 约 12 · Low 若干

> **提交前复核声明（2026-09-12）**：本报告在对外登记 issue 前，对全部 `文件:行号` 断言做了逐条重验，**修正 H-01（三处→四处）与 H-07b（header 形态统计）**，并新增确认 `docker-build` 亦为 PR 排除项。明细见 §九。
>
> **合入前复检声明（2026-09-12，追加）**：本分支初基于 `ca0665c4`，落后 main 达 **326 提交**。合入 `dfbeb2ef` 后对 13 条发现**逐条回 `main` 重验**，结论：**11 条仍成立、1 条已排期变形（H-01）、2 条已被上游修复（H-04 部分 / H-09 全部）**。凡状态变化均已在对应条目标注，issue 侧同步。明细见 §十。

---

## 三、Critical 级问题（2 项）

### C-01｜同步数据库连接池未配置，全平台共享 15 连接

> Issue: [#1516](https://github.com/DUElost/stability-test-platform/issues/1516)

**三位成员独立确认（架构师 H-01 / 工程师 C-1 / PM R-02），我已复核源码确证。**

- **证据**：`backend/core/database.py:57-61` — `get_sync_engine_kwargs()` 仅设 `future`、`pool_pre_ping`，**未设 `pool_size` / `max_overflow`** → 回退 SQLAlchemy 默认 `5 + 10 = 15`。
- **对照**：`database.py:46-54` 异步池已明确设 `pool_size=30, max_overflow=60, pool_recycle=1800`。**同步池被遗漏**。
- **影响面（远大于风险台账描述）**：
  - 台账原文仅列 4 个调度线程；
  - 实际共享该池的消费方：**12 个 APScheduler 周期任务**（`app_scheduler.py:213-352`）+ **SAQ worker 默认并发 10**（`saq_worker.py:52`）+ **84 处 `SessionLocal()` 调用点（31 个文件）**；
  - 雪上加霜：`core/leader_election.py:75` **每次 tick 都从该池借连接**取 advisory lock，与业务 tick 争抢同一池。
- **失效链**：DB 抖动/NFS 慢 → `QueuePool limit of size 5 overflow 10 reached` → 调度 tick 失败 → 因 `leader_election.py:101-113` fail-closed，**连锁跳过所有 singleton 调度** → Recycler/Reconciler 停摆 → Job 卡在 RUNNING/UNKNOWN 不收敛。
- **修复**：`get_sync_engine_kwargs` 补 `pool_size`/`max_overflow`/`pool_recycle`，env 驱动。**建议 ≥ 20+40**。
- **工作量**：**小**（约 5 行）｜**性价比全报告最高**

### C-02｜RunConsole 进程内单例：未被登记的水平扩展硬阻塞点

> Issue: [#1517](https://github.com/DUElost/stability-test-platform/issues/1517)

**架构师新发现，风险台账未收录。**

- **证据**：`backend/services/run_console.py:157-167` — `_instance` 单例 + `self._runs: Dict` + `self._inflight_keys: set`，**全部进程内内存**；跨 api/services/realtime **20+ 调用点**（`ai_assistant.py:603,630`、`dedup.py:303,364,409`、`hosts.py:781,819`、`orchestrator.py:599,650,652` 等）。
- **影响**：Console run 的状态与 **OS 进程句柄**只存在于创建它的进程；但可经 HTTP 从任意实例访问。多副本部署下非宿主实例 `status()` 返回 None、`cancel()` 返回 False → **表现为"控制台莫名丢失 / 取消失效"**，且无跨实例路由或 fallback。
- **为何危险**：ADR-0027 的 room/adapter 只解决 SocketIO 信令与 Agent RPC，**完全不覆盖进程内 subprocess 注册表**；ADR-0025/0027 均未登记此项。
- **缓解（待确认）**：若生产为单副本 + LB sticky，则当前不构成在线故障，属"误扩容/未来扩容"的潜伏雷。
- **修复**：短期在部署检查清单显式登记"RunConsole 必须 sticky 或单副本"；中期外置注册表（Redis 状态 + 共享日志存储），与 ADR-0027 P3 同轨补 ADR。
- **工作量**：登记 **小**；外置 **大**

---

## 三·补｜H-09｜`action_templates` 死代码：ADR-0020 Phase 6 删除未闭环

> **合入前复检（2026-09-12）：此条已由上游修复。** 合入 `dfbeb2ef` 后全仓 `git grep action_templates` **零命中**（`56234cb3 refactor: schema-sync 基线收敛` 已清除该表及注册）。本条仅作审查基线时点的历史记录保留；issue [#1526](https://github.com/DUElost/stability-test-platform/issues/1526) 已可按「已完成」关闭。

> Issue: [#1526](https://github.com/DUElost/stability-test-platform/issues/1526)

**PM 独立发现，经主理人全仓复核确证。**

- **证据（后端齐全）**：`api/routes/action_templates.py`（完整 CRUD）、`models/action_template.py`、`models/__init__.py:2,33`、`main.py:52,365`（**真实注册到 FastAPI**）、`tests/api/test_action_templates.py`（有测试）。
- **证据（前端无消费）**：仅 `utils/api/tools.ts:8-20`（API 封装）、`utils/api/types.ts:865-890`（类型）、`utils/api/index.ts:15,42,68,97`（导出聚合）、`api.test.ts:319-320`（**断言仅 `toBeDefined()`**）。全仓 **0 个 `.tsx` 页面调用**、**0 个 service / scheduler / agent 引用**。
- **性质**：这是 **ADR-0020 阶段 6 删除工作未闭环**的残留 —— 后端完整存活、有测试、有路由注册，却无任何真实调用方；测试因只断言"已定义"而**无法暴露其死代码状态**。
- **文档漂移（加重项）**：
  - `docs/design/02-backend.md:71` **仍将其列为活跃端点**（"Action 模板"）；
  - `docs/adr/ADR-0007:103` **仍标注"仍活跃"**。
  - → 文档在**主动维护一个已死的 API 面**，读者据文档接入即踩空。
- **工作量**：**小**（删 4 处后端文件 + 2 处注册 + 前端 3 处封装；同步修正 2 处文档）
- **为何值得单列**：它是本报告中**唯一"代码—测试—文档三者同时维持一个假象"**的问题 —— 测试给了它安全感，文档给了它权威性，而它没有任何真实用途。

---

## 四、High 级问题（8 项）

| # | 问题 | 证据 | 影响 | 工作量 |
|:--:|------|------|------|:------:|
|---|------|------|------|:------:|
| **H-01**<br>[#1518](https://github.com/DUElost/stability-test-platform/issues/1518) | 心跳超时默认值**多处重复定义**（首轮记三处、复核为四处、合入前再验为**三处**） | 报告基线 `ca0665c4` 命中四处：`session_watchdog.py:34` `_HOST_HEARTBEAT_TIMEOUT`=**120** vs `devices.py:29`=**300** vs `hosts.py:53`=**300** vs `reachability.py:40`=**300`。**合入前复检（`dfbeb2ef`）：`session_watchdog.py` 已整体移除，120 那处随之消失；余三处默认值均为 300，但其为三份独立字面量仍属重复定义**（`settings.py` 从 `hosts.py` 转引） | watchdog 120s 判死 job，诊断接口 300s 内仍显示"心跳新鲜"——**排障结论与实际行为自相矛盾**，且 reachability 注释已成误导性文档 | 小 |
| **H-02**<br>[#1519](https://github.com/DUElost/stability-test-platform/issues/1519) | 分层反向依赖 | `services/ai_assistant/plan_run_ops.py:109,214,290`、`dispatch.py:89` 直接 import `api.routes.plans._require_active_wifi_pool`、`plan_runs._load_job_in_run` 等**私有函数** | services 依赖 HTTP 层内部实现；最坏造成导入环、单测无法脱离 FastAPI 装配、路由重构即破坏服务层 | 中 |
| **H-03**<br>[#1520](https://github.com/DUElost/stability-test-platform/issues/1520) | 业务逻辑沉淀在 God-module 路由 | `api/routes/` 17,746 行含 **534 处直接 DB 操作**；`plan_runs.py` **3,328 行**/68 次 DB/仅引 7 个 service；`agent_api.py` 3,239 行/91 次；`projects.py` 997 行/**0 个 service** | API 层同时承担 HTTP+事务+业务规则+聚合；H-02 的根因；git churn 热点 | 大 |
| **H-04**<br>[#1521](https://github.com/DUElost/stability-test-platform/issues/1521) | NFS 原始日志零 TTL（R-01）⚠️**合入前复检：部分缓解** | 报告基线 `ca0665c4`：`cron_scheduler.py:215-240 run_retention_cleanup` **只删 DB 行**；全仓无 `devices/`、`dedup/` 文件清理。**合入前复检（`dfbeb2ef`）：`cron_scheduler.py:344-352` 已新增 `purge_job_log_files(stale_job_id_list)`（控制台日志落盘文件清理）——DB 之外的清理**首次**出现；但 `devices/`（原始设备日志）、`dedup/`（扫描产物）**仍无 TTL**，主体责任面未变** | 台账 R-01 成立。机制精确定位：**DB 行有 TTL、NFS 原始日志无 TTL，两者由不同进程/生命周期管理**。916GB 盘打满路径确定 | 中 |
| **H-05**<br>[#1522](https://github.com/DUElost/stability-test-platform/issues/1522) | HddSpill 腾退速率不足 | `agent/local_disk_monitor.py:30` `_MAX_SPILL_PER_CYCLE=20`；`:35` `_interval=300.0`；`:154` 每轮开头清空 `_spill_enqueued_ids` | 事件产生速率 > 每 300s 腾退 20 目录时，磁盘水位无法回落；与 R-01 是**同一失效链两端**（R-01 不清理 / 本项清理不足） | 中 |
| **H-06**<br>[#1527](https://github.com/DUElost/stability-test-platform/issues/1527) | `merge_task` 全失败不产生终态 | `saq_tasks.py:761-766` — merge 全失败返回 `""` 时**直接 return**，不 enqueue extract，且**不写任何终态字段**，仅 `logger.info` | SAQ 视 job 为成功，PlanRun 停在 RUNNING，无 extract 产物。**违反"失败必须收敛到 failed"契约** | 小 |
| **H-07**<br>[#1523](https://github.com/DUElost/stability-test-platform/issues/1523) | ADR 元数据缺陷 | ① `ADR-0031` **ID 冲突**（`-platform-ai-assistant` 与 `-appendix-phase3-core-write-tools` 共用同一编号，建议改为 0031a/0031b 子编号）；② `adr/README.md` 漏登记附录、自称"37"与磁盘 38 不符 | 引用必然歧义；SSOT 可信度受损 | 小 |
| **H-07b**<br>[#1524](https://github.com/DUElost/stability-test-platform/issues/1524) | **ADR 状态行格式不一致**（PM 发现，主线程复核后**修正其统计**） | 38 篇 ADR 中：**36 篇**为 `- 状态：…`（其中 7 篇在值上再套粗体，如 `- 状态：**Accepted**`）；**1 篇**为 `- **状态**：…`（`ADR-0035`，粗体打在**键**上而非值上）；**1 篇（`ADR-0022`）无状态行**，正文以 `> **实施完成 (2026-06-12)**` 完成通告开头 | 原报告称"3 种 header 形态 + ADR-0022 为表格变体"——**经复核不成立**（ADR-0022 是「缺状态行」而非表格变体，粗体形态也非孤立）。真实缺陷更轻但性质不同：**1 篇缺状态行 + 1 篇键位粗体**，脚本化校验需同时容忍 `状态` 与 `**状态**` 两种键形，并对无状态行文档给 fallback | 小 |
| **H-08**<br>[#1525](https://github.com/DUElost/stability-test-platform/issues/1525) | PR 门禁不覆盖控制面测试（**已知取舍**） | `ci.yml:25,82` `if: github.event_name != 'pull_request'` → `backend-test`(1900+ 用例)、`frontend-check`(vitest+build) **PR 时不跑** | 缺陷可先合入 main，最迟次日 UTC 18:00 夜间 backstop 才暴露。**但 `ci.yml:229-230` 明确记载这是有意取舍** | 大 |

> **关于 H-08 的重要澄清**：`ci.yml:229-230` 注释原文写道——
> *「风险：PR 合入前不跑 backend/tests、vitest / docker build，由合入 main 后的全量兜底；若要"合并前全量"应改用 Merge Queue。」*
> 因此这**不是疏漏，而是团队已知并书面接受的有意取舍**（注意力预算 vs 拦截时机）。建议的讨论应聚焦于"该取舍的敞口是否可量化、是否需要引入 Merge Queue"，而非"修复漏洞"。

---

## 五、风险台账复核（R-01 ~ R-09）

台账源：`docs/notes/architecture/2026-09-03-platform-risk-register.md`（2026-09-03 快照）。
**复核窗口：2026-09-03 → 2026-09-11 期间合入 815 个提交。**

| 编号 | 风险名称 | 当前状态 | 代码证据 |
|:----:|---------|:--------:|---------|
| R-01 | 原始日志无 TTL | **仍存在**（Critical） | `cron_scheduler.py:215` 仅删 DB 行；无 NFS 文件清理任务 |
| R-02 | 同步池仅 15 连接 | **仍存在且被低估** | `database.py:57-61` 未设参；影响面 4 → **32 模块** |
| R-03 | 展锐无自动化刷机 | **仍存在** | `flash_firmware/v1.3.11:254` `_MTK_VENDOR_ID="0e8d"`，16 版本全无 UNISOC/PAC 路径 |
| R-04 | 无电池高温熔断 | **部分修复** | `stats.py:334` 有 >45℃ 统计展示，但**仍无自动停测/熔断/告警** |
| R-05 | 无机架拓扑/USB 切电 | **仍存在** | 全仓 `rack_id`/`slot_id`/`uhubctl` **仅命中台账自身** |
| R-06 | SAQ 进程耦合/多实例竞争 | **仍存在** | `saq_worker.py:137` 默认 `"1"`；`socketio_redis.py:35` 默认 `"0"`。**新增派生风险**：`saq_tasks.py:84 _SYNC_OVERLAP_GUARDS` 是进程内 set，多副本下 #1123 保护完全失效 |
| R-07 | 参数分层未闭环/MTBF 无短路 | **仍存在** | `project.py:8` variables 标 D4 挂起；`mtbf_suite.py` 无 fail-fast |
| R-08 | 高频日志表无分区 | **仍存在** | `audit.py:14`、`job.py:96` 均无分区声明；无 pg_partman |
| R-09 | AI 工具面偏离测试归因 | **仍存在** | `ai_assistant/tools.py` 注册恰好 26 个运维工具；`jira/crash/堆叠/查重` 命中 **0** |

**复核结论**：
- **完整修复 0 条**｜**部分缓解 2 条**（R-01 的 DB 侧 / R-04 的可见性）｜**仍存在 7 条**
- **台账事实纪律可信**——R-01/R-02/R-03/R-04/R-06 经独立代码复核**全部成立，无误报**
- **增量价值**：R-02 影响面被低估（4→32 模块）；R-01 机制归因精确化（DB/NFS 双轨治理缺口）；台账**未收录** C-02（RunConsole）与 H-02（分层穿透）
- **台账本身的问题**：停留在 09-03 快照，**无状态回写机制**；量化数据已过期——架构师与 PM **两路独立实测吻合**：脚本目录已从台账基线 199 文件/67,288 行增长至 **217 文件/73,582 行（+9.4%）**。建议台账增加**「数据采集日期」字段**并约定每里程碑回写状态。

---

## 六、值得肯定的工程质量（避免只报问题）

| 优点 | 证据 |
|------|------|
| **零 TODO/FIXME/HACK** | 全量 AST+正则扫描 246k 行非测试代码，**0 命中** |
| **主链契约真实落地** | 状态机、终态唯一入口（仅 `/complete`）、UNKNOWN 围栏、abort 保租约、CAS、幂等键——代码中逐条一致，非纸面设计 |
| **锁顺序显式固化** | `b5064977 fix(agent): lock Job before Lease` 等，注释说明为何如此排序 |
| **测试反模式近乎为零** | 0 个 `assert True`、0 个 pass-only、0 个 try/except 吞断言、硬 skip 仅 5 处且全部合理 |
| **测试规模可观** | 约 3,500 个用例 / 350+ 文件；核心链路有真实状态断言的集成测试 |
| **文档纪律罕见** | 核心索引（README/DOC-MAP/adr）**零死链**；`docs/notes/` 命名规范 **0 违规**；Post-09-05 新 note 头部 **0 缺失** |
| **需求覆盖完整** | 27 个前端页面全部挂载；28 个后端路由模块全部注册；无孤儿页面 |
| **安全门禁有效** | ip-leak / immutability / schema-sync / ADR 索引漂移 / 空行污染——逐个实跑 self-test 均通过，且**真阻塞** |

---

## 七、改进建议（按优先级）

### 第一批：小工作量止血（建议立即，各 ≤0.5 人日，独立可并行）

| # | 动作 | 对应问题 |
|---|------|---------|
| 1 | `database.py:57-61` 补同步池参数（≥20+40，env 驱动） | C-01 |
| 2 | 心跳超时抽到 `job_timeout_config.py` 单一常量，**四处**统一引用（`session_watchdog` / `devices` / `hosts` / `reachability`） | H-01 |
| 3 | `saq_tasks.py:761` merge 全失败改为 raise 或写显式失败标记 | H-06 |
| 4 | 解决 ADR-0031 ID 冲突；补登附录、修正 README 计数 | H-07 |
| 5 | 修复 5 个 notes 文件相对链接深度（`../` → `../../`） | 文档漂移 |
| 6 | 删除 `action_templates` 死代码（后端 4 文件 + 2 处注册；前端 3 处封装）+ 修正 2 处文档 | H-09 |

### 第二批：本期（各 1-2 人日）

| # | 动作 | 对应问题 |
|---|------|---------|
| 6 | HddSpill 改为动态腾退（跌破 target 或无可腾退才停） | H-05 |
| 7 | leader election unlock 失败时 `db.invalidate()` 而非归还池 | 并发缺陷 |
| 8 | 租约对账去掉 `break` 全循环（改 `continue` 或加 `ORDER BY`） | 队首阻塞 |
| 9 | 部署检查清单登记 RunConsole 阻塞点（未开通前必须单副本/sticky） | C-02 |
| 10 | 为 `check-script-version-immutability.py` 补 `--self-test` | 门禁一致性 |

### 第三批：下期 / 方向级（3-5 人日以上）

| # | 动作 | 对应问题 |
|---|------|---------|
| 11 | 新增 control-plane 侧 NFS 日志 TTL job（终态 + mtime + 容量水位） | H-04 / R-01 |
| 12 | `api.routes` 私有逻辑下沉 `services/`，加"路由不得被 services 反向 import"门禁 | H-02 |
| 13 | 将 `backend/tests` 纳入 PR required check（或引入 Merge Queue） | H-08 |
| 14 | RunConsole 注册表外置（Redis + 共享存储） | C-02 |
| 15 | 脚本版本膨胀 P1（零引用退役）+ 刷新 feasibility note 过期基线 | H-06（架构） |
| 16 | 渐进拆分 `plan_runs.py` / `agent_api.py` 业务逻辑 | H-03 |

### 建议的最小行动组合

**#1 + #2 + #3 + #4**：全部小工作量、独立可并行、无回归风险，可在一次迭代内完成，直接消除 1 个 Critical + 2 个 High。

---

## 八、待确认事项（需环境/运行/业务方能判定）

1. **生产部署拓扑**：是否单副本？若为单副本 + sticky，则 C-02 当前不构成在线故障，紧迫度降为"潜伏雷"。
2. **R-02 历史故障证据**：台账称"极易耗尽"，但未找到已发生的生产报错记录。建议用 `pg_stat_activity` 采样 + 日志中 `QueuePool limit` 频次校准严重度。
3. **`PLAN_RUN_RETENTION_DAYS=3` 是否为生产实际值**：若是，报告所需历史 run 可能已被清理——这既是 H-04 方案设计的约束，也可能本身是未登记的数据资产风险。
4. **H-08 取舍的敞口**：`ci.yml:229-230` 已书面接受该风险。需决策的是"是否引入 Merge Queue"，而非"是否修复漏洞"。
5. **（已撤销）`resource_pools`**：**前期疑点已排除**。前端 `WifiPage.tsx:54,59,75,90` 四处消费 `api.resourcePools.*`，`PlanExecutePage.tsx:239` 另有 `api.resourcePools.available('wifi')`，前后端全仓命中 32 处 —— **非孤儿 API**，是 WiFi 资源池的活跃入口。
   > `action_templates` 经复核后**确认成立**，已从本清单移出，升格为 H-09（见 §三·补）。
6. **ADR-0037 状态需核对联审记录**：该 ADR 档案日期为 **2026-09-11（当日初版，v0.1）**、标注"R14-F04 #1250 触发"、状态 `Proposed`——**很可能是新提案在途（有意为之），而非状态滞写**。
   - 但 PM 指出其**风险方向相反**：由架构师与 PM 两路独立确认，`stp_agent_priv.py` wrapper / sudoers 生成 / Ansible playbook / smoke 脚本 / `priv_mode` 审计**均已实现落地**。
   - 即：**实现已先行于决策**。需核对 R02 安全联审记录后判定——若联审已完成，仅是状态待推进；**若联审未完成而实现已上线，则是"实现先于决策"的流程倒置**，对一个"是否允许回退到宽 sudoers"的方向级安全边界而言，后者更严重。
7. **`docs/design/07-execution-protocol.md`**（最后更新 07-15）与 09 月 ADR-0036/0037 的行为一致性，需协议 owner 确认。
8. **R-01 的 485GB 是否仍为当前实测值**（09-03 快照），建议以当前磁盘实测复核。

---

## 九、审查方法说明

- **只读约束**：全程未修改任何项目文件。所有写操作仅限团队内部报告传递与项目外的临时扫描脚本目录。
- **证据标准**：所有结论均附 `文件:行号` 或 git 提交号；明确区分「确证 Bug」（有确定性代码路径可推导）与「疑似/需运行验证」。
- **交叉验证**：主线程对四维度报告的核心结论独立复核了源码（连接池 `database.py:57-61`、心跳超时三处、CI 门禁 `ci.yml:25/82/229`、分层依赖 `plan_run_ops.py:109`、ADR-0031/0037、`action_templates` 全仓 70 处命中），并**修正了两处成员推断**：
  1. ADR-0037 状态被推为"滞后于实现" → 复核其档案日期为 2026-09-11 当日 v0.1 初版，`Proposed` 很可能是有意为之（但方向性风险仍在，见 §八.6）；
  2. PR 门禁缺口被定性为"漏洞" → 复核 `ci.yml:229-230` 为**书面接受的取舍**，改述为"需决策是否引入 Merge Queue"。
- **提交前复核（2026-09-12 追加）**：报告定稿后、对外登记前，对全部 `文件:行号` 断言做了一次逐条重验，**修正两处**：
  - **H-01**：原记"三处不一致"，实测为**四处**——漏了 `backend/api/routes/hosts.py:53`（且 `settings.py:11` 再转引），已更正；**合入前再验（`dfbeb2ef`）：`session_watchdog.py` 已移除，复归三处且默认值已一致，问题性质由"取值冲突"收窄为"多份重复字面量"**；
  - **H-07b**：原记"3 种 header 形态、ADR-0022 为表格变体"，实测为 **36 常规 / 1 键位粗体 / 1 无状态行**；ADR-0022 的问题是**缺状态行**而非表格变体，已重写并下调其严重度表述。
  另新增确认：`docker-build`（`ci.yml:352`）同样为 PR 排除项，H-08 的敞口比原述更宽。
  - C-01、C-02、H-02~H-06、H-08、H-09、R-03、R-05、R-06、R-09 全部**行号与计数逐条复核无误**。
- **环境限制**：本机 bash shim 缺失 coreutils，文本处理全部改用 Python 3.13；本机无 pytest/sqlalchemy，未能实跑测试套件，相关结论基于静态分析。
- **未覆盖范围**：真机 ADB/NFS 路径行为、生产数据库实际统计（R-08）、硬件侧事实（R-05 的机架/USB 切电）——均超出静态只读审查能力。

---

*本报告为四维度并行只读审查产出，不含任何代码或文档改动。*

---

## 十、合入前复检（2026-09-12 · 基线 `dfbeb2ef`）

### 背景

本报告初始基线为 `ca0665c4`，与合入时的 `main` 对比**落后 326 提交**。为避免“拿着过期行号报缺陷”，在 PR 合入前将 `main`（`dfbeb2ef`）并入本分支后，对全部 13 条发现逐条回 `main` 源码重验。

### 复检结论总表

| 编号 | 主题 | 复检结论 | 证据 |
|:----:|------|:--------:|------|
| **C-01** | 同步连接池未配容量参数 | ✅ **仍成立** | `backend/core/database.py` `get_sync_engine_kwargs` 仍只返回 `{"future": True, "pool_pre_ping": True}`，无 `pool_size`/`max_overflow`/`pool_recycle`；同文件 `get_async_engine_kwargs` 有 30/60/1800 |
| **C-02** | RunConsole 进程内单例未登记为扩展阻塞点 | ⚠️ **部分缓解** | 单例仍在（`run_console.py:223-224,265-274` `_instance`/`_instance_lock`），但 **#1114（`cabe1e49`/`166de054`）已落地**：ADR-0027 补写多实例边界、`multi_instance_console_warning()` + `console_run_miss_hint()` 新增，并附专项测试 |
| **H-01** | 心跳超时默认值多处不一致 | 🔁 **形态变化** | 基线四处（120/300/300/300）；`dfbeb2ef` 上 `backend/agent/session_watchdog.py` **已整体移除**，现为三处（`devices.py:30`/`hosts.py:53`/`reachability.py:40`）默认值均 300——取值冲突消失，**重复定义仍在** |
| **H-02** | `services → api.routes` 反向依赖 | ✅ **仍成立** | `git grep` on `dfbeb2ef`：`dispatch.py:94`、`plan_run_ops.py:109,214,290` 仍 import `backend.api.routes.*` |
| **H-03** | God-module 路由 | ✅ **仍成立** | `plan_runs.py` **3341** 行、`agent_api.py` **3331** 行（较基线 3328/3239 更大）；`plans.py` 1241、`hosts.py` 1030、`projects.py` 1003 |
| **H-04** | NFS 原始日志零 TTL（R-01） | ⚠️ **部分缓解** | `cron_scheduler.py:344-352` 新增 `purge_job_log_files(stale_job_id_list)`——首次出现 DB 之外的落盘清理；但 `devices/`、`dedup/` 仍无 TTL |
| **H-05** | HddSpill 腾退不足 | ✅ **仍成立** | `local_disk_monitor.py:30` `_MAX_SPILL_PER_CYCLE = 20`、`:35` `_interval = 300.0` 均未变 |
| **H-06** | `merge_task` 全失败无终态 | ✅ **仍成立** | `saq_tasks.py:761-766` 仍为 `if result != "ok": logger.info(...); return`，无告警/无终态/无 `archive` 记录 |
| **H-07** | ADR-0031 ID 冲突 | ✅ **仍成立** | `docs/adr/` 下 `ADR-0031-appendix-phase3-core-write-tools.md` 与 `ADR-0031-platform-ai-assistant.md` 并存 |
| **H-07b** | ADR 状态标注碎片化 | ✅ **仍成立（精确复核）** | `docs/adr/` 38 篇：**36** 篇 `- 状态：`、**1** 篇 `- **状态**：`（`ADR-0035`）、**1** 篇**无状态行**（`ADR-0022-patrol-heartbeat-aggregation.md`，注意其在合入期间已更名） |
| **H-08** | PR 门禁不覆盖控制面测试 | ✅ **仍成立（面更宽）** | `.github/workflows/ci.yml`：`backend-test:25`、`frontend-check:82`、**`docker-build:352`** 均 `if: github.event_name != 'pull_request'` |
| **H-09** | `action_templates` 死代码 | ❌ **已被上游修复** | `git grep action_templates` on `dfbeb2ef` **零命中**（`56234cb3 refactor: schema-sync 基线收敛` 清除） |
| **R-05** | 机架/USB 切电能力缺失 | ✅ **仍成立** | `git grep -E "rack_id|slot_id|uhubctl"` on `dfbeb2ef` **零命中**（与基线一致） |
| **R-09** | AI 助手工具面窄 | ✅ **仍成立** | `services/ai_assistant/tools.py` 仍为 **26** 个工具；`jira`/`crash`/`stack`/`dedup` 相关工具仍为 0 |

> **R-09 计数校准**：基线表述“26 个工具”经复检确认无误（`name="..."` 恰 26 处；`ToolSpec` 28 处含 2 处为新架构重复计量口径，工具**实体**数仍 26）。

### 与同日上游审查稿的关系

合入期间发现 `main` 在 **同日（2026-09-11）** 另落一批 10 篇 `REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_*` 审查稿（#1349/#1369）。**主题不同**：那批聚焦“审查覆盖度与修复有效性”（元审查），**非代码缺陷**。关键词交叉比对确认，本报告 13 条发现中仅 `RunConsole`（3/10 篇提及）、`NFS`/`HddSpill`（4/10、2/10 篇）存在**主题相邻**，但均未给出本报告的代码级行号与判定，**不构成重复**。

### 处置

- **H-09**：issue [#1526](https://github.com/DUElost/stability-test-platform/issues/1526) 建议**关闭**（原因注明“上游 `56234cb3` 已修复”）。
- **H-01 / H-04 / C-02**：issue 已追加复检评论说明状态变化，**不关闭**（主体责任面仍在）。
- **C-01 / H-02 / H-03 / H-05 / H-06 / H-07 / H-07b / H-08 / R-05 / R-09**：无需改动，逐条复验通过。
