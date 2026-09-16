# PR-Agent 复评状态评论收敛：自动 job 关闭 final_update_message

Status: implemented
Class: process

#2124（2026-09-15）上 github-actions 留下 4 条评论：1 条 `## PR Reviewer Guide`
（persistent，原地更新）+ 3 条 `**[Persistent review](…)** updated to latest commit …`
状态行。本 note 决定收敛后者。相关决策见
[2026-08-30-pr-agent-fully-async](2026-08-30-pr-agent-fully-async.md)（顾问模式定位）。

## Decision

`.github/workflows/pr-agent.yml` 的自动 review job（`pr-agent-review`）env 增
`pr_reviewer.final_update_message: "false"`；`/review` 命令 job（`pr-agent-comment`）
不动。

影响面：

- guide persistent comment 仍原地更新，头部仍带 `#### (Review updated until commit <url>)`
  ——「当前 head 已复评」的信息不丢，一个 PR 上 github-actions 的评论数回落到 1（guide）；
- 判定步 `Read PR Agent review verdict` 读的是 guide 正文里的 security 段落，不受影响；
- S4 治理锚点（digest pin / fallback_models / job 分离 / security 判定与 issue 兜底）
  均未触碰。

根因在镜像内代码，不是本仓配置错误：`publish_persistent_comment_full` 更新既有
persistent comment 之后，若 `final_update_message` 为真就单发一条状态评论；
`[pr_reviewer] final_update_message` 默认 `true`。pinned 镜像 digest 经 Docker Hub
tags API 反查 = `pragent/pr-agent:0.42.0-github_action`（2026-08-08 构建），其
`pr_reviewer.py:191` 读取该键并透传，返回值无消费方。

## Alternatives

- **不动**（维持每次复评一条状态评论）：近 25 个已合入 PR 共 15 条（13 个 PR 有，
  `#2212`/`#2217` 各 2；另 `#2124` 实测 3 条）。频率不高，但每条都不携带新信息，
  且工作流注释「复评更新同一条 persistent comment，不刷屏」与事实不符——留着就要
  么改注释要么改行为；
- **减少复评触发**（去掉 push 触发）：会丢掉「当前 head 必有审查意见」这个既有意图，
  代价大于收益；
- **命令 job 一并关闭**：人工 `/review` 场景里那条状态行就是回执，保留；
- **用 `PR_REVIEWER.FINAL_UPDATE_MESSAGE` 大写形式**：语义等价（dynaconf 大小写不
  敏感），但本仓 workflow 统一用 `<section>.<key>` 小写点号形式（`config.model` 等），
  随大流。

## Verification

- 2026-09-16 本机：`yaml.safe_load` 解析 workflow，确认键落在自动 job 的 env、
  命令 job 无该键；
- 2026-09-16 本机：dynaconf 3.3.5（该版本约束 `>=3.3.5,<4`）以 v0.42.0 的
  `configuration.toml` 为基线实测——env `pr_reviewer.final_update_message=false`
  使该键变 `False`，同 section 的 `persistent_comment` 仍为 `True`（覆盖不误伤相邻键）；
- 源码核对（v0.42.0）：`pr_reviewer.py:191` 读取并透传该键；
  `git_provider.publish_persistent_comment_full` 是唯一发状态行处；
- `scripts/run_gates.py check:quick`（含 gov-surface S4 锚点）→ `OK (10 gates)`；#2243 的
  六个 required check（lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests /
  pr-migrate-empty-db）全绿；
- **端到端实测（合入 main 后，2026-09-16 03:12 起）**：
  - 运行日志（run 35051253658，03:18:40）显示 `pr_reviewer.final_update_message: false`
    随 docker 步的 `-e` 透传进容器——该键确实生效，不是被静默忽略；
  - 正例 #2245：guide 评论 03:11:34 创建、**03:20:10 原地更新**（同一次复评写入），
    该 PR 至今 github-actions 评论数 = 1，无状态行；合入前对照 #2241 在 03:02:43 有状态行；
  - 反例扫描：合入时刻（03:12:10）之后，最近 25 个 PR 的状态行数 = 0。

## Revisit

- 若「当前 head 已复评」确有可观测性需要（例如 reviewer 依赖那条状态行判断最新审查
  落在哪个 commit），改回 `true`，并另找更省的载体承载该信号；
- PR-Agent 升级时按 `configuration.toml` 的 `[pr_reviewer]` 段核对该键仍在（镜像
  digest 反查 tag 的方法：Docker Hub `tags` API 按 digest 匹配）。
