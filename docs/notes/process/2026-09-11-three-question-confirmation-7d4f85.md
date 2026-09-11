# 三问确认稿（会话 7d4f85）：R01–R15 覆盖面 / 修复有效性 / 多 Harness 模式

Status: implemented
Class: process

## Decision

按用户指定命名 `REVIEW_THREE_QUESTION_CONFIRMATION_2026-09-11_{本 harness 会话编号后六位}.md`
产出独立确认稿。本 harness 会话 `DSH_SESSION_ID` = `session-f4f2a372-fc73-4756-821d-bfe5ac7d4f85`，
**末六位 = `7d4f85`**（与既有 `_4e188e` / `_e16d6d` 的「去连字符末六位」约定一致；本例带不带连字符同值）。

**内容围绕三问给出独立判定**，不采信同题他源结论，关键数据一律自行重取：

| 问 | 判定 |
|---|---|
| Q1 覆盖面 | **有缺口**：跨区收口未启动（总纲 §6 硬条件）、15/15 轮无逐维覆盖记录、15/15 轮全静态、**148 条 pre-R-era 存量池未被任何轮次纳入**、治理面自身未审 |
| Q2 修复有效性 | **准确=高**（141/141 有修复 commit；1 条 `NOT_PLANNED` 经核为同根因去重关闭）；**有效=高须折价**（第二波缺陷 / 纸面关单 / 止血无出口）；**可持续=否** |
| Q3 模式 | **方向正确、闭环不完整、当前不可持续**；瓶颈在集成收尾侧 |

**最高优先级单点**：`#1246`（FIFO 队首红无解毒路径）——**截至本稿仍 OPEN 且未实施**。

**与早期同题观测的差异（均已重取核实）**：
- R 发现关单 107 → **141**（净欠 70 → **36**），R08/R12 已清零；
- Registry 陈旧率 69% → **53%**（改善但未解决）；
- 09-09 FIFO 停摆中位合入延迟 **1053min（17.5h）** 仍可复现于 PR 数据，09-10 恢复、09-11 仍有长尾。

## Alternatives

- **沿用他源已得出的数字**——否决：实测已变（141 vs 107），沿用即产出错误报告。
- **等一轮再写（避免快照过期）**——否决：高吞吐窗口（09-10/09-11 每日 27–52 PR 合入）
  下"等到稳定"不会到来；改以**显式标注快照日期 + 触发式重取**替代。
- **修正他源稿件中的错误**——否决：按 AGENTS.md「只改当前 Requirement 必需内容」，
  以「关联 + 留痕」标注（如同题稿引用 4 份不存在文件、`R10-F01` 同号双单），
  修正动作另立 D8/D10 待裁决，不在本轮越界改他人产物。
- **产出综合裁决而非确认稿**——否决：用户明确指定命名与体裁为 `CONFIRMATION`；
  汇聚（Mode C 第二阶段）应由综合轮执行，本稿仅作独立输入。

## Verification

命令与结果（均为本稿实际执行）：

- `env | grep DSH_SESSION_ID` → `session-f4f2a372-fc73-4756-821d-bfe5ac7d4f85`；
  `${VAR: -6}` → `7d4f85`。命名依据。
- `git rev-parse --short HEAD` → `e5d05033`；`origin/main` → `187388ba`（基线声明）。
- `gh issue list --limit 1000 --state all --json ...`（594 条）→
  OPEN 209 / CLOSED 385；R 发现 **177 = 141C + 36O**；开放无 R 关联 151，其中 **pre-R-era(<880) = 148**。
- 分轮关单率重算（标题 `Rxx-Fyy|Ryy` 匹配）→ R08 6/6、R12 8/8 清零；
  R10 16/17；R13 欠 7、R05 欠 6。
- `git log --all --grep="#<n>"` 对 141 已关单核验 → 首轮报 5 例"无引用"，
  经查 #1295–#1298 由 commit `32d973c8`（消息以 `1293/1294/...` 无 `#` 形式列举）修复，
  属**检索假阳性**；**净缺失 = 0**。
