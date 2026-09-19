# Agent Note：终端设备日志链全局语义汇总

Status: implemented
Class: architecture

## Decision

在拍板 ADR-0033 Phase 2 A/B/C 之前，先落一份**用户可读、可引用**的日志链全局语义汇总
[`docs/design/2026-log-chain-global-semantics.md`](../../design/2026-log-chain-global-semantics.md)：

- 从 Accepted ADR + 现态代码整理端到端阶段（Watcher / 归档采集 / host 汇总 / 上送 / 控制面 merge / extract）
- 拆开易混概念：采集 vs 导出 vs 去重 vs 汇总 vs merge vs Scan-Result-GT vs `start_log_scan` 三种 argv
- 挂上 ownership X2 四层；写清 A/B/C **语义前提**（不替用户拍板）
- 同步 DOC-MAP / docs README；X2 与 Phase 2 阻塞笔记各加一行指针

**不改行为、不落适配器。**

## Alternatives

- **仅 Project 文档、不进仓**：能服务当前拍板，但 ownership X2 已明示「缺可引用汇总」，仓外文档无法被 DOC-MAP / 后续 Execution 稳定引用 → 否决
- **改写 ADR-0033/0032 正文做决策**：超出「先澄清语义」范围 → 否决；拍板后再开决策 PR

## Verification

- 对照：`scan_runner.py` / `unisoc_scan_runner.py`（`-m 0` / `scan_result -d`）、`dedup_scan.build_merge_argv`（`-merge_files_list`）、ADR-0032 D3/D7/D10、ADR-0033 v1.4、阻塞笔记
- 文档交叉链：DOC-MAP、docs/README 速查、ownership §1 X2、阻塞笔记「不选 A/B/C」段
- 无代码 / 无测试变更；`test_impact=none`

## Revisit

- Owner 拍 A/B/C 后：按选项修订 ADR-0033 措辞（A）或 D3（B）或样板范围（C），再开实现 PR
- 若实现与本文冲突：以代码为准并回写本汇总
