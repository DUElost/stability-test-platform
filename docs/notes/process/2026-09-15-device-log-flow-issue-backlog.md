# 设备日志流转待改善项：载体分层与 issue 台账

Status: proposed
Class: process

## Decision

2026-09-15 设备日志流转四层评估（前端 / 控制面 / Agent / 中心存储）产出的待改善项，**按载体分层**而不是打包成一份文档：

| 载体 | 判据 | 本台账条目 |
|---|---|---|
| **既有 ADR 修订** | 改变跨进程契约 / 系统边界，且已有 ADR 覆盖同一语义 | R2–R4 → [ADR-0032 v0.8 修订提案](../architecture/2026-09-15-adr0032-v08-platform-routing-revision.md)（R1 已由「改代码对齐 B1」关闭，见 I-2） |
| **缺陷 issue（可直接修）** | 实现偏离已定决策，无需新裁决 | ~~I-3、I-4~~ **均已实施**（2026-09-15 裁决后） |
| **验证类（不依赖裁决）** | 先取证再决策 | I-5 |
| **可观测 / 前端 issue** | 不改契约 | I-1（已实施）、I-6、I-7、I-8 |
| **门禁** | 可机器查，优先做强制力而不是写文档 | I-9 |
| **文档漂移 issue** | 事实已定，表述过期 | I-10、I-11 |
| **方向级（非本 ADR 域）** | 跨 ADR 的存储/执行位置选择 | I-12、I-13（**已裁决**：D → A / 先 A，见[提案](../architecture/2026-09-15-center-storage-and-merge-locus-proposal.md) §裁决记录） |

分层理由与"为什么不新立一份统一治理 ADR"：见 [ADR-0032 v0.8 提案](../architecture/2026-09-15-adr0032-v08-platform-routing-revision.md) §Alternatives 与本文 §Alternatives。

**提交纪律**：本台账各项按仓库执行契约领单——`python tools/dev/ai_work.py status` 前检后 `declare`；**决策类条目必须 `declare --issue <n>`**（契约 v1.8「决策实体唯一性」）；每条 issue 提交前先查重（同主题已有 #1050 / #735 / #73 / #220 等承载者，见 I-11、I-3 备注）。

**本台账不含**：多站点交付（属 [ADR-0041](../../adr/ADR-0041-independent-site-delivery-and-management.md)，与日志链正交）；I-12 / I-13 的具体方案（已立项，见[方案提案](../architecture/2026-09-15-center-storage-and-merge-locus-proposal.md)，本台账不预设选型）。

---

## A. 依赖 ADR-0032 v0.8 裁决后实施（I-2 已关闭，剩 I-1 / I-3 / I-4）

### I-1 多平台 merge 结果不可观测（**原判「缺陷 / P1」有误，已更正并实施**）

> **2026-09-15 更正**：初稿称「`any_ok` 吞掉单平台真失败」——**不成立**。`run_merge_sync`
> 在工具/校验/发布真失败时全部 `raise`（`backend/services/dedup_scan.py:402,405,419,425,431,455`），
> `run_merge_all_platforms_sync` 不捕获异常、直接向上传播，`merge_task`/手动端点据此失败收敛。
> 空串只来自「工具未配置」与「该平台本轮无 org 文件」两种正常情况。真实缺口只是**逐平台结果
> 无处可查**。

- **类型 / 严重度**：可观测 / P3（原判缺陷 / P1 有误，已下调）—— **已实施**
- **前置**：无（纯增量，无需 ADR 裁决）
- **证据**：`backend/services/dedup_scan.py:461-494`（聚合只回字符串，无逐平台记录）
- **期望 / 已实施**：逐平台 `ok` / `skipped_failed` / `no_input` 写
  `run_context.merge_platforms`，并打 `merge_platforms plan_run=… mtk=… unisoc=…` 日志；
  返回字符串与控制流不变，记录失败不影响 merge 结论
