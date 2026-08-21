# PR-Agent (DeepSeek) 试点 PR review

Status: implemented
Class: process

## Decision

在 GitHub Actions 上试点社区开源 PR-Agent（The-PR-Agent/pr-agent v0.42.0），
用 DeepSeek 做 PR 自动 review，定位为 **纯参考模式**：

- 触发：`pull_request` 的 opened / reopened / ready_for_review /
  review_requested，以及 PR 评论（支持 `/review`、`/describe`、`/improve`、
  `/ask` 等手动命令）。
- 只开 `auto_review`，显式关闭 `auto_describe` / `auto_improve`；
  `synchronize` 不自动复评（与 CodeRabbit `auto_incremental_review=false`
  的既有约定一致，复评走 PR 评论显式触发）。
- 模型只使用 `deepseek/deepseek-v4-flash`（低成本档，无 fallback）；密钥为
  repo secret `DEEPSEEK_API_KEY`，经 `DEEPSEEK.KEY` 注入 LiteLLM 的
  `DEEPSEEK_API_KEY`。
- workflow 直接用 `docker://pragent/pr-agent@sha256:b81235c3...` 固定
  `pragent/pr-agent:0.42.0-github_action` 镜像 digest，不跟随 `main` 或
  mutable 的 `:github_action` tag；也不依赖 checkout 本地 action 文件。
- 不新增 required check；secret 未配置时 job 直接跳过，避免每个 PR 挂红。
- 产物是 PR 评论，不是 status check；未来若要当门禁，需仿照
  `code-rabbit-gate` 由 workflow 包装，不在本次试点范围。

## Alternatives

- 内网 newapi 端点（`newapi.tinno.com`，OpenAI 兼容，含 deepseek-chat /
  gpt-5-chat 等模型）：公网 DNS 不可解析，GitHub 托管 runner 无法访问；
  改用 self-hosted runner 需要常驻内网机器，且会与生产机（生产 PG +
  `.env.backend` + hosts.ini）共享「执行第三方代码」的入口；转私密仓库
  又要消耗 Actions 配额并让 CodeRabbit 免费完整审查失效。三者叠加后
  放弃，选 DeepSeek 官方 API（公网可达、零运维）。
- 自托管 GitHub App（webhook）：需要公网可达的 webhook URL，本仓库生产机
  没有，暂不选。
- 直接用官方 action `the-pr-agent/pr-agent@main`：其 Dockerfile 引用
  mutable 的 `pragent/pr-agent:github_action` tag，action 代码与镜像都不
  固定，放弃。
- 由 CodeRabbit 承担全部 AI 审查：CodeRabbit 配额不稳定且是 required
  best-effort 门禁；试点新增一个参考通道，不改变门禁语义。

## Verification

- `actionlint` 校验通过。
- 首次冒烟发现本地 action 在无 checkout 的 runner 上不可用
  （`Can't find action.yml...`），改为 `docker://` + digest 后验证通过。
- 合入后、配置 `DEEPSEEK_API_KEY` secret 的 PR 上观察评论质量与成本。
- 无 secret 时 job skip，不影响 auto-merge 与合并路径注意力预算。

## Revisit

- 试点 1–2 周后评估：review 质量、DeepSeek 成本、评论噪音。
- 试点若发现 v4-flash 质量不足，可切回 `deepseek-v4-pro`（成本更高）；
  PR-Agent 或 DeepSeek 模型升级时重新 pin digest。
- 若需要 fork PR 支持或门禁语义，再评估 `pull_request_target` 与
  GitHub App 方案。
