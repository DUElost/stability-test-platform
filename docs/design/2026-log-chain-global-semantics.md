# 终端设备日志链 · 全局语义

- **状态**：Living（语义汇总；不新增决策）
- **读者**：拍板 ADR-0033 Phase 2 A/B/C 前需要「整条链是什么」的人；不要求读完全部 ADR
- **权威关系**：行为细节以代码与测试为准；Accepted ADR 与 [`2026-scan-upload-merge-contract.md`](./2026-scan-upload-merge-contract.md) 为内容权威；本文是**可引用汇总**（填 ownership X2「缺可引用汇总」），不新增决策
- **关联**：ADR-0025 / 0027 / 0028 / 0032 / 0033；[`2026-semantic-ownership.md`](./2026-semantic-ownership.md) X2；[`2026-09-18-adr0033-phase2-unisoc-merge-blocker.md`](../notes/architecture/2026-09-18-adr0033-phase2-unisoc-merge-blocker.md)；#745 / #2546 / #463
- **日期**：2026-09-19

---

## 1. 一句话：是什么 / 不是什么

**是什么**：把设备上的异常日志，经 Agent 主机预处理，按需上送中心存储，再由控制面合并与提取，最终形成可追踪事件台账与可交付产物（xls / jira 束）的端到端能力。

**不是什么**：

| 不是 | 说明 |
|------|------|
| 不是「一个叫 scan 的黑盒」 | 采集、主机汇总去重、多 host merge、extract 归档是**不同阶段、不同宿主、不同工具 argv** |
| 不是厂商工具本体 | 平台自研的是 DLE / log_signal / 分区完备性 / merge 编排 / 状态机；`start_log_scan` / `scan_log_gt` / `scan_result` 是**外置 CLI** |
| 不是运行日志链路 | Agent SSD 上的 `logs/runs/{job_id}/` **永不**上中心（ADR-0025） |
| 不是已落地的 Tool Contract / DedupMergeEngine | ADR-0033 Phase 2/3 **零适配器代码**；现态仍是私有 env + 直接 argv |

---

## 2. 端到端阶段图

两条**互补**层（ADR-0032 D2）：**Watcher 实时**（跑测中）与 **归档终态**（scan→upload→merge→extract）。共享路由键 `device.platform`，**不共享工具**。

```text
┌─ 设备 ─────────────────────────────────────────────────────────────┐
│  MTK: /data/aee_exp (+ vendor)     UNISOC: /data/ylog/uniview_*   │
└───────────────┬───────────────────────────────────┬───────────────┘
                │ Watcher 拉取                       │ （归档时再扫 HDD）
                ▼                                   │
┌─ Agent HDD（第一落点 / 预处理） ──────────────────┤
│  事件目录 + 本机 scan 工作区                        │
│  Watcher → job_log_signal + DLE(LOCAL…)            │
│  归档: MTK ScanRunner / UNISOC UnisocScanRunner    │
│        → host 级 *_org.xls → UploadManager         │
└───────────────┬───────────────────────────────────┘
                │ 中心：xls → dedup/{run}/{mtk|unisoc}/
                │       事件 → devices/{run}/（EventUploader）
                ▼
┌─ 控制面 + 中心存储 ───────────────────────────────────────────────┐
│  SAQ: scan_task → upload_task → merge_task → extract_task         │
│  merge: start_log_scan -merge_files_list（两平台同一工具）         │
│        → dedup/{run}/merge/{mtk|unisoc}/                          │
│  extract → jira/{run}/  +  DLE REMOTE→ARCHIVED                    │
└───────────────────────────────────────────────────────────────────┘
```

**串行约束（UNISOC，ADR-0032 D8）**：Job RUNNING 只跑 Watcher；终态/`scan_now` 只跑归档 runner——**禁止**两路径并发写同一目录。

---

## 3. 分阶段：谁执行、工具、I/O、事实权威

### 3.1 Watcher 实时（跑测中）

| | MTK | UNISOC |
|--|-----|--------|
| **执行方** | Agent `AeeDbHistoryReconciler`（+ inotifyd 兜底） | Agent `UnisocUniviewReconciler` |
| **模块** | `backend/agent/aee/`；`job_session` 按 `device.platform` 路由 | 同左 + `unisoc_reconciler` |
| **输入** | 设备 `db_history` / AEE 目录 | uniview / dropbox·ylog 增量 |
| **输出** | HDD 事件目录；`job_log_signal`；DLE `DETECTED→LOCAL` | 同形；`event_type=UNIVIEW` |
| **事实权威** | **信号流**：`job_log_signal`（ADR-0018）；**事件台账**：`device_log_event`（ADR-0028）；文件在 HDD | 同左 |
| **权威文档** | ADR-0028；`aee/AGENTS.md`；ADR-0032 D8/B5 | 同左 |

