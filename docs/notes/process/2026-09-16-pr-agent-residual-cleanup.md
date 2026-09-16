# PR Agent 下线后残留清理（注释 / 夹具 / secret 指引）

Status: implemented
Class: process

## Decision

#2351 合入后扫残留并收口：

- `ci.yml` 治理面检查注释：去掉「pr-agent 防绕过锚点」旧措辞，改为 S4「禁止
  `pr-agent.yml` 回潮」；
- `queue_head_telemetry.py` 自测假 check 名 `pr-agent-review` →
  `non-required-advisory`（避免与已删 AI review job 混淆）；
- 下线 note Revisit 补 `DEEPSEEK_API_KEY`（与 `PR_AGENT_LLM_API_KEY` 同为无消费者
  orphan secret，待管理员删除）。

不改 `pr-agent-tests` job 名（required check / 分支保护 / 队列脚本耦合面大）。

## Alternatives

- **顺手重命名 `pr-agent-tests`**：否决。它是 pytest 门禁，改名要动分支保护与大量
  文档/脚本，收益只是消歧。
- **批量改写 docs/reviews 历史稿**：否决。快照应按当时事实留档。

## Verification

- `python3 -m tools.dev.queue_head_telemetry --self-test`（或模块内自测入口）绿；
- `python3 tools/dev/check_governance_surface.py --check` 绿；
- `python3 scripts/run_gates.py check:quick`。

## Revisit

管理员确认无其他消费者后删除 repo secrets：`PR_AGENT_LLM_API_KEY`、
`DEEPSEEK_API_KEY`。
