# ADR-0033 Phase 2 样板阻塞：unisoc `DedupMergeEngine` ≠ Scan-Result-GT CLI

Status: implemented
Class: architecture

## Decision

**2026-09-18**：停做把 Scan-Result-GT 挂进控制面 merge 的样板——与 ADR-0032 D3 + GT 仅 `-d` 冲突；登记 A/B/C，ADR-0033 v1.4。

**2026-09-19**：Owner 选定 **选项 A**。阻塞解除；实现与措辞见
[`2026-09-19-adr0033-phase2-option-a.md`](./2026-09-19-adr0033-phase2-option-a.md) 与 ADR-0033 **v1.5**。

- 样板 = 包 **B5** `start_log_scan -merge_files_list`（薄 `DedupMergeEngine`）
- GT **继续只做 B2**；不改 merge 执行位置；不修订 D3

### 阻塞事实（仍成立，解释为何不能选「GT=merge 引擎」）

1. **Scan-Result-GT 没有控制面多文件 merge CLI**（仅 `scan_result.py -d` → Agent B2）
2. **ADR-0032 D3** 要求控制面 mtk/unisoc **同一** `-merge_files_list` 工具

### 现态分工（行为权威仍属 ADR-0032）

| 阶段 | unisoc | mtk | 宿主 |
|---|---|---|---|
| 主机汇总 → `_org.xls`（B2） | `scan_result.py -d`（Scan-Result-GT） | `start_log_scan` 本地 scan / dedup_org | Agent |
| 控制面多文件 merge（B5） | **同一** `start_log_scan -merge_files_list` | 同左 | 控制面 `dedup_scan` + `dedup.StartLogScanMergeEngine` |

### 可选出口（已拍板）

| 选项 | 含义 | 状态 |
|---|---|---|
| **A. 收窄 Phase 2 样板命名** | 第一个 `DedupMergeEngine` 包现态 B5 merge CLI；GT 留在 B2 | **已选** |
| **B. 推翻/修订 ADR-0032 D3** | unisoc 控制面独立 merge 工具 | 未选 |
| **C. Phase 2 改挂 Agent 侧 ACL** | 包 B2 GT Adapter | 未选（可另开） |

### 明确不做（选项 A 本轮仍成立）

- 不做包存储 tar.gz、Phase 3、全量厂商迁移
- 不新增工具私有 env 键（ADR-0033 §5.4）
- 不开 auto-merge / 不自 merge

## Alternatives

见上表；否决「假装 GT 支持 `-merge_files_list`」——无上游契约。

## Verification

- 阻塞期证据：toolkit README；`UnisocScanRunner.run_scan_result`；`build_merge_argv`；ADR-0032 §D3/B3
- 解除后：ADR-0033 v1.5；`backend/services/dedup/`；`test_dedup_merge_engine.py`

## Revisit

- 选项 B/C 触发条件见 [`2026-09-19-adr0033-phase2-option-a.md`](./2026-09-19-adr0033-phase2-option-a.md) Revisit
