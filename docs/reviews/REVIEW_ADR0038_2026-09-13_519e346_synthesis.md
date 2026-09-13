# ADR-0038 v0.1 多 Harness 独立评审综合（synthesis）

- **日期**：2026-09-13
- **性质**：9 份独立只读评审 + 1 条非独立补充评论的**去重汇聚**（issue #1557 Mode C：`1 审计 Requirement → N 独立评审 → N Findings → 去重汇聚`）。本文件 R 编号为唯一权威映射，后续引用一律用 R 编号。
- **边界**：本稿只做汇聚与建议，**不修改 ADR-0038、不做 Accepted 裁决、不开实现单**——三者均在人工裁决之后（issue #1557「目的」节）。
- **汇聚基线**：`origin/main = 7fce1286`。各评审稿基线为 `b003b443`～`82c0462c`，ADR 正文自 `b003b443` 起无实质变更（仅 #1557 回链两行）；汇聚时对承重结论按 main 抽样重验（§6），其中 **1 项发现已被合入 main 的修复合销**（§3 F-6）。

## 0. 结论速览

- **总评谱系：9/9 Needs-revision，0 Accept**。无任何一稿认可 v0.1 原样转 Accepted。
- **方向共识（9/9 认可，无需裁决）**：D1（`retired_at` 单一生命周期真源、不新增 `HostStatus`）、D2（删除/退役分家、`DELETE` 预检原样）、D3（退役即终态、不做带历史硬删）、D7（显式不做清单）、§3 对 `status=RETIRED`/`deleted_at`/force 硬删/FK SET NULL/心跳 409/心跳自动复活六项否决、对 `extra['archive']` 命名占用的规避。**D4「不做 409」的取舍本身 9/9 成立**（Agent 对非 2xx 吞异常返回 None，心跳响应承载 backpressure/版本门禁，`backend/agent/heartbeat.py:83-91`、`backend/agent/heartbeat_thread.py:345-390`）。
- **阻断簇集中在四件事**（详见 §1 R1–R4，均为 4 源以上共振）：①派发面 `host_retired` 归位未定 → 准入队列永久 QUEUED；②「沿用现有告警去重惯例」所述机制不存在；③D4 使现存 `status` 判据对退役活体全部放行、五面不穷尽；④D6 的换机归因与「boot_id/agent_instance_id 审计可见」承诺零落点。
- **分级分歧说明**：`d04f71`（claude-code）为唯一 0 阻断稿，但其 F2-02（派发裁决）/F3-01（去重载体）与其他稿的阻断项**实质同物**——该稿判「建议」的理由是「按字面实现不产生数据破坏或不可逆风险」，属严重度标定分歧，非实质分歧。汇聚按多数稿定级。
- **处置建议**：ADR-0038 **修订为 v0.2 后转 Accepted**；v0.2 须完成 §3 事实性更正（与裁决无关，直接改）+ §1 中标注「必须」的条款改写 + §4 二选一的人工裁决；实现单在裁决后另开。

## 1. 裁决总表（R1–R19）

「级别」= 各稿最高定级；「N 源」= 独立命中的评审稿数（补充评论不计源数）。

