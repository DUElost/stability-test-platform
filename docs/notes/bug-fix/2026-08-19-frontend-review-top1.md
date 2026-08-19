# 前端 UI 审查 top 1 实施（B2/B3/B5/B6）

Status: implemented
Class: bug-fix

依据：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 推荐第 ① 项（单页硬伤打包）。
四个「看一眼就知道不对」的缺陷，共约 30 行改动。

## Decision

- **B2**（`pages/audit/AuditLogPage.tsx`）：两个 `datetime-local` 输入包进可见
  `<label>`（「开始时间/结束时间」）并限宽 `w-52`。根因是 `Input` 默认 `w-full` +
  筛选容器 `flex-wrap`，任何视口都各占一整行；限宽后四控件自然同行。不加查询/
  重置按钮（筛选 onChange 即时生效，语义不变）。
- **B3**（`components/ui/Toaster.tsx`）：`offset={{ top: 88 }}` 全局让位页头。
  **取 88 而非实施任务书估的 72**：实测 `AppShell.tsx:128` 顶栏为 `h-20`（80px），
  且经 `HeaderSlotProvider` 各页 `PageHeader` 的 action（含「新建定时任务」按钮）
  渲染在这条顶栏内——72px 仍会压住顶栏底部 8px。
- **B5**（`pages/results/ResultsPage.tsx`）：删「类型」列（th + 含 `run.task_type`
  chip 的 td）。**API 字段与 `types.ts` 类型保留**：`results.py:232-233` 的
  `task_type` 与 `task_name` 同取 `plan_name_norm`，删 UI 列即可；保留响应字段
  兼容旧客户端与缓存响应。
- **B6**（`components/network/ExpandableHostTable.tsx`）：`host.name === host.ip`
  时省略副标题 ip 行（生产环境主机名即 IP，每行同值显示两遍）。
- 同步断言：`ExpandableHostTable.test.tsx` 补 name===ip 单行用例（audit/results
  目录无测试文件，无需同步）。

## Alternatives

- **B3 页内 inline 错误提示**（否决）：需拆 `SchedulesPage` 的 `Promise.all` 错误
  处理，改动大；且「表格有数据 + 报错 toast」并存是预期行为（另一查询已成功），
  inline 提示反而语义不合。全局 offset 一并解决所有页面同类遮挡。
- **B5 后端删字段**（否决）：`task_type` 属响应契约，删字段是破坏性变更；同名
  字段还存在于 task_schedule 域（MONKEY/MTBF/DDR…），同名不同源，动了易误伤。
- **B6 拆成单行组件**（否决）：一行条件渲染即可，不值得抽象。

## Verification

- 门禁：`npx tsc --noEmit` 通过；vitest 4 文件 25 用例全绿（含新增 B6 用例）；
  eslint 改动文件 `--max-warnings 0` 通过。
- DOM 实测（Playwright `/tmp/ui-shot-rig/verify-top1.js`，1440×900，登录生产
  控制面只读 GET + route 拦截）：
  - B2：`/audit` 四控件 `getBoundingClientRect().y` 全等（113），日期框 208px
    （≤300），label 文本在；
  - B5：`/results` 表头无「类型」，30 行 td 数与表头列数零失配；
  - B6：`/hosts` 首行「主机」列仅 1 个文本 div（`172.21.15.20`）；
  - B3：`/schedules` 拦截首个 `/api/v1/schedules` 返回 500，toaster top=88、
    前 toast top=79（≥72），与「新建定时任务」按钮矩形（24–56px）不相交——
    7 条 toast 堆叠的病态场景下仍通过。

## Revisit

- **B3 offset 随页头高度耦合**：顶栏若改高度，`Toaster.tsx` 的 88 需同步；更优
  解是 CSS 变量（如 `--header-h`）联动，待下次动 AppShell 时做。
- **B5 复核钩子**（审查 §7）：后端 `results` 的 `task_type` 若未来获得独立语义
  （非 `plan_name_norm`），UI 列可复活；届时连同 `types.ts` 注释一起改。
- **存量问题（本轮发现、未修，超出 §6 top1 范围）**：`useToast()` 每次渲染返回
  新对象，`SchedulesPage` 的 `loadAll`（useCallback 依赖 `toast`）随之外围重建，
  拉取 effect 自持循环（`setSchedules` 无条件写新数组供血）——实测 3 秒内
  `/schedules` 请求 150+ 次；端点持续失败时叠加 `duration: Infinity`，错误
  toast 无限刷屏并向上溢出 offset 区（62 条堆叠时前 toast 被顶到 y=35，重新
  压住页头）。**已由后续修复处理**：useToast 引用稳定化 + error duration 10s，
  见 `2026-08-19-use-toast-stable-ref.md`。
- A3 落地后（审查 §7）：`RUN_RESULT_STATUS_CHIP` 并入 `StatusBadge` 时会再动
  ResultsPage 同一张表。
