# #107 七天自动续跑验收裁决

Status: implemented
Class: process

## Decision

按 owner 2026-09-26 明确的口径，验收自动 CHAIN/SCHEDULE 链连续运行；部署和独立人工验证可在窗口内发生。依据 #107 的 09-18 至 09-24 日检和 09-26 补充日检，在审计主稿 §12 记录判据、例外和结论，并收口 #107。

## Alternatives

严格要求生产零人工动作会把已授权的部署和验证误算为自动续跑失败；只数每日 run 数量则无法区分自动链与 MANUAL 验证。采用 issue 日检中逐日 CHAIN/SCHEDULE 与中止后自动恢复的记录。

## Verification

- 复核 #107 正文及 09-25、09-26 两条日检评论；09-18 至 09-24 每日均有 CHAIN/SCHEDULE 轮次。
- 09-23 run 523 因 force 热更新中止，下一轮 525 自动续跑；09-26 的人工中止后 563 等轮次自动续跑。
- `/home/debian13/stability-test-platform/.venv/bin/python scripts/run_gates.py check:quick`：16 项通过；schema-at-head 因未配置 `DATABASE_URL` 提示跳过。
- 未把本次历史验收推断为 150/3750 容量通过。

## Revisit

若出现需人工续跑的链断档，重新起算七日窗口并按 #107 同口径留痕。容量目标由 #105/#106 单独验收。