| R | 发现（共振源） | 级别 | 裁决 | v0.2 落点 |
|---|---|---|---|---|
| R1 | 派发面缺 `host_retired` 归位：`_FATAL_DISPATCH_REASONS = ("not_found","no_host")`（`backend/services/plan_dispatcher_sync.py:68`），其余拒因一律可重试排队；且「正常排队不消耗重试计数」（`backend/services/admission_pump.py:292-293` 原文），`host_offline` 可自愈、`host_maintenance` 有 TTL，**退役永不自愈** ⇒ 若归 retryable 则命中退役主机的 PlanRun 无上界、无 dead-letter、无告警地永久 QUEUED（**9 源**：45308c、409d16、db232b、5ff80e、713685、747cae、d04f71、ec183be3、182d4e） | **阻断**（8/9） | **待裁决 D-1**（建议：归 fatal，prepare 400 + 明确错误码） | D5 第一面写死归位；§5 验收补「拒绝而非永久排队」判据 |
| R2 | 不变量 3「沿用现有告警去重惯例」所述机制**不存在**：去重身份 `(event_type, run_id, task_id, device_serial)` 无 host 维度（`backend/services/notification_service.py:329-336`），`run_id is None` 直接短路不去重（`:343-345`，汇聚核验✅）；SAQ 键 `notif:{event}:{run_id}:{serial}` 不含 host_id（`:649-654`）；`EventType` 仅 4 值、无主机级事件（`backend/models/notification.py:16-20`，汇聚核验✅）；ADR-0036 D7 明写不承诺端到端去重。**附实现陷阱（4 源）**：`Host.extra` 每次心跳整键重建、仅 6 个 `agent_*` 白名单键保留（`backend/api/routes/heartbeat.py:249-262,281`，汇聚核验✅）——去重标志放 `extra` 裸键会被抹除 ⇒ 每拍重响。可复用先例是发射点状态跃迁门（`heartbeat.py:142-148` 的 `already_offline`）（**9 源**） | **阻断**（7/9） | **待裁决 D-4**（载体 + 震荡重置定义） | D4/不变量 3 重写：删除「沿用惯例」句，指定事实源与重置条件；§5 验收 3 改为可判据 |
| R3 | D4×D5 直接冲突：D4 明许退役主机 `status=ONLINE`（心跳如实写，`heartbeat.py:236`），而 claim 只比 ONLINE（`backend/api/routes/agent_api.py:401`，汇聚核验✅）、派发只排 OFFLINE（`plan_dispatcher_sync.py:155`）⇒ §1.2「退役只要同时被这两处识别即可落在真正的风险面上」在 D4 之下为假；实测 19 处显式 status 判据/12 文件全部放行（5ff80e），遗漏面见 §2 矩阵（**9 源**） | **阻断** | 采纳（方向无分歧）：每个过滤点显式新增 `retired_at IS NULL` 判据，禁依赖 status | D5 第五面改为「动作/读面清单 + 统一判据」，点名共享收口点 |
| R4 | D4 事实锚点锚在**无调用方的副路径**：ADR 引 `agent_api.py:806-829` = `POST /api/v1/agent/heartbeat`，全仓零调用方；权威路径是 `POST /api/v1/heartbeat`（`backend/agent/heartbeat.py:84` 客户端；`backend/agent/heartbeat_thread.py:27` 自述 "SOLE authority"；汇聚核验✅），且权威路径多两个轻量端点没有的行为：**按 IP 找回退役行**（`heartbeat.py:199-206`）与**设备 re-home**（`:403`，汇聚核验✅）。两端点不对称：轻量端点无条件置 ONLINE、不写 boot_id（**5 源**：5ff80e 阻断，747cae、d04f71、182d4e、db232b/6f44a7 补充） | **阻断**（1/9）/建议（4/9） | 采纳：按 v0.1 文字实现，D4 的「保持退役 + 单次告警 + 徽标」在真实心跳上永不触发 | §1.2/D4 锚点改指 `heartbeat.py:196-236`；实现单必须覆盖两条端点；「IP 找回命中退役行」同样视为活体退役 |
| R5 | D6「同 IP 换机 = 同一身份（id 本就 IP 派生）」**归因不成立且边界未写**：同 IP 起不了第二行的真实原因是 `Host.ip` 全局唯一（`backend/models/host.py:41`，汇聚核验✅）+ 创建显式 409；`id` 仅在创建时由 IP 派生，此后 `_sync_host_identity` 只改 ip/name 不改 id（`heartbeat.py:76-113`）⇒ 判据单向（同 IP ⇒ 复用旧行；同身份 ⇏ 同 IP）。**冲突后缀分支「死代码」争议**：45308c/713685 称不可达；db232b/5ff80e/182d4e 证明**在 id/ip 脱钩场景可达**（旧行 IP 漂移或为 NULL + 旧哨兵自动注册 `heartbeat.py:63-65,207-208`；`PUT /hosts/{id}` 改 ip 不改 id `hosts.py:407-409`；182d4e 实跑冲突用例 5 passed，`backend/tests/test_host_identity.py:22` 汇聚核验存在）——该场景同一物理 IP 分裂两行、一退役一在用，与 D6 裁定方向相反（**9 源**） | 建议（6/9）～阻断（45308c） | 采纳：结论（只能 unretire）保留，**理由改写**；后缀边界按「可达」裁决（有测试实证），v0.2 写明期望行为 | D6 改述为「生命周期按主机行 id 判定，ip 是可变属性，同 IP 复用是结果非判据」；补 id 命中 > IP 命中 > 分配 的优先级契约与后缀场景处置 |
| R6 | D6「`boot_id`/`agent_instance_id` 变化在详情与审计可见」**零落点**：`HostOut` 不含这两列、前端零命中、`heartbeat.py` 全文件 `record_audit` 零命中、`previous_boot_id` 阅后即焚（`agent_api.py:2903-2904`）；`job_instance` 无身份快照（`backend/models/job.py:25`），唯一留痕是 `device_lease.agent_instance_id`（`backend/models/device_lease.py:34`）；且 `boot_id` 每次开机随机（`backend/agent/identity.py:21-42`，汇聚核验✅）⇒ **换机与重启在信号上不可分辨**（**9 源**） | 建议（6/9）～阻断（45308c、5ff80e、713685） | 采纳：承诺要么兑现要么降级 | D6 二选一：(a) 降级为「详情可见当前值」+ unretire/身份变化时 `record_audit` 快照（推荐，6 源同方向）；(b) 列明交付面（HostOut + types.ts + 审计事件） |
| R7 | D5 未回答「**已在飞的 Run 遇 retire**」按冻结快照还是活读，而仓内同构开关已有相反先例：`watcher_admin_active` 明确选**冻结快照**（`backend/services/plan_dispatcher_core.py:84-88` 原文「一旦 PlanRun 已 prepare……必须消费同一份快照，而不是回读 Host 当前状态」，汇聚核验✅），claim 今天是活读（`agent_api.py:396-401` `with_for_update`）；且 QUEUED 的 PlanRun 无 Job 行（ADR-0026 不变量①）⇒ D2 前置「无活跃 Job」**覆盖不到已排队计划**（**5 源**：5ff80e、182d4e、747cae、ec183be3、d04f71；db232b-F1-C 相邻） | **阻断**（2/9）/建议（3/9） | **待裁决 D-2** | D5 新增 D5bis：写明 prepare 后 Run 的判定基准与已 QUEUED 单的收敛路径 |
| R8 | 退役前置「非 ONLINE」是**代理条件而非证明**（status 由 4 类写手维护，网络分区/假死/超时窗内「进程还在、判据已满足」）；繁忙集群存在「停 Agent ↔ 新派发」竞态，无 Cordon 过渡态；§4「退役要求先停 Agent」表述过强。租约分歧：45308c/ec183be3 主张前置补「无 ACTIVE DeviceLease」；747cae/d04f71 主张显式写「不检查」（retire 不删行，租约由 reconciler 回收，与 DELETE 动机不同）（**8 源**） | 建议 | **待裁决 D-3** | D2 前置语义二选一写死（严格 OFFLINE/过期谓词 vs Cordon）；租约进前置与否显式化；retire/unretire 与 claim 同行锁复检（747cae-F1-2、182d4e-R01） |
| R9 | 不变量 1 的 `⇔` 过强 + DEGRADED 未界定：现状 DEGRADED「可派发（只排 OFFLINE）不可认领（要 ONLINE）」⇒ 字面实现＝未声明的容量行为变更；`session_watchdog.py:43-48` 只扫 ONLINE（汇聚核验✅）⇒ 停机的 DEGRADED 主机永不转 OFFLINE；DEGRADED 写入者存在分歧（45308c 引 `capacity_reporter.py:161`；db232b 反证那是 `health.status` 子对象非主机状态、现无写入者；5ff80e/747cae 确认 schema 通道存在 `schemas/host.py:104`、当前 Agent 未上报）⇒ latent（**8 源**） | 建议 | 采纳：不变量 1 拆两条（派发：`retired_at IS NULL ∧ status != OFFLINE ∧ ¬维护窗`；认领：`retired_at IS NULL ∧ status = ONLINE`）并注明与现状一致 | D2/D4 一句话写明 DEGRADED 定位（推荐「存活但降级，不可退役」）+ §1.2 补「latent」注记 |
| R10 | 「回滚 = drop 三列（无状态依赖）」不成立：`retired_at` 是 D1 自称的唯一真源，drop＝一次性、无审计、无前置地解除全 fleet 退役，与 §3 否决「心跳自动复活」的理由自相矛盾；且 CI 从不跑 downgrade（5ff80e 三处 grep 零命中）；182d4e 进一步指出：仅回滚应用而保留列，旧 worker 也会忽略该列恢复派发——**schema 可降级 ≠ 运维决定不被静默撤销**（**8 源**：182d4e 阻断，5ff80e 确定缺陷，其余建议/观察） | **阻断**（1/9） | 采纳 | §4 迁移改写：发布顺序（迁移 → 全部消费方支持退役 → 开放 retire 入口）；存在退役行后禁无条件删列；缓解 = retire/unretire 强制审计（`audit_logs` 独立表，回滚不动它 ⇒ 事实可重放）+ downgrade 守卫/往返测试 + 「drop 会丢失退役状态」注释 |
| R11 | 审计字段与双真源：`retire_reason` 未定是否必填（**8 源一致建议必填 min_length=1**）；unretire 未要求 reason（**9 源一致要求**）；`retired_by`/`retire_reason` 与 `audit_logs` 语义重叠且三列只能承载最后一次 retire（5ff80e-S5、ec183be3-F6-2 建议：事件真源走 `record_audit`，列=当前态投影）；`core/audit.py:61-88` 缺表时降级吞掉 ⇒ admin 退役路径应 fail-closed（5ff80e、182d4e-R06：审计失败则事务失败）；重复 retire/unretire 幂等与 unretire 写回语义（清空 vs 保留最近痕迹）未定义（182d4e-R06、d04f71-F1-03） | 建议 | **待裁决 D-6**（三列去留）；其余采纳 | D1/D2 补：reason 强制、`retired_by` 取 current_user、unretire 记因、幂等与写回语义 |
| R12 | ADR-0035 交互只写「落地后重审」：缺「**retire ≠ credential revoke**」的显式声明（per-host 凭据落地后退役主机凭据若仍可用即成新洞；现状无凭据可吊销）；Revisit-2 挂在 ADR-0035 触发条件上可能长期无时间点（ec183be3-F4-3）；换机序列缺 SSH host key/凭据步骤（747cae-F4-5/F1-5）；不应为恢复退役机心跳自动同步轮换后的 secret（182d4e-R09）（**8 源**） | 建议 | 采纳：新增 D8「retire ≠ 凭据吊销」+ Revisit 2 补替代触发 | D6/Revisit 2 回链 #906；换新序列写进 §4 运维代价 |
| R13 | 三列 additive 迁移配套未写：ORM↔alembic↔Pydantic↔前端 TS 四端配对；`check_schema_sync` 门禁与**禁 `--rebaseline`**（`_diff_key` 对 add_column 取到 Column repr，`check_schema_sync.py:68-71`，5ff80e-O8）；不动 `schema_sync_baseline.json`；单 head（届时 `alembic heads` 实读，评审时点为 `cc33dd44ee55`）；类型拼写钉死 `sa.DateTime(timezone=True)`（`check_schema_sync` 未配置 compare_type/server_default，`sa.DateTime()` 与模型不一致**可能漏检**，ec183be3-F5-1）；`retired_by` 建议对齐 `String(128)`（`models/audit.py:22`）；本次**不新增索引**（drop 列连带删索引 = #644 事故形态，5ff80e）；conftest 走 `create_all()` 漏迁移不会红（ec183be3-F5-1）；downgrade 无自动验证需补往返测试（**9 源**） | 建议 | 采纳 | §4 迁移段补配套清单（同表先例 `m8n9o0p1q2r3_host_maintenance_window.py`） |
| R14 | §4 前端面事实错误 + 消费面穿透：「`api.hosts.remove` 无 UI 调用点」**双错**——方法名是 `delete`（`frontend/src/utils/api/hosts.ts:24-25`，汇聚核验✅）且有调用点（`HostsPage.tsx:107` 单删 + `:308` 批删，汇聚核验✅）（**3 源**：db232b、5ff80e、d04f71）；`include_retired` 需穿透全站共享取数 `fetchHostList`（`hosts.ts:33`，limit=200 静默截断）与 `hostKeys.list()=['hosts']` 三页共享缓存（HostsPage/PlanExecutePage/DevicesPage）（6f44a7-I2、5ff80e-S10、ec183be3-F2-3、d04f71-F2-03）；前端判定面 `planExecuteReadiness.ts:165-179`/`bulkHotUpdate.ts:51-70` 只认 ONLINE | 建议 | 采纳 | §4 前端面改写；实现单盘点 `/hosts` 全部列表调用点的参数穿透 |
| R15 | §1.1「跑过 Job ⇒ 永久 409」受 `run_retention_cleanup` 时间限制：默认 **3 天**删终态 PlanRun 的 Job/StepTrace/租约/产物（`backend/scheduler/cron_scheduler.py:27` `PLAN_RUN_RETENTION_DAYS=3`、`:224-330`，汇聚核验✅；注册于 `app_scheduler.py:244`）⇒ 「永久」的是**设备维度**（device 行无删除入口 + serial 全局唯一），立项依据应以设备维度为准（**1 源**：5ff80e；汇聚核验✅） | 建议 | 采纳（已核验） | §1.1 改写 |
| R16 | D3/§1.3/Revisit-3 把 DB purge 交给 ADR-0025 = **死引用**：`docs/adr/ADR-0025-phase4-architecture-alignment.md` 对 `retention|purge|保留期` **零命中**（汇聚核验✅，该文只管日志文件闭环）；现行 DB 清理是 `run_retention_cleanup`，本 ADR 不改动它即可，但「purge 归属」需另立裁决对象（**1 源**：5ff80e；ec183be3-F5-2、d04f71-F5-04 对 ADR-0025 范围的独立描述与之相容） | 建议 | 采纳（已核验） | D3/§1.3/Revisit-3 改写「DB purge 议题归属另立」 |
| R17 | F7 验收判据收紧：「反例实证」需留痕（命令+结果记入 Agent Note/PR）且**逐点 mutation 防多层防护互相遮蔽**（182d4e-F7）；Python 侧 mutation 须清 `__pycache__` 防假绿（747cae-F7-1）；统计面「移除过滤点转红」不成立（泛型 group_by/raw SQL 删特例仍输出看似正常的数值）⇒ 改为断言**排除退役主机后的具体计数值**（5ff80e、ec183be3-F7-1）；「单次告警」四条边沿断言（首拍 1 条/持续 N 拍仍 1 条/超时恢复再 1 条/unretire→retire 重新计轮）+「震荡」需先定义（**9 源**） | 建议/观察 | 采纳 | §5 验收 2/3 改写 + 收口点↔测试文件映射（d04f71-F7-01、db232b-F7-A、747cae-F7-4 已列现成落点） |
| R18 | ~~心跳超时双默认值 120s/300s 不一致~~（db232b-F1-A、5ff80e-S1、747cae-F1-1、ec183be3-F5-3 提出）：**已被 main 合销**——`HOST_HEARTBEAT_TIMEOUT_SECONDS` 已单源到 `backend/core/job_timeout_config.py:86`（默认 300），`session_watchdog.py:25` 与 `hosts.py:14` 同源导入（汇聚核验✅）。各稿写作时该修复未入其基线 | 已合销 | 无需行动 | v0.2 不再引用「双默认值」论据 |
| R19 | 观察项汇总（单源或低危，实现单/后续处理）：① claim 拒绝分支 fail-open 静默——非 ONLINE 分支无日志（对照维护窗有 `claim_skipped_host_maintenance`，`agent_api.py:399-409`，汇聚核验✅），退役拒绝须携带可区分信号（6f44a7-I1）；② 心跳响应可加 `is_retired` 标志/背压退避（45308c-F3、d04f71-F3-03 建议记入备选）；`script_catalog_outdated` 仍返回（713685-F3.2）；③ 退役机不再被热更新 ⇒ `agent_code_sync_status` 长期 drift 红点（5ff80e-S9）；④ 术语占用：script-versioning 已用「退役」（d04f71-F5-03）；⑤ `docs/DOC-MAP.md` 未登记 ADR-0037/0038（5ff80e-O9，v0.2 时补，synthesis 链接一并入 ADR 行）；⑥ watchdog per-host Counter 永不过期（5ff80e-O4）；⑦ Ansible inventory 手工维护属平台外选靶通道（5ff80e-O5）；⑧ CSRF `x-agent-secret` 豁免只判存在不校验值（5ff80e-F6 附带，**既有面非本 ADR 引入**，可另立核查）；⑨ 锚点微偏（`hosts.py:477`→`:466`、`metrics.py:39` 为声明行、`agent_api.py:3167` 为 docstring 读取在 `:3179`，5ff80e-O1）；⑩ 心跳/重连写路径的「可写/不可写字段边界」未界定（db232b-F2-C：身份与归属字段、设备 re-home 是否照写）；⑪ D2 双前置与已派发未认领存量作业的处置口径（db232b-F1-C） | 观察 | 择要吸收进 v0.2，其余留实现单 | — |