- **验收**：`test_run_merge_all_platforms_records_per_platform_outcomes` /
  `test_run_merge_all_platforms_records_skipped_failed`；
  `test_dedup_scan_merge.py + test_saq_tasks.py + test_dedup_scan_endpoints.py` → 98 passed
- **未做**：前端展示 `run_context.merge_platforms`（属展示面，未纳入本轮）

### I-2 完备性要求每 host 双平台齐，纯平台 host 永不计数 —— **已关闭（2026-09-15）**

> **裁决与实施**：owner 裁决「改代码对齐 B1」，同日实施完成。`require_platforms`
> 退役，改为 `dedup_scan.scan_completeness(run_id, expected)` 的 (host, platform) 对判定，
> 期望集由 `plan_run_scan_scope.load_expected_scan_platforms` 按各 host 的设备平台构成派生。
> 证据与用例见 [`2026-09-15-scan-completeness-per-host-platform.md`](../bug-fix/2026-09-15-scan-completeness-per-host-platform.md)。
> **残留**：混平台 host 只配一个平台工具时仍会等到超时（需 per-host 能力声明，已在该 note 的
> Revisit 登记触发条件）；真实 fleet 观测仍缺。以下保留为原始问题记录。

- **类型 / 严重度**：缺陷 / P1（已修）
- **前置**：ADR-0032 v0.8 R1 —— **已由「改代码对齐 B1」关闭，不走 ADR 修订**
- **证据**：`backend/services/dedup_scan.py:186-206`（`require_platforms` 逐 host 要求平台子集，已移除）；调用点 `backend/tasks/saq_tasks.py:450-454,482-486`（已改为单位口径）；Agent 侧 `backend/agent/scan_runner.py:246-277`（两个 runner 都跑、各自按 serial 过滤）
- **现象 / 影响**：纯 MTK host 的 UNISOC 工具扫不到 uniview 目录 → 无 unisoc 产物 → 该 host 永远不计入 `hosts_done`；每轮 `scan_task` 烧满轮询预算（默认 300s + grace）后记 `saq_scan_partial_artifacts` WARNING。不丢数据（后继仍链），但轮询预算被浪费且**真实缺口会被假警报掩盖**
- **期望**：期望集 = `{(host_id, platform) | 该 host 在本 PlanRun 持有 ≥1 台该平台设备}`，按 (host, platform) 判完备；保留"部分未齐仍链后继"语义
- **验收**：三型 host 用例 + 期望集派生用例已落 `test_dedup_scan_merge.py` /
  `test_plan_run_scan_scope.py`（80 passed；扩大 233 passed）；真实 fleet 观测未做

### I-3 QCOM「路由到空」缺少未支持态归因 —— **已实施（2026-09-15，R4-b b1 + b3）**

- **类型 / 严重度**：缺口（可观测） / P2 —— **已修**
- **前置**：ADR-0032 v0.8 R4-b —— **已裁决**（b1 控制面派生为主、b3 Agent 留痕为辅；b2 留作升级路径）
- **实施**：
  - **b1（控制面 + 前端）**：`core.dedup_platform.has_collection_impl` 新增；`WatcherPlatformBucketOut.reconciler_supported`
    （`backend/api/schemas/plan_run.py`）由 `_aggregate_watcher_platform_buckets` 填充（`plan_runs.py`）；
    前端 `types.ts` 的 `WatcherPlatformBucket` 增字段、`AnomalyDashboard` 平台分桶对
    `reconciler_supported === false` 渲染「平台未支持」而不是「信号 0 · 设备 0」。
  - **b3（Agent）**：`job_session._maybe_start_aee_reconciler` 在 `reconciler_cls is None` 时打
    `platform_reconciler_unsupported`（对照 UNISOC 的 `platform_reconciler_start_degraded`）。
