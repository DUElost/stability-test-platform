import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type JobInstance } from '@/utils/api';
import { formatLocalDateTime, parseIsoToDate } from '@/utils/time';
import { Play, Clock, CheckCircle, XCircle, AlertCircle, ChevronRight } from 'lucide-react';

const STATUS_BADGE: Record<string, string> = {
  COMPLETED: 'bg-green-100 text-green-700',
  FAILED: 'bg-red-100 text-red-700',
  RUNNING: 'bg-blue-100 text-blue-700',
  PENDING: 'bg-gray-100 text-gray-600',
  PENDING_TOOL: 'bg-gray-100 text-gray-600',
  ABORTED: 'bg-yellow-100 text-yellow-700',
  UNKNOWN: 'bg-gray-100 text-gray-600',
};

const STATUS_ICON: Record<string, React.ElementType> = {
  COMPLETED: CheckCircle,
  FAILED: XCircle,
  RUNNING: Play,
  PENDING: Clock,
  PENDING_TOOL: Clock,
  ABORTED: AlertCircle,
  UNKNOWN: AlertCircle,
};

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '-';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatTime(iso: string | null): string {
  return formatLocalDateTime(iso);
}

export default function TaskRunsPage() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const pageSize = 20;

  const { data, isLoading } = useQuery({
    queryKey: ['task-runs', page, pageSize],
    queryFn: async () => {
      const skip = (page - 1) * pageSize;
      const result = await api.execution.listJobs(skip, pageSize);
      return {
        items: result.items,
        total: result.total,
        total_pages: Math.ceil(result.total / pageSize),
      };
    },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">任务实例</h1>
          <p className="text-gray-500 mt-1">查看所有任务执行记录</p>
        </div>
        <Button onClick={() => navigate('/orchestration/workflows')}>
          新建工作流
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>执行记录</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : data?.items.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              暂无任务执行记录
            </div>
          ) : (
            <div className="space-y-2">
              {data?.items.map((job: JobInstance) => {
                const StatusIcon = STATUS_ICON[job.status] || Clock;
                const statusBadgeClass = STATUS_BADGE[job.status] || 'bg-gray-100 text-gray-600';
                return (
                  <div
                    key={job.id}
                    className="flex items-center gap-4 p-4 rounded-lg border hover:bg-gray-50 cursor-pointer transition-colors"
                    onClick={() => navigate(`/runs/${job.id}/report`)}
                  >
                    <StatusIcon className={`w-5 h-5 ${
                      job.status === 'COMPLETED' ? 'text-green-500' :
                      job.status === 'FAILED' ? 'text-red-500' :
                      job.status === 'RUNNING' ? 'text-blue-500' :
                      'text-gray-400'
                    }`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">工作流 #{job.workflow_definition_id ?? '-'}</span>
                        <span className={`px-2 py-0.5 rounded-full text-xs ${statusBadgeClass}`}>
                          {job.status}
                        </span>
                      </div>
                      <div className="text-sm text-gray-500 mt-1">
                        {job.device_serial ? `${job.device_serial}` : `设备 #${job.device_id}`} | 主机 {job.host_id}
                      </div>
                    </div>
                    <div className="text-right text-sm text-gray-500">
                      <div>{formatDuration(job.ended_at && job.started_at
                        ? (parseIsoToDate(job.ended_at)?.getTime() ?? 0) / 1000 - (parseIsoToDate(job.started_at)?.getTime() ?? 0) / 1000
                        : null)}</div>
                      <div>{formatTime(job.started_at ?? null)}</div>
                    </div>
                    <ChevronRight className="w-5 h-5 text-gray-400" />
                  </div>
                );
              })}
            </div>
          )}

          {data && data.total_pages > 1 && (
            <div className="flex justify-center gap-2 mt-4">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 1}
                onClick={() => setPage(p => p - 1)}
              >
                上一页
              </Button>
              <span className="flex items-center text-sm text-gray-500">
                {page} / {data.total_pages}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= data.total_pages}
                onClick={() => setPage(p => p + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
