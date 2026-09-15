# 发评论幂等化：`gh_comment_once.py`（#2131）

Status: implemented
Class: process

## Decision

- **问题不是「重试」而是「校验」**：2026-09-15 在 #735 发一条记录时连发 4 条。两处错误——
  ① 用「评论**条数**」判定是否已落地（`before/after` 计数）；② 基线取数失败（代理 EOF）
  时把「未知」当成「未发布」。计数比较在首个基线缺失后恒为假，于是每次都发。
- **幂等键 = 正文尾部的隐藏 marker**（`<!-- gh-comment-once:<marker> -->`）：marker 缺省按
  正文归一化后的 sha256 前 12 位派生（同正文=同键，尾随空白/换行差异不改变结论），
  可用 `--marker` 显式钉住（正文改写仍保持幂等）。
- **判定一律靠 marker 复读，不靠计数**：发布前查重；创建失败后先复读——已落地（响应丢失）
  记为 `created` 且**不重发**；复读确认不存在才允许退避重试。
- **「未知」是独立终态**：查重不可用 → 本次**不发**，退出码 `2`（重跑安全）。这一条是本次
  修复的核心：旧写法在无法判定时会继续发。
- **`--force` 单向逃生门**：跳过查重直接发（可能重复），只在人工确认未发布、且查重长期不可用
  时使用；默认路径下任何「无法判定」都不会发布。
- **落地形态是工具 + 文档**：`tools/dev/gh_comment_once.py`（所有 harness 可复用），纪律写进
  `docs/development/repository-workflow.md`——判定落地靠内容/ID，不靠计数。

## Alternatives

- **只在 Agent Note 里写「注意别发重」**：不解决工具缺失——重复的根因是「没有幂等发布器时
  只能手搓计数比较」；这类临时脚本下次仍会被写出来。否决。
- **对评论做后期去重（发现重复再删）**：删评论是额外破坏性动作（且删错要恢复），把成本推给
  事后。工具侧一次做对更便宜。否决。
- **按「评论数量」或「最近 N 条」保守判断**：两者都会在并发/分页/他人评论场景失效；marker 是
  唯一与内容绑定的稳定判定。否决。
- **把 marker 写进正文可见区**：会污染读者视图（测评/记录类评论经常被引用），改用 HTML
  注释（GitHub 渲染时不可见）。否决可见形态。
- **自动重试直到成功**：正是重复评论的来源（请求已落地、响应丢失）。改为「复读确认后才重试」，
  且无法确认时停手。

## Verification

| 项 | 命令 | 结果 |
|---|---|---|
| 新增单测（离线，注入 fake gh） | `./scripts/run_pytest.sh tests/test_gh_comment_once.py -q` | **18 passed** |
| 反事实 1（变异：查重不可用仍照发） | 注入变异后重跑 | **2 failed**（`unknown` 语义 + CLI 退出码两处转红），还原后 18 passed |
| 反事实 2（变异：创建失败后跳过复读直接重试） | 注入变异后重跑 | **2 failed**（「响应丢失」与「复读不可用」两例转红），还原后 18 passed |
| 真实验证（真实 issue #2131） | 同一 marker 连跑两次 | 第 1 次 `created`（comment 5675552885）；第 2 次 `skipped` 同一条；库内 marker 计数 **= 1** |
| 真实验证·更新路径 | 同 marker + `--update` | `updated`（PATCH 同一条）；随后无 `--update` 重跑仍 `skipped` 且 `comment_id` **仍是 5675552885** |
| repo 自动解析 | 不带 `--repo` 运行 | JSON 输出 `"repo": "DUElost/stability-test-platform"`（来自 `git remote origin`） |
| fail-safe 现场实证 | 代理 EOF 窗口内连跑 4 次 | 4 次均 `unknown`（**未发布**），事后计数仍为 1 |
| 仓库离线子集（PR 路径） | `./scripts/run_pytest.sh tests/ -q --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py` | **739 passed** |
| 门禁 | `.venv/bin/python scripts/run_gates.py check:quick` | `OK (10 gates)` |

> 说明：`tools/dev/check_orphan_models.py` 的 `SyntaxWarning: invalid escape sequence`
> 是既有噪声（纯净主干同样输出），与本单无关。

## Revisit

- **非评论类写动作未覆盖**：本工具只管评论。issue 创建 / 标签 / 里程碑等非幂等写动作仍靠
  「先查后写」，若同类重复事故复发，应把 marker 思路扩散成通用 `gh_write_once`。
- **`gh` CLI 依赖**：工具复用 `gh api`（仓库 AI 工作流既有依赖）。若将来改用 REST + token，
  `GhApi` 三方法（list/create/update）即适配面。
- **代理侧**：EOF 频率与 `NO_PROXY=api.github.com,github.com` 相关（见 `project_local_tooling`
  记忆）；本工具不修改代理配置，只在不可用时安全停手。