QCOM：无 Reconciler / 无采集实现；控制面标 `reconciler_supported=false`（ADR-0032 D9）。

### 3.2 归档·采集（host 级扫 HDD / 问题包）

触发：PlanRun 终态（含 FAILED）或手动/`auto_archive` → 控制面 `scan_now` → Agent。

| | MTK | UNISOC |
|--|-----|--------|
| **执行方** | Agent `ScanRunner` | Agent `UnisocScanRunner` |
| **工具** | `start_log_scan.py -m 0 -d {staging} -side …`（**不是** `-dedup_org`） | `scan_log_gt.py -m sprd`（Monkey-Log-Scan-GT-SPRD） |
| **env** | `STP_DEDUP_SCAN_*` | `STP_UNISOC_LOG_SCAN_*` |
| **输出** | 本机 `Result_*_org.xls`（扫描产物） | 问题包目录（供下一步汇总） |
| **事实权威** | 文件落 Agent 本机；尚未是中心权威 | 同左 |

### 3.3 归档·主机汇总去重（仍是 Agent，单 host）

| | MTK | UNISOC |
|--|-----|--------|
| **工具** | 同一 `start_log_scan` 的 `-dedup_org`（`ScanRunner.run_dedup_org`） | **`scan_result.py -d <dir>`**（Scan-Result-GT） |
| **env** | 同 `STP_DEDUP_SCAN_*` | `STP_UNISOC_SCAN_RESULT_*` |
| **语义** | 对本机已产出的 `_org.xls` 再去重 | 对第一阶段保存根做汇总 → `*_org.xls` |
| **与 merge 的关系** | **等价位次 ≠ 同一阶段**：这是 **host 汇总**，不是控制面多文件 merge | **GT ≡ MTK 的 `run_dedup_org`**（ADR-0032 D7），**不是** `-merge_files_list` |

### 3.4 上送（xls 与事件目录是两条路）

| 对象 | 执行方 | 落点 | 权威 |
|------|--------|------|------|
| scan / 汇总 xls | Agent `UploadManager` | `dedup/{run_id}/{mtk\|unisoc}/{host_id}_*.xls` | 中心文件 + 控制面 `plan_run_artifact`（`run_scan_sync` 登记） |
| 事件目录 | **仅** Agent `EventUploader` | `devices/{plan_run_id}/`（或 spill 路径） | DLE：`UPLOAD_PENDING→REMOTE`；控制面 `upload_task` **只标状态、不拷贝** |

过滤模型（ADR-0025 / 0028）：CIFS 只收 scan 引用的有效子集 + HDD≥95% 溢出；**无**「连续全量上送」。

实现层差异（**不构成 DLE 状态机分叉**，ADR-0032 D10）：MTK 由 `upload_task` 读 xls Path 列精选；UNISOC 可入库即提升 `UPLOAD_PENDING`（#1957）。

### 3.5 控制面 merge（多 host → 分区总表）

| | 内容 |
|--|------|
| **执行方** | 控制面 `dedup_scan.run_merge_sync` / `run_merge_all_platforms_sync`（SAQ `merge_task` 或手动 API） |
| **工具** | **两平台同一**：`STP_BACKEND_DEDUP_SCAN_*` → `start_log_scan.py -merge_files_list {listfile} -side …`（ADR-0032 D3，B3 已验） |
| **输入** | 中心 `dedup/{run}/{mtk|unisoc}/` 下本轮 `*_org.xls`（分平台各一次 merge） |
| **输出** | 本机工具目录中转 → 发布 `dedup/{run}/merge/{platform}/`；登记 `merge_result_xls` |
| **事实权威** | 中心路径 + `plan_run_artifact`；`run_context.merge_platforms` 记逐平台 ok/skip |
| **FAILED** | 自动链 `skipped_failed`；手动可 `allow_failed`（ADR-0028 D2 / #697） |
| **多实例** | merge 为本机 `flock` + 本机工具目录 → **实例绑定**（ADR-0027 清单第 7 条） |

### 3.6 extract 与 DLE 终态归档

