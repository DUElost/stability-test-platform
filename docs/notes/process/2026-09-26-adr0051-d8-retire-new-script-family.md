# ADR-0051 D8 减法：check_new_script_family 退役——D0 归类由族级 kind 登记承担（2026-09-26）

Status: implemented
Class: process

## Decision

ADR-0051 D8「减」清单第 2 项：`check_new_script_family.py`「并入包登记门禁」。它要求新增顶层族的 PR 在
diff/提交说明写一行自由文本归类声明（`ADR-0033 归类：<族> = platform-authored | external-tool`）。
ADR-0051 v1.3（#3250，09-24）起 `tool_manifest.json` 的族级 `kind` 就是这份声明的**机器可读形态**，
`check_script_packages` 已按 kind 强制「族树必须以 kind=script 登记」——两道门禁对同一件事各判一次，
且声明载体一个是自由文本、一个是登记事实。按 09-25 生产验收记录的顺序约束（「仅在替代门禁生效后退役」），
替代已生效一天余，本单退役：

1. 删 `tools/dev/check_new_script_family.py` 与三处接线（`run_gates` gate + quick/pr 两 profile、CI 步骤、
   `check_governance_surface` S5x 配对条目，留出处注释）。
2. `check_script_packages.check()` 把 D0 语义说清：族树挂在 `kind=tool` 条目下 → 「外部工具源码不得入仓
   （ADR-0033 D0）」；未登记 → 「登记即归类声明」（原提示只说「未登记」，kind=tool 挂树时会误导成去登记）。
3. ADR-0033 v1.14（非决策变更：D0 判据不变，声明从自由文本变登记事实）；§5.6 门禁段加退役指针，正文留作历史。

「例外声明格式收敛」（验收记录第 4 条）：仓内已无 `MIGRATION-EXCEPTION` 机制（`git grep` 零命中），
本单删掉最后一种自由文本归类声明后，归类只剩 `kind` 一种载体——收敛即完成。

## Alternatives

- **保留两道门禁**：同一判据两处执法、两种声明载体，正是 D8 要减的形态；弃。
- **把 check_new_script_family 改成读 kind**：等于在另一个文件里重写 check_script_packages 已有的判据；弃。

## Verification

- `tests/test_adr0033_d0_d2_gates.py` 改为**行为钉**（不只断言旧门禁不在）：未登记新族树 → 红；登记为
  `kind=tool` 却挂树 → 红（D0）；`kind=script` 登记 → 绿。用 origin/main 版 `check_script_packages` 跑该用例 → 红
  （旧提示无 D0 语义），修复后 2 passed
- `check_script_packages` self-test 新增「族树登记为 kind=tool 应红」；真树 35 族绿
- 引用 run_gates / 治理守卫 / check_script_packages 的 11 个测试文件 → 84 passed；`check:quick` → **16 gates OK**（少了本门禁）
- 并行核对：`ci.yml` 上 3 条 cursor 记录为 STALE/NO_PR，全部 worktree 无 `ci.yml` 未提交改动

## Revisit

- ADR-0051 状态行「治理减法未收口」：本单完成 D8「减」第 2 项；剩 `check-deploy-source.sh`「D6 落地后由结构替代」——
  D6 已于 09-25 落地，但构建仍从工作树取料（bundle 复制检出 + gitignored 物料），结构替代需「从提交构建 + 外部物料清单」，
  且 `backend/agent/resources/` 随 D7 入包后才离开 bundle——排在 #3288 之后。ADR-0051 状态行随下一次触及该 ADR 的 PR
  更新（避免与在队的 v1.7 #3365 版本行冲突）。
