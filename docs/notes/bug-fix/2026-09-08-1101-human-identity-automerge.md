# #1101 修复：队列 auto-merge 改人类身份 PAT，恢复原生关单与事件级联

Status: implemented
Class: bug-fix

## Decision

对账（09-06 以来 41 带关键词合入 PR / 46 关联链接）+ 沙盒实验钉死：**auto-merge
由谁最后启用，GitHub 就以谁的身份执行合入；GITHUB_TOKEN（bot）身份的合入不
触发 linked-issue 原生关闭（生产 39/39 失败），且其 `pull_request: closed`
事件被级联抑制（沙盒实测不点火）**。人类身份合入 4/4 秒级关闭。

修复（根因侧，最小 diff）：

1. `enable-auto-merge.yml` 与 `pr-update-branch.yml` 的三个 reconcile/
   update-branch 步骤 `GH_TOKEN` 改为 `secrets.AUTO_MERGE_PAT ||
   secrets.GITHUB_TOKEN`——队列启用队首 auto-merge 落在人类身份上，GitHub
   执行合入时 `mergedBy=DUElost`，原生关单恢复（秒级），且 PAT 合入产生的
   closed 事件可正常级联（队首合入后下一个 PR 的 reconcile 由事件驱动，
   hourly cron 降为兜底）。secret 未配置时回退 GITHUB_TOKEN，行为与现状
   完全一致。
2. `repository-workflow.md` §关单关键词与自动关单 回填：根因=合入执行者身份，
   「平台故障窗口」与「写法失效因子」叙事作废；纪律改为按 `mergedBy` →
   `closingIssuesReferences` → 写法排序排查；backstop 的「main 前进跳过」
   guard 使其在密集合入日不可依赖，手工核销仍是第一道。

**不移除 bot 合入机制**：FIFO auto-merge 队列是多并行会话的串行集成承重墙
（AGENTS.md 明文），移除=每次合入回到人守；本修复在保留队列的前提下换身份。

## Alternatives

- **移除 bot 身份合入 / 恢复人工合队首**——放弃：破坏 FIFO 串行集成与
  ~2min 注意力预算设计，每个队列合入都需会话显式动手；
- **`pull_request: closed` 触发的独立关单 workflow**——放弃：沙盒实测 bot
  合入的 closed 事件被级联抑制，恰好在最需要它的路径上失灵；仅对人类合入
  生效，与现状增益为零；
- **独立高频 cron 关单 workflow**——放弃：GitHub 对高频 schedule 节流严重
  （08-30 统计 25.6h 仅 4 次 run），延迟不可控；且与既有 hourly reconcile +
  每日 backstop 功能重叠；
- **合并进队首后由 reconcile 直接以 PAT 调 merge API（不经 GitHub auto-merge
  系统）**——放弃：改变队列与 GitHub auto-merge 的分工语义（checks 等待逻辑
  需自建），diff 面大；「换启用身份」即可达成同一 mergedBy 结果。

## Verification

- 行为验证依赖 secret 设置后的首个队列合入（验收标准见 #1101）：本 PR 阶段
  为 **pending**——secret 未设置前 `AUTO_MERGE_PAT || GITHUB_TOKEN` 回退路径
  与现状逐字节一致（表达式语义：未设 secret 求值为空串→取 GITHUB_TOKEN）；
- `run_gates.py check:quick` 7 门禁全绿（gov-surface 覆盖 workflow 变更面）；
- 沙盒证据留档：#1101 正文（mergedBy 交叉表 39/39 vs 4/4、沙盒 closed 事件
  抑制实测、人类身份 1s 原生关闭对照）。

## Revisit

- `AUTO_MERGE_PAT` 为 fine-grained PAT（仅本仓库 Contents/Pull requests RW），
  有过期时间——到期前需轮换（建议日历提醒）；轮换只需重设 secret，无代码
  变更；
- secret 设置前存在过渡窗口：队列仍以 bot 合入、原生关单仍失效——期间依赖
  手工核销 + 每日 backstop；#1101 列出的 7 个存量 issue（#986/#987/#989/
  #990/#1003/#1004/#1006）在该窗口内需人工补关；
- 若 GitHub 未来修正 bot 合入的 linked-issue 语义，本机制冗余但无害（人类
  身份合入本就是被支持的主路径）；
- `main-ci-backstop.yml` 的关单步骤保持 GITHUB_TOKEN（bot 关 issue 可用，
  #868 实证），不换 PAT——它不做合入，无身份语义。
