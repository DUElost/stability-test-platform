# 容量 P0 手册 Step 4 补「首次出现的 5xx 序列取原始计数器值」观测量纪律（2026-09-25）

Status: implemented
Class: process

## Decision

`docs/operations/2026-09-24-capacity-p0-rollout-runbook.md` §2 Step 4 的观测配方补一条纪律：

> 对**本轮首次出现**的 5xx / 异常序列，判据取**原始计数器值**（或 `count_over_time`），
> 不要用 `increase()`。

依据：2026-09-25 真机复跑（plan_run 556）取证时，用
`increase(stability_api_requests_total{status_code="503"}[30m])` 得到 **0**，一度误判为
「bulkhead 503 未进请求级计数器」的埋点缺口；复核**原始计数器 = 503:4 / 200:1533**，
与应用侧 `terminal_bulkhead_rejected_total`（+4）与 nginx（4 条，全 `/complete` 同秒 17:21:36）
**三方一致**。成因是 Prometheus 语义：**带标签的计数器序列首次自增才出现**，窗口内首个样本
（`17:22 → 4`）已含全部增量，`increase()` 只能看到 4→4；未打标签的单例计数器自进程启动即有
0 样本，不受影响。错误结论已在 [#3244 评论](https://github.com/DUElost/stability-test-platform/issues/3244#issuecomment-5830168536)
撤回。

## Alternatives

- **只在 #3244 评论更正、不改手册**：不采纳——owner 已建议"再采 1–2 次同口径中止"，下次仍会照
  Step 4 配方算 `increase()`，同一假阴性会重现；手册是复跑的唯一入口，纪律必须落在入口。
- **把 503 判据改成"看 bulkhead 计数器即可"**：不采纳——请求级计数器仍是「HTTP 面真实返回了什么」
  的权威面（含非 bulkhead 来源的 5xx）；修的是**读数方法**，不是换观测面。
- **顺手重写 Step 4 全部观测命令**：不采纳，超出本 Requirement；其余命令（池、p99、outbox）本轮实测无坑。

## Verification

- 原始计数器：`stability_api_requests_total{endpoint="/api/v1/agent/jobs/{job_id}/complete"}` → `200=1533 / 503=4`。
- 序列样本窗（2h range）：503 序列首个样本 `17:22 → 4` 且恒为 4；200 序列 `15:34 → 500` 起连续有样本。
- 未打标签反例：`increase(stability_terminal_bulkhead_rejected_total[15m])` = +4.01（正常）。
- 治理检查与链接核对：`check_governance_surface --self-test/--check` 全绿；本文件与手册相对链接 0 缺失。

## Revisit

- 若请求级指标改为 native histograms 或做标签基数治理后重设计，本纪律以届时语义为准。
- 下一次同口径复跑时按新纪律读数，并把「首次出现的 5xx 序列」列为 Step 4 固定检查项。
