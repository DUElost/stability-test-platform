import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Plus, Loader2 } from 'lucide-react';
import { TaskDataTable, type Task, type TaskStatus, type TaskType } from '../../components/task/TaskDataTable';
import { api, type WorkflowDefinition } from '../../utils/api';

export default function TaskList() {
  const { data: tasks, isLoading, isError } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => api.orchestration.list(0, 200),
    refetchInterval: 5000,
  });

  const filteredTasks = useMemo(() => tasks ?? [], [tasks]);

  const formattedTasks: Task[] = useMemo(() => {
    return filteredTasks.map((t: WorkflowDefinition) => ({
      id: t.id,
      name: t.name || `Workflow #${t.id}`,
      type: 'WORKFLOW' as TaskType,
      status: 'PENDING' as TaskStatus,
      priority: 1,
      created_at: t.created_at,
    }));
  }, [filteredTasks]);

  const total = filteredTasks.length;

  const handleViewDetail = (task: Task) => {
    window.location.href = `/orchestration/workflows/${task.id}`;
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">任务管理</h2>
          <p className="text-sm text-gray-400">查看和管理稳定性测试工作流定义</p>
        </div>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">任务管理</h2>
          <p className="text-sm text-gray-400">查看和管理稳定性测试工作流定义</p>
        </div>
        <div className="p-4 bg-red-50 text-red-600 rounded-lg border border-red-100">
          加载工作流失败，请检查后端连接。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">任务管理</h2>
          <p className="text-sm text-gray-400">查看和管理稳定性测试工作流定义（编排蓝图）</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/orchestration/workflows"
            className="inline-flex items-center gap-2 bg-gray-900 hover:bg-gray-800 text-white px-4 py-2 rounded-lg font-medium transition-all"
          >
            <Plus className="w-4 h-4" />
            新建工作流
          </Link>
        </div>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-4 flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
          <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
          </svg>
        </div>
        <div>
          <div className="text-xl font-semibold text-gray-900">{total}</div>
          <div className="text-xs text-gray-500">工作流定义</div>
        </div>
      </div>

      <TaskDataTable tasks={formattedTasks} onViewDetail={handleViewDetail} loading={isLoading} />
    </div>
  );
}
