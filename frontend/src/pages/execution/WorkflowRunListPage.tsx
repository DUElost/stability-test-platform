import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type WorkflowRun, type WorkflowStatus } from '@/utils/api';
import { Rocket } from 'lucide-react';

const WF_STATUS_BADGE: Record<WorkflowStatus, string> = {
  RUNNING:         'bg-blue-100 text-blue-700',
  SUCCESS:         'bg-green-100 text-green-700',
  PARTIAL_SUCCESS: 'bg-orange-100 text-orange-700',
  FAILED:          'bg-red-100 text-red-700',
  DEGRADED:        'bg-gray-200 text-gray-600',
};

function formatTime(iso: string | null | undefined) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

function formatDuration(start: string, end: string | null | undefined) {
  if (!end) return '—';
  const s = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

export default function WorkflowRunListPage() {
  const navigate = useNavigate();

  const { data: runs, isLoading } = useQuery({
    queryKey: ['workflow-runs'],
    queryFn: () => api.execution.listRuns(0, 100),
    refetchInterval: 15000,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">执行历史</h1>
        <Button size="sm" onClick={() => navigate('/execution/run')}>
          <Rocket className="w-4 h-4 mr-1" />
          发起测试
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>所有 WorkflowRun</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <div className="p-4 space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : !runs?.length ? (
            <div className="text-center py-12 text-gray-400 text-sm">
              暂无执行记录，
              <button className="text-blue-500 hover:underline ml-1" onClick={() => navigate('/execution/run')}>
                立即发起测试
              </button>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-gray-50 text-left text-xs text-gray-500 uppercase">
                  <th className="px-4 py-2 font-medium">Run ID</th>
                  <th className="px-4 py-2 font-medium">状态</th>
                  <th className="px-4 py-2 font-medium">蓝图 ID</th>
                  <th className="px-4 py-2 font-medium">失败阈值</th>
                  <th className="px-4 py-2 font-medium">开始时间</th>
                  <th className="px-4 py-2 font-medium">耗时</th>
                  <th className="px-4 py-2 font-medium">触发者</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {runs.map((run: WorkflowRun) => {
                  const badge = WF_STATUS_BADGE[run.status] ?? 'bg-gray-100 text-gray-600';
                  return (
                    <tr
                      key={run.id}
                      className="border-b hover:bg-gray-50 cursor-pointer transition-colors"
                      onClick={() => navigate(`/execution/runs/${run.id}`)}
                    >
                      <td className="px-4 py-3 font-mono font-semibold text-gray-700">#{run.id}</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${badge}`}>
                          {run.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-600">#{run.workflow_definition_id}</td>
                      <td className="px-4 py-3 text-gray-600">{(run.failure_threshold * 100).toFixed(0)}%</td>
                      <td className="px-4 py-3 text-gray-600">{formatTime(run.started_at)}</td>
                      <td className="px-4 py-3 text-gray-600">{formatDuration(run.started_at, run.ended_at)}</td>
                      <td className="px-4 py-3 text-gray-500 text-xs">{run.triggered_by ?? '-'}</td>
                      <td className="px-4 py-3">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={e => { e.stopPropagation(); navigate(`/execution/runs/${run.id}`); }}
                        >
                          查看
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