- **验收**：`test_watcher_summary_platform_bucket_flags_unsupported_platform`（QCOM → `False`，MTK → `True`）；
  `test_has_collection_impl_matches_agent_side_routing`；
  `test_collection_support_is_equivalent_to_dedup_partition_today`（钉住等价性，防静默改义）；
  `test_reconciler_skipped_on_qcom_platform`（caplog 断言）；
  `AnomalyDashboard.test.tsx` 新增用例（11 passed）
- **未做**：b2（Agent 上报 `watcher_capability="unsupported_platform"`）——按裁决留作"要按 Agent 实测而非设备登记判定"时的升级路径

### I-4 `PlatformCollector.detect()` 死接口收口 —— **已实施（2026-09-15，R4-a a1）**

- **类型 / 严重度**：债务 / P2 —— **已修**
- **前置**：ADR-0032 v0.8 R4-a —— **已裁决 a1**
- **实施**：协议收窄为 `platform` + `parse_metadata`（`backend/agent/aee/collector.py`）；MTK / UNISOC / QCOM
  三个实现的 `detect` 与相关 `Callable`/`Optional` 导入一并删除；实现规格
  `docs/design/2026-device-log-event-implementation-spec.md` §5.1/§5.2/§5.4 同步。
- **验收**：`test_collector_protocol_declares_no_detect`（新增，钉住"不得复活"：协议与三个实现均无 `detect`）；
  `test_get_collector_qcom_is_stub_only` 已移除 detect 断言；`grep -rn "\.detect(" backend/` 无 collector 命中。

### I-4 `PlatformCollector.detect()` 死接口收口

- **类型 / 严重度**：债务 / P2
- **前置**：ADR-0032 v0.8 R4-a
- **证据**：`backend/agent/aee/collector.py:36`（协议定义）；三个实现 `collectors/mtk.py:20`、`collectors/unisoc.py:149`、`collectors/qcom.py:14`；全仓零调用点——平台判定实际走 `backend/agent/device_platform.py:98`（调用点 `backend/agent/job_session.py:339`）
- **期望**：删除 `detect()`，协议收窄为 `platform` + `parse_metadata`；平台判定权威唯一化为 `detect_device_platform`
- **验收**：`collector.py` 无 `detect` 符号；`grep -rn "\.detect(" backend/agent/` 无 collector 命中；现有 `backend/agent/tests/test_platform_collector.py` 相应调整

---

## B. 验证类（不依赖裁决，可立即启动）

### I-5 ADR-0032 B3 分平台 merge spike 未执行

- **类型 / 严重度**：验证缺失 / P1
- **证据**：`docs/adr/ADR-0032-...md:140-146` 五项全为 `[ ]`；实现侧 `backend/services/dedup_scan.py:325` 对所有平台只读 `STP_BACKEND_DEDUP_SCAN_*`；UNISOC org xls 为 15 列 `aeeexp`（`ADR-0032:24`），与 MTK 报表不同构
- **现象 / 影响**：ADR 记「同一 merge 工具」为已决事项，但**无任何验证证据**；该假设若为假，UNISOC 报表会静默为空（并被 I-1 的聚合掩盖）
- **期望**：执行 B3 五项验收；通过 → 证据入库；失败 → 触发 ADR-0032 v0.8 R3 的条件分支（引入 `STP_BACKEND_UNISOC_MERGE_*`）
- **验收**：UNISOC org xls 进 merge 且 `dedup/{run}/merge/unisoc/` 有产物；`jira/{run}/merge/unisoc/` 有产物；无 `_org` 后缀产物不进 merge glob

---

## C. 前端与可观测（可直接修，不改契约）

### I-6 DLE 终态视图缺平台维度与失败态

