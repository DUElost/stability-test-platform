# 前端 UI 审查 B10-a：错误文案归一化 + InlineError 重试 + A5 时间格式去重

Status: implemented
Class: bug-fix

依据：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 9 位（B10）。
侦察结论与审查原文不同：**toApiError 已是完整归一化器**（client.ts 依次
body.error → body.detail → details.message → 校验 → source.message →
兜底「请求失败 (status)/网络请求失败」），缺的不是基建，是展示层 6 处
绕过它直读 `(error as Error)?.message`。

## Decision

- **6 处 message 位点换 `toApiError(error).message`**：UsersPage /
  PlanRunDetailPage / PlanRunListPage / PlanListPage / ScriptManagementPage /
  HostHotUpdateConfirmDialog。对 HTTP 错误从 axios 样板文案（"Request
  failed with status code 500"）变为后端 detail；网络错误变「网络请求失败」。
- **`InlineError` 加可选 `onRetry`**（默认不渲染按钮，与 ErrorState 同款
  outline+RefreshCw）。四页接线：audit（`loadLogs`）、users（`refetch`）、
  wifi（`refetch`）、issues（`refetch`）。选加 prop 而非让四页换
  ErrorState：它们占位在页内局部（筛选区下方/卡片内），整卡 ErrorState
  会重演 A1 的「整页组件塞进小面板」。
- **A5 前两处去重**：NotificationBell:139 与 NotificationsPage:590 逐字
  重复的 `new Date(x).toLocaleString('zh-CN')` → 既有
  `formatDateTimeLocale(x, '')`（同 locale、显式 hour12:false，合法 ISO
  输出逐字节等价；非法输入由 'Invalid Date' 变空串，更好）。
  **第三处单列处理**：FileServerPage:171 Recharts labelFormatter 输入是
  Unix 秒而非 ISO，不硬套统一函数，新增 `formatUnixSeconds(seconds, empty)`
  数值助手（utils/format.ts，紧邻 formatDateTimeLocale）。
- **duration 阶梯结案**：3/4/5/10 梯度有内在逻辑（越需要读的留越久），
  无「错误 toast 来不及读」的实际反馈，use-toast note 的 Revisit 已改
  「已评估维持现状」，不再是待办。
- **顺手修复 main 红**：ADR-0029 P2 合入的 `ProjectsPage` import 了 #321
  已删除的 `LoadingGrid/CardSkeleton`（该线合入未经 typecheck，main 的
  tsc 处于红态）。按「加具名变体不加拉杆」给 `PageSkeleton.Cards` 加
  有界 `layout: 'stack' | 'grid'`（grid 映射原 columns=3 的唯一用法），
  ProjectsPage 迁移为 `Cards count={3} layout="grid"`。

## 与侦察结论的一处修正（toApiError 的边界）

toApiError 对 **HTTP/网络错误**是实质改进，但对 TanStack 内部错误
（`["users"] data is undefined`，无 `.response`）**照样透传
source.message**——B 轨截图那条内部串换完后仍会显示。不治的理由：
这类错误是 queryFn 契约违反（前端 bug），生产态真实故障都走 HTTP/
网络路径；用启发式遮罩内部串会把真 bug 藏进「网络请求失败」里。
若要治，正确位置是 queryFn 层保证不抛 undefined 结构错误，不在展示层。

## Alternatives

- **四页换 ErrorState 整卡**（否决）：位置语义不合（见 Decision）。
- **给 toApiError 加内部错误遮罩**（否决）：见上节。
- **A5 第三处硬套 formatDateTimeLocale**（否决）：输入类型不同（秒 vs
  ISO 串），转换语义应显式成 `formatUnixSeconds`，重载/新函数比隐式
  类型分派诚实。

## Verification

- 门禁：tsc / eslint（18 改动文件 `--max-warnings 0`）/ 全量 vitest
  **622 用例**（+error-state.test / format.test / Cards layout 用例）/
  build 全绿。**含 main 红修复**（ProjectsPage，见 Decision 末条）——
  修复前 origin/main 的 tsc 因引用已删导出而失败。
- DOM 实测（`/tmp/ui-shot-rig/verify-b10a.js`）6/6：
  - `/users` 拦截前 2 次（初始+1 重试，QueryProvider 全局 `retry:1`）
    500 带 detail → InlineError 显示「加载用户失败：模拟的后端错误」
    （非 axios 样板）；点重试（第 3 次起放行）→ 列表恢复；
  - `/audit` 错误态出现重试按钮（手写 fetch 无自动重试，单次 500 即达），
    点击后恢复；
  - `/notifications` 记录页 20 个时间戳全部命中统一
    `YYYY/M/D HH:mm:ss`（zh-CN 24h）格式。

## Revisit

- **B10-b 结案存档**：5 个无错误分支页——resources/settings 零查询、
  change-password 表单+toast（死路径，不补 UI）；results 已在 ADR-0029
  MTBF 线补全（ErrorState + retry + 404 清筛选）；plan-run-logs 的
  eventsQ 经子面板有 isError 传递，其余查询若发现具体缺口再单独立项。
- `formatUnixSeconds` 目前唯一消费者是 FileServerPage 的 labelFormatter；
  同为秒轴的 tickFormatter（HH:mm 截断）语义不同，未强行合并。
- 若 queryFn 层将来统一保证结构错误不外抛（`data is undefined` 消灭在
  源头），toApiError 的透传边界问题随之消失。
