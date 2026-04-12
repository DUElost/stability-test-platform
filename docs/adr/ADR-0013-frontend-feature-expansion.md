# ADR-0013: 前端功能模块扩展 - 任务实例、问题追踪与环境资源
- 状态：Accepted
- 优先级：P1
- 目标里程碑：M2
- 日期：2026-02-25
- 决策者：平台研发组
- 标签：前端, UI, 扩展, 用户体验

## 背景

随着平台功能演进，前端需要新增多个功能模块以提升用户体验和测试效率：
1. **任务实例页面** - 集中展示任务运行记录，区别于任务列表
2. **问题追踪页面** - 与后处理流水线（JIRA Draft）集成，支持问题查看与提单
3. **环境资源管理** - 统一管理 WiFi 配置、存储工具等环境资源
4. **账户管理** - 用户密码修改等账户安全功能

## 决策

新增以下前端页面模块，统一纳入现有路由与导航体系：

| 页面 | 路由 | 导航位置 | 功能 |
|------|------|----------|------|
| 任务实例 | `/task-runs` | 路由注册（未在侧边栏独立展示） | 分页展示任务运行记录，支持状态筛选 |
| 问题追踪 | `/issue-tracker` | 侧边栏 - 结果区块 | 显示 JIRA Draft，支持刷新与查看详情 |
| 环境资源 | `/resources` | 侧边栏 - 设备区块 | WiFi 配置、存储工具管理等 |
| 修改密码 | `/account/password` | AppShell 用户菜单 | 用户密码修改表单 |

### 技术实现要点

- 使用 React Router Lazy Loading 懒加载新页面
- 复用现有执行链路 API（`api.execution.listJobs`、`api.execution.getCachedJobJiraDraft`）
- 复用现有 UI 组件（Card、Button、Skeleton 等）
- 遵循现有代码规范与样式约定

## 备选方案与权衡

- 方案 A：将所有功能集成到现有页面（如 ResultsPage）
  - 优点：减少路由复杂度
  - 缺点：页面职责不清晰，功能耦合
- 方案 B：独立页面模块（当前决策）
  - 优点：职责分离清晰，便于维护与扩展
  - 缺点：新增路由入口

## 影响

- 正向影响：提升用户体验，测试闭环更完整
- 路由与导航需同步更新
- 需维护新增页面的 API 调用与状态管理

## 落地与后续动作

- ✅ 创建 TaskRunsPage 组件
- ✅ 创建 IssueTrackerPage 组件
- ✅ 创建 ResourcesPage 组件
- ✅ 创建 ChangePasswordPage 组件
- ✅ 更新 Sidebar 导航配置
- ✅ 更新 Router 路由配置

## 关联实现/文档

### 前端组件
- `frontend/src/pages/task-runs/TaskRunsPage.tsx` - 任务实例页面
- `frontend/src/pages/issues/IssueTrackerPage.tsx` - 问题追踪页面
- `frontend/src/pages/resources/ResourcesPage.tsx` - 环境资源页面
- `frontend/src/pages/account/ChangePasswordPage.tsx` - 修改密码页面

### 路由与布局
- `frontend/src/router/index.tsx` - 路由配置
- `frontend/src/layouts/Sidebar.tsx` - 侧边栏导航

### API 复用
- `api.execution.listJobs` - 获取任务运行列表
- `api.execution.getCachedJobJiraDraft` - 获取 JIRA 草稿
- `api.users.changePassword` - 修改密码
