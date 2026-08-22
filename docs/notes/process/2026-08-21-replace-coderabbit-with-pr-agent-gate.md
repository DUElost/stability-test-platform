# 用 PR-Agent 完整替代 CodeRabbit（审查 + 门禁）

Status: implemented
Class: process

## Decision

CodeRabbit 因 OSS 免费档新增「仓库 ≥10 stars 才自动审查」的政策（本仓库 1
star）长期停摆，政策短期不会恢复。决定完整替代：

- **自动审查**：PR-Agent（DeepSeek v4-flash，`pragent/pr-agent` v0.42.0
  digest pin）在 opened / reopened / ready_for_review / review_requested /
  synchronize 时自动 review；每次 push 自动复评并更新同一条 persistent
  comment（v4-flash 单次调用，成本可接受）。
- **门禁**：`pr-agent-gate` 成为 required check，替代 `code-rabbit-gate`：
  - review 对当前 head 完成且无 security concerns → success；
  - 有 security concerns → failure 阻断（B-lite 语义：只有安全问题有否决权，
    其余 findings 仅参考，避免 AI 误报卡合入）；
  - review 未完成 / 工具或 API 失败 / 输出缺失 / `DEEPSEEK_API_KEY` 未配置
    → failure 阻断（fail-closed）；逃生为修复后 push 自动复评、对失败检查点
    rerun，或临时从分支保护摘除该 required check。
- 复评路径：security concerns 阻断后，修复并 push（synchronize）即自动复评
  并重算门禁；PR 评论 `/review` 只更新 persistent comment、不重算门禁。
- 命令通道与门禁分离：`/review`、`/ask` 等命令由独立 `pr-agent-comment`
  job 处理，不产生 required check，避免评论触发的成功 run 顶掉门禁。
- **移除 CodeRabbit**：`enable-auto-merge.yml` 删除 merge-gate job 与
  `pull_request_review` / `status` 触发；删除 `.coderabbit.yaml`；分支保护
  required checks 换为 `pr-agent-gate`；GitHub App 由仓库管理员手动卸载。

## Alternatives

- 语义 A（review 完成即过）：AI 只有流程作用、无否决权，不算「同等作用」，
  未采用。
- 语义 B（所有 findings 阻断）：AI 误报会卡合入，噪音即等待，未采用；当前
  B-lite 只给 security concerns 否决权。
- 保留 CodeRabbit：政策短期不恢复，保留 App 仅剩手动触发价值，卸载。

## Verification

- actionlint v1.7.12 校验通过。
- 替换 PR 自身作为首个冒烟样本：`pr-agent-gate` check 出现、review 完成、
  security 段落解析通过后 auto-merge。
- 分支保护切换后新 PR 的 required checks 列表含 `pr-agent-gate`、不含
  `code-rabbit-gate`。
- 已存在的 open PR（如 #343）在切换后需一次新的 pull_request 事件
  （push / close+reopen）才能获得 `pr-agent-gate` check。

## Revisit

- 试点 1–2 周：观察 security 误报率、review 质量、DeepSeek 成本、合入路径
  时长（预期 2–4 分钟）。
- DeepSeek API 不可用导致合入阻塞的频率若不可接受，再评估「AI 不可用放行」
  的包装语义。
- PR-Agent 或 DeepSeek 模型升级时重新 pin digest。
