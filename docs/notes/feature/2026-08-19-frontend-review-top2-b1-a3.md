# 前端 UI 审查 top 2 实施（B1 图壳 + A3 状态徽章统一）

Status: implemented
Class: feature

依据：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 推荐 ②（B1 + A3）。
两条共用一个规矩：状态与图表文案只有一套中文。

## Decision

- **A3**（`status-badge.tsx` / `ResultsPage.tsx` / `tokens.ts`）：REGISTRY 新增
  `job-result` kind，键集 = results API 出参映射全集 **5 键**
  （QUEUED 排队中 / RUNNING 运行中 / FINISHED 完成 / FAILED 失败 /
  CANCELED 已中止）。`RUN_RESULT_STATUS_CHIP` 整体删除（唯一消费者
  ResultsPage 已切换）。
  - `resolveStatusEntry` 加可选第三参 `{ fallbackToRaw }`，`StatusBadge` 加
    同名 prop（默认 false）：未命中且状态非空时回显**原文**（灰底 +
    HelpCircle），而非「未知」。仅 ResultsPage 开启。动因：
    `_normalize_job_status` 是 `.get(raw, raw or "RUNNING")` 原样透传，
    后端新增状态时运维不应只看到「未知」。
  - CANCELED 用 **secondary（中性灰）** 而非 JOB.ABORTED 的 destructive：
    主动取消 ≠ 失败，随 GitHub Actions / GitLab CI 的 cancelled 惯例；
    warning（琥珀）保留给降级/不稳定。
- **B1**（四张仪表盘图）：外层区块标题（Dashboard.tsx 的四个 h4）保留，
  内层卡的 CardTitle/CardHeader 全删——**每张图都有空态+正常双分支，共
  8 处**，一处不删就在空态复现。卡壳 `border-none shadow-none`（保留
  bg-card 软分区）。图例 `LABELS` 中文化并与 `device-ui` 注册表同词
  （空闲/测试中/离线/错误）；同组件内 tooltip「N devices」→「N 台设备」、
  空态「No devices/No hosts available」→「暂无设备/暂无主机」一并中文化。
  两张趋势图无人使用的 `title` prop 摘除。
  范围外留置：三张排行榜图（方案成功率/节点失败率/通过率趋势）为自标题
  单层结构，非 B1 双层病灶，不动。

## Alternatives

- **ResultsPage 侧判断命中后自渲染灰 chip**（否决）：一行里保两种渲染
  路径正是 A3 要消灭的病；开关制把语义收进 status-badge 单点。
- **CANCELED 与 JOB.ABORTED 同色 destructive（严格"与 job 同色"）**
  （否决）：取消与失败视觉同级会误导排期判断；行业惯例中性灰。
- **job-result 继承 JOB 表再覆盖**（否决）：REGISTRY 是整表查找无继承，
  引入继承机制改动更大；5 键全表最直白。
- **DISPATCHED 带入新表**（否决）：死键。backend 无写入路径
  （同名仅 `DISPATCHED_TIMEOUT_SECONDS` 配置变量）；`job_instance.status`
  是 PG ENUM `job_status`（成员 PENDING/RUNNING/COMPLETED/FAILED/ABORTED/
  UNKNOWN，`validate_strings=True`），DISPATCHED 非成员写不进；生产库
  DISTINCT 实测 COMPLETED 379 / ABORTED 286 / RUNNING 50 / FAILED 8。

## Verification

- 门禁：`tsc --noEmit` / eslint（8 个改动文件 `--max-warnings 0`）/
  **全量 vitest 78 文件 588 用例**（新增 6：job-result 中文标签 ×1、
  RUNNING=info ×1、CANCELED=secondary ×1、fallbackToRaw 三态 ×3）/
  `vite build` 全绿。既有 status-badge 用例未改一行仍绿（fallbackToRaw
  默认关闭的兼容性证明）。
- DOM 实测（`/tmp/ui-shot-rig/verify-top2.js`，1440×900）：
  - A3 `/results`：30 行状态列全部 `data-kind="job-result"` 徽章、无旧式
    方角 chip 残留、表格无横向溢出；**拦截 summary 把首行 status 改为
    `MAGENTA` → 渲染出「MAGENTA」原文而非「未知」**（fallbackToRaw 透传
    实证）；
  - B1 `/`：无 "Device Status"/"Host Resources" 文本节点；四个外层标题
    在位且各区块内层 h3=0；图例四项全中文（空闲/测试中/离线/错误）；
    四区块图卡 computed borderTopWidth 全部 0px。

## Revisit

- `DEDUP_STATUS_CHIP`（tokens.ts）是否并入 StatusBadge 注册表——审查 §7
  钩子，模式已就绪（照 job-result 加 kind 即可）。
- 图例词表目前是 DeviceStatusChart 内的 LABELS 字面量，与 status-badge
  注册表同词但两处维护；若第三处需要同词，抽共享枚举表。
- `fallbackToRaw` 目前只有 job-result 消费；其他后端原样透传状态的场景
  （如有）可复用，不必默认开启。
- 三张排行榜图若未来加外层区块标题，会复现 B1 双层病灶，照本条模式拆。
