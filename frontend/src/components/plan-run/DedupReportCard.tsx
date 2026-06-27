/**
 * DedupReportCard — ADR-0025 Sprint 4: 去重报告区（scan/merge/extract 状态 + 产物下载）。
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Scan, Merge, FileDown, Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import { dedupKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/hooks/useToast';
import { PANEL, TEXT, TOOL_BTN } from '@/design-system';
import { cn } from '@/lib/utils';

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

  const statusQ = useQuery({
    queryKey: dedupKeys.status(runId),
    queryFn: () => api.planRuns.getDedupStatus(runId),
    staleTime: 15_000,
  });

  const scanMut = useMutation({
    mutationFn: (isFinal: boolean) => api.planRuns.triggerScan(runId, isFinal),
    onSuccess: () => {
      toast.success('已触发扫描');
      qc.invalidateQueries({ queryKey: dedupKeys.status(runId) });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`扫描触发失败: ${msg}`);
    },
  });

  const mergeMut = useMutation({
    mutationFn: () => api.planRuns.triggerMerge(runId),
    onSuccess: () => {
      toast.success('合并完成');
      qc.invalidateQueries({ queryKey: dedupKeys.status(runId) });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`合并失败: ${msg}`);
    },
  });

  const extractMut = useMutation({
    mutationFn: () => api.planRuns.triggerExtract(runId),
    onSuccess: () => {
      toast.success('已提取日志到提单目录');
      qc.invalidateQueries({ queryKey: dedupKeys.status(runId) });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error(`提取失败: ${msg}`);
    },
  });

  const artifacts: ScanArtifact[] = (statusQ.data?.artifacts || []) as ScanArtifact[];

  return (
    <section className={PANEL.root} data-testid="dedup-report-card">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className={cn('text-sm font-semibold', TEXT.heading)}>去重报告</span>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => scanMut.mutate(false)}
            disabled={scanMut.isPending}
            className={TOOL_BTN}
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
            className={TOOL_BTN}
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
            className={TOOL_BTN}
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
          <div className={cn('flex items-center gap-1.5 text-xs', TEXT.subtitle)}>
            <Loader2 className="h-3 w-3 animate-spin" /> 加载去重状态...
          </div>
        ) : artifacts.length === 0 ? (
          <p className={cn('text-xs', TEXT.subtitle)}>暂无去重产物。归档完成后点击「扫描」开始。</p>
        ) : (
          <div className="space-y-1" data-testid="dedup-artifacts">
            {artifacts.map((a) => (
              <div key={a.id} className="flex items-center gap-2 rounded border px-2 py-1 text-[11px]">
                <span className={cn('font-mono', TEXT.subtitle)}>
                  {TYPE_LABELS[a.artifact_type] || a.artifact_type}
                </span>
                {a.host_id && <span className="text-muted-foreground/70">{a.host_id}</span>}
                <span className="text-muted-foreground/70">{formatSize(a.size_bytes)}</span>
                <span className="flex-1 truncate font-mono text-muted-foreground/70" title={a.storage_uri}>
                  {a.storage_uri}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