- **类型 / 严重度**：可观测 / P2
- **证据**：`frontend/src/utils/api/types.ts:1857`（`PlanRunLogEvent.platform` 已存在，后端 `backend/api/routes/plan_runs.py:2328` 也返回）；`frontend/src/components/plan-run/LogEventsCard.tsx:99-105`（表头无平台列）、`:34-40`（`STATE_CHIP` 缺 `UPLOADING` / `UPLOAD_FAILED` / `PULL_FAILED` / `PRUNED`，全部落到 muted 灰底）
- **期望**：表格增平台列；补齐四个状态的语义色；按 B5「UI 按 platform 分桶」语义可加平台筛选
- **验收**：前端用例断言平台列与四态 chip

### I-7 上送缺口原因未在前端露出 + TS 类型与后端不同步 —— **主诉已实施（2026-09-15）**

- **类型 / 严重度**：可观测 / P2（原因已可见；余下为下钻能力）
- **证据（原始）**：后端写 `upload_summary` 含 `mark_ready` / `events_ready` / `incomplete_reason` / `compensation`（键源 `backend/services/device_log_event.py:107-149`，`backend/tasks/saq_tasks.py:864-897` 追加）；前端 `RunContextUploadSummary` 无这四个字段，展示只取 pending/failed/remote（`DedupReportCard.tsx`）
- **一处事实更正**：这四个键落在 **`run_context.upload_summary`**，**不是** `run_context.archive`（后者是 scan 完备性，`dedup/status` 返回）。此前评估报告把两者混为同一响应，此处以代码为准。
- **已实施**：
  - `types.ts` 的 `RunContextUploadSummary` 补齐四字段；
  - `DedupReportCard` 在 `ready === false` 时渲染原因：已知码 → 人话（`upload_mark_timeout`→上送标记未确认 / `upload_events_pending`→事件仍在途 / `merge_skipped_failed_plan_run`→PlanRun 未成功，跳过合并）；**未知码原样露出**（不静默吞掉后端新增原因），`title` 保留原始码；
  - 用例：`DedupReportCard.test.tsx` 两条（已知码 / 未知码）。
- **仍未做**：`extract.missing` 仍只有计数、无缺失目录名清单（原「期望」第 3 条，需后端加清单字段）。
- **验收对照**：`ready=false` 展示 reason ✅；types.ts ↔ 后端字段一一对应 ✅（两处写点均已核）。

### I-8 手动扫描缺少"最终轮"入口 + 归档状态分散三卡

- **类型 / 严重度**：可观测 / P3
- **证据**：`frontend/src/components/plan-run/DedupReportCard.tsx:102`（`scanMut.mutate(false)` 固定 `is_final=false`）；状态分散在 `ArchiveStatusCard.tsx:95-99`（只有一个 `Scan: {scanStatus}` 字符串）、`DedupReportCard`、`LogEventsCard` 三处
- **期望**：补 `is_final` 开关；评估三卡收拢为一张"归档流水线"纵表（阶段 × 平台 × 完成度）
- **验收**：存在触发最终轮的入口；三卡信息可在一处读完

---

## D. 门禁（复利杠杆，优先）

### I-9 `types.ts` ↔ 后端 schema 无强制力 —— **部分实施（2026-09-15，新增轴线 C）**

- **类型 / 严重度**：契约强制力缺失 / P2 —— **Pydantic 侧已收口，手搓 dict 侧仍开**
- **证据**：`docs/design/2026-08-governance-surface-protection.md:136` 记为 **residual（review 兜底）**；`AGENTS.md` 硬不变量「前端 API 类型以 `types.ts` 为入口，并与后端 schema 同步」此前无人强制
- **实施**：在既有契约测试 `tests/test_api_response_shape_contract.py`（#2129）中新增**轴线 C**：
  `Pydantic 响应模型 ↔ TS 接口` 双向对拍（`_MODEL_PAIRS` + `_pydantic_model_fields`），登记 5 对：
  - watcher-summary 链：`WatcherSummaryOut`↔`WatcherSummary`、`WatcherPlatformBucketOut`↔`WatcherPlatformBucket`、
    `WatcherCategoryOut`↔`WatcherCategory`（**含刚新增的 `reconciler_supported`，并设 canary 用例**）；
  - 日志链：`PlanRunLogEventOut`↔`PlanRunLogEvent`、`PlanRunLogEventsOut`↔`PlanRunLogEventsPayload`。
