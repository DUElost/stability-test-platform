/**
 * DedupReportCard — ADR-0025 Sprint 4: 去重报告区（scan/merge/extract 状态 + 产物下载）。
 *
 * 展示该 PlanRun 的 scan/merge 产物列表（plan_run_artifact），
 * 手动触发 scan/merge/extract 按钮 + RunConsole 实时日志入口。
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Scan, Merge, FileDown, Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import LiveConsole from '@/components/console/LiveConsole';

interface Props {
  runId: number;
}

interface ScanArtifact {
  id: number;
  host_id: string | null;
  storage_uri: string;
  artifact_type: string;
  size_bytes: number | null;
  created_at: string | null;
}

const TYPE_LABELS: Record<string, string> = {
  scan_result_xls: 'Scan',
  merge_result_xls: 'Merge',
};

function formatSize(bytes?: number | null): string {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DedupReportCard({ runId }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  const [consoleRunId, setConsoleRunId] = useState<string | null>(null);

  const statusQ = useQuery({
    queryKey: ['dedup-status', runId],
    queryFn: () => api.planRuns.getDedupStatus(runId),
    staleTime: 15_000,
  });

  const scanMut = useMutation({
    mutationFn: (isFinal: boolean) => api.planRuns.triggerScan(runId, isFinal),
    onSuccess: (data) => {
      toast.success('已触发扫描');
      setConsoleRunId(data.console_run_id);
      qc.invalidateQueries({ queryKey: ['dedup-status', runId] });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`扫描触发失败: ${msg}`);
    },
  });

  const mergeMut = useMutation({
    mutationFn: () => api.planRuns.triggerMerge(runId),
    onSuccess: (data) => {
      toast.success('已触发合并');
      setConsoleRunId(data.console_run_id);
      qc.invalidateQueries({ queryKey: ['dedup-status', runId] });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`合并触发失败: ${msg}`);
    },
  });

  const extractMut = useMutation({
    mutationFn: () => api.planRuns.triggerExtract(runId),
    onSuccess: () => {
      toast.success('已提取日志到提单目录');
      qc.invalidateQueries({ queryKey: ['dedup-status', runId] });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`提取失败: ${msg}`);
    },
  });

  const artifacts: ScanArtifact[] = (statusQ.data?.artifacts || []) as ScanArtifact[];

  return (
    <section
      className="rounded-xl border border-gray-200 bg-white"
      data-testid="dedup-report-card"
    >
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className="text-sm font-semibold text-gray-800">去重报告</span>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => scanMut.mutate(false)}
            disabled={scanMut.isPending}
            className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            data-testid="dedup-scan-btn"
            title="扫描归档目录产 Result_*.xls"
          >
            {scanMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Scan className="h-3 w-3" />}
            扫描
          </button>
          <button
            type="button"
            onClick={() => mergeMut.mutate()}
            disabled={mergeMut.isPending}
            className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            data-testid="dedup-merge-btn"
            title="集中合并各 agent _org.xls"
          >
            {mergeMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Merge className="h-3 w-3" />}
            合并
          </button>
          <button
            type="button"
            onClick={() => extractMut.mutate()}
            disabled={extractMut.isPending}
            className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
            data-testid="dedup-extract-btn"
            title="按去重结果提取事件日志到提单目录"
          >
            {extractMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <FileDown className="h-3 w-3" />}
            提取
          </button>
        </div>
      </div>

      <div className="space-y-2 p-4">
        {statusQ.isLoading ? (
          <div className="flex items-center gap-1.5 text-xs text-gray-400">
            <Loader2 className="h-3 w-3 animate-spin" /> 加载去重状态...
          </div>
        ) : artifacts.length === 0 ? (
          <p className="text-xs text-gray-400">暂无去重产物。归档完成后点击「扫描」开始。</p>
        ) : (
          <div className="space-y-1" data-testid="dedup-artifacts">
            {artifacts.map((a) => (
              <div key={a.id} className="flex items-center gap-2 rounded border px-2 py-1 text-[11px]">
                <span className="font-mono text-gray-500">
                  {TYPE_LABELS[a.artifact_type] || a.artifact_type}
                </span>
                {a.host_id && <span className="text-gray-400">{a.host_id}</span>}
                <span className="text-gray-400">{formatSize(a.size_bytes)}</span>
                <span className="flex-1 truncate font-mono text-gray-400" title={a.storage_uri}>
                  {a.storage_uri}
                </span>
              </div>
            ))}
          </div>
        )}

        {consoleRunId && (
          <LiveConsole consoleRunId={consoleRunId} />
        )}
      </div>
    </section>
  );
}
