# backstop 重跑归因：固定 attempt + 稳定 name + 完整集合（#3573）

Status: implemented
Class: bug-fix

关联：[#3573](https://github.com/DUElost/stability-test-platform/issues/3573)（Planner 裁定方案 v1.0 → v1.1；
10-03 审计证据 comment 5962949183，返修裁定 comment 5965570261）。前序：#3586（目标 job 级等待 + 慢预算）、
#3590（vitest 双空格提取，本单判据 3）。
基线：v1.0 `origin/main@5ed3aef8303c13469b73c34230a2b4646077817f`（PR #3596 → merge `a908882f`）；
v1.1 返修 `origin/main@a908882f81cd62f36d6396e9d7f61dcf07b7d61f`（新 PR，Refs #3573）。

## Decision

按方案 v1.0 在**同一选择/聚合出口**一次修三处独立缺陷（前两项导致空结论，第三项导致提前
错误分类——不能统称「任何一条恒空」，与正文 §1 口径一致）：

1. **固定 attempt**（`scripts/ci/backstop-attribution.sh`）：重跑前按
   `runs/$CI_RUN_ID/attempts/$A/jobs`（分页合并，`total_count` 收齐）做完整快照，目标集合 =
   快照中 `conclusion=failure` 的 job **name**；旧 job id 只用于取旧日志（`actions/jobs/{id}/logs`），
   禁止用于重跑选择。重跑前再次确认 attempt 未变化；轮询只读固定的 `attempts/$((A+1))/jobs`；
   轮询发现当前 attempt 已超过 A+1（并发重跑）即 unknown，不追随最新。attempt>1 的既有
   「不再自动重跑」分支保留。
2. **稳定身份**：按 API `.name` 精确相等匹配（本仓 `ci.yml` 无 matrix/自定义显示名，实测 9 job
   名在一次 attempt 内唯一）；禁用子串/正则。空目标、重复 name 不再可能经 jq 空集 `all()` 判成
   success——空目标在重跑前即 unknown，重复名在快照/新 attempt 两处都明确 unknown。
3. **落定判定**：目标未齐全，或任一目标 `status != completed`，或 `conclusion = null`，一律
   「未定」继续等（已有目标失败也不提前结束）；全部落定才聚合：全绿=success、全红
   `{failure,timed_out,startup_failure}`=failure、仅绿+红且两类都有=mixed（→ deterministic 并写
   「部分转绿、部分仍红」）；`cancelled/skipped/neutral/action_required` 等无法归因的**非空结论**
   明确 unknown。预算耗尽与结构性错误（重复身份、取数/解析异常、并发重跑）备注写真实原因，
   不再共用「预算内未完成」。

不变式保持：输出字段（`classification` / `rerun_conclusion` / `wait_budget` / 两个 md）与
`flake|deterministic|unknown` 枚举不变；`WAIT_INTERVAL=30` / `WAIT_MAX=120` /
`WAIT_MAX_SLOW=240` / `SLOW_JOBS=backend-test` 不变；证据先于重跑、一次自动重跑、
`continue-on-error` 与通知出口不变；无新 env/持久状态/门禁。

测试侧：新增 `tests/test_backstop_attribution_real_path_3573.py`（`DRY_RUN=0` 执行生产
脚本 + 临时 PATH 的 gh 桩，覆盖正文 §3 九条）；既有两个 dry-run 文件文义修正——注明
`DRY_RUN_RERUN_CONCLUSION` 是预置结论，**不覆盖**真实路径的 job 选择 / 跨 attempt 匹配 /
分页 / 落定判定。

### v1.1 返修：轮询计数器（2026-10-03）

首轮交付核对（`a908882f` 隔离 checkout）发现 `wait_conclusion` 用 Bash **特殊变量 `_`** 当
循环计数器：`line="$(poll_once ...)"` 执行后 `$_` 被覆盖为空，`[ "$_" -lt "$budget" ]` 打印
`integer expression expected` 并**跳过全部 sleep**——待定目标以 API 调用速度烧完全部轮数，
名义 60/120 分钟窗口不生效。独立 sleep 桩探针实测：3 轮 / 0 次 sleep / 约 0.26s 后 unknown /
stderr 3 条整数比较错误；应有 2 次 `sleep(30)`。

返修：计数器改为显式局部变量 `poll_index`；`pending` 且仍有下一轮才 `sleep(WAIT_INTERVAL)`，
落定 / 致命 unknown / 预算末轮不额外 sleep。预算、固定 attempt、稳定身份与输出契约不变；
不用扩大预算补偿。真实路径测试新增 `sleep` 桩（记录参数、不真实等待）与节奏断言
（轮数 + sleep 次数 + 参数 + stderr）；Note 与测试 docstring 的「任何一条恒空」按正文 §1
更正为「前两项导致空结论，第三项导致错误分类」。

## Alternatives

- **只做 ID 类型归一（`map(tonumber)`）**：否决——重跑生成新 attempt，同名 job 拿全新数字 id，
  类型对齐后旧 id 匹配仍必然落空（#3573 §1 第②项）；单修类型仍恒空。
- **轮询 latest attempt 再按名匹配 / 回落旧 attempt**：否决——并发重跑下的 attempt 归属不可判，
  按「证据不确定即 unknown」处理，不追随最新；回落旧 attempt 会把旧结论当新结论。
- **未落定/null 继续落 `else mixed`**：否决——重跑刚受理时 job 必为 `queued/in_progress`，
  会立即误判 deterministic 并写「部分转绿、部分仍红」，正是本单要修的第三处缺陷。
- **不予等待、未完成一律直接 unknown**：否决——重跑刚触发就下结论等于放弃归因；方案要求
  在既有预算内等目标落定，预算耗尽才 unknown。
- **扩大预算 / 恢复 run 级等待 / 变更 CI 作业划分**：否决（方案 §6），预算语义与作业面不在本单。

## Verification

### v1.0（PR #3596，merge `a908882f`；worktree `/tmp/stp-3573-cb`，base `5ed3aef8`）

- `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python scripts/run_pytest.py
  tests/test_backstop_attribution.py tests/test_backstop_attribution_budget_3573.py
  tests/test_backstop_attribution_real_path_3573.py tests/test_main_ci_backstop_guards.py
  tests/test_attribution_vitest_extraction_3573.py -q` → **60 passed**（19 既有 + 30 真实路径 + 11 守卫/提取）。
- `env -i PATH="$PATH" .venv/bin/python scripts/run_gates.py check:quick` →
  `[OK] check:quick (16 gates)`。
- required CI（最终 head `1f729fca`）六项全绿。

### v1.1 返修（worktree `/tmp/stp-3573b-cb`，base `a908882f`；本 PR）

- 五文件 → **63 passed**（19 既有 + 33 真实路径〔含 3 条 §3.1 节奏用例〕+ 11 守卫/提取）。
- `check:quick` → `[OK] check:quick (16 gates)`。
- `_` 计数器变异（临时副本把 `poll_index` 改回 `_`）：新节奏用例组 **2 failed / 1 passed**——
  两条 pending 用例先命中 stderr 断言（实得 3 条 `[: : integer expression expected`），
  `sleep_calls` 为 `[]` 而非 `['30','30']`；控制副本（显式局部变量、未变异）**33 passed**。
  `immediate_settle_and_fatal_unknown_do_not_sleep` 在两种实现下都应 0 sleep——它的职责是钉住
  「落定/致命不额外 sleep」，不用于检出「漏 sleep」，变异仅由前两条检出。
- 旧三项变异在 v1.1 脚本上复跑仍红：`old-id` **17 failed / 16 passed**、
  `old-attempt` **24 failed / 9 passed**、`null-mixed` **8 failed / 25 passed**
  （计数较 v1.0 表增加，来自同时失败的 3 条节奏用例，符合预期）。

变异自证（§3，均在临时脚本副本上执行，`STP_BACKSTOP_SCRIPT` 指向副本，共享 checkout 未变异）：

| 变异 | 恢复的缺陷 | 结果 | 失败形态 |
|---|---|---|---|
| `old-id` | 目标身份退回旧 job id（快照 id 列 + `.id == $t`） | **15 failed / 15 passed** | 新 attempt 同名 job id 全新，目标恒缺 → pending → 预算耗尽 unknown；跨 attempt 两个主用例、404/等待/分页/输出契约等全部失败 |
| `old-attempt` | 轮询退回旧 attempt（`fetch_attempt_jobs 1`） | **21 failed / 9 passed** | 桩在重跑后禁止旧 attempt 轮询（exit 97）→ 聚合致命 unknown；跨 attempt、404、分页、证据顺序等失败 |
| `null-mixed` | 未落定/null 不再拦在 pending，末节点从 unknown 退回 `mixed` | **6 failed / 24 passed** | `completed+null`、`queued/in_progress` 首轮即判 mixed→deterministic；`cancelled/skipped/neutral/action_required` 被误判 deterministic |

完整失败用例清单（逐名）：

- old-id（15）：`test_new_attempt_new_ids_same_name_green_is_flake`、
  `..._red_is_deterministic`、`test_new_attempt_404_then_appears_polls_fixed_attempt_only`、
  `test_new_attempt_not_created_yet_waits_then_settles`、
  `test_success_plus_null_keeps_waiting_then_settles`、`test_failure_plus_null_keeps_waiting_then_settles`、
  `test_queued_and_in_progress_keep_waiting_then_settle`、`test_partial_targets_wait_then_settle`、
  `test_all_red_conclusions_is_deterministic`、
  `test_real_mixed_success_and_failure_is_deterministic_with_note`、
  `test_unrelated_pending_sibling_does_not_block_targets`、
  `test_duplicate_target_in_new_attempt_unknown`、
  `test_completed_uncategorizable_conclusion_unknown`、
  `test_pagination_collects_second_page_before_classifying`、
  `test_output_contract_fields_and_summary_line`。
- old-attempt（21）：上述 15 个除去 `duplicate_target_in_new_attempt_unknown` 之外的全部，
  另加 `test_missing_target_exhausts_small_budget_unknown`、
  `test_completed_null_exhausts_small_budget_unknown`、
  `test_always_pending_exhausts_small_budget_unknown`、
  `test_non_404_api_error_unknown_with_real_reason`、`test_invalid_json_response_unknown`、
  `test_evidence_before_rerun_and_exactly_one_rerun`（并已单独复跑两个跨 attempt 用例确认失败）。
- null-mixed（6）：`test_success_plus_null_keeps_waiting_then_settles`、
  `test_failure_plus_null_keeps_waiting_then_settles`、
  `test_queued_and_in_progress_keep_waiting_then_settle`、
  `test_completed_null_exhausts_small_budget_unknown`、
  `test_always_pending_exhausts_small_budget_unknown`、
  `test_completed_uncategorizable_conclusion_unknown`。

未验证（按方案 §5 留待生效阶段）：合入后**自然红灯**的端到端归因；本返修 PR（v1.1，Refs #3573）
不制造 main 红灯。required CI（lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests /
pr-migrate-empty-db）以返修 PR head 最终结论为准。

## Revisit

- **生效阶段按 §5 分层记录**：v1.0 merge `a908882f`；v1.1 返修合入后记录 merge SHA；下一次
  自然失败的 main backstop 需贴——所用 workflow/script revision、run 链接、自动 rerun 的
  固定 attempt、完整目标的 status/conclusion、classification/rerun_conclusion、预算与归因块。
  正常 backstop success、普通 feature 分支红灯、离线 fixture 或合入本身都不算真实红灯验收。
  若该次归因仍 unknown，先按备注区分「预算耗尽」与「结构性 unknown（重复身份/取数解析/
  并发重跑）」再定性，不要按旧「vitest 慢」口径解释（已证伪）。
- **轮询节奏回归已由 §3.1 sleep 桩用例钉住**；禁止用 Bash 特殊变量 `_` 保存跨命令还需读取的
  状态（脚本内已留注释），否则会重演「名义预算、实际零等待」。
- **CI 脚本随 workflow checkout**，无控制面/Agent 部署或脚本包发布；合入即被下次 backstop
  run 使用（无需额外激活）。
- **身份前提变更即退回**：若未来 `ci.yml` 引入 matrix/自定义显示名使同 attempt 目标 name 不唯一，
  运行时会明确 unknown（不会误判 success/mixed）；届时按 §6 退回 Planner 重新裁定身份口径，
  实施者不自行改选修法。
- 判据 3（vitest 提取）已由 #3590 修复，本单以真实路径用例回归；若后端改用 job 级结论检索
  之外的聚合口径，需同步复核本 Note 的 Decision 1–3。