| | 内容 |
|--|------|
| **执行方** | 控制面 `dedup_extract.run_extract_sync`（SAQ `extract_task`） |
| **输入** | 必须有本轮 `merge_result_xls`；否则 `dedup_extract_skip_no_merge` |
| **输出** | `jira/{plan_run_id}/` 产物束 |
| **DLE** | **唯一** `REMOTE→ARCHIVED` 写入点 = extract 内 `mark_events_archived`；MTK/UNISOC **同义同判**（D10） |
| **口径** | 无 `merge_result_xls` ⇒ 停在 `REMOTE` 是**事实终态**，不是卡住 |

### 3.7 SAQ 链（控制面编排）

```text
scan_task → upload_task → merge_task → extract_task
```

完备性单位：**(host, platform) 对**（`scan_completeness`），不是「每 host 双平台齐」。细节见 scan-upload-merge 契约。

---

## 4. 易混概念对照表

| 概念 | 宿主 | 做什么 | 不是什么 |
|------|------|--------|----------|
| **采集（Watcher）** | Agent | 设备→HDD；发 signal / 建 DLE | 不是出 `_org.xls`；不是 merge |
| **采集（归档 scan）** | Agent | 扫 HDD/问题包，为汇总准备输入 | MTK 的 `-m 0` ≠ `-dedup_org` ≠ `-merge_files_list` |
| **导出 / 上送** | Agent UploadManager + EventUploader | xls→`dedup/`；事件→`devices/` | 控制面 `upload_task` 不 copy 文件 |
| **主机去重 / 汇总** | Agent | MTK `-dedup_org`；UNISOC **Scan-Result-GT `-d`** | **不是**控制面多 host merge |
| **Scan-Result-GT** | Agent（现态） | `scan_result.py -d <dir>` → host `*_org.xls` | **无**公开多文件 merge argv；≠ DedupMergeEngine 现成 CLI |
| **控制面 merge** | 控制面 | `-merge_files_list` 合并多 host `_org.xls` | 不是 GT；不是 Watcher；不是 extract |
| **`start_log_scan` 三种用法** | 看 argv | `-m 0` 本机扫描；`-dedup_org` host 再去重；`-merge_files_list` **仅控制面**多文件合并 | 同一二进制、**三种语义** |
| **dedup（口语）** | 含糊 | 常混指「整条归档链」或「某次去重」 | 精确场合应说：host 汇总 / 控制面 merge / 路径族 `dedup/` |
| **extract / ARCHIVED** | 控制面 | 按 merge 引用抽事件进 jira 束并标 ARCHIVED | 「上送即归档」已否决（D10） |
| **log_signal vs DLE** | 控制面 DB | signal = 异常事件**流**；DLE = 事件生命周期**台账** | 风险计数以 DLE 为主、未链接 signal 为补充 |

**Phase 2 混淆的一句话**：ADR-0033 文案曾把「unisoc 分区的 `DedupMergeEngine`」写成挂 **Scan-Result-GT**——但现态 GT 只做 **Agent 主机汇总**，控制面 merge 已由 **同一 `start_log_scan -merge_files_list`** 承担（D3）。二者叠成一个接缝即冲突。

---

## 5. 与 ADR / ownership（X2）的映射

ownership 索引（`2026-semantic-ownership.md`）**X2**：日志域四层权威**并存且都对**——缺的是可引用汇总（本文填此位）。

| X2 层 | 回答什么 | 权威锚 | 本文对应 |
|-------|----------|--------|----------|
| **DLE 台账** | 事件在哪、什么状态 | ADR-0028「唯一权威记录」→ `dle-record` | §3.1 / §3.6 |
| **log_signal 流** | 异常事件权威流 | ADR-0018 → `log-signal-stream` | §3.1 |
| **dedup 行为与分区** | 并列流水线、禁止混工具 | ADR-0032 D1 → `dedup-pipeline-behavior` | §2–§3、§4 |
| **中心存储 / 三阶段布局** | 搬运 + 汇总去重 + 分类提取 | ADR-0025 D4 → `center-storage-model` | §3.4–§3.6 |
| **merge 执行位置** | 控制面实例绑定 | ADR-0027 第 7 条 → `R-merge-locus` | §3.5 |
| **merge 消费关系** | merge 消费谁的产物 | scan-upload-merge 契约 → `R-merge-consumes-log` | §3.5 |
| **工具宿主分层** | Tier1/2/3 | ADR-0033 D1 → `R-tool-hosted-by-tier` | 采集=Tier2；merge=Tier1 |
| **跨进程契约** | SAQ / 完备性 / 路径 | `2026-scan-upload-merge-contract.md` | §3.7 |

**产品类 B（终端日志链）**：平台深嵌自研核心（DLE、编排、状态机）；厂商 CLI **不入仓**；Adapter 仅为薄接缝（尚未落地）。