- `gh api graphql` 批量核验 141 单 `stateReason` → **仅 #949 = NOT_PLANNED**；
  `gh issue view 949 --json comments` → 其末条评论载明与 **#1253 同根因**、已由 PR #1327 修复
  （`host_updater.py:363` + `test_host_updater.py` 回归）→ **正确的去重关闭**。
- `gh pr list --limit 700 --state all --json ...` → 按日开→合中位延迟：
  09-08 26.8min / **09-09 1053.3min** / 09-10 19.8min / 09-11 57.6min。
- `gh pr view 1309 --json statusCheckRollup` →
  `backend-test: COMPLETED SKIPPED`、`frontend-check: COMPLETED SKIPPED`、
  `docker-build: COMPLETED SKIPPED`；
  与 `sed -n '21,26p;80,84p' .github/workflows/ci.yml` 的
  `if: github.event_name != 'pull_request'` 一致 → **"PR 全绿 ≠ 通过测试套件"** 得证。
- `git show --stat` 抽查 #890/#987/#1254/#1248 → 代码 + 回归测试 + Agent Note 三件套齐备。
- `grep -q "## Revisit"` 遍历 180 篇 bug-fix Note → 缺失 20 篇，**全部早于 2026-09-03**；
  09-07 后 100% 合规。
- `python tools/dev/ai_work.py status` → liveness LIVE 103 / **STALE 115**；
  重复认领 #906×3、#909×2、#1211×2、#1198×2、#1123×2。
- `git log --grep=Revert` → 无一条与 R 修复相关。
- 代码级核验 design note D1–D4 **已落地**：`backend/api/routes/auth.py:161-165`、
  `backend/models/user.py:21`、迁移 `n4o5p6q7r8s9`、`backend/services/auth_session.py`（三面收敛）
  —— 而 `docs/design/2026-09-08-session-identity-revocation.md` **仍标 Proposed 且不在 DOC-MAP**。
- `cat scripts/ci/pr-automerge-queue.sh` → 队首-only + 非 SUCCESS 即 `exit 0`；
  有 conflicts 解毒、**无红 check 解毒**；#1246 三条处置未实施。

**未运行**：pytest / vitest / run_gates / docker / 连库 / 真机。
故 Q2「有效性」为元数据与代码级判定，**不是运行验证结论**，已在正文 §6 声明。

**方法错误更正留痕（2 处，均在正文标注）**：
1. `grep -rL "## Revisit"` 误用 `-L` 语义 → 曾得「160/180 缺失」，更正为 20/180；
2. `git log --grep="#<n>\b"` 锚定 `#` → 曾误报 #1295–#1298「无修复引用」，实为假阳性。

## Revisit

- **触发 1（快照失效）**：本稿为 2026-09-11 快照。高吞吐窗口下 issue/PR/registry
  每日显著漂移；**任何引用前必须重取**，或以 `e5d05033` 为锚重算。
  本稿**不原地追加**（避免与 §1.1 计数口径混版本）——触发即**新开同题稿并递增会话后缀**。
- **触发 2（D1 落地）**：`#1246` 修复后，§3.3「集成通道不自持停摆」要件由 ❌ 转 ✅，
  Q3「不可持续」判定须重评——该单是当前唯一结构性硬约束。
- **触发 3（D7 落地）**：PR CI 开始跑 diff 相关测试后，§3.3「验证网」证据失效，
  Q2 的「全量缺陷窗口 24h」论据须撤下。
- **触发 4（D2 启动）**：存量池 §1.2(d) 的「148」须按当时 API 重取。
- **触发 5（D8 落地）**：治理面收口后，§1.2(e) 各项（Proposed 状态、DOC-MAP 缺失、
  `R10-F01` 同号、F/R 序列不统一）应逐条勾销。
- **终结出口**：同题确认稿已存在多份（`fb87d5f1` / `4a955874` / `d00273d0` / 本稿）。
  若继续有多 harness 重复产出同题稿而无汇聚，应回报 ADR-0034 Mode C「先独立、后汇聚」
  的**独立阶段过执行**问题，由综合轮收敛；本稿不建议再增平行稿。
