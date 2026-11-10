# 影子轮 0 结果空回执（B8 G3e）

Status: implemented
Class: feature

## Decision

影子扫描（`fact_round_id is not None`）的 MTK 工具以 rc=0 退出、没有 fresh
`Result_*_org.xls`，但扫描根下存在本次运行写出的机器可读 0 结果证明时，
Agent 在 `round_manifest_path(nfs_root, plan_run_id, fact_round_id, host_id)`
登记一条空回执：

```json
{
  "platform": "mtk",
  "fact_round_id": 7,
  "empty_receipt": true,
  "registerable": false
}
```

条目刻意不带 `file_key`：没有报表文件可指向；`registerable: false` 保证任何
报表读取面都不会把它当成产物。`backend/services/shadow_scan_rounds.py` 的
`read_round_reports` 已直接接受该形态，覆盖分类为 `EMPTY`，因此本单元不改
控制面、不改清单 schema 版本、不改工具、不改 Unisoc 路径。

证明只取扫描根下 **本次运行 fresh** 的结构化文件（mtime ≥ 扫描开始时间 - 1s）：

- `Result_*_members.json`：`schema == "stp.scan_membership"` 且
  `rows == []`（`rows` 是一个结果条数字段）；
- `Result_*_summary.json`：`counts.aee_result_org_count` /
  `counts.aee_result_final_count` 为 int 且为 0。

判定为证明必须同时满足：至少有一个结果条数字段（members `rows`，或 summary
的 `aee_result_org_count` / `aee_result_final_count`）；这些字段全部为 0；
没有任何否决（`dbg_count` 为 int 且 > 0、`spill_unstaged`、`events_present`）。`dbg_count`
只作否决，本身不计入证明——仅凭 `dbg_count=0` 不构成 0 结果证明
（评审 6766083327）。日志、stdout、“没有 org.xls”都不作为证据。证明在
staging 回收之前读取。

存在未暂存的溢出输入（`events is None` 且 `spill_inputs` 非空，即
`spill_unstaged` 非空）时，即使本地有 0 结果证明也不登记：本地 0 结果不覆盖
整台主机。此时记 warning `scan_runner_empty_result_unproven`，evidence 为
`spill_unstaged`，覆盖保持 `MISSING`，交给后续重发补齐（评审 6762617060）。

`load_scan_events` 返回非 None 且非空（本 host 本 run 已有 DLE 事件）而工具结果为
0 时，同样不登记空回执：这说明事件存在但工具没有产出任何行，不能声称主机为空。
此时记 warning `scan_runner_empty_result_unproven`，evidence 为 `events_present`，
覆盖保持 `MISSING`，交给后续重发（评审 6766165762）。`events is None` 的情况
不受本项影响，仍由 `spill_unstaged` 否决处理；没有 spill 时按现有证明规则判定。

不登记并保持 `MISSING` 的路径：工具 rc≠0、超时/抛异常、扫描根准备失败、
rc=0 但没有有效证明、`spill_unstaged` 非空、`events_present`。无有效证明或
存在否决时新增可检索 warning `scan_runner_empty_result_unproven`；
证明被接受时记录 `scan_runner_empty_result_proven`。

空回执登记成功后沿用现有影子观测：日志
`shadow_round_empty_receipt_registered`（与报表的
`shadow_round_report_uploaded` 同级），并计入心跳字段
`shadow_round_report_uploaded_total`。登记写入失败与普通分片失败同语义，
抛 `ShardRegistrationError`。

涉及文件：`backend/agent/scan_runner.py`、`backend/agent/upload_manager.py`、
`backend/agent/tests/test_b8_g3e_empty_receipt.py`。另因移除 scan_runner
函数体内的 UploadManager 导入，按棘轮纪律把
`tools/dev/check_inner_imports.py` 的 `_BASELINE` 610 → 608；并在
`tests/test_agent_test_import_ratchet.py` 登记本测试文件对
`backend.services.shadow_scan_rounds` 的 1 个控制面 import 例外——验收 1
要求用真实 `read_round_reports`/`classify_round_coverage` 读回 Agent 写的
清单，整例迁移到 `backend/tests/` 会混用 agent/控制面 fixture，且该验收需在
`pr-agent-tests` 内运行。

### 工具 0 结果的实测产物（v2.1.1 / dc5d9c1）

在隔离目录用发行副本（`release.json` 记录 `tag=start-log-scan/v2.1.1`、
`git_commit=dc5d9c1fe22273dcfc28e41b285e8fdb3bfb6694`、`git_dirty=false`；
`start_log_scan.py` / `modules/mode/ScanAeeTne.py` / `modules/dedup/membership.py`
的 sha256 与 `release.json` 一致）运行：

```text
start_log_scan.py -m 0 -d <scanroot> -side shanghai
```

扫描根只含两个空设备目录
`MonkeyAEEinfo_20261110_120000/SN-EMPTY-{1,2}/`。结果 rc=0，**没有**
`Result_*_org.xls`，但同一秒写出：