---

## 6. Phase 2 A/B/C：语义前提（不拍板）

背景：阻塞笔记结论——停做控制面 unisoc/`Scan-Result-GT` 的 `DedupMergeEngine`，待选出口。

| 选项 | 语义上意味什么 | 与现态权威的关系 |
|------|----------------|------------------|
| **A** | Phase 2「第一个 DedupMergeEngine」= 包一层**现态控制面 merge CLI**（`start_log_scan -merge_files_list`）；GT **继续只做** Agent host 汇总 | **行为不变**；需改 ADR-0033 措辞（样板=ACL 接缝，工具仍 D3）。与 B3/D3 **一致** |
| **B** | unisoc 控制面改用**独立** merge 工具（可能扩展 GT 或多文件契约） | **修订 ADR-0032 D3**；现态 GT **仍无** `-merge_files_list`，须先有上游契约再动控制面；可能引入 `STP_BACKEND_UNISOC_MERGE_*`（曾作条件分支，B3 通过后未启用） |
| **C** | Phase 2 样板改挂 **Agent 侧** GT Adapter（包 `UnisocScanRunner`→`scan_result -d`） | 兑现「GT 适配器」字面；**不**兑现「插在 per-platform merge 循环内」；控制面 merge 路径不动 |

```text
         Agent host 汇总              控制面多 host merge
    ┌─────────────────────┐      ┌──────────────────────────┐
    │ MTK: -dedup_org     │      │ 两平台: -merge_files_list │
    │ UNISOC: GT -d       │ ───► │ （D3 同一工具）            │
    └─────────────────────┘      └──────────────────────────┘
         ▲ C 挂这里                    ▲ A 挂这里
                                         B = 拆掉「同一工具」、unisoc 另挂
```

**选之前只需确认**：你要防腐的接缝是「控制面 merge」还是「Agent 上的 GT」，还是愿意改 D3 让 unisoc merge 换工具。

---

## 7. 非目标与已知过渡例外

**本文 / 当前拍板前明确不做**

- 不实现 Adapter / `DedupMergeEngine` / 包存储
- 不改 `run_merge_sync` 行为
- 不替用户选 A/B/C

**已知过渡例外（ADR-0033 §5.4）**

| 例外 | 形态 | 边界 |
|------|------|------|
| 展锐三族 | 中心 `tools/{name}/` **未打包源码目录** + 私有路径 env | 仅这三族；不得扩散；不得再增工具私有 env 键 |
| 族名 | `Start-Log-Scan` / `Monkey-Log-Scan-GT-SPRD` / `Scan-Result-GT` | 终态仍是 Package Store（触发条件见 0033） |

**其他已知边界**

- merge 多实例无跨路径互斥（0027 §7）——登记限制，非本汇总可解
- QCOM 无采集实现
- 列名归一（UNISOC→MTK 表头）靠 merge 工具内部行为；漂移时 WARNING、不阻断发布（契约 #2256）

---

## 8. 权威索引（速查）

| 主题 | 文档 / 代码 |
|------|-------------|
| 并列双轨 + D3 同一 merge | [ADR-0032](../adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md) |
| 过滤上送 + DLE | [ADR-0025](../adr/ADR-0025-phase4-architecture-alignment.md)、[ADR-0028](../adr/ADR-0028-device-log-event-and-continuous-upload.md) |
| merge 实例绑定 | [ADR-0027](../adr/ADR-0027-control-plane-horizontal-scaling.md) 清单第 7 |
| 工具结构 / Phase 2 阻塞 | [ADR-0033](../adr/ADR-0033-tool-kit-ecosystem-integration.md) v1.4；[阻塞笔记](../notes/architecture/2026-09-18-adr0033-phase2-unisoc-merge-blocker.md) |
| 跨进程契约 | [`2026-scan-upload-merge-contract.md`](./2026-scan-upload-merge-contract.md) |
| 上送时序图 | [`2026-adr-0025-log-flow-sequence.md`](./2026-adr-0025-log-flow-sequence.md) |
| ownership X2 | [`2026-semantic-ownership.md`](./2026-semantic-ownership.md) §1 |
| Agent scan | `backend/agent/scan_runner.py`、`unisoc_scan_runner.py`、`upload_manager.py`、`event_uploader.py` |
| 控制面 | `backend/services/dedup_scan.py`、`dedup_extract.py`、SAQ tasks |

**冲突处理**：代码/测试与本文或 ADR 散文不一致时，以代码与测试为准，并回写权威文档。
