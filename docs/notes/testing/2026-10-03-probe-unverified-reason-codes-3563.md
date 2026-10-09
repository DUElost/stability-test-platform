# 探针 UNVERIFIED 原因码与重试纪律（#3563 / #3516）

Status: implemented
Class: testing

## Decision

Owner 2026-10-03 对“探针是否区分瞬时 API 重试与真正的工具错误”采纳建议的**第一步**：先让原因可见，
**不改任何判卷结论**。

1. `tools/dev/harness_probe.py`：把读取最终答复的逻辑做成单一来源
   `_read_final_response(stdout, protocol) -> (answer, reason)`，`final_response` 变成只取答案的薄封装——
   结论与原因出自同一段代码，结构上不会漂移。拒绝时给出**固定词表**的原因码（不含原始输出），由
   `run_form` 写进报告的 `error`，例如 `… [transport-retry(api_retry x6)]`；JSON 协议形态进程非零退出
   时先写 `exit=N`，**仅当 stdout 还能读出流级拒绝原因**才附在其后——协议有效的最终答复配非零退出、
   以及纯文本形态，仍恰为 `exit=N`（非零退出本身就是原因，无方括号码不是诊断缺失）。
2. `harness-probes.md`：原因码表、重试纪律、宿主环境提示（继承的 `CLAUDE_CODE_EFFORT_LEVEL`）。
3. **没有做**第二步：让“只有 `api_retry` 的有效答案”通过。那是判卷语义变更，现有设计有意保持严格、
   工具不提供豁免开关；严格的代价是时间而不是错误结论。见 Revisit。

触发：2026-10-03 的 [#3563 矩阵报告](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5966758919)
里 Claude 两形态各有数格被判 UNVERIFIED，而报告只写笼统的 `no valid final answer or protocol/tool error`，
靠人工重放才看出该流里有 6 个 `system/api_retry` 事件（`error:'unknown'`，退避 0.6s→16.6s），
最终答案其实正确（CLI 自行恢复）。

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
  运行 `_read_final_response`：root 那份 → `transport-retry(api_retry x6)`，与当时人工重放看到的重试
  事件数一致（原因码本身不证明答案有效或故障瞬时，见下一条与 Revisit）；agent 那份 → 有效答案。
  两份流上 `final_response` 的结论都与 origin/main 旧实现一致。
- Ruff（CI 同形：`backend/ tools/ scripts/ tests/`）通过；仓库没有 formatter，改动文件在 main 上本来就
  不满足 `ruff format`，未改动格式。
- 根目录完整离线测试：**2325 passed / 18 skipped**（440 s，同一保护入口），在首个提交的干净树
  （`275c935c`，即 #3611 首次推送的 head）上运行；复核返修之后**未重跑**，见下条。
- 本变更与 #3604 的一次性合并树（不入库）上，`tests/test_harness_probe.py`：**90 passed**（89 + #3604 的 1 个）。
- 未重跑真实 CLI 会话：原因码在 mock 子进程层与真实保存的事件流上验证。
- **复核返修（2026-10-06，独立复核结论“需修改”，代码与判卷未改）**：
  - ① 手册 / Note / PR 把“JSON 非零退出附原因码”写成了无条件规则。实际 `run_form` 仅当 stdout 还能读出
    流级原因才附码；协议有效的最终答复配非零退出仍只写 `exit=N`——`test_nonzero_exit_of_a_json_cli_gains_the_stream_reason`
    早已锁定该反例。已收窄，并注明“`exit=N` 后没有方括号码不是诊断缺失”。
  - ② 原因码表把 `transport-retry` 写成“瞬时、已自行恢复、与 `result-error` 同现即重试耗尽”。它只证明流里
    有 `api_retry` 事件：该事件自带非空 `error` 字段，有它的流恒被判出错，其它缺陷被掩盖，401 这类持续性错误
    也同码。复现（本地证据 `masking-repro.txt`，对 `_read_final_response` 逐条运行）：只有 retry 没有最终
    `result`、retry + 格式不对的 success 答案、retry + 非 success 的 `result`、retry + 两个 `result`、
    401 retry + 有效答案，都得到
    `transport-retry(api_retry xN)`；401 retry + 带错误的 `result` 得到 `…+result-error`。已按此收窄原因码表与
    重试纪律第 1、4 条。
  - ③ 代码里有两处 docstring（`_is_api_retry`、`_error_reason`）和 `run_form` 的一处注释带着同样的过度
    表述，一并改正，**只改注释**：`ast.dump` 去掉 docstring 后与 `275c935c` 的 `tools/dev/harness_probe.py`
    逐节点相同（附一个“改一个字符串字面量即被判为不同”的对照），测试文件逐字未动。
  - 返修后只重跑了 Ruff、`tests/test_harness_probe.py`（89 passed）与 `check:quick`（见 PR 描述）；完整离线套件
    **没有**重跑，因为改动只含文档、docstring 与注释，可执行代码与上面 2325 passed 的那棵树相同。
  - required CI 另行判定，本地结果不替代它；返修后的复核 pending。

## Revisit

- **第二步（是否容忍仅有 `api_retry` 的有效答案）仍待 Owner 决策，暂不放宽（2026-10-06 裁定）。** 先用原因码
  积累数据。**统计口径要小心**：`transport-retry` 只表示流里有 `api_retry` 事件，**不等于**“有效答案仅被
  重试阻断”——缺最终 `result`、`result` 非 success、答案格式不对、401 这类持续性错误都会得到同一个码
  （该事件自带非空 `error`，使流先于其它检查被判出错，其余缺陷被掩盖）。所以统计前须能区分这些情形：
  对留档的本地流手动重放；或另行裁决是否让原因码带上“流的其余部分是否有效”——那是新的诊断变更，不在本 PR。
  若放宽，应收窄到：仅 `system/api_retry`、流以唯一的 `result/success/is_error=false` 结尾、无其它错误节点、
  报告附重试次数；带错误的最终 `result`（`result-error`）、`tool_result.is_error`、4xx `error_status` 仍判
  UNVERIFIED，并各有红绿测试。
- 原因码词表与含义措辞是对外承诺（操作手册里有表）；新增协议或拒绝分支时须同步表与测试。`transport-retry`
  只许写成“流里有重试事件”，不得再写成“瞬时 / 已恢复 / 已耗尽”。
- 与 #3604（OpenCode 退役）互相独立：二者改同一批文件的**不同位置**（本变更把文档新增段放在「运行与状态」
  内、`stderr 证据` 之前，刻意避开 #3604 在「已知不可跑形态」之后的插入点），`git merge-tree` 检查两个方向
  均无文本冲突，合入顺序不限。
