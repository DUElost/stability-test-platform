# 前端技术设计

> **入口**：`frontend/src/main.tsx` → `App.tsx` → `router/index.tsx`  
> **栈**：React 18、React Router 7、TanStack Query 4、Tailwind、Socket.IO Client

---

## 1. 目录结构

```
frontend/src/
├── main.tsx, App.tsx
├── router/index.tsx       # 路由 + 懒加载 + 鉴权守卫
├── pages/                 # 页面（按业务域分子目录）
├── components/            # 可复用组件（plan-run/ 最大）
├── layouts/AppShell.tsx   # 主布局
├── hooks/                 # useAuthSession, useSocketIO, ...
├── contexts/              # Toast 等
├── utils/api/             # API 客户端（types.ts 为类型权威源）
├── design-system/         # 设计令牌、通用 UI
├── config/
└── test/                  # Vitest setup
```

---

## 2. 路由与权限

| 路径 | 页面 | 权限 |
|------|------|------|
| `/login`, `/register` | 登录/注册 | 公开（已登录跳转首页） |
| `/` | Dashboard | 登录 |
| `/orchestration/plans` | Plan 列表 | 登录 |
| `/orchestration/plans/:id` | Plan 编辑 | 登录 |
| `/execution/plan-execute` | 执行 Plan | 登录 |
| `/execution/plan-runs` | PlanRun 列表 | 登录 |
| `/execution/plan-runs/:runId` | **PlanRun 详情**（主战场） | 登录 |
| `/execution/plan-runs/:runId/matrix` | 设备矩阵（遗留副路由） | 登录 |
| `/execution/plan-runs/:runId/logs` | PlanRun 日志 | 登录 |
| `/runs/:runId/report` | 单 Job 报告 | 登录 |
| `/script-management` | 脚本目录 | 登录 |
| `/hosts`, `/devices` | 主机/设备 | 登录 |
| `/users`, `/audit`, `/settings`, `/notifications` | 管理 | **admin** |
| `/schedules`, `/resources`, `/wifi`, `/issue-tracker` | 调度/资源 | 登录 |

**守卫**：`ProtectedRoute`（登录）、`AdminRoute`（`role === 'admin'`）。  
**代码分割**：除 auth 外页面均 `React.lazy()`。

---

## 3. API 客户端

位置：`frontend/src/utils/api/`

| 模块 | 用途 |
|------|------|
| `client.ts` | axios 实例、Cookie、CSRF、401 处理 |
| `types.ts` | **与后端 Pydantic 对齐的类型权威源** |
| `queryKeys.ts` | React Query key 工厂 |
| `plans.ts` / `planRuns.ts` | Plan / PlanRun |
| `hosts.ts` / `devices.ts` | 主机设备 |
| `pipeline.ts` / `dedup.ts` | Pipeline 模板、去重 |
| `logs.ts` | 日志查询 |
| `auth.ts` | 登录会话 |

**约定**：新增端点先改 `types.ts`，再改页面。

---

## 4. 核心页面与组件

### PlanRun 详情（ADR-0021 C5）

`pages/execution/PlanRunDetailPage.tsx` + `components/plan-run/`：

| 组件 | 职责 |
|------|------|
| `PlanRunTopbar` / `PlanRunHero` | 状态、中止、导出 |
| `PlanChainBreadcrumb` | Plan 链 |
| `DispatchGateCard` | 派发门禁；`retryable` 等读后端 capabilities |
| `BusinessFlowTimeline` | 时间线 + 事件流 |
| `DeviceOverview` / `DeviceDetailDrawer` | 设备矩阵；`is_stuck` / deadline / aborted UI |
| `WatcherSummaryCard` | 异常聚合 |
| `ArchiveStatusCard` | 归档；最终归档弹窗需 `capabilities.final_archive` + readiness |
| `DedupReportCard` / RunConsole | 去重报告 |
| `AnomalyDashboard` | 包名榜、crash 下钻 |

权威投影：`PlanRun.capabilities`、设备 `JobActionCapabilities`、结构化 `ApiError`（`utils/api/client.ts`）。契约见 [`07-execution-protocol.md`](./07-execution-protocol.md)。

### 其他

| 域 | 组件/页面 |
|----|-----------|
| 主机 | `ExpandableHostTable`（紧凑列 / code sync）、`HostBulkActionBar`（浮动、单机热更新）、`HostsPage` |
| 通知 | `NotificationBell`（AppShell）、`NotificationsPage` 通知记录 tab → `notification_logs` API |
| Pipeline 编辑 | `PlanEditPage`、`PipelineEditor` |
| 脚本 | `ScriptManagementPage` |
| 日志 | `XTerminal`、`PlanRunLogsPage` |

---

## 5. 实时更新

**Hook**：`hooks/useSocketIO.ts`

| Namespace | 用途 |
|-----------|------|
| `/dashboard` | 前端订阅 |
| PlanRun room | `job_status`、`plan_run_status`、`watcher_signal`、`precheck_update` |
| 全局 | `notification:new`（铃铛未读刷新） |

策略：SocketIO 事件作 **invalidation hint**，权威态以 REST refetch 为准。

---

## 6. 状态管理

- **服务端状态**：TanStack Query（`useQuery` / `useMutation`）  
- **会话**：`useAuthSession` → `GET /auth/me`  
- **本地 UI 状态**：组件 `useState`；无全局 Redux
- **外观主题**：`ThemeProvider`（`contexts/ThemeContext.tsx`）— `light` / `dark` / `system`，`localStorage` 键 `stp.theme`；`index.html` 内联脚本防 FOUC；顶栏 / 登录页 `ThemeToggle` 循环切换。令牌见 `index.css` `:root` / `.dark`，组件优先用 `design-system/tokens`

### 6.1 暗色主题（全 App）

| 项 | 约定 |
|----|------|
| 切换入口 | 顶栏 `ThemeToggle`（图标）；登录页同组件（图标+文案） |
| 循环顺序 | `light` → `dark` → `system` → `light` |
| DOM | `<html class="dark">` + `style.color-scheme`；Tailwind `darkMode: ["class"]` |
| Toast | `Toaster` 跟随 `resolvedTheme`（Sonner `theme` prop） |
| 禁止 | 新代码硬编码 `gray-*` / `slate-*` / `blue-500` 等；图表用 `hsl(var(--…))` 或 `CHART_COLORS` |
| 验收 | 登录页 / AppShell / Plan Execute 三态在深色下层次可读；刷新无浅色闪屏；跟随系统切 OS 偏好即时生效 |

---

## 7. 构建与配置

| 命令 | 说明 |
|------|------|
| `npm run dev` | Vite 开发 :5173 |
| `npm run build` | 生产构建 |
| `npx tsc --noEmit` | 类型检查 |
| `npx vitest run` | 单元测试 |

生产：`VITE_API_BASE_URL=`（空）+ Nginx 同源反代 `/api/`、`/socket.io/`。

---

## 8. 测试

- 36+ `*.test.tsx` 分布于 `components/`、`pages/`  
- 见 [`development/testing.md`](../development/testing.md)

---

## 9. 方案 C 前端债（跟踪 #32）

- `ArchiveStatusCard` 仍基于 `watcher-summary.archive` / `run_log_bundle`  
- `RunReportPage` risk_summary 全零误导（#16）— 已部分落地
