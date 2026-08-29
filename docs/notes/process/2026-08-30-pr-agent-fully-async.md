# PR-Agent 顾问模式收尾：全异步 + security concerns 走 issue

Status: implemented
Class: process

前一步（[2026-08-29-pr-agent-advisory-mode](2026-08-29-pr-agent-advisory-mode.md)）把
pr-agent-gate 从 required checks 摘除并移除 #421 disable-auto。本条收尾剩下的
两个不一致：check 仍会因基础设施故障变红，以及 security concerns 没有留得住
的载体。

## 决定了什么

- job 更名 `pr-agent-gate` → `pr-agent-review`：它既不 gate 也不被等待，
  旧名字在常驻文档驱动的仓里会持续误导
- **判定全部 exit 0**，docker 步加 `continue-on-error` + 步级 12 分钟超时：
  LLM 代理故障 / 输出缺失 / 无法解析一律绿 + `::warning::`
- **security concerns 改走单独 issue**（标题 `[pr-agent] PR #<N> 报告 security
  concerns`，@ PR 作者，正文含审查输出摘录），同一 PR 复评只追加评论
- 唯一会变红的情况：有 security concerns 却连 issue 都开不出来 —— 那是
  「发现没送达」，值得分诊
- 治理检查器同步：S5 不再把 pr-agent.yml 当 required check 来源；S4 新增
  锚点「security concerns 开 issue 兜底」，防止唯一送达路径被静默删除

## 为什么（实测数据，2026-08-22 ~ 08-29）

| 事实 | 数值 |
|---|---|
| pr-agent 运行时长 | median 133s / p90 300s / 代理故障时 45–60 分钟 |
| required checks 长极（ci.yml） | median ~125s（CodeQL 仅 ~70s） |
| 8 天 failure | 15 次，其中 **12 次是 LLM 代理故障**（假阳性），3 次真 security concerns（涉 2 个 PR） |
| review 实质产出 | 近 20 个 PR 中 9 个有 `Recommended focus areas`，定位到 file:line 且可复现 |

两条推论：

1. **合入根本不等它**。auto-merge 只等 required checks（~125s），而审查中位数
   133s 才结束。所以「gate 失败时关 auto-merge」是一场中位数就已经输的赛跑，
   偶尔生效反而比稳定无效更糟 —— 这是移除它的正当理由，不只是「顾问模式
   语义一致」。
2. **价值在评论，成本在 check 颜色**。高质量产出集中在不参与判定的
   `Recommended focus areas`；而红叉 80% 来自代理故障，纯分诊税。

## 放弃的备选

- **恢复 required + fail-open**（只在 security concerns 红、故障放行）：安全
  阻断变确定性，代价是合入 median +8s / p90 +175s。被否：注意力预算优先，
  且本仓主防线是人工评审 + CodeQL（确定性、已 required、覆盖同一维度）。
  8 天仅 2 例真发现，不值得把合入路径绑定到无 SLA 的自建 LLM 代理。
- **security concerns 只发 PR 评论**：PR-Agent 的 persistent comment 会被下次
  复评原地覆盖 —— 本次调查中那 3 次真实阻断具体报了什么已经无法复原，
  正是这个覆盖行为造成的。issue 才有去重、追溯、关闭语义。
- **完全不判定、纯跑 review**：省不了多少，却丢掉 security concerns 的自动
  分流。

## 如何验证

- `python3 tools/dev/check_governance_surface.py --self-test` / `--check` 全绿；
  反例验证：把 workflow 里 `Open follow-up issue on security concerns` 改名 →
  `--check` 报 S4 丢失锚点、exit 1（已实测）
- 判定分支用 fixture 跑过三态（真实 "No security concerns" 评论体 / 合成
  security concerns / 空输出），三者均 exit 0，输出 `security=clean|concerns|unknown`；
  摘录管道对 PR-Agent 的 HTML 表格能剥出可读正文
- `No security concerns` 是 `Security concerns` 的超串，判定顺序（先查前者）
  保证 clean 不会误判为 concerns —— fixture 已覆盖

## 何时重议

- 出现「AI 报了 security concerns 但 PR 已合入且无人处理」的实例 ≥2 次 →
  考虑恢复 required + fail-open（上面「放弃的备选」第一条，代价已量化）
- LLM 端点获得可用性保证（不再是自建代理）→ fail-closed 的成本假设失效，
  可重新评估
- 若引入供应链投毒类高危变更的自动阻断需求 → 应做窄语义专用门禁，而不是
  把通用 AI review 重新绑上合入路径
