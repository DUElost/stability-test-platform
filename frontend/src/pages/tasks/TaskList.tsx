import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { TaskList as TaskListComponent } from '../../components/task/TaskList';
import { PageContainer, PageHeader } from '../../components/layout';
import { api } from '../../utils/api';

export default function TaskList() {
  const { data: tasks, isLoading, isError } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.tasks.list().then(res => res.data),
  });

  if (isLoading) {
    return <div className="p-8 text-center text-slate-500">Loading tasks...</div>;
  }

  if (isError) {
    return (
      <div className="p-4 bg-red-50 text-red-700 rounded-lg border border-red-200">
        Error loading tasks.
      </div>
    );
  }

  const formattedTasks = tasks?.map(t => {
    // Map backend status to frontend display status
    let status: 'running' | 'completed' | 'failed' | 'queued' | 'pending' = 'pending';
    if (t.status === 'RUNNING') status = 'running';
    else if (t.status === 'COMPLETED') status = 'completed';
    else if (['FAILED', 'CANCELED'].includes(t.status)) status = 'failed';
    else if (t.status === 'QUEUED') status = 'queued';
    else if (t.status === 'PENDING') status = 'pending';

    return {
      id: String(t.id),
      type: t.type,
      startTime: new Date(t.created_at).toLocaleString(),
      deviceCount: t.target_device_id ? 1 : 0,
      status
    };
  }) || [];

  const actionButton = (
    <Link
      to="/tasks/new"
      className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg flex items-center gap-2 transition-all shadow-sm hover:shadow btn-press"
    >
      <Plus size={18} />
      New Task
    </Link>
  );

  return (
    <PageContainer>
      <PageHeader
        title="Task Management"
        subtitle="View and manage stability test tasks"
        action={actionButton}
        breadcrumbs={[{ label: 'Tasks' }]}
      />
      <TaskListComponent tasks={formattedTasks} />
    </PageContainer>
  );
}