## 2. R3 遗漏面矩阵（D5 五面之外/之内需显式新增 `retired_at IS NULL` 判据的读/写/下行面）

| # | 面 | 关键锚点 | 源 |
|---|---|---|---|
| 1 | 派发快照与准入分类（含 TOCTOU 复检、准入泵） | `plan_dispatcher_sync.py:68,88-100,155-168,509-519,827`；`admission_pump.py:666-675`；第二快照点 `plan_dispatcher_core.py:82-107` | 9 |
| 2 | claim（活读、`with_for_update`） | `agent_api.py:396-409` | 9 |
| 3 | scan/archive 命令扇出（历史 Run 的 scan_now/archive_now） | `plan_run_scan_scope.py:113-122`（缺行默认 `"OFFLINE"` fail-closed）+ `saq_tasks.py:314-315`、`plan_runs.py:549-566`、`dedup.py:464-477`、`ai_assistant/plan_run_ops.py:391-410` | 9 |
| 4 | Socket.IO 房间注册与下行控制（含零校验点） | `socketio_server.py:154-186,749-760,511/541`；`dedup.py:508-513` reload-config **连 host 存在性都不查** | 9 |
| 5 | 统计/容量/metrics（三口径分叉：ORM 谓词 / raw SQL 全表 / 泛型 group_by） | `stats.py:264-272,286-298（raw SQL 无 WHERE）,451-459（历史 KPI）`；`metrics.py:44-53`；AI `tools.py:134-149` | 8 |
| 6 | 批量热更新脚本（`--direct` 自选靶，无服务端路由） | `backend/scripts/batch_hot_update.py:89-95` | 8 |
| 7 | install（**现状无任何存活门禁**——退役将是第一道闸门，须写明是新增而非对齐） | `hosts.py:793-825` | 7 |
| 8 | upgrade-gate（agent 自服务端点带 `abort_running_jobs`；服务层仅查存在性） | `agent_api.py:3262-3300,3310-3335`；`host_upgrade_gate.py:229-231`（汇聚核验✅） | 6 |
| 9 | AI 助手读写面 | `orchestrator.py:670-691`（reload 只判 ONLINE）；`tools.py:374-389`（`_q_hosts` 全量） | 8 |
| 10 | 设备面（列表/创建归属/前端多选/就绪判定） | `devices.py:98,294-377`；`DeviceMultiSelect.tsx:22-27`；`planExecuteReadiness.ts:165-179` | 8 |
| 11 | 预检 SSH 同步与准入 Phase A（脚本校验先于退役过滤，可 SSH 触碰退役机） | `precheck/runner.py:189,216` → `precheck/sync.py:68-104`；`admission_pump.py:497-557,817` | 4 |
| 12 | Agent 自服务写面（recovery/sync 覆写 boot_id、心跳设备 re-home 抢回、远程日志 SSH、脚本目录重拉） | `agent_api.py:2887-2910`；`heartbeat.py:403`；`logs.py:244-262`；`routes/scripts.py:212,231,241` | 6 |
| 13 | watcher-admin-state / dead-letter replay 等管理写路径 | `hosts.py:557-582,931-1030` | 5 |
| 14 | 前端判定与共享缓存 | `planExecuteReadiness.ts`；`bulkHotUpdate.ts:51-70`；`hostKeys.list()` 三页共享；`fetchHostList` limit=200 | 4 |

