# 探针 UNVERIFIED 原因码与重试纪律（#3563 / #3516）

Status: implemented
Class: testing

## Decision

Owner 2026-10-03 对“探针是否区分瞬时 API 重试与真正的工具错误”采纳建议的**第一步**：先让原因可见，
**不改任何判卷结论**。

1. `tools/dev/harness_probe.py`：把读取最终答复的逻辑做成单一来源
   `_read_final_response(stdout, protocol) -> (answer, reason)`，`final_response` 变成只取答案的薄封装——
   结论与原因出自同一段代码，结构上不会漂移。拒绝时给出**固定词表**的原因码（不含原始输出），由
   `run_form` 写进报告的 `error`，例如 `… [transport-retry(api_retry x6)]`；JSON 协议形态非零退出时
   附在 `exit=N` 之后（纯文本形态仍恰为 `exit=N`）。
2. `harness-probes.md`：原因码表、重试纪律、宿主环境提示（继承的 `CLAUDE_CODE_EFFORT_LEVEL`）。
3. **没有做**第二步：让“只有 `api_retry` 的有效答案”通过。那是判卷语义变更，现有设计有意保持严格、
   工具不提供豁免开关；严格的代价是时间而不是错误结论。见 Revisit。

触发：2026-10-03 的 [#3563 矩阵报告](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5966758919)
里 Claude 两形态各有数格被判 UNVERIFIED，而报告只写笼统的 `no valid final answer or protocol/tool error`，
靠人工重放才看出是 CLI 对上游的瞬时重试（`system/api_retry`，`error:'unknown'`，6 次，退避
0.6s→16.6s）、最终答案其实正确。

## Alternatives

- **让 `api_retry` 单独出现时通过**：属判卷语义变更，需 Owner 另行决策；先用本变更的原因码积累数据。
- **探针内自动重试**：会把“重试了几次、为什么”藏进工具；重试是否合理是操作者的判断，应显式、留档，
  所以写成纪律而不是工具行为。
- **保存原始 stdout 供事后查看**：违背“不保存原始 CLI 输出”的既有设计（可能含私有内容、体积不可控）；
  原因码已足以回答“为什么不可判”。
- **另写一个只做诊断的函数 + 一致性测试**：判定与原因分处两段代码，日后改一处忘另一处就会漂移；
  单一来源更稳。

## Verification

- main 上原有 61 个测试在重构后**原样通过**（含对各类拒绝形态“不得 PASS”的既有回归）；新增 28 个：每个拒绝
  分支一个原因码、答案与原因互为同一决定的两个视图、`run_form` 报告与非零退出的原因码、超深事件不崩。
  `tests/test_harness_probe.py`：**89 passed**（项目 Python + `scripts/run_pytest.py` 保护入口，6 GiB / swap=0）。
- **严格性守卫** `test_a_recovered_transport_retry_is_explained_but_never_forgiven`：重试后的正确答案仍是
  UNVERIFIED，去掉重试的同一答案才是 PASS。
- **自检发现并修掉的一处回归**：第一版把 `_error_reason` 放在 `try` 之外。`_has_error` 遇到第一个出错事件
  就短路，`_error_reason` 却会遍历全部事件，于是“前面有出错事件、后面有 `json.loads` 能解析但
  `_has_error` 走不动的超深事件”这种流，旧代码判 UNVERIFIED，第一版会抛 `RecursionError` 崩掉整个探针
  （用 origin/main 旧实现与工作树新实现对同一条流对跑复现：旧 → `None`，新 → 崩）。现两者放进同一个
  `except (…, RecursionError)` 保护；`test_an_event_too_deep_to_walk_is_rejected_not_a_crash` 守住，深度
  靠探测而非写死，前提失效时测试失败而不是空过。这是我自己的测试集在第一版里漏掉的边角，所以补记。
- **变异自证**（修复后重做；每种变异后从备份恢复，核对哈希逐字一致且转绿）：M1 放宽判卷（忽略
  `api_retry`）→ 2 个测试变红；M2 破坏分类 → 5 个（3 个测试函数）；M3 原因码不写进报告 → 2 个；
  M4 非零退出不附原因 → 2 个；M5 还原成修复前的 `try` 作用域 → **恰好 1 个**（即上面那条新测试），
  输出里出现 `RecursionError`。
- **真实数据**（修复后的最终代码上复跑）：对 2026-10-03 保存的两份真实 Claude 事件流（本地证据，未入库）
  运行 `_read_final_response`：root 那份 → `transport-retry(api_retry x6)`，正是当时人工重放得出的结论；
  agent 那份 → 有效答案。两份流上 `final_response` 的结论都与 origin/main 旧实现一致。
- Ruff（CI 同形：`backend/ tools/ scripts/ tests/`）通过；仓库没有 formatter，改动文件在 main 上本来就
  不满足 `ruff format`，未改动格式。`check:quick`：**16 gates OK**（暂存后的最终树，23 s）。
- 根目录完整离线测试：**2325 passed / 18 skipped**（440 s，同一保护入口），在除本 Note 外与最终提交
  逐字相同的干净树上运行；其后只补了这一行证据。
- 本变更与 #3604 的一次性合并树（不入库）上，`tests/test_harness_probe.py`：**90 passed**（89 + #3604 的 1 个）。
- 未重跑真实 CLI 会话：原因码在 mock 子进程层与真实保存的事件流上验证。
  required CI 另行判定，本地结果不替代它；独立复核 pending。

## Revisit

- **第二步（是否容忍仅有 `api_retry` 的有效答案）仍待 Owner 决策。** 本变更落地后，用原因码统计
  `transport-retry` 单独导致 UNVERIFIED 的频率再定。若放宽，应收窄到：仅 `system/api_retry`、流以唯一的
  `result/success/is_error=false` 结尾、无其它错误节点、报告附重试次数；重试耗尽（`result-error`）、
  `tool_result.is_error`、4xx `error_status` 仍判 UNVERIFIED，并各有红绿测试。
- 原因码词表是对外承诺（操作手册里有表）；新增协议或拒绝分支时须同步表与测试。
- 与 #3604（OpenCode 退役）互相独立：二者改同一批文件的**不同位置**（本变更把文档新增段放在「运行与状态」
  内、`stderr 证据` 之前，刻意避开 #3604 在「已知不可跑形态」之后的插入点），`git merge-tree` 检查两个方向
  均无文本冲突，合入顺序不限。
