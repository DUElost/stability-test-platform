# ADR-0059 U1：契约类文档与 SOP 写作约定落地（#3622）

Status: implemented
Class: process

## Decision

按 ADR-0059 §5 U1 落地写作入口：新增约定正文 `docs/development/writing-conventions.md`，
写明适用范围、术语、强度词、SOP 步骤和自查清单；新增 skill `contract-sop-writing`，
内容只转指约定正文；在 `05-data-model.md` 的 PlanRun 节加一条界面名对照
（通知页「任务完成 / 任务失败」= PlanRun）；在 docs/README、DOC-MAP、development/README
登记；PR 模板「文档」节加自查勾选项。`AGENTS.md` 的入口链接属于 U2，不在本单。

## Alternatives

- 对照条目只登记有跨边界证据的一条（界面文案 → PlanRun），没有把「任务」的其他含义集中建表（ADR-0059 §1.6 R2-3）。
- DOC-MAP 行没有链接 ADR-0059：S12 要求 DOC-MAP 中链接 ADR 的行带版本号，带了就要随 ADR 版本同步；
  约定正文已经链接 ADR，DOC-MAP 只写编号，避免新增一个同步面。
- 没有改 `harness-adapters.md`：skill 经现有 `.agents/skills` 链接被 Codex / Cursor 发现，不是新的适配面。

## Verification

- `check_governance_surface.py --check --base origin/main`：S1–S15、S5x 全绿（系统 Python 3.11；本容器无 `.venv`）。
- `tests/test_skill_type_registry.py`、`tests/test_skill_usage_report.py`、`tests/test_memory_lint.py`、
  `tests/test_governance_m7_board_3205.py`：73 passed（临时 venv 只装 pytest，`--noconftest`）。
- `check:quick`（复核返修后，项目入口 `./scripts/project_python.sh scripts/run_gates.py check:quick`）：
  退出码 0，16 个门禁通过。环境是本容器内新建的 `.venv`（`--require-hashes` 安装 `backend/requirements-dev.lock`）
  加 `frontend/` 下 `npm ci`。`schema-at-head` 因未设置 `DATABASE_URL` 按门禁设计跳过（WARN）；
  本次未连接任何数据库，也未使用生产连接串。
- 复核返修（PR #3628 Owner 复核）：skill 的步骤按 D4 拆成「核对适用范围」与「不适用即停止」两步、
  「自查」与「PR 勾选」两步，勾选步骤补「预期：」；步骤外两条禁止分写并写出执行者「作者」。
  `05-data-model.md` 的对照列改为「通知页事件名称中的「任务」（指 PlanRun）」，完整事件名作为证据保留。
- 生效验收（ADR-0059 D5-6），2026-10-09，在本分支提交的临时 worktree 中运行，探针会话只给
  Read / Glob / Grep / Skill 工具：

  | Harness | 正例：新写 SOP | 反例：无关代码问题 |
  |---|---|---|
  | Claude Code CLI 2.1.295（`claude -p`，默认模型） | **PASS**：首个工具调用即 `Skill(contract-sop-writing)`，随后完整读取 `writing-conventions.md`（未截断），草稿按 §3–§4 书写，并把不知道的查法标为缺口 | **PASS**：只做一次 Grep 就回答，未触发该 skill |
  | Codex | UNVERIFIED（本容器无 Codex CLI） | UNVERIFIED |
  | Cursor / OpenCode / Zcode / CodeBuddy CLI / dsh | UNVERIFIED（未运行） | UNVERIFIED |

  限制：每例只跑 1 次；探针由同一工作面发起并判读，存在利益冲突，不算独立证据。
  正例里会话还加载了与题目相关的 `workspace-hygiene`，不影响判定。原始事件流只存在本地临时目录，未入库。

## Revisit

- 按 ADR-0059 §6：U1 正例为 FAIL，或其他 Harness 长期只有 UNVERIFIED 时，复议 D5 的入口设计。
- `contract-sop-writing` 为 persistent 型 skill，适用 14 天 HOLLOW 观察窗（`tools/dev/skill_usage_report.py`）；
  若被判为空洞，先查触发描述是否覆盖真实写作任务，再决定删留。
- U2（`AGENTS.md` 入口链接）落地后，补测只读 `AGENTS.md` 的 Harness。