防过度过滤（182d4e-R02/R07、747cae-F2-8、db232b-F2-D 共识）：**历史 KPI/趋势、报告导出、延迟上传 fencing、恢复路径按既有语义保留**；「历史归属集合」与「可发新命令的目标」必须区分——对历史 scan scope 直接过滤会把缺失 host 从预期证据集合移除并虚报完整，应显式 `skipped_retired`（待裁决 D-5）。

## 3. 事实性更正（与裁决无关，v0.2 直接改）

| # | v0.1 原文 | 事实 | 源/核验 |
|---|---|---|---|
| F-1 | §4「`api.hosts.remove`（hosts.ts:25）无 UI 调用点，随实现单决定保留或删除」 | 符号是 `delete`；有调用点 `HostsPage.tsx:107`（单删）、`:308`（批删）。两个删除入口正是 D2 要缓解的运维死路界面，实现单须显式决定其与 retire/unretire 的关系 | db232b、5ff80e、d04f71；汇聚核验✅ |
| F-2 | §1.1「跑过 Job ⇒ 永久 409」 | `run_retention_cleanup` 默认 3 天删终态 Run 的 Job/StepTrace/租约/产物（`cron_scheduler.py:27,224-330`）；永久的是**设备维度** | 5ff80e；汇聚核验✅ |
| F-3 | D3/§1.3/Revisit-3「purge 归 ADR-0025」 | ADR-0025 对 retention/purge/保留期零命中（只管日志文件闭环）＝死引用；现行 DB 清理是 `run_retention_cleanup` | 5ff80e；汇聚核验✅ |
| F-4 | §1.2「冲突追加短后缀」隐含为换机路径 | 同 IP 换机主路径不可达（ip 唯一约束 + 按 ip 复用）；后缀分支在 id/ip 脱钩场景**可达**（旧哨兵自动注册/PUT 改 ip），且与 D6 裁定方向相反——见 R5，v0.2 按边界写明期望 | db232b、5ff80e、182d4e（实证）vs 45308c、713685（死代码说）→ 按有测试实证一方裁决 |
| F-5 | §1.2 锚点微偏：`hosts.py:477-549`（实际路由 `:465`/预检 `:481-533`）、`metrics.py:39`（声明行，计数 `:44-53`）、`agent_api.py:3167`（docstring，读取 `:3179`） | 建议顺带校正 | 5ff80e-O1（3 源零散佐证） |
| F-6 | （评审发现，非 ADR 原文）心跳超时双默认值 120s/300s | **已合销**：`HOST_HEARTBEAT_TIMEOUT_SECONDS` 单源于 `backend/core/job_timeout_config.py:86` | 汇聚核验✅（main 7fce1286） |