- **强制力**：根 `tests/` 离线子集已在 `scripts/run_gates.py:227-236`（对应 CI `pr-agent-tests`），故新用例**已在 CI 生效**。
- **红绿双向已验**：临时从 `types.ts` 删除 `reconciler_supported` → `test_model_fields_are_declared_in_ts`
  与 canary 双双报出该字段名；还原后 12 passed。
- **跟进（同日第二段）**：`GET /plan-runs/{id}/dedup/status` 的"手搓 dict"边界已**从根上消掉**——
  该端点正规化为 `response_model=ApiResponse[DedupStatusOut]`（新增 `DedupStatusOut` /
  `DedupArtifactOut` / `DedupScanArchiveOut`），随之纳入轴线 C 登记（3 对），前端
  `getDedupStatus` 也由匿名内联类型改为具名 `DedupStatusPayload`。
  取证要点：`DedupScanArchiveOut` 必须 `extra="allow"`——该段是自由 JSONB，Pydantic 默认会
  **静默丢弃**未声明键（比不建模更糟），已加"未知键必须透传"用例并做红绿双向验证。
- **仍未覆盖（诚实边界）**：
  - 其余 `response_model=ApiResponse[dict]` + `ok({...})` 端点：`dedup.py` 内另有 6 处
    （scan / merge / extract / reload-config / jira-run 等）。AST 判据只认 ``return <Dict>``，
    识别不到包在 `ok(...)` 里的字典字面量——**逐条正规化**比扩展正则更可靠（本端点即为样板）；
  - `Field(alias=…)` 未处理（登记前提是"无别名"）；
  - 跨文件基类会让解析器**显式报错**而非静默少收字段。
- **原始验收对照**：`GET /plan-runs/{id}/log-events` ✅；`GET /plan-runs/{id}/dedup`（`archive`/`scan_failed`）✅。

---

## E. 文档漂移

### I-10 `backend/agent/aee/AGENTS.md` UNISOC 监测目录过期

- **类型 / 严重度**：文档漂移 / P3
- **证据**：`backend/agent/aee/AGENTS.md:52` 写 UNISOC 监测目录为 `/data/uniview` + `/data/vendor/uniview`；权威根已改为 `/data/ylog/uniview_exception`（`backend/agent/aee/collectors/unisoc.py:39`，2026-09-14 真机实测，附 JSONL 与 normalboot 丢弃说明）
- **期望**：文档改为权威根，并注明 `unievent_info`（无 `.json`、JSONL）与 normalboot 丢弃
- **验收**：文档与 `collectors/unisoc.py` 的 `UNIVIEW_ROOT` / `UNIVIEW_INFO_FILENAME` 常量一致

### I-11 实现规格与代码漂移 9 处（归 #1050，不重复立单）

- **类型 / 严重度**：文档漂移 / P3
- **证据**：`docs/reviews/REVIEW_CROSS_REGION_CHAIN_B_2026-09-13.md:283`（F-B3 清单）+ `:179-181`
- **处置**：**不新立 issue**——#1050（R09-F09「规格描述已替代契约」）已跟踪。本条目仅登记为收口输入，附带该项自身的两项待单源化问题：spill 阈值双口径（类默认 95% `backend/agent/local_disk_monitor.py:44` vs `backend/agent/main.py:823` 注入 80%）
- **验收**：F-B3 清单逐条关闭或标注"有意漂移 + 理由"

---

## F. 方向级（非 ADR-0032 域）：已裁决（D-4 = D → A；D-5 = 先 A）

