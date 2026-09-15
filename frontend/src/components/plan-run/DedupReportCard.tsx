/**
 * DedupReportCard — ADR-0025 Sprint 4: 去重报告区（scan/merge/extract 状态 + 产物下载）。
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Scan, Merge, FileDown, Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import { dedupKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/hooks/useToast';
import { SLOW_REFETCH_MS } from '@/hooks/plan-run/planRunDetailUtils';
import { PANEL, TEXT, TOOL_BTN } from '@/design-system';
import { cn } from '@/lib/utils';
import type {
  DedupArtifact,
  DedupScanArchive,
  RunContextExtractSummary,
  RunContextUploadSummary,
} from '@/utils/api/types';

interface Props {
  runId: number;
  /** #300 P3-2: PlanRun.run_context.upload_summary（merge_task 落盘）。 */
  uploadSummary?: RunContextUploadSummary | null;
  /** #300 P3-4: PlanRun.run_context.extract（run_extract_sync 落盘）。 */
  extractSummary?: RunContextExtractSummary | null;
}

/** merge_task 写入的 `upload_summary.incomplete_reason` → 人话后缀（I-7：原因要可见）。 */
const REASON_SUFFIX: Record<string, string> = {
  upload_mark_timeout: '（上送标记未确认）',
  upload_events_pending: '（事件仍在途）',
  merge_skipped_failed_plan_run: '（PlanRun 未成功，跳过合并）',
};

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

/** 未就绪原因的可读后缀；**未知代码原样露出**（不静默吞掉后端新加的原因）。 */
function formatReason(reason?: string): string {
  if (!reason) return '（原因未记录）';
  return REASON_SUFFIX[reason] ?? `（${reason}）`;
}

/**
 * 流水线阶段状态（#2185）：ok=已过 / warn=进行中或有缺口 / fail=失败 /
 * unknown=**无记录**（明说缺什么，而不是显示成 0——0 与"不知道"是两件事，
 * 前者会让人以为该阶段已空跑完）。
 */
type StageState = 'ok' | 'warn' | 'fail' | 'unknown';

const STAGE_DOT: Record<StageState, string> = {
  ok: 'bg-success',
  warn: 'bg-warning',
  fail: 'bg-destructive',
  unknown: 'bg-muted-foreground/40',
};

interface StagePart {
  text: string;
  testid?: string;
  title?: string;
}

interface Stage {
  key: 'scan' | 'upload' | 'merge' | 'extract';
  label: string;
  state: StageState;
  parts: StagePart[];
}

/**
 * 归档流水线四阶段（#2185）：把原先散在「去重报告 / 存储运维概览 / DLE 卡」里的
 * "走到哪一步、卡在哪"收拢成一处（scan → upload → merge → extract）。
 *
 * 数据来源全部是**本卡已有**的输入：`dedup/status`（archive / scan_failed / 产物）
 * 与 `run_context`（upload_summary / extract）。retention 阶段不在其中——它没有
 * per-run 状态（只有 host 级 ops 指标，仍在「存储运维概览」卡里）。
 */
function buildStages(args: {
  archive?: DedupScanArchive | null;
  scanFailed?: boolean;
  scanArtifactCount: number;
  mergeArtifactCount: number;
  upload?: RunContextUploadSummary | null;
  extract?: RunContextExtractSummary | null;
  statusError: boolean;
}): Stage[] {
  const {
    archive, scanFailed, scanArtifactCount, mergeArtifactCount, upload, extract, statusError,
  } = args;
  const stages: Stage[] = [];

  if (statusError) {
    // 不重复下方错误块的措辞（同一屏说两遍同一件事）；这里只说"这一阶段未知"。
    stages.push({
      key: 'scan', label: '扫描', state: 'unknown',
      parts: [{ text: '未知（状态查询失败）' }],
    });
  } else if (!archive || (archive.hosts_triggered ?? 0) <= 0) {
    stages.push({
      key: 'scan', label: '扫描', state: 'unknown',
      parts: [{ text: '未开始（无本轮 host 计数）' }],
    });
  } else {
    const triggered = archive.hosts_triggered ?? 0;
    const done = archive.hosts_with_artifacts ?? 0;
    const notAcked = archive.hosts_not_acked ?? 0;
    const parts: StagePart[] = [{ text: `host 完成度 ${done}/${triggered}` }];
    if (notAcked > 0) parts.push({ text: `未回执 ${notAcked} 台` });
    if (scanFailed) parts.push({ text: '扫描未产生任何报表' });
    stages.push({
      key: 'scan', label: '扫描',
      state: scanFailed ? 'fail' : done >= triggered ? 'ok' : 'warn',
      parts,
    });
  }

  if (!upload) {
    stages.push({
      key: 'upload', label: '上送', state: 'unknown',
      parts: [{ text: '无进度记录（run_context.upload_summary 缺失）' }],
    });
  } else {
    const parts: StagePart[] = [
      { text: `待上送 ${upload.pending}` },
      { text: `失败 ${upload.failed}` },
      { text: `已完成 ${upload.remote}` },
    ];
    if (upload.ready === false) {
      parts.push({
        text: `未等齐${formatReason(upload.incomplete_reason)}`,
        testid: 'upload-not-ready',
        title: upload.incomplete_reason,
      });
    }
    stages.push({
      key: 'upload', label: '上送',
      state: upload.failed > 0 ? 'fail' : upload.ready === false ? 'warn' : 'ok',
      parts,
    });
  }

  if (mergeArtifactCount > 0) {
    stages.push({
      key: 'merge', label: '合并', state: 'ok',
      parts: [{ text: `产物 ${mergeArtifactCount} 份` }],
    });
  } else if (scanArtifactCount > 0) {
    stages.push({
      key: 'merge', label: '合并', state: 'warn',
      parts: [{ text: `未合并（本轮 scan 产物 ${scanArtifactCount} 份）` }],
    });
  } else {
    stages.push({
      key: 'merge', label: '合并', state: 'unknown',
      parts: [{ text: '无产物可合并' }],
    });
  }

  if (!extract) {
    stages.push({
      key: 'extract', label: '提取', state: 'unknown',
      parts: [{ text: '未执行（run_context.extract 缺失）' }],
    });
  } else {
    stages.push({
      key: 'extract', label: '提取',
      state: extract.missing > 0 ? 'fail' : extract.copied >= extract.targets ? 'ok' : 'warn',
      parts: [
        { text: `已拷贝 ${extract.copied}/${extract.targets}` },
        { text: `缺失 ${extract.missing}` },
        { text: `已归档 ${extract.archived}` },
      ],
    });
  }

  return stages;
}