## 4. 需人工裁决的二选一清单（D-1～D-6）

> 每条给出各稿倾向与推荐；裁决后按 §5 写进 v0.2。

- **D-1（R1）退役在派发面的归位**：
  - ① 归 **fatal**（`host_retired` ∈ `_FATAL_DISPATCH_REASONS`，prepare 400 + 在队 Run 明确 FAILED + 审计）——45308c、409d16、713685、d04f71、ec183be3、747cae、db232b（倾向①）；语义与「退役=配置事实、永不自愈」最一致。
  - ② 归 retryable 但给出停止条件（超时/运维 abort）——仅 5ff80e 提出需说明为何可接受永久排队。
  - **推荐 ①**；无论哪种，须同时定义「同一 Run 部分主机退役」的行为（747cae 建议 all-or-nothing，与准入队列语义一致）与 fatal 判据**不得被较早的暂态拒因遮住**（182d4e-R01：分类器先查 device 再查 host）。
- **D-2（R7）已在飞 Run 遇 retire：冻结快照 vs 活读**：
  - ① 派发/认领**活读** `retired_at`，`PlanRunHost` 投影与统计按 prepare 冻结（5ff80e-Z6 建议；与 claim 现状活读一致，unretire 会使同一 Run 判定翻转须接受）。
  - ② 沿用 `watcher_admin_active` 冻结快照先例——则已 prepare 的 Run 仍可能派发到退役机，违背 D5 字面。
  - ③ 旧 QUEUED/PRECHECK 快照不删，以显式 `HOST_RETIRED` 原因收敛执行（182d4e-R01），并禁止「过滤退役设备后继续剩余设备」破坏 ADR-0026 不可变快照与原子准入。
  - **推荐 ①+③ 组合**（活读判废 + 显式收敛，不静默缩小目标集合）。
