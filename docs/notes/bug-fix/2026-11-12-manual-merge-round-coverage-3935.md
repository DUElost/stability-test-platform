# 手动合并的输入范围：终态窗口内每主机最新一份（#3935）

Status: implemented
Class: bug-fix

## Decision

r2 返修（PR #3943 第 1 轮复核 B1–B3，评论 6817887914）后的最终口径。

**轮次锚点**（`resolve_manual_merge_round`）：

- 终态 run（SUCCESS / PARTIAL_SUCCESS / FAILED）且有 `ended_at`：在**可解析**的
  `scan_round_id` 中按轮次开始时间取最新一条。旧轮晚登记不再改变锚点；没有可解析
  标记时退回 #1077 的登记顺序选择，由调用方保持单轮。
- 非终态或缺 `ended_at`：沿用 #1077 按登记时间取最后一条打标行。
- 无打标行（legacy）：`min(created_at)` 作为兼容下界。

**合并输入**（`_load_org_files_for_merge`）：

- 手动意图只有同时满足「终态 + `ended_at` 非空 + 锚点可解析」才扩窗；窗口下界 =
  `min(锚点开始时刻, ended_at)`。其余形态保持 #1077 单轮。
- 窗口内：可解析轮次按 `round_start >= floor` 进入候选；锚点同 ID 精确命中；NULL
  标记按 `created_at >= floor` 进入；其它非 ISO 标记不进入候选（无法证明轮次归属）。
- 最新轮缺报表的主机回退到窗口内更早轮次的最新一份，`merge_org_files_late_hosts`
  显式列出主机；`merge_window` 记录 anchor/floor。
- 单轮形态：`scan_round_id` 同 ID 精确匹配（非 ISO 轮号全部保留）+ NULL 标记
  `created_at >= floor`；无可靠下界时拒绝调用工具。
- 每个（host, 平台）只取一份，排序键 `round_start → created_at → id`，消除重复计数。
  同轮非等价报表（不同事件子集）的语义按复核 C 另案裁定，本单未改。

Run 1256 的映射：中止后 11:01 自动终扫与 11:03 手动扫描两轮都晚于终态时刻，
窗口覆盖两轮。.15.83 在最新轮的两份报表只取一份（消除重复 18 行）；
.15.84/.15.86/.9.126 的最新报表在旧轮，按迟到输入补入（补齐漏掉的 50 行），
合并输入恢复为 10 台各一份。

涉及：`backend/services/dedup_scan.py`、`backend/api/routes/dedup.py`；测试见
`backend/tests/api/test_dedup_merge_round_window_3935.py`、
`backend/tests/services/test_dedup_scan_merge.py`、
`backend/tests/api/test_dedup_scan_endpoints.py`。

## Alternatives

- 始终扩窗（r1 行为）：RUNNING/缺 `ended_at` 时混轮、旧轮晚登记拉早窗口、非 ISO
  标记被登记时间切分（复核 B1–B3）。
- 锚点按 `created_at` 选：登记顺序不等于轮次顺序，迟到登记会顶掉正确锚点。
- 窗口内接受所有非 ISO 标记：无法证明轮次归属，会把其它轮按注册时间误纳入。
- 同轮多份全传：重复计数；只取一份：同轮非等价报表可能丢事件（待裁定）。

## Verification

- 新端到端测试 `backend/tests/api/test_dedup_merge_round_window_3935.py`：带认证的
  真实手动路由 + 假合并子进程记录实际 `-merge_files_list` 输入。
- 反例基线：在 `d8b95c8b`（#3943 合入提交）副本跑同一文件 → **6 failed / 2 passed**
  （B1×2、B2×2、B3×2 全红；1256 形态与无下界拒绝保持绿）。返修后 **8 passed**。
- 1256 断言：10 台 10 份、`.15.83` 取最新一份、`hosts=15.84,15.86,9.126` 回退日志、
  `merge_window anchor=03:03:07.007712 floor=03:00:00`。
- 回归：`test_dedup_scan_merge.py` + `test_dedup_scan_endpoints.py` +
  `test_dedup_merge_round_window_3935.py` + `test_shadow_scan_rounds.py` +
  `test_shadow_merge.py` + `test_shadow_delivery.py` → **182 passed**；
  `test_saq_scan_pipeline.py` + `test_shadow_scan_send_3875.py` +
  `test_shadow_scan_settled_3875.py` → **57 passed**。
- 既有单测 `test_run_merge_sync_manual_window_starts_at_run_end` 增补 RUNNING +
  残留 `ended_at` 不扩窗的反例。
- 同 PR 按棘轮把 `check_inner_imports` 基线 608 → 607（`PlanRun` 从函数内回顶层；
  models 纯定义、无环）。

## Revisit

- 终态 run 缺 `ended_at` 时不扩窗；若生产出现该形态，可在 scan 产物上补 final 轮标记。
- 同轮非等价报表（复核 C）待规划者裁定「每主机取一份」的适用前提。
