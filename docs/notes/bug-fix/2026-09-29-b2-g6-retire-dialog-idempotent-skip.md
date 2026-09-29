# B2-G6：解除退役弹窗「幂等跳过」计数反转——prop 按行为语义收敛为 idempotentSkipCount（#3483）

Status: implemented
Class: bug-fix

## Decision

按 #3497 §3 G6 定案执行（v1.1，规划者定案、不留二选一）：`RetireDevicesDialog` 的 prop 由
`alreadyRetiredCount` 收敛为按行为语义命名的 `idempotentSkipCount`——「本次提交会被后端幂等
跳过的台数」，父层按 mode 计算：`retire` = `selectedRetiredCount`；`unretire` =
`selectedCount − selectedRetiredCount`。组件只消费该数，两种 mode 的既有文案语义保持不变。

- 缺陷（#3483）：修复前两种 mode 都无条件传 `selectedRetiredCount`，而 unretire 的文案是
  「并未退役（幂等跳过）」——读数恰为补集。全选已退役做解除（正常操作形态）会误报「全部 N 台
  将跳过」；全选在役做解除（本应全部跳过）反而不提示。后端判据：`unretire_device` 对
  `retired_at is None` 原样返回（`backend/services/device_retirement.py:189-190`，docstring 明示
  幂等）。
- prop 注释写明契约（父层按 mode 计算）；组件不再隐含「已退役数」语义。调用点经 grep 确认只有
  `DevicesPage.tsx` 一处。
- #3483 的附带观察（unretire toast 把幂等 no-op 计入成功）不处理：属 #3497 §8 登记的证据项，
  §6 明确不做。

## Alternatives

- **保留 `alreadyRetiredCount`、组件按 mode 反转读数**：prop 名与 unretire 语义持续冲突，且反转
  逻辑藏进组件；#3497 v1.1 已裁决改为行为语义命名，不采纳。
- **父层传两个数、组件按 mode 取用**：把 mode 语义复制进 props（冗余），与「组件只消费幂等跳过
  数」的契约相反，不采纳。
- **两处调用内联展开弹窗文案**：重复弹窗结构（原因校验 / 提交态 / 结果回执），属重构，不采纳。
- **顺带改 toast 跳过分解**：#3497 §6 明确不做，退出本批。

## Verification

- `cd frontend && npx vitest run src/pages/devices/components/RetireDevicesDialog.test.tsx src/pages/devices/DevicesPage.test.tsx`
  → 2 files / 19 passed
  - 新增反例：解除退役全选已退役 → 不出现「并未退役（幂等跳过）」；混入 1 台在役 → 提示
    「其中 1 台并未退役（幂等跳过）」；retire 模式读数不变 → 「其中 2 台已是退役态（幂等跳过）」；
    组件侧另钉幂等跳过 0 台时不出现提示、retire 有跳过时显示原读法。
- **变异自证**：把父层改回 `idempotentSkipCount = selectedRetiredCount`（复现修复前读数）→
  两条 unretire 反例转红（`2 failed | 17 passed`），retire 读数用例与组件契约用例保持绿（与
  「retire 模式读数不变」判据一致）；恢复修复后 19 passed。
- `cd frontend && npm run lint -- --max-warnings 0` → OK（无输出）
- `cd frontend && npm run type-check` → OK（无输出）
- `cd frontend && npm run knip` → OK（无输出）
- `python scripts/run_gates.py check:quick` → OK（16 gates）
- `python -m pytest tests/ -q` → 1933 passed, 18 skipped（650.80s；仓库级门禁测试，与本次前端改动无用例交集）

## Revisit

- 弹窗只提示「会被幂等跳过」，提交后的 toast 仍未按 跳过 / 实际翻转 分解（#3483 附带观察、
  #3497 §8）：若后续纳入，改动点在 `DevicesPage.tsx` 的 `retireMutation.onSuccess` unretire
  分支（当前逐台串行只统计 ok/failed）。
