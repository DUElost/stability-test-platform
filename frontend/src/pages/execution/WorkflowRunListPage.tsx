import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type WorkflowRun, type WorkflowStatus } from '@/utils/api';
import { formatLocalDateTime, parseIsoToDate } from '@/utils/time';
import LogsPage from '@/pages/logs/LogsPage';
import { Rocket, ListTodo, FileSearch } from 'lucide-react';

const WF_STATUS_BADGE: Record<WorkflowStatus, string> = {
  RUNNING: 'bg-blue-100 text-blue-700',
  SUCCESS: 'bg-green-100 text-green-700',
  PARTIAL_SUCCESS: 'bg-orange-100 text-orange-700',
  FAILED: 'bg-red-100 text-red-700',
  DEGRADED: 'bg-gray-200 text-gray-600',
};

function formatTime(iso: string | null | undefined) {
  return formatLocalDateTime(iso);
}

function formatDuration(start: string, end: string | null | undefined) {
  const startDate = parseIsoToDate(start);
  const endDate = parseIsoToDate(end);
  if (!startDate || !endDate) return end ? '-' : '—';
  const s = Math.round((endDate.getTime() - startDate.getTime()) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

function HistoryView() {
  const navigate = useNavigate();

  const { data: runs, isLoading } = useQuery({
    queryKey: ['workflow-runs'],
    queryFn: () => api.execution.listRuns(0, 100),
    refetchInterval: 15000,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>所有 WorkflowRun</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <div className="space-y-3 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : !runs?.length ? (
          <div className="py-12 text-center text-sm text-gray-400">
            暂无执行记录，
            <button className="ml-1 text-blue-500 hover:underline" onClick={() => navigate('/execution/run')}>
              立即发起测试
            </button>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-gray-50 text-left text-xs uppercase text-gray-500">
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
                    className="cursor-pointer border-b transition-colors hover:bg-gray-50"
                    onClick={() => navigate(`/execution/runs/${run.id}`)}
                  >
                    <td className="px-4 py-3 font-mono font-semibold text-gray-700">#{run.id}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge}`}>
                        {run.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-600">#{run.workflow_definition_id}</td>
                    <td className="px-4 py-3 text-gray-600">{(run.failure_threshold * 100).toFixed(0)}%</td>
                    <td className="px-4 py-3 text-gray-600">{formatTime(run.started_at)}</td>
                    <td className="px-4 py-3 text-gray-600">{formatDuration(run.started_at, run.ended_at)}</td>
                    <td className="px-4 py-3 text-xs text-gray-500">{run.triggered_by ?? '-'}</td>
                    <td className="px-4 py-3">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/execution/runs/${run.id}`);
                        }}
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
  );
}

export default function WorkflowRunListPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const view = searchParams.get('view') === 'logs' ? 'logs' : 'history';

  const changeView = (next: 'history' | 'logs') => {
    if (next === 'history') {
      setSearchParams({});
      return;
    }
    setSearchParams({ view: 'logs' });
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">执行观测</h1>
          <p className="mt-1 text-sm text-gray-500">统一查看执行历史与日志总览</p>
        </div>
        <Button size="sm" onClick={() => navigate('/execution/run')}>
          <Rocket className="mr-1 h-4 w-4" />
          发起测试
        </Button>
      </div>

      <div className="inline-flex rounded-lg border border-gray-200 bg-white p-1">
        <button
          type="button"
          onClick={() => changeView('history')}
          className={`inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm transition-colors ${
            view === 'history'
              ? 'bg-slate-100 text-slate-800'
              : 'text-gray-600 hover:bg-gray-50'
          }`}
        >
          <ListTodo className="h-4 w-4" />
          执行历史
        </button>
        <button
          type="button"
          onClick={() => changeView('logs')}
          className={`inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm transition-colors ${
            view === 'logs'
              ? 'bg-slate-100 text-slate-800'
              : 'text-gray-600 hover:bg-gray-50'
          }`}
        >
          <FileSearch className="h-4 w-4" />
          日志总览
        </button>
      </div>

      {view === 'history' ? <HistoryView /> : <LogsPage embedded />}
    </div>
  );
}
