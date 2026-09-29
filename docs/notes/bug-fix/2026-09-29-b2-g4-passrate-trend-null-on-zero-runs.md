# B2-G4 通过率趋势图零填充日画 null（#3186）

Status: implemented
Class: bug-fix

## Decision

批次 B2 单元 G4，按规划方案 #3497 §3 G4 行执行：`PlanRunPassRateTrendChart` 的
`chartData` 映射把 `run_count === 0` 的后端零填充日置 `ratePct: null`（recharts 默认
`connectNulls=false`，折线在该点断开），tooltip 对 null 显示「—」而非 `0%`。
缺陷形态 = #3497 §1 F3「无数据被画成 0」：分母为 0 时 `avg_pass_rate` 无真值，
画成 0% 与「全部失败」不可分。同形态参照（KPI 显示「—」、以计数字段判零）见
§1 F3 行；§3 G4 行同时明确**不改**同目录 `PlanRunFailedDeviceTrendChart`
（其 0 是合法计数，不是比率）。

## Alternatives

- 后端不再零填充、直接省略无数据日：被否——#3497 §0 硬边界「只做前端」，且零填充
  序列对前端 X 轴定序有意义，改动后端口径属升级 Owner 事项。
- 前端把 `run_count === 0` 的点从序列中过滤掉：被否——丢点会让折线跨越无数据日
  直接相连，视觉上仍是"编造连续性"；null 断线才表达「这天没有事实」。
- tooltip 显示「无数据」等整句文案：未选——§3 只要求「不显示 0%」，F3 参照实现的
  展示惯例是「—」，逐点按最小方案执行。

## Verification

- 新增反例 4 条（#3497 §4 G4 的两条 + tooltip 分支两条，钉在调用点——桩 recharts
  取 LineChart 实际拿到的 `data`，先例 PlanFailedDevicesChart.test.tsx #2987）：
  `run_count=0` → `ratePct` 为 null；`run_count>0 且 avg_pass_rate=0` → 仍为 0；
  tooltip 对 null 点显示「—」不显示 0%；真实全失败日照常显示 0%。
- 变异自证：撤映射条件（恢复无条件 parseFloat）→ 反例 1、3 转红，控制组 2、4 绿；
  只撤 tooltip null 分支 → 反例 3 转红；恢复后 8/8 绿。两处修复面各自独立被抓住。
- 门禁（均实际运行）：`npm run lint -- --max-warnings 0` rc=0；`npm run type-check`
  rc=0；`npx vitest run src/components/charts/PlanRunPassRateTrendChart.test.tsx`
  8 passed；`npm run knip` rc=0；`python scripts/run_gates.py check:quick`
  16 gates OK；`python -m pytest tests/ -q` 1933 passed / 18 skipped，rc=0
  （套 systemd-run MemoryMax=6G 硬顶执行）。

## Revisit

- 若后端零填充语义变更（例如新增 `has_data` 标志），判零字段应从 `run_count`
  改钉到新标志，反例随之迁移。
- §1 F1 类「查询失败被读成确定事实」在本组件的对应面（isLoading 之外的 isError
  呈现）由消费方页面处理，不在 G4 范围；若父页查询失败仍走空态，属 #3497 §8
  登记证据路径，待后续批次裁决。
