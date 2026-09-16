# 下线 PR Agent advisory review

Status: implemented
Class: process

## Decision

删除 `.github/workflows/pr-agent.yml`（自动 review + `/review` 命令通道一并下线）。
同步：

- `pr-update-branch.yml` 的 `workflow_run` 触发去掉 `"PR Agent (advisory review)"`；
- PR 模板去掉 PR-Agent / `/review` 引导；
- 治理面 S4 从「五锚点必须在」改为「`pr-agent.yml` 不得存在」（防回潮）；
- `repository-workflow.md` 与治理设计文档改写现行语义；关闭 §8「findings 修复闭环」观察项。

`pr-agent-tests`（pytest 子集 required check）**不受影响**，名称易混淆但职责不同。

## Alternatives

- **只关自动 job、保留 `/review`**：否决。观察期显示命令通道近乎零使用，保留仍要
  维护镜像 pin、LLM secret、issue 兜底与治理锚点，成本大于收益。
- **路径过滤后继续自动跑**：否决。security 否决权已无真阳性样本；合入路径仍不等
  审查；过滤只能减量，不能改变「评论常在合入后」的结构。
- **重新绑回 required**：否决。08-30 已量化代理无 SLA 与合入赛跑；主防线仍是
  CodeQL + 确定性门禁。

## Verification

- `python3 tools/dev/check_governance_surface.py --self-test` / `--check`：S4 绿
  （文件不存在）；人为 touch `pr-agent.yml` 后 `--check` 应红；
- `python3 -m pytest backend/tests/test_ci_and_test_harness_files.py -q`：模板断言绿；
- `python3 scripts/run_gates.py check:quick`；
- 合入后新 PR 不再出现 `PR Agent (advisory review)` workflow run。

## Revisit

- 若出现「合入后才发现、且确定性 gate/CodeQL 都漏掉」的高危变更 ≥2 次，再议窄语义
  专用门禁（不要恢复通用 LLM review 默认全开）。
- 仓库 secret `PR_AGENT_LLM_API_KEY` 与早期试点遗留的 `DEEPSEEK_API_KEY` 均可在确认
  无其他消费者后由管理员删除（下线 PR 不碰 secrets；残面清理见
  [`2026-09-16-pr-agent-residual-cleanup.md`](2026-09-16-pr-agent-residual-cleanup.md)）。