- **D-3（R8）退役前置**：
  - ① **Cordon**：无活跃 Job（含 QUEUED 引用，见 D-2）即 retire，status 不参与前置——45308c、713685、ec183be3 倾向；配合 D4 告警与 D5 封死。
  - ② 严格门：`status == OFFLINE`（或 `last_heartbeat` 已超时）——713685-F1.1 白名单、db232b stale 谓词变体；需同步处理 watchdog 对 DEGRADED 的不收敛。
  - ③ 维持「非 ONLINE」仅修措辞（承认代理条件与竞态）——d04f71-F1-02、409d16-F1-1 认为「先停再退」窗口属产品取舍。
  - 附带：**租约是否进前置**（45308c/ec183be3 补检 vs 747cae/d04f71 显式不检查，后者与「retire 不删行」动机一致）；retire/unretire 与 claim 同行锁复检（747cae-F1-2、182d4e-R01）。
  - **推荐 ①（Cordon）+ 显式「不检查租约」+ 同行锁复检**：与 D4「活体退役是预期场景」自洽，消除停机竞态。
- **D-4（R2）告警去重载体与重置**：
  - ① 新增持久列（如 `retire_alerted_at`，与三列同批迁移）——db232b、713685、ec183be3、5ff80e（禁用 `Host.extra` 裸键为 4 源共识）。
  - ② `Host.extra` 白名单加键——45308c、409d16、d04f71 曾建议；**与 4 源「extra 重建陷阱」冲突**，若选此必须同步 `heartbeat.py:253-262` 白名单并在 ADR 写明风险。
  - ③ 无状态跃迁门（复用 `already_offline` 先例：仅当此前 last_heartbeat 已过期再收心跳才告警一次）——747cae-②；省存储但与超时阈值绑定。
  - ④ 逻辑事件键 `(host.id, retired_at, event_type)`（182d4e-R05）——与 ①②③ 正交，可组合；同周期跨心跳/重试/重启只建一个事件，unretire→retire 产生新周期。
  - 「状态震荡」重置定义（多数稿点名含糊）：**推荐**「同一 `retired_at` 周期内不重置；OFFLINE→ONLINE 震荡是否二次提醒由 D-4 选型附带定义；unretire 后再 retire 重新计轮」。
