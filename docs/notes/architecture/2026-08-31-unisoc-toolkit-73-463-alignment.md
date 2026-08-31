# 展锐 (#73) 与方向 4 (#463) — toolkit 工具对齐

Status: accepted
Class: architecture

> **权威**：[ADR-0032 v0.6 Accepted](../adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md)

## 已裁定摘要

| 项 | 决议 |
|----|------|
| **B1** | 路径分区 `dedup/{run}/mtk/`、`unisoc/`；双 merge → `merge/mtk/`、`merge/unisoc/`；UI 一张总表 |
| **D3** | 草案 env 键名；`STP_DEDUP_SCAN_TAG` **与 MTK 共用** |
| **D4** | 归档 **(c)** 独立进程；触发链与 MTK `scan_now` 同构 |
| **D8** | Watcher **(w1)** 专用 Reconciler；**按 platform 分支**；内置无独立 Watcher env |
| **目录** | Watcher 与归档 **串行、分树** |
| **B5** | `event_type=UNIVIEW`；`event_subtype=event_name`；G10 原始 subtype + 默认 B；summary 按 platform 分桶 |

## 双轨对照

| | MTK | UNISOC |
|--|-----|--------|
| Watcher | AEE Reconciler | uniview Reconciler (w1) |
| 归档采集 | `start_log_scan` | `scan_log_gt` |
| 归档汇总 | `run_dedup_org` | `scan_result.py` |
| NFS scan | `dedup/{run}/mtk/` | `dedup/{run}/unisoc/` |

## P1 编码顺序

1. `UnisocPlatformCollector` + `UnisocReconciler` (w1) + B5 契约
2. `UnisocScanRunner` (D4c) + B1 上送路径
3. 控制面双 merge + UI 总表分桶

## Verification

- B3 spike 清单（ADR）
- Z258 真机：Watcher 计数 + 终态 merge 两平台分区
