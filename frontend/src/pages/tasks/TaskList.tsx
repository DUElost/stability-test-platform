import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Loader2, Ban, RotateCcw } from 'lucide-react';
import { useToast } from '../../components/ui/toast';
import { useConfirm } from '../../hooks/useConfirm';
import { TaskDataTable, type Task, type TaskStatus, type TaskType } from '../../components/task/TaskDataTable';
import { api } from '../../utils/api';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '../../components/ui/alert-dialog';
import { Button } from '../../components/ui/button';
import { cn } from '@/lib/utils';

const statusMap: Record<string, TaskStatus> = {
  'PENDING': 'PENDING',
  'QUEUED': 'QUEUED',
  'RUNNING': 'RUNNING',
  'COMPLETED': 'COMPLETED',
  'FAILED': 'FAILED',
  'CANCELED': 'CANCELED',
};

const typeMap: Record<string, TaskType> = {
  'MONKEY': 'MONKEY',
  'MTBF': 'MTBF',
  'DDR': 'DDR',
  'GPU': 'GPU',
  'STANDBY': 'STANDBY',
  'AIMONKEY': 'AIMONKEY',
};

export default function TaskList() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false);
  const [taskToCancel, setTaskToCancel] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<number>>(new Set());

  const { data: tasks, isLoading, isError } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.tasks.list(0, 200).then(res => res.data.items),
    refetchInterval: 5000,
  });

  const cancelMutation = useMutation({
    mutationFn: (taskId: number) => api.tasks.cancel(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      setCancelDialogOpen(false);
      setTaskToCancel(null);
    },
  });

  const retryMutation = useMutation({
    mutationFn: (taskId: number) => api.tasks.retry(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });

  const batchCancelMutation = useMutation({
    mutationFn: (taskIds: number[]) => api.tasks.batchCancel(taskIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      setSelectedTaskIds(new Set());
    },
    onError: (error: any) => {
      toast.error(`批量取消失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const batchRetryMutation = useMutation({
    mutationFn: (taskIds: number[]) => api.tasks.batchRetry(taskIds),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      setSelectedTaskIds(new Set());
    },
    onError: (error: any) => {
      toast.error(`批量重试失败: ${error.response?.data?.detail || error.message}`);
    },
  });

  const handleBatchCancel = async () => {
    if (selectedTaskIds.size === 0) return;
    const ok = await confirmDialog({ description: `确定要取消 ${selectedTaskIds.size} 个选中任务吗？`, variant: 'destructive' });
    if (ok) {
      batchCancelMutation.mutate(Array.from(selectedTaskIds));
    }
  };

  const handleBatchRetry = async () => {
    if (selectedTaskIds.size === 0) return;
    const ok = await confirmDialog({ description: `确定要重试 ${selectedTaskIds.size} 个选中任务吗？` });
    if (ok) {
      batchRetryMutation.mutate(Array.from(selectedTaskIds));
    }
  };

  const filteredTasks = useMemo(() => {
    if (!tasks) return [];
    const filterUpper = statusFilter.toUpperCase();
    if (statusFilter === 'all') return tasks;
    if (filterUpper === 'PENDING') {
      return tasks.filter((t: any) => t.status === 'PENDING' || t.status === 'QUEUED');
    }
    return tasks.filter((t: any) => t.status === filterUpper);
  }, [tasks, statusFilter]);

  const formattedTasks: Task[] = useMemo(() => {
    return filteredTasks.map((t: any) => ({
      id: t.id,
      name: t.name || `Task #${t.id}`,
      type: typeMap[t.type] || 'MONKEY',
      status: statusMap[t.status] || 'PENDING',
      priority: t.priority || 1,
      target_device_id: t.target_device_id,
      target_device_serial: t.target_device?.serial,
      created_at: t.created_at,
      started_at: t.started_at,
      finished_at: t.finished_at,
      progress: t.status === 'RUNNING' ? 50 : undefined,
    }));
  }, [filteredTasks]);

  const stats = useMemo(() => {
    if (!tasks) return { total: 0, pending: 0, running: 0, completed: 0, failed: 0 };
    return {
      total: tasks.length,
      pending: tasks.filter((t: any) => t.status === 'PENDING' || t.status === 'QUEUED').length,
      running: tasks.filter((t: any) => t.status === 'RUNNING').length,
      completed: tasks.filter((t: any) => t.status === 'COMPLETED').length,
      failed: tasks.filter((t: any) => t.status === 'FAILED' || t.status === 'CANCELED').length,
    };
  }, [tasks]);

  const handleViewDetail = (task: Task) => {
    window.location.href = `/tasks/${task.id}`;
  };

  const handleCancelTask = (taskId: number) => {
    setTaskToCancel(taskId);
    setCancelDialogOpen(true);
  };

  const confirmCancel = () => {
    if (taskToCancel !== null) {
      cancelMutation.mutate(taskToCancel);
    }
  };

  const handleRetryTask = (taskId: number) => {
    retryMutation.mutate(taskId);
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">任务管理</h2>
          <p className="text-sm text-gray-400">查看和管理稳定性测试任务</p>
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
          <p className="text-sm text-gray-400">查看和管理稳定性测试任务</p>
        </div>
        <div className="p-4 bg-red-50 text-red-600 rounded-lg border border-red-100">
          Error loading tasks. Please check backend connection.
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">任务管理</h2>
          <p className="text-sm text-gray-400">查看和管理稳定性测试任务</p>
        </div>
        <div className="flex items-center gap-2">
          {selectedTaskIds.size > 0 && (
            <>
              <button
                onClick={handleBatchCancel}
                disabled={batchCancelMutation.isPending}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-red-50 text-red-600 hover:bg-red-100 transition-colors disabled:opacity-50"
              >
                <Ban className="w-4 h-4" />
                {batchCancelMutation.isPending ? '取消中...' : `批量取消 (${selectedTaskIds.size})`}
              </button>
              <button
                onClick={handleBatchRetry}
                disabled={batchRetryMutation.isPending}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-blue-50 text-blue-600 hover:bg-blue-100 transition-colors disabled:opacity-50"
              >
                <RotateCcw className="w-4 h-4" />
                {batchRetryMutation.isPending ? '重试中...' : `批量重试 (${selectedTaskIds.size})`}
              </button>
            </>
          )}
          <Link
            to="/orchestration/workflows"
            className="inline-flex items-center gap-2 bg-gray-900 hover:bg-gray-800 text-white px-4 py-2 rounded-lg font-medium transition-all"
          >
            <Plus className="w-4 h-4" />
            新建工作流
          </Link>
        </div>
      </div>

      {/* Stats Grid - Clickable for filtering */}
      <div className="grid grid-cols-5 gap-3">
        <button
          onClick={() => setStatusFilter('all')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'all' ? 'border-gray-400 shadow-sm' : 'border-gray-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
            <svg className="w-5 h-5 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
            </svg>
          </div>
          <div>
            <div className="text-xl font-semibold text-gray-900">{stats.total}</div>
            <div className="text-xs text-gray-500">全部任务</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('pending')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'pending' ? 'border-gray-400 shadow-sm' : 'border-gray-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
            <svg className="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div>
            <div className="text-xl font-semibold text-gray-600">{stats.pending}</div>
            <div className="text-xs text-gray-500">等待中</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('running')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'running' ? 'border-blue-400 shadow-sm' : 'border-blue-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center">
            <svg className="w-5 h-5 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <div>
            <div className="text-xl font-semibold text-blue-600">{stats.running}</div>
            <div className="text-xs text-gray-500">执行中</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('completed')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'completed' ? 'border-emerald-400 shadow-sm' : 'border-emerald-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-emerald-50 flex items-center justify-center">
            <svg className="w-5 h-5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
          </div>
          <div>
            <div className="text-xl font-semibold text-emerald-600">{stats.completed}</div>
            <div className="text-xs text-gray-500">已完成</div>
          </div>
        </button>
        <button
          onClick={() => setStatusFilter('failed')}
          className={cn(
            'bg-white rounded-lg border p-3 flex items-center gap-3 transition-all',
            statusFilter === 'failed' ? 'border-red-400 shadow-sm' : 'border-red-200'
          )}
        >
          <div className="w-10 h-10 rounded-lg bg-red-50 flex items-center justify-center">
            <svg className="w-5 h-5 text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
          </div>
          <div>
            <div className="text-xl font-semibold text-red-600">{stats.failed}</div>
            <div className="text-xs text-gray-500">失败</div>
          </div>
        </button>
      </div>

      <TaskDataTable
        tasks={formattedTasks}
        onViewDetail={handleViewDetail}
        onCancelTask={handleCancelTask}
        onRetryTask={handleRetryTask}
        loading={isLoading}
        selectedIds={selectedTaskIds}
        onSelectionChange={setSelectedTaskIds}
      />

      <AlertDialog open={cancelDialogOpen} onOpenChange={setCancelDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>取消任务</AlertDialogTitle>
            <AlertDialogDescription>
              确定要取消此任务吗？此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel asChild>
              <Button variant="outline">继续运行</Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                variant="destructive"
                onClick={confirmCancel}
                disabled={cancelMutation.isPending}
              >
                {cancelMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    取消中...
                  </>
                ) : (
                  '确认取消'
                )}
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
