# 前端布局优化实施计划

## 目标
通过组件化重构，建立统一的响应式布局系统，提升移动端适配和大屏体验。

## 实施步骤

### 阶段 1：基础设施 - AppShell 组件
**文件**: `src/layouts/AppShell.tsx`

- 整合 MainLayout、Sidebar、Header
- 实现 Sidebar 移动端抽屉状态管理
- 添加响应式断点处理 (`lg:` 切换点)

### 阶段 2：页面布局组件
**文件**: `src/components/layout/PageContainer.tsx`
- 统一页面容器：max-width、padding、spacing

**文件**: `src/components/layout/PageHeader.tsx`
- 统一页头：标题、副标题、操作按钮
- 可选面包屑导航支持

**文件**: `src/components/layout/StatsGrid.tsx`
- 统计卡片网格组件
- 统一响应式断点

### 阶段 3：Sidebar 响应式重构
**文件**: `src/layouts/Sidebar.tsx` (修改)
- 添加移动端抽屉遮罩层
- 实现关闭/打开动画 (transition-transform)
- Header 增加菜单按钮

### 阶段 4：页面统一改造
逐个页面替换为新的布局组件：

1. **Dashboard.tsx**
   - 使用 PageContainer
   - 保留原有统计卡片（使用 StatsGrid）
   - Dashboard 特殊布局保持不变

2. **DevicesPage.tsx**
   - 使用 PageContainer + PageHeader + StatsGrid
   - 简化页面代码

3. **HostsPage.tsx**
   - 使用 PageContainer + PageHeader + StatsGrid
   - 简化页面代码

4. **TaskList.tsx**
   - 使用 PageContainer + PageHeader
   - 简化页面代码

### 阶段 5：动画与体验优化
- 添加页面过渡动画
- 添加骨架屏组件 (Skeleton)
- 优化 hover 状态

## 响应式断点策略

| 断点 | 前缀 | 宽度 | Sidebar | 内容网格 |
|------|------|------|---------|----------|
| Mobile | - | < 640px | 抽屉模式 | 1列 |
| Tablet | sm | 640px+ | 抽屉模式 | 2列 |
| Laptop | lg | 1024px+ | 固定显示 | 3列 |
| Desktop | xl | 1280px+ | 固定显示 | 4列 |
| Wide | 2xl | 1536px+ | 固定显示 | 限制 max-w-7xl |

## 技术要点

1. **Sidebar 抽屉实现**
   - 使用 `transform: translateX(-100%)` 实现平滑动画
   - 添加遮罩层 `bg-black/50`
   - 使用 z-index 层级管理

2. **PageContainer 规范**
   ```
   max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6
   ```

3. **StatsGrid 规范**
   ```
   grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-4
   ```

4. **Content Grid 规范**
   ```
   grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4
   ```

## 预期效果

- ✅ 移动端侧边栏可正常收起/展开
- ✅ 大屏内容不再过度拉伸
- ✅ 各页面布局风格统一
- ✅ 代码复用性提升
- ✅ 动画过渡更流畅
