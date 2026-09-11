# T1 参数创建前校验 + 执行期异常必达终态（#1218）

Status: implemented
Class: bug-fix

## Decision

#1218（R13-F06）：`normalize_tool_params` 对 runconsole（T1）工具不做校验
（直接 `return dict(args)`），存在性校验（如 `run_agent_tests` 的 `file_path`）
在执行期 `build_runconsole_plan` 才跑，且该调用位于 `execute_action` 的
approved→running 状态抢占**之后**、异常收口之外——非法参数（不存在测试文件）
会创建 approved 动作、执行期抛 ToolValidationError 外溢，动作**永久停在
running**；人工审批路径还遗留 pending 占位与操作卡。

修复（issue 两项建议都落地）：

1. **创建前校验**：`normalize_tool_params` 对 `kind == "runconsole"` 的工具调
   `build_runconsole_plan(name, args)` 做真实校验（plan 只用于校验即弃）——
   非法参数在提案/创建前即抛 ToolValidationError，轮次把它作为
   「参数校验失败」回给模型，不产生动作；
2. **执行期异常必达终态**（双保险）：
   - `build_runconsole_plan` 移入 ToolValidationError 收口——存量 approved
     动作（本修复前创建的）带非法参数执行时落 `failed`（"参数校验失败：…"）；
   - `execute_action` 顶层兜底 `except Exception` → 记日志 +
     `_finalize_action(action_id, "failed", …)`（摘要经 redact_secrets 脱敏），
     终态回写自身失败只记日志——任何执行期异常都不再外溢成永久 running。

与 ADR-0033 的关系：无直接约束（平台自研助手域）。

## Alternatives

- 只做创建前校验不补执行收口：存量 approved 动作（修复前入库）仍会停在
  running；两处都收才满足「执行期异常必达终态」验收；
- 在 `_create_action` 前逐工具写 if 分支校验：runconsole 工具的校验器就是
  `build_runconsole_plan`，复用它而不是复制规则——规则漂移时一处生效；
- 顶层兜底后再 re-raise：调用方（审批端点 to_thread / auto 轮次）对异常无
  补救动作，终态事实源在 DB——吞掉异常、以 failed 状态表达。

## Verification

- `pytest backend/tests/api/test_ai_assistant_endpoints.py`：39 passed，新增
  3 例——`normalize_tool_params("run_agent_tests", {"file_path": 不存在})` 抛
  ToolValidationError / 合法参数原样通过 / **存量 approved 动作带非法参数执行
  → failed 且 result_summary 含「参数校验失败」**；
- `test_ai_tools.py` + `test_t2b_allowlist.py`：23 passed（tools 域无回归）；
- ruff 干净。

## Revisit

- 「同 Job 需要重新审批」类残留：修复前已永久 running 的动作不在本单清洗——
  可按 `status='running' AND updated_at < 上线时刻` 批量置 failed，另立单；
- runconsole plan 校验是「创建时点」语义：文件在创建后、执行前被删仍会失败，
  此时走执行期收口落 failed（两道闸互补）；
- 其他 kind（service/T2）的参数校验已有各自 normalize 分支，本单未动。
