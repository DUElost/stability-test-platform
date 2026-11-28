# B12 U3：前端 Stage 2「收尾中」统一展示

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 2 单元 U3（`#4206` v1.2）：消费 U2 已同步的 `ui_status` /
`finishing_*` 投影字段，不改 `frontend/src/utils/api/types.ts`（与并行 U4 零冲突）。

1. PlanRun 列表支持 `status=finishing` 筛选。列表与详情 Hero 的主状态服从 `ui_status`：
   纯收尾显示「收尾中」，混合 Run 显示「运行中」。Run 摘要只展示分组计数与最近截止时间点；
   混合 Run 的计数标为「部分设备收尾中」，不附第二个 Run 状态徽章。
2. Job 子类型（`解除配置中` / `刷机临界区保护中`）、触发原因与秒级倒计时仅在设备分页表和
   Job 详情抽屉展示。有效截止随本地时钟更新，到点显示「已到期」，不依赖轮询；无截止不显示倒计时。
3. Minimap 只消费原有 7 字段以及响应级 `offline_failed_job_ids`。收尾设备同时命中离线标记时，
   保留「收尾中」着色和可访问标签，用边框与连接状态提示离线，不将执行态改写为失败。
4. `DeviceFilterBar` 虽未列入方案文件表，实际已增加 `finishing` 词条，使 KPI 的
   `by_status.finishing` 筛选有对应芯片和计数。Run 与 Job 的浅色收尾徽章共享高对比度配色。

## Alternatives

- 在 Hero 下方加第二个 Run 徽章：会使混合 Run 同时显示两个主状态，否决；
  Hero 显示投影主状态，DetailPage 的摘要独立展示设备计数。
- 在 `DeviceOverview` 另做收尾筛选指示：会与现有筛选栏重复，改为复用 `DeviceFilterBar`。
- 列表仍只显示持久化 `RUNNING`：无法满足 D1「收尾中」可视性，否决。
- 在 Minimap 附加子类型字段：破坏契约 §5.1，否决。

## Verification

- 首轮复核反馈 B1–B5 和 B6/B7 已处理。`npx tsc --noEmit`、改动文件 ESLint
  `--max-warnings 0`、`check:quick`（16 项）通过；未配置 `DATABASE_URL`，schema 对齐检查跳过。
- 定向 Vitest 7 文件 159 个用例通过，覆盖 Run 投影与分层、表格和抽屉跨截止、触发文案、
  空截止及七字段 Minimap 的收尾与离线共存。
- 本地 Vite 构建 + Playwright/Chromium API 夹具核验 B1–B5：设备表和抽屉均实际等待
  `5s → 4s → 已到期`，期间 Run、设备表及 Job 详情没有重新请求。浅色列表、Hero 与设备表徽章
  对比度实测约 `8.24:1`；1440×900 / 1280×800 页面横向溢出均为 0，列表筛选控件高度差为 0px。
  本地复核脚本、日志与截图位于 `/tmp/stp-4232-r2/`，不涉及生产或真实设备。

## Revisit

- U7 落地 `force=true` 二次确认与 `force_unsupported_hosts` 回执；本单元明确不做。
- 阶段 2 激活后以线上 B6 解除期 PlanRun 核验「收尾中」投影与 `null` deadline。