- **D-5（R3/R19）数据回收类动作的取舍**：对 scan_now/archive_now、日志尾读、reload-config 等下行——① 一律拒绝（45308c 防污染重分配 IP 的硬安全定性）；② 区分「执行/配置类（拒绝：热更新/安装/升级门禁/reload/watcher）」与「数据回收类（允许但显式 admin 触发 + 审计 + `skipped_retired` 不虚报完整）」（747cae-F2-5、182d4e-R02；否则退役前最后一批设备日志无法回收）；Socket.IO 建议保留连接、只做下行判据（db232b、d04f71；拒绝连接会牵动 Agent 重连策略）。**推荐 ②**。
- **D-6（R11）三列去留与审计真源**：① 三列全留，`retire_reason` 必填、事件同时写 `audit_logs`（多数稿默认）；② 仅留 `retired_at`（过滤真源），who/reason 全走 `audit_logs`（ec183be3-F6-2、5ff80e-S5 的冗余论）；③ 审计失败是否阻断事务（182d4e-R06 建议本操作 fail-closed，不改全站降级政策）。**推荐 ①+③**；unretire 写回语义推荐「`retired_at` 清空，`retired_by`/`retire_reason` 保留为最近一次退役痕迹」（d04f71-F1-03）。

## 5. v0.2 最小修订集（裁决后执行）

1. **§1.2/D4（R4）**：心跳锚点改指 `backend/api/routes/heartbeat.py:196-236`；声明轻量端点 `agent_api.py:796` 的归属（共用检测或列入双通道收敛）；「按 IP 找回命中退役行」视为活体退役。
2. **D5（R1/R3/R7）**：按 D-1/D-2 写死派发归位与在飞 Run 语义；「五面」改为**清单面**（§2 矩阵并入，允许扩充），每面判据＝`retired_at IS NULL` ∧ 原判据，点名共享收口点（`_classify_dispatch_devices_sync`、claim、`iter_plan_run_scan_hosts`、`begin_host_upgrade`、`precheck/sync` 两函数、`emit_agent_control` 调用点）；按 D-5 写明数据回收类取舍。
3. **D4/不变量 3（R2）**：按 D-4 重写告警去重；显式声明「`Host.extra` 每次心跳重建，非持久状态容器」（§1.2 补一行）；徽标判据与告警去重解耦（`retired_at IS NOT NULL ∧ status=ONLINE`，409d16-F3-3）。
4. **D6（R5/R6）**：身份契约三句（id 命中 > IP 命中 > 分配；ip 可变属性；复用是结果非判据）+ 后缀边界期望 + boot_id 不可分辨换机/重启的诚实表述；按 R6 选型兑现或降级「详情与审计可见」。
5. **D2/§4（R8/R9/D-3）**：前置语义按裁决写死；DEGRADED 定位一句话；不变量 1 拆两条。
6. **D1/D2（R11/D-6）**：reason 必填、`retired_by` 取会话、unretire 记因、幂等与写回语义、审计 fail-closed。
7. **§1.1/D3/Revisit-3（R15/R16）**：两处事实改写。
8. **§4 迁移（R10/R13）**：发布顺序 + 回滚=有意数据丢失面（`audit_logs` 重放出口）+ 配套清单（四端配对/禁 `--rebaseline`/`DateTime(timezone=True)`/String(128)/不新增索引/往返测试/单 head）。
9. **新增 D8（R12）**：retire ≠ credential revoke；Revisit 2 补替代触发与换新序列（含 SSH host key）。
10. **§4 前端面（R14）**：F-1 更正 + `include_retired` 消费面穿透；**§5 验收（R17）**：按 R17 判据改写 + 收口点↔测试映射；**登记面**：`docs/adr/README.md` 状态行与 `docs/DOC-MAP.md` 补 ADR-0038 行（含本 synthesis 链接，5ff80e-O9）。

