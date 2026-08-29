# PR-Agent LLM 切换 OpenAI 兼容代理

Status: implemented
Class: process

## Decision

官方 DeepSeek API 返回 `Insufficient Balance`，`pr-agent-gate` 全线失败。
改为 OpenAI 兼容代理 `https://ai.hybgzs.com/v1`，模型
`deepseek-ai/deepseek-v4-flash-0731`；repo secret 使用 `PR_AGENT_LLM_API_KEY`
（与平台 AI 助手及其他 LLM 配置解耦）。

Workflow 注入：`OPENAI__API_BASE` + `OPENAI__KEY` + `config.model:
openai/deepseek-ai/deepseek-v4-flash-0731`（PR-Agent / LiteLLM 约定）。

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
