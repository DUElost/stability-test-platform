import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from '../layouts/AppShell';
import Dashboard from '../pages/Dashboard';
import TaskList from '../pages/tasks/TaskList';
import CreateTask from '../pages/tasks/CreateTask';
import TaskDetails from '../pages/tasks/TaskDetails';
import HostsPage from '../pages/hosts/HostsPage';
import DevicesPage from '../pages/devices/DevicesPage';

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<Dashboard />} />

          <Route path="tasks">
            <Route index element={<TaskList />} />
            <Route path="new" element={<CreateTask />} />
            <Route path=":taskId" element={<TaskDetails />} />
          </Route>

          <Route path="hosts" element={<HostsPage />} />
          <Route path="devices" element={<DevicesPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