export default function DedupReportCard({ runId, uploadSummary, extractSummary }: Props) {
  const qc = useQueryClient();
  const toast = useToast();
  /** #2185：手动扫描此前固定 `is_final=false`——操作者想让本轮"收口"时没有入口。 */
  const [isFinalScan, setIsFinalScan] = useState(false);

  const statusQ = useQuery({
    queryKey: dedupKeys.status(runId),
    queryFn: () => api.planRuns.getDedupStatus(runId),
    staleTime: 15_000,
    // #1193：scan/upload/merge/extract 产物终态后仍可能陆续上送；慢轮询保持可见
    // （标签页失焦时 React Query 默认暂停轮询）。
    refetchInterval: SLOW_REFETCH_MS,
  });
  const { isError: statusError, refetch: refetchStatus } = statusQ;

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

  const artifacts: DedupArtifact[] = statusQ.data?.artifacts ?? [];
  const stages = buildStages({
    archive: statusQ.data?.archive,
    scanFailed: statusQ.data?.scan_failed,
    scanArtifactCount: artifacts.filter((a) => a.artifact_type === 'scan_result_xls').length,
    mergeArtifactCount: artifacts.filter((a) => a.artifact_type === 'merge_result_xls').length,
    upload: uploadSummary,
    extract: extractSummary,
    statusError,
  });

  return (
    <section className={PANEL.root} data-testid="dedup-report-card">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className={cn('text-sm font-semibold', TEXT.heading)}>去重报告</span>
        <div className="flex items-center gap-1.5">
          <label
            className="flex items-center gap-1 text-[11px] text-muted-foreground"
            title="最终轮：按「本轮之后不再有新产物」收口（自动链在终态也会走最终轮）。"
          >
            <input
              type="checkbox"
              checked={isFinalScan}
              onChange={(e) => setIsFinalScan(e.target.checked)}
              data-testid="dedup-scan-final"
              className="h-3 w-3 accent-primary"
            />
            最终轮
          </label>
          <button
            type="button"
            onClick={() => scanMut.mutate(isFinalScan)}
            disabled={scanMut.isPending}
            className={TOOL_BTN}
            data-testid="dedup-scan-btn"
            title={
              isFinalScan
                ? '扫描归档目录产 Result_*.xls 并作为最终轮（is_final=true）'
                : '扫描归档目录产 Result_*.xls（非最终轮）'
            }
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

      <div className="space-y-2 px-3 py-2.5">
        {statusQ.isLoading ? (
          <div className={cn('flex items-center gap-1.5 text-xs', TEXT.subtitle)}>
            <Loader2 className="h-3 w-3 animate-spin" /> 加载去重状态...
          </div>
        ) : (
          <>
            {/* #2185：四个阶段在一处读完；状态缺失时明说缺什么，不静默消失。 */}
            <div className="space-y-1" data-testid="archive-pipeline">
              {stages.map((s) => (
                <div
                  key={s.key}
                  className="flex items-start gap-2 text-[11px]"
                  data-testid={`pipeline-${s.key}`}
                >
                  <span className={cn('w-8 shrink-0', TEXT.subtitle)}>{s.label}</span>
                  <span
                    className={cn('mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full', STAGE_DOT[s.state])}
                    aria-hidden
                  />
                  <span
                    className={cn(
                      'flex flex-wrap gap-x-2',
                      s.state === 'fail' ? 'text-destructive' : 'text-muted-foreground/70',
                    )}
                  >
                    {s.parts.map((p) => (
                      <span key={p.text} data-testid={p.testid} title={p.title}>
                        {p.text}
                      </span>
                    ))}
                  </span>
                </div>
              ))}
            </div>

            {statusError ? (
              // #1195: 查询失败不得展示「暂无产物」空态 CTA——那是成功空结果
              // 的语义，会误导用户重复扫描。
              <div className={cn('flex items-center justify-between gap-2 text-xs', TEXT.destructive)}>
                <span>去重状态加载失败，暂无法判断产物。</span>
                <button
                  type="button"
                  onClick={() => void refetchStatus()}
                  className="underline underline-offset-2"
                >
                  重试
                </button>
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
          </>
        )}
      </div>
    </section>
  );
}
