import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import MainLayout from '../layouts/MainLayout';
import Dashboard from '../pages/Dashboard';
import TaskList from '../pages/tasks/TaskList';
import CreateTask from '../pages/tasks/CreateTask';
import TaskDetails from '../pages/tasks/TaskDetails';

export default function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<MainLayout />}>
          <Route index element={<Dashboard />} />

          <Route path="tasks">
            <Route index element={<TaskList />} />
            <Route path="new" element={<CreateTask />} />
            <Route path=":taskId" element={<TaskDetails />} />
          </Route>

          <Route path="devices" element={<div className="p-4">Device Management - Coming Soon</div>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
