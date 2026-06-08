import { lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import AppShell from '../layouts/AppShell';
import { useAuthSession } from '@/hooks/useAuthSession';

// Auth pages stay as static imports (always needed on first load)
import LoginPage from '../pages/auth/LoginPage';
import RegisterPage from '../pages/auth/RegisterPage';

// All other pages use React.lazy for route-level code splitting
const Dashboard = lazy(() => import('../pages/Dashboard'));
const RunReportPage = lazy(() => import('../pages/runs/RunReportPage'));
const HostsPage = lazy(() => import('../pages/hosts/HostsPage'));
const DevicesPage = lazy(() => import('../pages/devices/DevicesPage'));
const ResultsPage = lazy(() => import('../pages/results/ResultsPage'));
const WifiPage = lazy(() => import('../pages/wifi/WifiPage'));
const UsersPage = lazy(() => import('../pages/users/UsersPage'));
const NotificationsPage = lazy(() => import('../pages/notifications/NotificationsPage'));
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage'));
const ChangePasswordPage = lazy(() => import('../pages/account/ChangePasswordPage'));
const SchedulesPage = lazy(() => import('../pages/schedules/SchedulesPage'));
const AuditLogPage = lazy(() => import('../pages/audit/AuditLogPage'));
const NotFoundPage = lazy(() => import('../pages/NotFoundPage'));
const IssueTrackerPage = lazy(() => import('../pages/issues/IssueTrackerPage'));
const ResourcesPage = lazy(() => import('../pages/resources/ResourcesPage'));
// ADR-0020 Plan 层页面
const PlanListPage = lazy(() => import('../pages/orchestration/PlanListPage'));
const PlanEditPage = lazy(() => import('../pages/orchestration/PlanEditPage'));
const PlanExecutePage = lazy(() => import('../pages/execution/PlanExecutePage'));
const PlanRunListPage = lazy(() => import('../pages/execution/PlanRunListPage'));
const PlanRunDetailPage = lazy(() => import('../pages/execution/PlanRunDetailPage'));
const PlanRunLogsPage = lazy(() => import('../pages/execution/PlanRunLogsPage'));
// Legacy minimap-only matrix view kept as a secondary route until C5c lands the
// integrated DeviceMatrix inside PlanRunDetailPage.
const PlanRunMatrixPage = lazy(() => import('../pages/execution/PlanRunMatrixPage'));
const ScriptManagementPage = lazy(() => import('../pages/scripts/ScriptManagementPage'));

function AuthGateLoading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background text-muted-foreground">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      校验登录状态中...
    </div>
  );
}

// 受保护的路由组件
function ProtectedRoute() {
  const sessionQ = useAuthSession();
  if (sessionQ.isLoading) return <AuthGateLoading />;
  return sessionQ.isSuccess ? <Outlet /> : <Navigate to="/login" replace />;
}

// 公开路由组件（已登录用户重定向到首页）
function PublicRoute() {
  const sessionQ = useAuthSession();
  if (sessionQ.isLoading) return <AuthGateLoading />;
  return sessionQ.isSuccess ? <Navigate to="/" replace /> : <Outlet />;
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
            <Route index element={<Dashboard />} />

            <Route path="runs/:runId/report" element={<RunReportPage />} />

            <Route path="script-management" element={<ScriptManagementPage />} />

            <Route path="hosts" element={<HostsPage />} />
            <Route path="devices" element={<DevicesPage />} />
            <Route path="wifi" element={<WifiPage />} />
            <Route path="results" element={<ResultsPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="notifications" element={<NotificationsPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="account/password" element={<ChangePasswordPage />} />
            <Route path="schedules" element={<SchedulesPage />} />
            <Route path="audit" element={<AuditLogPage />} />
            <Route path="issue-tracker" element={<IssueTrackerPage />} />
            <Route path="resources" element={<ResourcesPage />} />

            {/* ADR-0020 Plan 路由 */}
            <Route path="orchestration">
              <Route path="plans" element={<PlanListPage />} />
              <Route path="plans/:id" element={<PlanEditPage />} />
            </Route>
            <Route path="execution">
              <Route path="plan-execute" element={<PlanExecutePage />} />
              <Route path="plan-runs" element={<PlanRunListPage />} />
              <Route path="plan-runs/:runId" element={<PlanRunDetailPage />} />
              <Route path="plan-runs/:runId/logs" element={<PlanRunLogsPage />} />
              <Route path="plan-runs/:runId/matrix" element={<PlanRunMatrixPage />} />
            </Route>

            {/* 404 */}
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
