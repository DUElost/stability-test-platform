# 手动合并的输入范围：终态窗口内每主机最新一份（#3935）

Status: implemented
Class: bug-fix

## Decision

`_load_org_files_for_merge` 在轮次过滤之后增加「每个（host, 平台）只取最新一份」：
候选按 `(轮次开始时刻, created_at, id)` 取最大者。自动链仍严格限定在
`scan_round_id` 指定的一轮（`include_late_hosts=False`），迟到报表属于下一轮或
补充轮，不得并入本轮（ADR-0062 D5 第 4 条）。

手动合并（`POST /plan-runs/{id}/dedup/merge`）传 `include_late_hosts=True`：
输入窗口下界 = min(最新轮开始时刻, `plan_run.ended_at`)；窗口内某主机在最新轮
没有报表时，回退到更早轮次的最新一份，并在 `merge_org_files_late_hosts` 日志中
显式列出主机；`merge_window` 日志给出本次解析的 anchor 与 floor。无轮次下界时
仍拒绝（#1077：不合并全部历史）；run 仍在运行（`ended_at` 为空）时窗口不扩大，
保持单轮行为。

Run 1256 的映射：中止后 11:01 自动终扫与 11:03 手动扫描两轮都晚于终态时刻，
窗口覆盖两轮。.15.83 在最新轮的两份报表只取一份（消除重复 18 行）；
.15.84/.15.86/.9.126 的最新报表在旧轮，按迟到输入补入（补齐漏掉的 50 行），
合并输入恢复为 10 台各一份。

涉及：`backend/services/dedup_scan.py`、`backend/api/routes/dedup.py`；
测试见 `backend/tests/services/test_dedup_scan_merge.py`、
`backend/tests/api/test_dedup_scan_endpoints.py`。

## Alternatives

- 只按最新一轮 + 主机内去重（不跨轮）：1256 仍漏 3 台 50 行。
- 合并该 run 全部 scan 产物：退回 #1077 禁止的「无约束历史」。
- 用 `created_at` 作为轮次排序：迟到登记会顶掉更新轮次的正确报表；
  用 `scan_round_id` 的轮次开始时刻更贴近归属。
- 在 SQL 里做窗口过滤：`scan_round_id` 是非规范化 ISO 串，跨方言解析/比较不可靠；
  保持 SQL 粗筛 + Python 精确判定。

## Verification

- 反例（修复前红）：`test_load_org_files_for_merge_dedupes_same_host_within_round`
  在 origin/main 代码下返回同一主机两份；修复后只保留最新一份。
- 1256 形态：`test_load_org_files_for_merge_covers_late_hosts_without_unbounding_history`
  ——最新轮仅 host-a/host-b、旧轮 host-c；窗口结果 = 最新 a/b + 旧轮 c，
  旧轮 a 被最新轮取代；run 无终态时窗口不扩大。
- 无约束历史仍拒绝：`test_load_org_files_for_merge_refuses_unbounded_history_with_late_hosts`。
- 平台维度：`test_load_org_files_for_merge_dedupes_per_host_and_platform`。
- 窗口下界：`test_run_merge_sync_manual_window_starts_at_run_end`。
- 端点：#1077 既有 `test_merge_passes_resolved_round_to_merge_all` 增断言
  `include_late_hosts=True`。
- `./scripts/run_pytest.sh backend/tests/services/test_dedup_scan_merge.py
  backend/tests/services/test_shadow_scan_rounds.py
  backend/tests/api/test_dedup_scan_endpoints.py -q` → 110 passed。
- 反向验证：将 `dedup_scan.py` 还原为 origin/main 版本后，同一组 5 个新用例全红
  （含旧签名 TypeError）；恢复后 110 passed。

## Revisit

- 窗口下界依赖 `plan_run.ended_at`。若终态 run 的 `ended_at` 缺失，窗口不会扩大；
  若未来需要更精确的「终态轮次」判定，可在 scan 产物上补 `is_final` 标记。
- 跨轮「每主机最新一份」假设同一主机同一轮的多份报表内容等价（1256 各份行键相同）。
  若出现同轮部分报表（不同事件子集），应改为按事件并集而不是取单份。
