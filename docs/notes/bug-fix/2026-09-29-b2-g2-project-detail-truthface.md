# 项目详情页真值面修复（#3497 G2）：截断判据改过滤后 total + 四查询失败态 + 零分母「—」

Status: implemented
Class: bug-fix

## Decision

按批次方案 #3497 §3 G2 执行（参照实现 = DedupReportCard 的 statusError 分支），修
`frontend/src/pages/projects/ProjectDetailPage.tsx` 一个文件，三个缺陷面：

- **F2（#3194）截断判据口径**：截断横幅判据原用 `project.device_count` /
  `project.plan_count`（原始计数：设备含退役、计划含 legacy AEE），而卡内列表是过滤后
  集合——原始 >20 且过滤后 ≤20 时「未截断也报截断」。改为：`devicesQ` 直接用响应里的
  过滤后 `total`；`plansQ` 去掉 `.then(r => r.items)`、保留整个分页响应取其 `total`；
  横幅「共 X 台/个」同步改用过滤后 total。卡标题保留原始计数，当过滤后 total 小于原始
  计数时在标题旁注明口径（设备「含退役未显示」/ 计划「含 legacy 未显示」）——否则 #3134
  想止住的「同屏矛盾」以新形态复发。
- **F1 四查询失败态**：`devicesQ` / `plansQ` / `modelsQ` / `riskTrendQ` 均未处理
  `isError`，失败时回落成功空结果语义（「该项目暂无设备」「该项目暂无计划」「当前没有
  设备归属此项目」、结果块 0 Run / 0%）。四处各加失败分支：destructive 色失败文案 +
  「重试」按钮（`refetch()`），与空态/列表互斥，查询成功时空态文案不变。
- **F3 零分母**：`total_runs === 0` 时成功率渲染 0% 与「全部失败」不可分，改为显示
  「—」（KPI 加 `data-testid="success-rate-kpi"` 供断言）。

## Alternatives

- **截断横幅加「含退役/legacy」注解而不改判据**：被否——#3194 的核心是判据与列表不同
  口径，注解只是把谎报写明白，方案 §3 定案判据必须换到过滤后 total。
- **modelsQ / riskTrendQ 复用 ErrorState 整卡替换**：被否——归属规则卡与结果块各有
  骨架内容（规则列表、S 级清单），整卡替换扩大失败面；按参照实现做卡内行内失败条。
- **顺手抽全站通用「查询失败」组件/hook**：方案 §6 明确不做（属重构），逐点按参照实现修。

## Verification

- `npx vitest run src/pages/projects/ProjectDetailPage.test.tsx`：26/26 通过（重写 2 条
  旧截断测试为 #3194 反例对 + 新增 4 条 F1 失败态 + 2 条 F3；devices 失败态含重试恢复链路）。
- 变异自证：`git apply -R` 反向撤销源文件修复（保留新测试）后 7 条新测试全部转红
  （「filtered total 25 出横幅」在旧代码下亦成立、保持绿，属行为钉子；变异敏感的是
  「raw 25 / filtered 18 不出横幅」「四失败态」「—」等 7 条），恢复修复后 26/26 复绿。
- 门禁（均实跑通过）：`npm run lint -- --max-warnings 0`；`npm run type-check`；
  `npm run knip`；`python scripts/run_gates.py check:quick`（16 gates）；
  `python -m pytest tests/ -q`（结果见 PR 正文）。

## Revisit

- KPI 带「设备/Plan」两格仍用原始计数（`project.*_count`），其口径注未纳入本单元方案
  范围；若后续 L2 核对发现同屏歧义，另立单处理。
- §8 登记的证据项（`NotificationBell.unreadQ` 等 20 处非本形态/仅登记查询）不在本单修。