- `Result_None_None_MonkeyAEE_SH_20261110_153611_members.json`（30,892 B）：
  `schema=stp.scan_membership`、`schema_version=1.0`、`rows=[]`、`groups=[]`、
  `stats.rows=0`、`meta.tool.version=2.1.1`、`meta.tool.git_commit=dc5d9c1...`；
- `Result_None_None_MonkeyAEE_SH_20261110_153611_summary.json`（5,241 B）：
  `counts.aee_result_org_count=0`、`counts.aee_result_final_count=0`、
  `counts.dbg_count=0`，`generated_files` 只有 members_json / summary_json，
  没有 org_excel。

这两个文件位于 **扫描根顶层**：工具 v2.1.1 源码
`modules/mode/ScanAeeTne.py` 的 `__generate_result` 以
`result_dir = self._scan_root_dir` 写出 members/summary，因此实现保留扫描根
顶层的 `scan_root.glob("*_members.json")` / `scan_root.glob("*_summary.json")`，
不递归查找（评审 6762617060 放宽原第 2 项；子目录递归与对应用例不再要求）。

## Alternatives

- **工具侧在 0 结果时写只有表头的 org.xls**：需要改外部工具并重新发版；实测
  v2.1.1 已经稳定写出 members/summary，停止条件未命中，故不选。
- **从“没有 org.xls”或 stdout/日志推断**：会把工具失败、扫描根错误与真正 0
  结果混为一谈，issue 判据 2 明确禁止。
- **复用 `upload_scan_report` 造一个占位文件**：会产生指向不存在文件的
  `file_key`，或让空回执被 legacy glob 当成报表；违反不变量。
- **新增清单 schema 字段/版本**：`read_round_reports` 已接受无 `file_key`
  的 `empty_receipt: true` 条目，改 schema 只会扩大兼容面。

## Verification

- 实测：上述 v2.1.1 隔离运行，rc=0、无 org.xls、members rows=[]、summary
  aee_result_*_count=0（证据见 Decision）。
- 反例先行：第一轮新测试在 `origin/main d3458b9` 上 9 failed / 6 passed；
  第一轮实现后 15 passed。评审退回后新增 `dbg_count=1`、`spill_unstaged`、
  仅 `dbg_count=0` 三条反例；G3e-2 再新增 `events_present` 反例，当前
  19 passed。
- 控制面真实读取：`test_shadow_zero_result_with_v211_proof_registers_empty_receipt`
  用 `read_round_reports` + `classify_round_coverage` 断言覆盖为 `EMPTY`；
  无证明/旧证明/条数 >0/`dbg_count > 0`/仅 `dbg_count=0` 时断言
  `MISSING`；`test_shadow_zero_result_with_unstaged_spill_input_is_unproven`
  断言 `spill_unstaged` 时覆盖为 `MISSING`；
  `test_shadow_zero_result_with_events_present_is_unproven` 断言
  `events_present` 时覆盖为 `MISSING`。
- 本轮相关测试（新文件 + `test_b8_g3_round_scan.py` + `test_scan_runner.py` +
  `test_upload_manager.py` + 两项棘轮）：116 passed。
- Agent 全量：3220 passed，1 failed 为
  `test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails`；
  已在同 revision 的干净 `origin/main` 工作树复现（本机用户处于 dialout 组），
  与本变更无关。
- `check:quick`：16 gates 全绿；`tests/test_inner_import_ratchet.py` 20 passed。
- 变异自证：
  - 去掉证明检查（`if True` 代替计数判定）→
    `test_shadow_zero_result_without_valid_proof_stays_missing` 三个参数全红；
  - 去掉 `fact_round_id` 判断 → 现行扫描用例
    `test_legacy_zero_result_with_proof_keeps_current_scan_untouched` 变红
    （`round_manifest_path` 拒绝 None）；
  - 去掉 `dbg_count` 检查 → `test_shadow_zero_result_without_valid_proof_stays_missing[dbg_count]`
    变红（清单被写出、覆盖变 `EMPTY`）；
  - 去掉 `spill_unstaged` 阻断 → `test_shadow_zero_result_with_unstaged_spill_input_is_unproven`
    变红（清单被写出）；
  - 把 `dbg_count` 重新当作证据 → `test_shadow_zero_result_with_only_dbg_count_is_not_proof`
    变红（仅 `dbg_count=0` 被登记成 `EMPTY`）；
  - 去掉 `events_present` 否决 → `test_shadow_zero_result_with_events_present_is_unproven`
    变红（清单被写出）。

## Revisit

- 工具 0 结果产物形态变化（不再写 members/summary，或计数字段改名）时，
  需同步 `_fresh_zero_result_evidence` 与 fixture。
- 控制面若要求空回执也带 `size_bytes`/`file_key`，需先改读侧契约再改写侧。
- ADR-0062 D12.1 若把空回执扩到 UNISOC，需要单独的 Unisoc 证据源，不能复用
  本函数的 `platform: "mtk"` 条目。
