# 三问多源汇聚裁决（R01–R15 覆盖 / 修复有效性 / 多 Harness 模式）

Status: implemented
Class: process

## Decision

同题已有 ≥5 份独立稿（`CA-*` fb87d5f1、`IV-*` 4a955874、`CF-*` d00273d0、4e188e、e16d6d）
**全部标注「待综合」**，而 ADR-0034 规定的「先独立、后汇聚」中**汇聚阶段零执行**。
故本轮不新增第 6 份平行意见，改为补齐缺失的收敛步骤，产出
[`docs/reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_pf8rII-convergence.md`](../../reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_pf8rII-convergence.md)。

**两处分歧裁定**（均有本稿独立证据，不采信源稿结论）：

1. **Q1**：CA-C03 称「无整块被遗忘的业务域」→ **证伪**。
   全量解析得 **150 条 pre-R-era 开放 issue**（IV 的 44 条口径低估），
   其中含 4 条**已实测**性能缺陷（#730/#731/#740/#741，实测 70× / 8.9s→16.5s）
   与多条数据丢失路径（#789/#792/#793/#796）。
   裁定 **IV 方向正确、CA 否定论不成立**。此裁定有实际后果：若采纳 CA，
   下一步会跳过该池治理。
2. **Q2**：CA 判「可持续=条件成立」，IV/CF 判「条件已被违反」→ **裁定后二者正确**。
   决定性证据：`ci.yml:22-25,81-83` 中 `backend-test`/`frontend-check` 的
   `if: github.event_name != 'pull_request'` → **PR 上恒为 SKIPPED**，
   全量缺陷发现窗口最长 24h；且 `Main CI backstop failed` 自动 issue 共 8 条，
   **失败不阻断任何合入**。

**五稿一致、无需再议**：`#1246`（FIFO 队首红无解毒路径）为最高优先级单点。

## Alternatives

- **再出一份独立稿**——否决：同题重复劳动的边际信息已趋零，而收敛价值（裁决/去重/
  优先级）未兑现；继续平行会放大而非缩小认知负担。
- **直接采信已有某一稿**——否决：CA 与 IV 在 Q1/Q2 上**直接冲突**且均无相互引用，
  不裁定则两个相反结论同时生效，后续 harness 无法据以排期。
- **只报告不落文档**——否决：ADRs/Notes 是本仓库的复利载体；
  不留痕则下一个 harness 会重复第 7 次独立审计。
- **顺手修正 source 稿的错误**——否决：按 AGENTS.md「只改当前 Requirement 必需内容」，
  本稿以「修订留痕」方式记录两处错误（CA-C03、4 份悬空引用），
  修正动作另立 D8/D10 待裁定，不在本轮越界改他人产物。

## Verification

- `gh issue list --limit 1000 --state all --json ...`（593 条）→ 解析得
  OPEN 245 / CLOSED 348；R 发现 177（107C/70O）；无 R 关联开放 154，其中 **pre-R-era 150**。
- `gh pr list --limit 600 --state all --json ...`（600 条）→ 按合入日的开→合中位延迟：
  09-07 7.7min / 09-08 26.8min / **09-09 1053.3min** / 09-10 74.7min
  （09-09 跳升与 #1246 停摆时间线吻合）。
- `git log --all --grep="#<n>"` 对 **107/107** 已关 R 单核验 → **missing = 0**。
- `git show --stat` 抽查 4 单（#890/#987/#1254/#1248）→ 代码 + 回归测试 + Agent Note 三件套齐备。
- `git log --grep=Revert`（6 条）→ 均与 R 修复无关；`leader_election.py` /
  `precheck_reaper.py` / `auth_session.py` 修复后各仅 1 commit（无返工）。
- Revisit 合规：180 篇 bug-fix Note 中 20 篇缺失，**全部早于 2026-09-03**；
  09-07 后 100% 合规。
- `python tools/dev/ai_work.py status` → liveness LIVE 49 / **STALE 110（69%）**；
  重复认领 #906×3、#909×2、#1211×2、#1123×2。
- 代码级确认：`backend/services/auth_session.py`（D3 三面收敛）、
  `backend/api/routes/auth.py:161-165`（D1 `sub=str(user.id)` + D2 `ver`）、
  `backend/models/user.py:21` + 迁移 `n4o5p6q7r8s9`（`token_version`）→
  design note D1–D4 **已落地但文档仍标 Proposed**。
- `scripts/ci/pr-automerge-queue.sh` 通读 → 确认队首-only + 非 SUCCESS 即 `exit 0`，
  有冲突解毒路径、**无红 check 解毒路径**。

**未运行**：pytest / vitest / run_gates / docker / 连库 / 真机。故 Q2「有效性」为
元数据与代码级判定，**不是运行验证结论**。

**方法更正留痕**：首轮用 `grep -rL "## Revisit"` 得出「160/180 缺失」，
系误用 `-L` 语义（`-L` 输出不含匹配的文件，需配合 `grep -q` 判定）。
更正后为 20/180。已按「命令成功 ≠ 验证通过」纪律在正文 §3.2 留痕。

## Revisit

- 触发 1：预算/优先级允许启动 **D1（#1246 队首红解毒）** 时，
  本稿 Q3「不可持续」判定应重新评估——该单是当前唯一的结构性硬约束。
- 触发 2：**D2 存量池（150 条）** 启动后，§2.1 的「150」需按当时 API 重取；
  本稿数值为 2026-09-11 快照。
- 触发 3：D4（修复后独立复核）落地后，§3.3「第二波缺陷」折价应重测：
  若新一批修复不再产生 `修复后残留` 单，则 Q2 可持续性判定可上调。
- 触发 4：**D7 落地（PR CI 跑 diff 相关测试）后，§3.1 证据 A 失效**，
  Q2 的「验证网为红」论据须撤下。
- 触发 5：总纲 §5 若完成跨区收口（R17），§2.3 的 P0 缺口应关闭并回填链接。
- 终结出口：本稿为**汇聚快照**，非 Living 文档；上述任一触发命中时**新开汇聚稿**，
  不原地追加（避免与 §2 计数口径混版本）。
