# ADR-0033 Phase 2 样板阻塞：unisoc `DedupMergeEngine` ≠ Scan-Result-GT CLI

Status: proposed
Class: architecture

## Decision

**停做 Phase 2 控制面 `DedupMergeEngine`（unisoc / Scan-Result-GT）样板**——在下列未决之一拍板前，不落适配器代码、不改 `run_merge_sync` 调用路径。本笔记只登记阻塞与可选出口，不新立 ADR、不改 ADR-0032 D3。ADR-0033 升 **v1.4** 仅为进度/阻塞显式化（非方向变更）。

### 阻塞（两事实同时成立）

1. **Scan-Result-GT 没有控制面多文件 merge CLI**  
   上游 `stability_Scan-Result-GT` 公开用法只有：
   `python scan_result.py -d <第一阶段保存根目录> [--threshold …]`  
   （见 toolkit README）。产物是主机侧 `*_org.xls` / 去重 xls，落在 `-d` 目录。  
   仓内现态：该 CLI 已由 **Agent** `UnisocScanRunner.run_scan_result` 调用（`STP_UNISOC_SCAN_RESULT_*`），等价 MTK 的 host 级 `run_dedup_org`，**不是**控制面 `-merge_files_list`。

2. **ADR-0032 D3（Accepted + B3 已验证）要求控制面「同一 merge 工具」**  
   `run_merge_sync` / `build_merge_argv` 对 mtk 与 unisoc **都**走 `STP_BACKEND_DEDUP_SCAN_*` → `start_log_scan.py -merge_files_list`（§B3 spike #2183）。  
   若把 unisoc 分区改挂 Scan-Result-GT，会与 D3 直接冲突，且工具侧也无等价 argv。

ADR-0033 §1.1 / §4 Phase 2 文案把「展锐 `Scan-Result-GT`」写成「per-platform merge 循环内第一个 `DedupMergeEngine`」——把 **host 汇总去重工具** 与 **控制面多 host merge** 叠成同一接缝，现态证据不支持直接落地。

### 现态分工（行为权威仍属 ADR-0032）

| 阶段 | unisoc | mtk | 宿主 |
|---|---|---|---|
| 主机汇总 → `_org.xls` | `scan_result.py -d`（Scan-Result-GT） | `start_log_scan` 本地 scan / dedup_org | Agent |
| 控制面多文件 merge | **同一** `start_log_scan -merge_files_list` | 同左 | 控制面 `dedup_scan` |

编排（round / 水位线 / flock / 发布 `dedup/{run}/merge/{platform}/` / artifact 注册）已在 `dedup_scan.py`；缺的不是编排抽离，而是「Scan-Result-GT 能否、以及应否成为控制面 merge 的 vendor CLI」的裁定。

### 可选出口（需 owner 拍一）

| 选项 | 含义 | 代价 |
|---|---|---|
| **A. 收窄 Phase 2 样板命名** | 第一个 `DedupMergeEngine` 包现态控制面 merge CLI（`start_log_scan`），unisoc 分区走该引擎；Scan-Result-GT 继续只做 Agent host 汇总 | 与 ADR-0033 原文「Scan-Result-GT」字面不符，需 ADR-0033 修订说明「样板 = ACL 接缝，工具仍 D3」 |
| **B. 推翻/修订 ADR-0032 D3** | unisoc 控制面改挂独立 merge 工具；引入平台分支工具解析（曾讨论的 `STP_BACKEND_UNISOC_MERGE_*`） | 需新 ADR 修订或条件裁决；且 **Scan-Result-GT 仍无 `-merge_files_list`**，还要定义/扩展 GT 的多文件 merge 契约或另选工具 |
| **C. Phase 2 改挂 Agent 侧 ACL** | 把 `UnisocScanRunner` 对 Scan-Result-GT 的调用收成 Adapter 样板（仍非控制面 merge） | 满足「Scan-Result-GT 适配器」字面，但不兑现「插在 per-platform merge 循环内」 |

**本 Execution 不选 A/B/C**——硬做任一都会要么撒谎贴标签、要么改 Accepted 行为权威、要么偏离用户给定的控制面样板范围。

### 明确不做（本轮）

- 不新增 `backend/services/dedup/` 适配器代码  
- 不改 `run_merge_sync` / `run_merge_all_platforms_sync`  
- 不做包存储 tar.gz、Phase 3、全量厂商迁移  
- 不新增工具私有 env 键（ADR-0033 §5.4）  
- 不开 auto-merge / 不自 merge

## Alternatives

见上表 A/B/C；另否决「假装 GT 支持 `-merge_files_list` 并在适配器里伪造 argv」——无上游契约、无法行为等价验收。

## Verification

- toolkit README：`scan_result.py -d` only（`gh api …/stability_Scan-Result-GT/README.md`，2026-09-18）  
- 仓内：`backend/agent/unisoc_scan_runner.py` `run_scan_result` → `-d`；`backend/services/dedup_scan.py` `build_merge_argv` → `-merge_files_list` + `STP_BACKEND_DEDUP_SCAN_*`  
- ADR-0032 §D3 / §B3；ADR-0033 §1.1 / §4 Phase 2  
- `python3 tools/dev/ai_work.py status`：`adr0033-phase2-unisoc-dedup-merge` 已 declare（issues 745+2546）

## Revisit

- Owner 在 A/B/C 中拍板后，再开 Phase 2 实现 PR（或拆成「ACL 样板」与「GT merge 契约」两单）。  
- 若选 B：先向上游确认/扩展 Scan-Result-GT 多文件 merge 接口，再动控制面；不得靠新私有 env 键绕过 §5.4（除非同步修订例外表）。  
- #2769（ownership TBD + LINK_TREES）未合入不阻塞本结论。
