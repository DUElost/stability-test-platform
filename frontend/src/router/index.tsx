import { BrowserRouter, Routes, Route, Navigate, Outlet } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import Dashboard from '../pages/Dashboard';
import TaskList from '../pages/tasks/TaskList';
import CreateTask from '../pages/tasks/CreateTask';
import TaskDetails from '../pages/tasks/TaskDetails';
import HostsPage from '../pages/hosts/HostsPage';
import DevicesPage from '../pages/devices/DevicesPage';
import LoginPage from '../pages/auth/LoginPage';
import RegisterPage from '../pages/auth/RegisterPage';
import WorkflowsPage from '../pages/workflows/WorkflowsPage';
import ResultsPage from '../pages/results/ResultsPage';
import MapReducePage from '../pages/mapreduce/MapReducePage';
import WifiPage from '../pages/wifi/WifiPage';
import LogsPage from '../pages/logs/LogsPage';
import UsersPage from '../pages/users/UsersPage';

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
            <Route index element={<Dashboard />} />

            <Route path="tasks">
              <Route index element={<TaskList />} />
              <Route path="new" element={<CreateTask />} />
              <Route path=":taskId" element={<TaskDetails />} />
            </Route>

            <Route path="hosts" element={<HostsPage />} />
            <Route path="devices" element={<DevicesPage />} />
            <Route path="wifi" element={<WifiPage />} />
            <Route path="workflows" element={<WorkflowsPage />} />
            <Route path="results" element={<ResultsPage />} />
            <Route path="logs" element={<LogsPage />} />
            <Route path="mapreduce" element={<MapReducePage />} />
            <Route path="users" element={<UsersPage />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
