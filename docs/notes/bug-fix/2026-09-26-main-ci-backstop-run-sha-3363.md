# 夜间兜底运行与主干 SHA 绑定（#3363）

Status: implemented
Class: bug-fix

## Decision

09-26 兜底运行 `36188684216` 派发了主干 `56fcb036` 的全量 CI
`36188703274`，但派发后轮询只按 `branch=main` 和 `event=workflow_dispatch`
筛选，选到 09-24 的旧失败运行 `36058517011`，导致错误失败结论和 #3247
错误归因。本次把派发前快照和派发后轮询都绑定到 `main_sha`：轮询还要求
`run.id` 大于派发前同 SHA 的最大 ID，取符合条件的最大 ID；若新运行尚未
出现在列表中，继续等待。选定运行后读取其 `head_sha`，不匹配即失败。

`notify-failure` 在重跑与评论之前再次核对运行 SHA，评论前也复核。
这样即使上游以后改变输出，旧运行也不会被重跑或当作本轮失败归因。
派发前快照记录数量及首尾 ID，供再次出现索引延迟时定位。

## Alternatives

- 只扩充 `pre_existing_ids` 列表：不采纳。分页或索引瞬时不全仍可能遗漏旧运行。
- 使用 dispatch API 的 `return_run_details=true`：本轮没有验证返回体，不依赖该能力。
- 降低全量 CI 失败门槛：与本缺陷无关；#3247 的性能回归继续单独处理。

## Verification

- `python -m pytest tests/test_main_ci_backstop_guards.py tests/test_backstop_attribution.py -q`：15 passed。
  测试直接执行工作流中的 jq 过滤器，覆盖旧失败运行先出现、新运行延迟、
  异 SHA 运行出现、同 SHA 新运行出现及同 SHA 旧运行已在快照中的场景。
- `python scripts/run_gates.py check:quick`：16 gates 通过；未配置
  `DATABASE_URL`，`schema-at-head` 按门禁定义跳过。
- `bash -n` 检查三个改动的 workflow 脚本块：通过。
- GitHub REST 只读查询 `head_sha=56fcb036…&event=workflow_dispatch`：
  只返回真实运行 `36188703274`，其响应 `head_sha` 与查询一致。

## Revisit

合入后下一次真实兜底验证应确认日志中的运行 ID、`head_sha` 与派发目标
一致；全量 CI 的通过与否仍以该运行的实际 job 结论为准。
