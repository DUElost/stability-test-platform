# 展锐 (#73) 与方向 4 (#463) — toolkit 工具对齐

Status: proposed
Class: architecture

> **以 [ADR-0032 v0.4.1](../adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md) 为准**（Proposed）。本文为补充论据与实施备忘；术语用「采集阶段/汇总阶段」与「落地批次 P1/P2」，勿与工具阶段混称。

## 架构不变量：两条并列流水线，禁止交叉混用

MTK 与展锐（UNISOC）是**两条并列的采集→汇总流水线**，各自工具链端到端绑定，**不得交叉**：

| 平台 | 采集阶段 | 汇总阶段 | 平台侧 Watcher（实时信号） |
|------|----------|----------|---------------------------|
| **MTK** | `stability_Start-Log-Scan` / `start_log_scan.py`（`-m 0` AEE_TNE） | Agent `run_dedup_org` + 控制面 merge | Reconciler + inotifyd（`/data/aee_exp`） |
| **展锐** | `stability_Monkey-Log-Scan-GT&SPRD` / `scan_log_gt.py` | Agent `scan_result.py` + 控制面 merge（见 ADR D7） | 落地批次 P1 Agent 化；**不复用 MTK Reconciler** |

**禁止**（违反即 bug）：展锐机跑 MTK 工具；MTK 机跑 GT 工具；展锐采集不经 `scan_result.py` 进 merge；同一 merge 混收未分流 `*_org.xls`。

`Scan-Result-GT` 的「MTK 格式 xls」= **merge 下游列同构**，≠ 工具链可混用。

## 实施顺序：运行时 vs 工程

| 维度 | 顺序 |
|------|------|
| **运行时因果** | 必须 **采集阶段 → 汇总阶段 → upload → merge** |
| **工程落地** | 可用**历史问题包**先做 **落地批次 P2 离线 spike**；**生产端到端**须落地批次 P1（采集 Agent 化）+ 汇总接线齐备 |
| **若只选一个先做且要真机出报表** | 优先 **落地批次 P1 采集 Agent 化** |

## 现有代码预留（MTK 多平台）

### 已预留 — Watcher / 设备事实层

| 接口 | 位置 | 状态 |
|------|------|------|
| `PlatformCollector` | `backend/agent/aee/collector.py` | MTK 实现；UNISOC/QCOM **stub** |
| `device.platform` | 心跳 → `device` 表 | MTK/UNISOC/QCOM/UNKNOWN |
| `STP_WATCHER_AEE_RECONCILE_PLATFORMS` | `job_session.py` | 默认仅 MTK（#220） |
| `device_log_event.platform` | DLE 表 | 列已有 |

### 未预留 — 归档 scan→merge 链

| 能力 | 现状 |
|------|------|
| `ScanRunner` | 写死 `start_log_scan.py`，无 platform 分支 |
| `dedup_scan.resolve_scan_tool()` | 单一 `STP_BACKEND_DEDUP_SCAN_*` |
| merge 产物注册 | `*_org.xls` glob，**不按 platform 分流** |

展锐接入 = **新增并列模块**（独立 env + runner + merge 分流），非扩展 MTK 键。

## ADR 判定

| 范围 | 是否需要 ADR |
|------|----------------|
| 双轨架构、env 分离、merge 分流、#73/#463 总纲 | **是** → ADR-0032 |
| 落地批次 P2 离线 spike（手工问题包） | Proposed ADR 下可做 |
| 落地批次 P1 生产合入 | 须 ADR **Accepted** + D4 终裁采集形态 (b)/(c) |

## Verification

- `/mnt/automation-toolkit/python-tools/stability_{Monkey-Log-Scan-GT&SPRD,Scan-Result-GT}` 已存在
- TOOLKIT §2.4 G9；`test_platform_collector.py` / `test_job_session.py` 平台门禁

## Revisit

Proposed 下按 B3 做落地批次 P2 spike → 闭合 B1 / D3 键名 / TAG → **Accepted** → D4 终裁 → 落地批次 P1 真机验收。
