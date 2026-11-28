# B12 U3：前端 Stage 2「收尾中」统一展示

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 2 单元 U3（`#4206` v1.2）：消费 U2 已同步的 `ui_status` /
`finishing_*` 投影字段，不改 `frontend/src/utils/api/types.ts`（与并行 U4 零冲突）。

1. `status-badge` / `planRunStatus` 统一「收尾中」文案；子类型
   （`解除配置中` / `刷机临界区保护中`）与倒计时 helper 集中在 `planRunStatus.ts`。
2. PlanRun 列表支持 `status=finishing` 筛选；行内在 `ui_status=finishing` 时渲染
   投影徽章（持久化 `status` 仍为 `RUNNING`）。
3. PlanRun 详情 Hero 下方展示 Run 级 `finishing_kind_counts` 与最近
   `finishing_deadline_at`（`null` 不渲染假倒计时）。
4. KPI（`by_status.finishing`）/ 设备表 / Job 抽屉 / Minimap 统一消费
   `job_exec_status=finishing`；Minimap 仅靠 7 字段着色，不假设格子携带
   `finishing_*`。
5. 子类型文案与截止倒计时仅出现在设备表等待列与 Job 详情抽屉。
6. `DeviceFilterBar` 不在方案文件列表：KPI 切到 `finishing` 时由
   `DeviceOverview` 补激活态指示条（偏离说明见 PR）。

## Alternatives

- 改 `PlanRunHero` 承载 Run 级摘要：Hero 不在方案文件列表，改为 DetailPage
  Hero 下方摘要块（偏离说明见 PR）。
- 越界修改 `DeviceFilterBar` 加 chip：与方案文件边界冲突；改用 KPI + Overview
  激活指示，观察记入 PR。
- 列表仍只显示持久化 `RUNNING`：无法满足 D1「收尾中」可视性，否决。
- 在 Minimap 附加子类型字段：破坏契约 §5.1，否决。

## Verification

- `npx tsc --noEmit` → 通过
- `npx eslint <touched> --max-warnings 0` → 通过
- `npx vitest run`（planRunStatus / status-badge / PlanRunKpiGrid /
  DeviceOverview / DeviceDetailDrawer / PlanRunListPage / PlanRunDetailPage）
  → 155 passed
- `./scripts/project_python.sh scripts/run_gates.py check:quick` → 16 gates OK
- ui-preview / Playwright 截图自检 →（见 PR 正文；`ui_review_env.py up --sha 3dac7ebfe`）

## Revisit

- U7 落地 `force=true` 二次确认与 `force_unsupported_hosts` 回执；本单元明确不做。
- 阶段 2 激活后以线上 B6 解除期 PlanRun 核验「收尾中」投影与 `null` deadline。
- 若产品要求 DeviceFilterBar 原生 chip，可单开小补丁纳入过滤条（本批保持零越界）。