方案与效果判据见
[中心存储与 merge 位置方案提案](../architecture/2026-09-15-center-storage-and-merge-locus-proposal.md)（§1.3 基线、§2/§3 候选、§5 判据、§6 评审清单）。
§1.3 中可在文件系统层只读获得的基线（E-1 事件目录双份 / E-1b merge 报表双份 / E-3 控制面本地
`merge_result/`）**采集工具已就绪**：`backend/scripts/measure_center_storage.py`（只读、stdlib-only、
拒绝危险根、不跟随 symlink；测试 `backend/tests/test_measure_center_storage.py` 16 passed）。
E-2 / E-4 / E-5 仍需另行采集（脚本内已显式列为 not covered）。

### I-12 中心存储结构重排（ADR-0025 域）—— **已裁决：D → A**

- **类型 / 严重度**：方向级 / P2（2026-09-15 owner 裁决；方案与效果判据见
  [中心存储与 merge 位置方案提案](../architecture/2026-09-15-center-storage-and-merge-locus-proposal.md) §裁决记录）
- **裁决**：推进序 **D → A**（先 `_meta/{run}.json` 不动目录树，再做 `dedup/` 拆 `report/scan|merge` + `devices`/`jira` 分 TTL）；B/C 暂缓；**E-1 阈值先采数再定**
- **证据**：
  - 事件目录双份：`backend/agent/event_uploader.py:390-396`（`devices/{run}/{event_id}/{name}/`）vs `backend/services/dedup_extract.py:285-308`（`jira/{run}/{name}/`）——体积最大的一类，直到 retention 才同批清（`backend/scheduler/cron_scheduler.py:246`）
  - merge xls 双份：`backend/services/dedup_scan.py:788-807` vs `backend/services/dedup_extract.py:323-367`
  - `dedup/` 命名承载 merge **输出**（`dedup_scan.py:788`），语义错位
  - **过度收敛**：`devices/` 侧加 `event_id` 消除撞名（#1073），`jira/` 侧又按 basename 合并（`dedup_extract.py:270-274`，#386）——撞名时只保留一份、其余永远停在 REMOTE
- **性质**：改变中心存储契约与生命周期，属 ADR-0025（方案 C 存储模型）域。**本台账不预设方案**，只登记为待裁决输入；若裁决立项，按"决策实体唯一性"先做主题查重，作为 ADR-0025 修订推进
- **参考方向（未裁决）**：`raw/`（短 TTL）· `report/scan|merge/` · `delivery/`（长 TTL）· `_meta/{run}.json`（用 manifest 替代"扫目录推完备性"）

### I-13 merge 执行位置（ADR-0025 / ADR-0033 域）—— **已裁决：先 A**

- **类型 / 严重度**：方向级 / P2（2026-09-15 owner 裁决；方案与效果判据见
  [中心存储与 merge 位置方案提案](../architecture/2026-09-15-center-storage-and-merge-locus-proposal.md) §裁决记录）
- **裁决**：先做 **A（无状态化）**——merge 产物落中心 staging、控制面本地不保留；B 作目标态且**必须与 ADR-0033 一起推进**；
  依赖顺序上 **R3（D-1）先于本项**
- **证据**：`backend/services/dedup_scan.py:344-346`（工具固定输出到控制面本机 `{工具目录}/merge_result/{ts}/`）、`:768-807`（再发布到中心）、`:350`（跨进程 flock `#1072`）
- **性质**：控制面因此成为有状态的处理节点（本地磁盘 + 工具目录 + 锁）。改变此项会触及 ADR-0033 的工具宿主模型，故不并入 ADR-0032 v0.8，单独登记

---

## 已核非缺陷（登记以免后来者误改）

