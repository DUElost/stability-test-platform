# PR-Agent LLM 切换 OpenAI 兼容代理

Status: implemented
Class: process

## Decision

官方 DeepSeek API 返回 `Insufficient Balance`，`pr-agent-gate` 全线失败。
官方 DeepSeek API 返回 `Insufficient Balance` 后曾切到 `ai.hybgzs.com`，但该域对
GitHub Actions（及本机）返回 Cloudflare **1010**，LLM 调用静默失败、门禁找不到
review 评论。现改为公网可达的 `https://api.astrdark.cyou/v1` + `grok-4.6`；
repo secret 仍为 `PR_AGENT_LLM_API_KEY`（须为该端点有效 key）。

Workflow 注入：`OPENAI__API_BASE` + `OPENAI__KEY` + `config.model:
openai/grok-4.6`（PR-Agent / LiteLLM 约定）。

## Alternatives

- 继续充值官方 DeepSeek：短期可行但与公网可达、密钥单一化无额外收益。
- 复用 `DEEPSEEK_API_KEY` 只改值：命名误导（已非官方端点），放弃。
- 内网 newapi：GitHub hosted runner 不可达（见 2026-08-21 pilot note）。

## Verification

- 合入前：`curl` 对 `ai.hybgzs.com/v1/models` 与 `chat/completions` 冒烟通过。
- 合入后：本 PR 自身 `pr-agent-gate` 绿 + 评论含 PR Reviewer Guide；
  可对一条失败 PR rerun `pr-agent-gate`。

## Revisit

- 代理质量/延迟不达标时评估换模型或供应商；secret 名 `PR_AGENT_LLM_API_KEY` 可保留。
- 官方 DeepSeek 恢复余额后是否回迁：仅当成本或合规明确要求时再议。
