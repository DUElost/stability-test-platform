import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { LogViewer } from '../../components/log/LogViewer';
import { api } from '../../utils/api';

export default function TaskDetails() {
  const { taskId } = useParams();
  const id = Number(taskId);

  const { data: task } = useQuery({
    queryKey: ['tasks', id],
    queryFn: () => api.tasks.get(id).then(res => res.data),
    enabled: !!id,
  });

  if (!task) return <div>Loading...</div>;

  // Dynamic WebSocket URL based on current host
  const wsUrl = `ws://${window.location.hostname}:8000/ws/logs/${id}`;

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[calc(100vh-8rem)]">
      <div className="lg:col-span-1 bg-white p-6 rounded-lg shadow-sm border border-slate-200">
        <h2 className="text-lg font-semibold mb-4">Task Details</h2>
        <div className="space-y-3 text-sm">
          <div>
            <label className="text-slate-500 block">ID</label>
            <span className="font-mono">{task.id}</span>
          </div>
          <div>
            <label className="text-slate-500 block">Name</label>
            <span>{task.name}</span>
          </div>
          <div>
            <label className="text-slate-500 block">Status</label>
            <span className="font-medium">{task.status}</span>
          </div>
        </div>
      </div>

      <div className="lg:col-span-2 bg-black rounded-lg overflow-hidden border border-slate-800">
        <LogViewer wsUrl={wsUrl} />
      </div>
    </div>
  );
}