| 项 | 结论 | 证据 |
|---|---|---|
| UNKNOWN 被静默改写为 MTK | **不成立**。路由侧保守放行到 MTK 是有意设计（adb 抖动不应漏采）；但**事实记录正确**——DLE.platform 写探测真值 | `backend/agent/aee/collector.py:56`、`backend/agent/job_session.py:308`、`backend/agent/device_platform.py:11-13`、`backend/agent/aee/device_log_event_client.py:104,266`、`backend/api/routes/agent_api.py:2365,2507,2640` |
| `jira/` 不在 retention 清轨内（链 B F-B1） | **已闭合**。清轨已含 `jira` | `backend/scheduler/cron_scheduler.py:246` |
| scan/upload/spill 被 watcher 开关门控（P2-3） | **已闭合**。已移出 `watcher_subsystem_enabled()` 块 | `backend/agent/main.py:833-866` |
| 启动窗口丢控制命令（P2-2a） | **已闭合**。早注册 handler + 队列回放 | `backend/agent/main.py:808-812,1228-1232` |
| HddSpill 按 mtime 盲删活跃目录（P0-2） | **已闭合**。改为查 `state=LOCAL` 走上传通道 | `backend/agent/local_disk_monitor.py:281-321` |

## 证据缺口（不当作"没问题"）

- 本轮为只读静态评估，**未运行测试**，未接触生产库与中心存储；"纯平台 host 导致 `hosts_done` 长期不足"是基于代码推导的预期行为，**未在真实 fleet 观测确认**（上线前应做 I-2 的三型 host 实测）。
- UNISOC 端到端真机验收（链 B G-B5）仍欠；`collectors/unisoc.py` 的 `/data/ylog/uniview_exception` 修正为 2026-09-14 真机结论，但端到端未验。
- CIFS 写满/慢的整链行为（spill → 上传失败 → 重试 → spill 再入队）无测试（链 B G-B2）。

## Alternatives

- **打包成一份"统一治理" ADR 或大文档**——放弃。ADR-0025 / 0028 / 0032 已覆盖该域方向级决策，第 4 份造成多份权威、复利为负（先例：`2026-09-11-three-question-confirmation-e16d6d.md:38-43`「新建 ADR：放弃」；`2026-09-09-agents-decision-principle.md:39-40`「升格为 ADR：否决——非方向级新决策」）。
- **把全部条目都写成 issue**——放弃。R1/R2 的选择改变跨进程契约（完备性单位、merge 聚合状态机），属方向级，必须走 ADR 修订；用 issue 承载会绕过裁决。
- **把全部条目都写成 ADR**——放弃。前端展示、文档漂移、门禁属任务形内容，进 ADR 必然过期（负复利）。
- **为 `types.ts` 同步引入代码生成器**——暂缓。先扩展 `tests/test_api_response_shape_contract.py` 的既有对拍模式即可获得强制力，生成器是更大的工具链投入，需单独评估。

## Verification

- 本次为**提案与台账**，未运行测试、未修改任何代码、未修改 `docs/adr/`。
- 全部 `file:line` 证据为本次会话亲读；`docs/design/2026-08-governance-surface-protection.md:136` 的 residual 结论、`docs/adr/ADR-0032-...md:140-146` 的未勾选状态、`docs/notes/README.md` 的 note 格式（`Status` / `Class` + 四节）均已逐条核对。
- 台账本身未入任何门禁；`python scripts/run_gates.py check:quick` 需在对应实现 PR 中运行（本提案不产生代码变更，故不构成"验证通过"）。

## Revisit

- ADR-0032 v0.8 若被否决或大幅改写：I-1 ～ I-4 的前置失效，需按裁决结果重写期望与验收。
- B3 spike 结果出来后：I-5 关闭并把证据回填 ADR-0032；若为失败，I-1 的验收需增加"UNISOC 报表可用性"判据。
- `types.ts` 门禁（I-9）若在多个 PR 反复失败：升级为生成器方案，届时按 ADR 级变更处理。
- 中心存储重排（I-12）或 merge 执行位置（I-13）一旦立项：本台账的 A/B/C/D/E 各段需重新评估与 ADR-0025 修订的先后关系，避免与在窗 Execution 冲突。