## 6. 评审样本清单、独立性与汇聚核验

### 6.1 样本（9 稿 + 1 补充）

| 稿 | harness | PR | 代码基线 | 总评 | 阻断/建议/观察（自报） |
|---|---|---|---|---|---|
| `…_45308c.md` | antigravity | #1573 | b003b443 | Needs-revision | 3 / 4 / 1 |
| `…_409d16.md` | cursor | #1602 | b003b443 | Needs-revision | 3 / 若干 / 若干 |
| `…_db232b.md` | zcode | #1609 | ea621988 | Needs-revision | 3 / 12 / 5 |
| `…_5ff80e.md` | dsh（第二稿，经人工确认另开） | #1623 | 6e881f55 | Needs-revision | 6 / 14 / 9 |
| `…_713685.md` | opencode | #1604 | b003b443 | Needs-revision | 4 / 4 / 3 |
| `…_747cae.md` | codebuddy | #1610 | b003b443 | Needs-revision | 3 / 21 / 9 |
| `…_d04f71.md` | claude-code | #1607 | 82c0462c | Needs-revision | 0 / 15 / 7 |
| `…_ec183be3.md` | dsh（第一稿） | #1608 | ea621988 | Needs-revision | 2 / 11 / 6 |
| `…_182d4e.md` | codex | #1628 | b003b443 | Needs-revision | 4 / 3 / 2 |

- 独立性：各稿独立性声明见各自稿内（`…_5ff80e.md` 披露曾见他稿标题与分级标签；`…_747cae.md` 披露检索辅助但逐条复核）。dsh 两稿按其稿内要求**按两个样本交叉去重**。
- 非独立补充：zcode 会话 `6f44a7` 的 issue 评论（I1/I2 + 面确认）已并入 R19/R14，不计独立源数。

### 6.2 汇聚核验（本稿对承重结论的抽样重验，main `7fce1286`）

✅ 实证通过：`agent_api.py:796` 路由与 `/api/v1/agent/heartbeat` 零调用方（客户端打 `/api/v1/heartbeat`，`agent/heartbeat.py:84`；`heartbeat_thread.py:27` "SOLE authority"）；claim 门 `agent_api.py:396-409`（ONLINE 门 + 非 ONLINE 分支无日志）；`heartbeat.py:199-206` 按 IP 找回、`:236` 如实写 status、`:249-262` extra 白名单重建、`:403` 无条件 re-home；`_FATAL_DISPATCH_REASONS`（`plan_dispatcher_sync.py:64-68` 注释原文确认 retryable 语义）；`admission_pump.py:289-293` 「Clean competition requeues do NOT consume…」；`notification_service.py:343-345` 短路、`:649-654` SAQ 键；`models/notification.py:16-20`；`models/host.py:41` ip unique；`session_watchdog.py:43-48` 只扫 ONLINE；`plan_dispatcher_core.py:84-88` 冻结快照原文；`host_upgrade_gate.py:229-231` 仅存在性；`batch_hot_update.py:89-95` ONLINE 选靶；`cron_scheduler.py:27` 3 天；ADR-0025 零命中；`HostsPage.tsx:107`、`hosts.ts:24-25`；`test_host_identity.py:22` 后缀用例存在；`agent/identity.py:21-42` boot_id 每开机随机；R18 单源化已合入。

未复验而按多源一致采信的：`stats.py:286-298` raw SQL、`HostOut` 缺字段、`socketio_server.py` 房间注册、`_delivery_identity` 四元组等（≥3 稿独立给出一致 file:line）。按 5ff80e 方法论提示，任何未标 file:line 或无法复核的条目已降级或剔除。

## 7. 交接

1. **人工裁决**：§4 D-1～D-6（每条已给推荐）；裁决可直接批注本 PR 或 issue #1557。
2. **ADR v0.2**：按 §3（直接改）+ §5（裁决后改）修订；同批补 `docs/adr/README.md`/`docs/DOC-MAP.md` 登记。修订 PR 建议由单一 Execution 承担并 `declare --issue 1557`。
3. **实现单**：ADR Accepted 后另开（ADR Accepted ≠ 实施启动）；建议按 5ff80e-S13 拆分为 ①迁移+模型+往返测试 ②retire/unretire API+审计+前置 ③列表/详情/统计/gauge 过滤 ④派发（fatal 分类）/claim/scan-scope/WS 收口 ⑤两条心跳的保持退役+告警去重 ⑥前端（徽标/批量 skip 枚举/共享 query key）；验收矩阵可直接采用 182d4e-F7 的 10 场景表与逐点 mutation 纪律。

Refs: #1557 · #796 · #937 · PR #1534 · ADR-0025/0026/0035/0036
