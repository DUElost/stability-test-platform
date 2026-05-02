import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/utils/api';
import type { ScriptExecutionListItem, ScriptExecutionDetail, ScriptExecutionJob } from '@/utils/api/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { formatLocalDateTime } from '@/utils/time';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { Plus, RefreshCw } from 'lucide-react';

const STATUS_CLASS: Record<string, string> = {
  RUNNING: 'bg-blue-100 text-blue-700',
  SUCCESS: 'bg-green-100 text-green-700',
  COMPLETED: 'bg-green-100 text-green-700',
  PARTIAL: 'bg-amber-100 text-amber-700',
  FAILED: 'bg-red-100 text-red-700',
  PENDING: 'bg-gray-100 text-gray-600',
  UNKNOWN: 'bg-yellow-100 text-yellow-700',
  ABORTED: 'bg-gray-100 text-gray-600',
};

function isActiveStatus(status: string) {
  return status === 'RUNNING' || status === 'PENDING';
}
function isSuccessStatus(status: string) {
  return status === 'SUCCESS' || status === 'COMPLETED';
}

function summarizeDetail(detail: ScriptExecutionDetail) {
  const jobs = detail.jobs || [];
  const steps = jobs.flatMap((job) => job.steps || []);
  const total = steps.length;
  const success = steps.filter((s) => isSuccessStatus(s.status)).length;
  const failed = steps.filter((s) => s.status === 'FAILED').length;
  const signals = jobs.reduce((sum, j) => sum + (j.log_signal_count || 0), 0);
  const started = jobs
    .map((j) => j.started_at ? new Date(j.started_at).getTime() : Number.NaN)
    .filter(Number.isFinite);
  const ended = jobs
    .map((j) => j.ended_at ? new Date(j.ended_at).getTime() : Number.NaN)
    .filter(Number.isFinite);
  let duration = '-';
  if (started.length > 0) {
    const start = Math.min(...started);
    const end = ended.length > 0 ? Math.max(...ended) : Date.now();
    const totalSeconds = Math.max(0, Math.floor((end - start) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    duration = minutes > 0 ? `${minutes}m${seconds}s` : `${seconds}s`;
  }
  return { total, success, failed, signals, duration };
}

export default function ScriptHistoryPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialRun = Number(searchParams.get('batch') || searchParams.get('run') || 0);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(
    Number.isFinite(initialRun) && initialRun > 0 ? initialRun : null,
  );

  const { data: listResp, isLoading } = useQuery({
    queryKey: ['script-executions'],
    queryFn: () => api.scriptExecutions.list(0, 50),
  });

  const executions = listResp?.items ?? [];

  useEffect(() => {
    if (selectedRunId || !executions.length) return;
    setSelectedRunId(executions[0].workflow_run_id);
  }, [executions, selectedRunId]);

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['script-execution', selectedRunId],
    queryFn: () => api.scriptExecutions.get(selectedRunId!),
    enabled: !!selectedRunId,
    refetchInterval: (data) => (
      data && isActiveStatus(data.status) ? 5000 : false
    ),
  });

  const selected = useMemo(
    () => executions.find((item) => item.workflow_run_id === selectedRunId) ?? null,
    [executions, selectedRunId],
  );

  const rerunMutation = useMutation({
    mutationFn: () => api.scriptExecutions.rerun(selectedRunId!),
    onSuccess: (result) => {
      toast.success('已重新下发');
      setSelectedRunId(result.workflow_run_id);
      setSearchParams({ run: String(result.workflow_run_id) });
      queryClient.invalidateQueries({ queryKey: ['script-executions'] });
      queryClient.invalidateQueries({ queryKey: ['script-execution'] });
    },
    onError: () => toast.error('重新执行失败'),
  });

  const detailSummary = detail ? summarizeDetail(detail) : null;

  const handleRerun = async () => {
    if (!selectedRunId || !detail) return;
    const deviceInfo = detail.jobs.length > 0
      ? `${detail.jobs.length} 台设备`
      : `${detail.jobs.length} devices`;
    const steps = detail.items.map((item) => item.script_name).join(' → ');
    const ok = await confirmDialog({
      title: '确认重新执行',
      description: `将重新下发到 ${deviceInfo}；步骤：${steps || '原步骤'}`,
      confirmText: '重新执行',
    });
    if (ok) rerunMutation.mutate();
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">执行记录</h1>
          <p className="mt-1 text-sm text-gray-500">查看脚本执行历史、单步输出、Watcher 摘要</p>
        </div>
        <Button asChild>
          <Link to="/execute">
            <Plus className="mr-2 h-4 w-4" />
            新建执行
          </Link>
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">执行列表</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-16 w-full" />)}
              </div>
            ) : !executions.length ? (
              <div className="rounded-md border border-dashed py-8 text-center text-sm text-gray-500">暂无执行记录</div>
            ) : (
              <div className="space-y-2">
                {executions.map((item: ScriptExecutionListItem) => (
                  <button
                    key={item.workflow_run_id}
                    type="button"
                    onClick={() => setSelectedRunId(item.workflow_run_id)}
                    className={`w-full rounded-md border p-3 text-left transition-colors ${
                      selectedRunId === item.workflow_run_id ? 'border-gray-900 bg-gray-50' : 'border-gray-200 hover:bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-gray-900">Run #{item.workflow_run_id}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[item.status] ?? 'bg-gray-100 text-gray-600'}`}>{item.status}</span>
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      {item.device_serials?.join(', ') || '-'} · {item.device_count} 台设备
                    </div>
                    <div className="mt-0.5 truncate text-xs text-gray-400">{item.script_names}</div>
                    <div className="mt-0.5 text-xs text-gray-400">{formatLocalDateTime(item.started_at)}</div>
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <CardTitle className="text-base">{selected ? `Run #${selected.workflow_run_id}` : '执行详情'}</CardTitle>
              {detail && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={rerunMutation.isLoading}
                  onClick={handleRerun}
                >
                  <RefreshCw className={`mr-2 h-4 w-4 ${rerunMutation.isLoading ? 'animate-spin' : ''}`} />
                  重新执行
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {detailLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : !detail ? (
              <div className="rounded-md border border-dashed py-10 text-center text-sm text-gray-500">请选择一条执行记录</div>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
                  <span>{detail.jobs.length} 台设备</span>
                  <span className="text-gray-400">|</span>
                  <span>{detail.items.map((item) => item.script_name).join(' → ')}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[detail.status] ?? 'bg-gray-100 text-gray-600'}`}>{detail.status}</span>
                </div>
                {detailSummary && (
                  <div className="grid gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 md:grid-cols-5">
                    <span>{detailSummary.total} 步</span>
                    <span>{detailSummary.success} 成功</span>
                    <span>{detailSummary.failed} 失败</span>
                    <span>总耗时 {detailSummary.duration}</span>
                    <span>crash 信号 {detailSummary.signals} 个</span>
                  </div>
                )}
                <div className="space-y-3">
                  {detail.jobs.map((job: ScriptExecutionJob) => (
                    <div key={job.id} className="rounded-md border border-gray-200 p-3">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-mono text-gray-900">{job.device_serial || `Device #${job.device_id}`}</span>
                        {job.device_model && <span className="text-gray-500">{job.device_model}</span>}
                        <span className="text-gray-400">|</span>
                        <span className="text-gray-500">Host {job.host_name || job.host_id || '-'}</span>
                        <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[job.status] ?? 'bg-gray-100 text-gray-600'}`}>{job.status}</span>
                      </div>
                      <div className="mt-1 text-xs text-gray-500">
                        Watcher: {job.watcher_capability ?? '-'} · signals {job.log_signal_count}
                      </div>
                      <div className="mt-2 space-y-2">
                        {job.steps.map((step, stepIdx: number) => (
                          <div key={step.step_id} className="rounded-md bg-gray-50 p-3">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-medium text-gray-900">{stepIdx + 1}. {step.script_name}</span>
                              <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[step.status] ?? 'bg-gray-100 text-gray-600'}`}>{step.status}</span>
                            </div>
                            {step.output && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-white p-2 text-xs text-gray-700">{step.output}</pre>}
                            {step.error_message && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-red-50 p-2 text-xs text-red-700">{step.error_message}</pre>}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
