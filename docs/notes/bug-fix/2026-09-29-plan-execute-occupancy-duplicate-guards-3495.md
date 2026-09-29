# 执行发起页两个派发前守卫查询失败不再伪装成「空闲 / 无重复」（#3495）

Status: implemented
Class: bug-fix
Batch: B2 / G5（#3497 §3 G5 行，缺陷形态 F1）

## Decision

按 #3497 §3 G5 行实施方案（参照实现 = `frontend/src/components/plan-run/DedupReportCard.tsx`
的 `statusError` 分支，#1195 判据「查询失败不得展示空态」），修复 #3495 描述的两个
「查询失败被读成确定事实」：

1. **设备占用**：`PlanExecutePage.tsx` 的 `activeJobsByDevice` 查询原先只取 `data`，
   失败时 `data` 回落 `undefined` → `occupancyByDeviceId` 为空 Map → 矩阵 / 表格把
   所有设备画成空闲。现在接出 `isError` / `refetch`，并把 `occupancyError` +
   `onRetryOccupancy` 传给 `DeviceMatrix`、`DeviceTablePanel`；两组件在设备区顶部渲染
   「设备占用信息加载失败，无法确认是否空闲。」+「重试」（两视图各自可见，不再只靠
   用户自己察觉）。
2. **重复发起检测**：`duplicateMatch` 查询原先失败时取默认 `null`，与「没有重复」不可分。
   现在接出 `isError` / `refetch` 并传给 `DispatchCockpit`；查询失败且无匹配时驾驶舱
   显示「重复发起检查不可用」+「重试」（有匹配时仍优先显示既有 `DuplicateLaunchBanner`）。

边界（#3497 §3 G5 / §6）：**只提示，不阻断**。不改派发逻辑、不在失败时禁用任何按钮；
后端 `plan_dispatcher_sync` 仍独立检查 `active_lease` / `active_job`，本单修的是前端
真值面，不新增并发安全边界。未抽取通用「查询失败」组件（属重构，另议）。

## Alternatives

- **页面顶部全局横幅**（复用 `PlanExecutePage.tsx:1034-1070` 的 hosts/scripts 横幅块）：
  被否——「空闲」的错误结论发生在设备瓦片 / 占用列旁边，远离顶部横幅；且 #3497 §3 G5
  行把 `DeviceMatrix.tsx` / `DeviceTablePanel.tsx` / `DispatchCockpit.tsx` 列入 scope，
  提示应在结论现场。
- **失败时把瓦片改画为「占用未知」第三态**：被否——#3497 §6 明确不做失败时阻断/新增
  状态语义；且 `resolveDeviceTileStatus` 的 busy 语义还被 `device.status === 'BUSY'`
  独立驱动，改色会与后端状态语义纠缠，超出本单。
- **失败时禁用派发按钮**：被否——#3497 §6 明确「G5 只提示，不禁用派发」；禁用会以
  「看不到占用」为由挡掉操作者，反而制造新的假阻塞。

## Verification

- `npx vitest run src/pages/execution/PlanExecutePage.test.tsx
  src/components/execution/plan-execute/DeviceMatrix.test.tsx
  src/components/execution/plan-execute/DeviceMatrix.virtual.test.ts` → 3 files / 66 passed。
  新增 3 条反例（#3497 §4 G5）：
  1. 占用查询失败 → 矩阵与表格视图均出现「占用未知」提示；仍可选中 → 预览 → 确认发起
     （按钮可用性与改动前一致）。
  2. 重复检测失败 → 驾驶舱出现「重复发起检查不可用」+ 重试；不出现重复横幅；预览 /
     确认发起仍可用。
  3. 两个查询成功且为空（无占用、无重复匹配）→ 两个提示均不出现。
- 变异自证（临时改回后复跑，随后 `git checkout` 恢复）：
  - 去掉占用提示渲染（`occupancyError ?` → `false ?`）→ 用例 1 转红；
  - 去掉重复提示渲染（`duplicateCheckError ?` → `false ?`）→ 用例 2 转红；
  - 提示改为无条件渲染（条件 → `true ?`）→ 用例 3 转红（证明成功空态守卫有判别力）。
- 通用门禁：`npm run lint -- --max-warnings 0`、`npm run type-check`、`npm run knip`、
  `python scripts/run_gates.py check:quick`、`python -m pytest tests/ -q` 全绿（逐条结果
  见 PR 正文）。

## Revisit

- 占用未知时瓦片仍按「无占用」底色渲染（本单只加横幅）。若后续要求就地可见（如瓦片
  打问号态），需要 `resolveDeviceTileStatus` 之外的独立「未知」语义与设计裁决，另立单。
- 提示的可见位置在设备区顶部；若未来设备区改为可折叠 / 分页顶栏，需复核提示是否仍在
  首屏（当前两视图均不折叠）。
- 重复检测查询 `enabled` 依赖「已选设备 + 近期 run」，选择集为空时不查询也不提示——
  语义正确（无可重复的对象），但若未来该查询扩展到「仅选 Plan」即可检测，需重新核对
  本处的提示条件。
