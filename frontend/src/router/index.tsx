import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import AppShell from '../layouts/AppShell';

// Auth pages stay as static imports (always needed on first load)
import LoginPage from '../pages/auth/LoginPage';
import RegisterPage from '../pages/auth/RegisterPage';

// All other pages use React.lazy for route-level code splitting
const Dashboard = lazy(() => import('../pages/Dashboard'));
const TaskList = lazy(() => import('../pages/tasks/TaskList'));
const CreateTask = lazy(() => import('../pages/tasks/CreateTask'));
const TaskDetails = lazy(() => import('../pages/tasks/TaskDetails'));
const RunReportPage = lazy(() => import('../pages/tasks/RunReportPage'));
const HostsPage = lazy(() => import('../pages/hosts/HostsPage'));
const DevicesPage = lazy(() => import('../pages/devices/DevicesPage'));
const WorkflowsPage = lazy(() => import('../pages/workflows/WorkflowsPage'));
const ResultsPage = lazy(() => import('../pages/results/ResultsPage'));
const MapReducePage = lazy(() => import('../pages/mapreduce/MapReducePage'));
const WifiPage = lazy(() => import('../pages/wifi/WifiPage'));
const LogsPage = lazy(() => import('../pages/logs/LogsPage'));
const UsersPage = lazy(() => import('../pages/users/UsersPage'));
const ToolsPage = lazy(() => import('../pages/tools/ToolsPage'));
const NotificationsPage = lazy(() => import('../pages/notifications/NotificationsPage'));
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage'));
const SchedulesPage = lazy(() => import('../pages/schedules/SchedulesPage'));
const TemplatesPage = lazy(() => import('../pages/templates/TemplatesPage'));
const AuditLogPage = lazy(() => import('../pages/audit/AuditLogPage'));
const NotFoundPage = lazy(() => import('../pages/NotFoundPage'));

function LazyFallback() {
  return (
    <div className="flex items-center justify-center h-64">
      <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
    </div>
  );
}

// 检查是否已登录
function isAuthenticated() {
  return !!localStorage.getItem('access_token');
}

// 受保护的路由组件
function ProtectedRoute() {
  return isAuthenticated() ? <Outlet /> : <Navigate to="/login" replace />;
}

// 公开路由组件（已登录用户重定向到首页）
function PublicRoute() {
  return !isAuthenticated() ? <Outlet /> : <Navigate to="/" replace />;
}

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        {/* 公开路由 */}
        <Route element={<PublicRoute />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
        </Route>

        {/* 受保护的路由 */}
        <Route element={<ProtectedRoute />}>
          <Route path="/" element={<AppShell />}>
            <Suspense fallback={<LazyFallback />}>
              <Route index element={<Dashboard />} />

              <Route path="tasks">
                <Route index element={<TaskList />} />
                <Route path="new" element={<CreateTask />} />
                <Route path=":taskId" element={<TaskDetails />} />
              </Route>

              <Route path="runs/:runId/report" element={<RunReportPage />} />

              <Route path="tools" element={<ToolsPage />} />

              <Route path="hosts" element={<HostsPage />} />
              <Route path="devices" element={<DevicesPage />} />
              <Route path="wifi" element={<WifiPage />} />
              <Route path="workflows" element={<WorkflowsPage />} />
              <Route path="results" element={<ResultsPage />} />
              <Route path="logs" element={<LogsPage />} />
              <Route path="mapreduce" element={<MapReducePage />} />
              <Route path="users" element={<UsersPage />} />
              <Route path="notifications" element={<NotificationsPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="schedules" element={<SchedulesPage />} />
              <Route path="templates" element={<TemplatesPage />} />
              <Route path="audit" element={<AuditLogPage />} />

              {/* 404 */}
              <Route path="*" element={<NotFoundPage />} />
            </Suspense>
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
