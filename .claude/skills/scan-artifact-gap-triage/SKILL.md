---
name: scan-artifact-gap-triage
description: scan 产物缺口分诊 SOP（saq_scan_no_artifacts / saq_scan_partial_artifacts 告警 → 读 run_context.archive 定位慢/坏 host）。触发时机：scan 无产物或部分产物告警、归档缺口疑云、后处理链疑似丢 host。
---

# scan 产物缺口分诊

## 执行前置检查

- [ ] 从告警 / 日志确认命中哪条：
      `saq_scan_no_artifacts plan_run=<id> hosts_triggered=<n> waited=<s>` 或
      `saq_scan_partial_artifacts plan_run=<id> hosts=<a>/<b> waited=<s>`
- [ ] 明确目标：**定位慢 / 坏 host**（定向处置），不是整轮重跑

## 标准作业流程（SOP）

1. 读该 PlanRun 的 `run_context.archive`（`WatcherArchiveOut`）：
   `signaled_jobs` / `pending_jobs` / `failed_jobs`（辅以 `scan_status` /
   `scan_triggered_at`），逐 host 对照；
2. 三字段判读：
   - `pending_jobs` 高 → 该 host **慢**（在途 / 排队）→ 查 Agent / 设备侧；
   - `failed_jobs` 高 → **坏 host** → 查 agent 日志与设备状态；
   - `signaled_jobs` 少 → 触发未达 → 查 host 在线 / 心跳；
3. 定向处置：慢 / 坏 host 单独修复或标记；不阻塞其余 host 的归档；
4. 契约背景：`docs/design/2026-scan-upload-merge-contract.md`。

## 后置验证

- 目标 host 的产物到达（archive 字段收敛）；
- 若触发重扫：核对 hosts 覆盖（done 对齐 triggered）。

## 踩坑守卫（负向约束）

- **「部分报表优于零报表」是既定取舍**：`hosts_done < n_triggered` 时仍 enqueue
  upload + merge 是有意设计（`docs/reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`）
  ——不要当缺陷重开；
- 分诊目标是定位慢 / 坏 host；**整轮重跑不是处置**（代价高且掩盖根因）；
- 卡片不展示 `hosts_with_artifacts / hosts_triggered` 差值属已知展示口径（同上审查
  记录）——用 archive 字段定位，不要据此臆断丢数据。
