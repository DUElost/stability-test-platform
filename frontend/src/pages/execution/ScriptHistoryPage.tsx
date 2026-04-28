import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/utils/api';
import type { ScriptBatch, ScriptBatchListItem, ScriptRunOut } from '@/utils/api/types';
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
};

function isActiveStatus(status: string) {
  return status === 'RUNNING' || status === 'PENDING';
}

function isSuccessStatus(status: string) {
  return status === 'SUCCESS' || status === 'COMPLETED';
}

function summarizeBatch(batch: ScriptBatch) {
  const runs = batch.runs || [];
  const total = runs.length;
  const success = runs.filter((r) => isSuccessStatus(r.status)).length;
  const failed = runs.filter((r) => r.status === 'FAILED').length;
  const signals = batch.log_signal_count || 0;
  const started = runs
    .map((r) => r.started_at ? new Date(r.started_at).getTime() : Number.NaN)
    .filter(Number.isFinite);
  const ended = runs
    .map((r) => r.ended_at ? new Date(r.ended_at).getTime() : Number.NaN)
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
  const initialBatch = Number(searchParams.get('batch') || 0);
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(
    Number.isFinite(initialBatch) && initialBatch > 0 ? initialBatch : null,
  );

  const { data: listResp, isLoading } = useQuery({
    queryKey: ['script-batches'],
    queryFn: () => api.scriptBatches.list({ limit: 50 }),
  });

  const batches = listResp?.items ?? [];

  useEffect(() => {
    if (selectedBatchId || !batches.length) return;
    setSelectedBatchId(batches[0].id);
  }, [batches, selectedBatchId]);

  const { data: batch, isLoading: detailLoading } = useQuery({
    queryKey: ['script-batch', selectedBatchId],
    queryFn: () => api.scriptBatches.get(selectedBatchId!),
    enabled: !!selectedBatchId,
    refetchInterval: (data) => (
      data && isActiveStatus(data.status) ? 5000 : false
    ),
  });

  const selected = useMemo(
    () => batches.find((item) => item.id === selectedBatchId) ?? null,
    [batches, selectedBatchId],
  );

  const rerunMutation = useMutation({
    mutationFn: () => api.scriptBatches.rerun(selectedBatchId!),
    onSuccess: (result) => {
      toast.success('已重新下发');
      setSelectedBatchId(result.id);
      setSearchParams({ batch: String(result.id) });
      queryClient.invalidateQueries({ queryKey: ['script-batches'] });
      queryClient.invalidateQueries({ queryKey: ['script-batch'] });
    },
    onError: () => toast.error('重新执行失败'),
  });

  const batchSummary = batch ? summarizeBatch(batch) : null;

  const handleRerun = async () => {
    if (!selectedBatchId || !batch) return;
    const device = batch.device_serial || `Device #${batch.device_id}`;
    const steps = batch.runs.map((r) => r.script_name).join(' -> ');
    const ok = await confirmDialog({
      title: '确认重新执行',
      description: `将重新下发到 ${device}；步骤：${steps || '原步骤'}`,
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
            <CardTitle className="text-base">批次列表</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-16 w-full" />)}
              </div>
            ) : !batches.length ? (
              <div className="rounded-md border border-dashed py-8 text-center text-sm text-gray-500">暂无执行记录</div>
            ) : (
              <div className="space-y-2">
                {batches.map((item: ScriptBatchListItem) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedBatchId(item.id)}
                    className={`w-full rounded-md border p-3 text-left transition-colors ${
                      selectedBatchId === item.id ? 'border-gray-900 bg-gray-50' : 'border-gray-200 hover:bg-gray-50'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-gray-900">Batch #{item.id}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[item.status] ?? 'bg-gray-100 text-gray-600'}`}>{item.status}</span>
                    </div>
                    <div className="mt-1 text-xs text-gray-500">
                      {item.device_serial} · {item.step_count} steps
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
              <CardTitle className="text-base">{selected ? `Batch #${selected.id}` : '批次详情'}</CardTitle>
              {batch && (
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
            ) : !batch ? (
              <div className="rounded-md border border-dashed py-10 text-center text-sm text-gray-500">请选择一条执行记录</div>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
                  <span className="font-mono">{batch.device_serial || `Device #${batch.device_id}`}</span>
                  {batch.device_model && <span className="text-gray-500">{batch.device_model}</span>}
                  <span className="text-gray-400">|</span>
                  <span>Host {batch.host_name || batch.host_id || '-'}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[batch.status] ?? 'bg-gray-100 text-gray-600'}`}>{batch.status}</span>
                </div>
                {batchSummary && (
                  <div className="grid gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700 md:grid-cols-5">
                    <span>{batchSummary.total} 步</span>
                    <span>{batchSummary.success} 成功</span>
                    <span>{batchSummary.failed} 失败</span>
                    <span>总耗时 {batchSummary.duration}</span>
                    <span>crash 信号 {batchSummary.signals} 个</span>
                  </div>
                )}
                <div className="text-xs text-gray-500">
                  Watcher: {batch.watcher_capability ?? '-'} · signals {batch.log_signal_count}
                </div>
                <div className="space-y-2">
                  {batch.runs.map((run: ScriptRunOut, index: number) => (
                    <div key={run.id} className="rounded-md bg-gray-50 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium text-gray-900">{index + 1}. {run.script_name}</span>
                        <div className="flex items-center gap-2">
                          <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_CLASS[run.status] ?? 'bg-gray-100 text-gray-600'}`}>{run.status}</span>
                          {run.exit_code != null && <span className="text-xs text-gray-400">exit={run.exit_code}</span>}
                        </div>
                      </div>
                      {run.stdout && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-white p-2 text-xs text-gray-700">{run.stdout}</pre>}
                      {run.stderr && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-red-50 p-2 text-xs text-red-700">{run.stderr}</pre>}
                      {run.metrics_json && Object.keys(run.metrics_json).length > 0 && (
                        <div className="mt-2 text-xs text-gray-500">metrics: {JSON.stringify(run.metrics_json)}</div>
                      )}
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
