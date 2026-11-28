/**
 * B11-U2 / #4199：`/issue-tracker` 首 Tab「提单表单生成」工作台。
 *
 * 只读展示后端 A 段预检与交付包事实；严禁自算去重计数或拼接 15.4 路径。
 * `PENDING_EXPORT` 必须以醒目标识标明「待导出（Path 尚未校验可达，不可直接送去提单）」。
 */
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileDown, Loader2, Play, RefreshCw, Search } from 'lucide-react';
import { api } from '@/utils/api';
import type {
  HandoffArtifactKind,
  HandoffBundleOut,
  HandoffBundleSummaryOut,
  HandoffPreflightOut,
} from '@/utils/api/types';
import { Button } from '@/components/ui/button';
import { InlineError } from '@/components/ui/error-state';
import { InlineEmpty } from '@/components/ui/empty-state';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ALERT_BOX, FORM, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { formatLocalDateTime } from '@/utils/format';
import { toApiError } from '@/utils/api/client';

export interface HandoffWorkbenchProps {
  /** 深链 `?planRunIds=1,2,3` 预填的正整数列表。 */
  initialPlanRunIds?: number[];
}

function formatPlanRunIdsInput(ids: number[] | undefined): string {
  return (ids ?? []).join(', ');
}

function parsePlanRunIdsInput(raw: string): number[] {
  const seen = new Set<number>();
  const out: number[] = [];
  for (const part of raw.split(/[,\s]+/)) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const n = Number(trimmed);
    if (!Number.isInteger(n) || n <= 0 || seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }
  return out;
}

function PendingExportBanner({ status }: { status: string }) {
  if (status === 'EXPORTED') {
    return (
      <div
        className={cn('rounded-md px-3 py-2 text-sm', ALERT_BOX.success)}
        data-testid="handoff-status-exported"
      >
        状态：EXPORTED（终稿已发布，Path 已校验可达）
      </div>
    );
  }
  return (
    <div
      className={cn('rounded-md px-3 py-2 text-sm font-semibold', ALERT_BOX.warning)}
      data-testid="handoff-status-pending-export"
      role="status"
    >
      待导出（Path 尚未校验可达，不可直接送去提单）
      <span className={cn('mt-0.5 block text-xs font-normal opacity-90')}>
        状态：{status || 'PENDING_EXPORT'} · A 段预览件，非终稿
      </span>
    </div>
  );
}

function CoverageGatePanel({ preflight }: { preflight: HandoffPreflightOut }) {
  const { gates, unbound_rows, runs, builds, identity_gate } = preflight;
  return (
    <div className="space-y-3" data-testid="handoff-preflight-result">
      <div
        className={cn(
          'rounded-md px-3 py-2 text-sm',
          gates.coverage_complete ? ALERT_BOX.success : ALERT_BOX.destructive,
        )}
        data-testid="handoff-coverage-gate"
      >
        {gates.coverage_complete
          ? '覆盖率门禁通过：期望主机报表齐全'
          : '覆盖率门禁未通过：禁止生成交付包'}
        {gates.blocking_issues.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-xs">
            {gates.blocking_issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
        {gates.warnings.length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-xs opacity-90" data-testid="handoff-preflight-warnings">
            {gates.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-md border border-border">
        <Table className="min-w-[720px] text-xs">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="h-8 px-2 py-1.5">PlanRun</TableHead>
              <TableHead className="h-8 px-2 py-1.5">Jira 项目</TableHead>
              <TableHead className="h-8 px-2 py-1.5">分区</TableHead>
              <TableHead className="h-8 px-2 py-1.5">缺失主机</TableHead>
              <TableHead className="h-8 px-2 py-1.5">多余主机</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => (
              <TableRow key={run.plan_run_id} data-testid={`handoff-run-${run.plan_run_id}`}>
                <TableCell className="px-2 py-1.5 font-mono">#{run.plan_run_id}</TableCell>
                <TableCell className="px-2 py-1.5 font-mono">
                  {run.jira_project_key || run.project_key || '—'}
                </TableCell>
                <TableCell className="px-2 py-1.5">{run.partition_mode}</TableCell>
                <TableCell
                  className="px-2 py-1.5 text-destructive"
                  data-testid={`handoff-missing-hosts-${run.plan_run_id}`}
                >
                  {run.missing_hosts.length > 0 ? run.missing_hosts.join(', ') : '—'}
                </TableCell>
                <TableCell className="px-2 py-1.5">
                  {run.unexpected_hosts.length > 0 ? run.unexpected_hosts.join(', ') : '—'}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className={cn('text-xs', TEXT.caption)} data-testid="handoff-identity-gate">
        发生身份：总行 {identity_gate.total_org_rows} · 唯一事件 {identity_gate.unique_events} ·
        已绑定 {identity_gate.keyed_rows} · 兼容回退 {identity_gate.compat_fallback_rows} ·
        未绑定 {identity_gate.unbound_rows}
      </div>

      {unbound_rows.length > 0 && (
        <div className="space-y-1" data-testid="handoff-unbound-rows">
          <div className={cn('text-xs font-medium', TEXT.heading)}>
            未绑定行明细（{unbound_rows.length}）
          </div>
          <div className="max-h-48 overflow-auto rounded-md border border-border">
            <Table className="min-w-[640px] text-[11px]" containerClassName="overflow-visible">
              <TableHeader className="sticky top-0 bg-card [&_tr]:border-b">
                <TableRow className="hover:bg-transparent">
                  <TableHead className="h-7 px-2 py-1">Run</TableHead>
                  <TableHead className="h-7 px-2 py-1">Host</TableHead>
                  <TableHead className="h-7 px-2 py-1">行</TableHead>
                  <TableHead className="h-7 px-2 py-1">串号</TableHead>
                  <TableHead className="h-7 px-2 py-1">原因</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {unbound_rows.map((row) => (
                  <TableRow
                    key={`${row.plan_run_id}-${row.host_id}-${row.row_no}-${row.reason}`}
                  >
                    <TableCell className="px-2 py-1 font-mono">#{row.plan_run_id}</TableCell>
                    <TableCell className="px-2 py-1 font-mono">{row.host_id}</TableCell>
                    <TableCell className="px-2 py-1 font-mono">{row.row_no}</TableCell>
                    <TableCell className="px-2 py-1 font-mono">{row.serial || '—'}</TableCell>
                    <TableCell className="px-2 py-1">{row.reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {builds.length > 0 && (
        <div className="flex flex-wrap gap-2" data-testid="handoff-build-preview">
          {builds.map((b) => (
            <span
              key={b.build}
              className={cn('rounded px-2 py-0.5 text-[11px] font-mono', STATUS_CHIP.muted)}
            >
              {b.build}
              {b.product ? ` · ${b.product}` : ''} · 设备 {b.device_count} · 事件 {b.unique_event_count}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function BundleDetail({
  bundle,
  onDownload,
  downloadingKey,
}: {
  bundle: HandoffBundleOut;
  onDownload: (kind: HandoffArtifactKind, build?: string) => void;
  downloadingKey: string | null;
}) {
  return (
    <div className="space-y-3" data-testid="handoff-bundle-detail">
      <PendingExportBanner status={bundle.status} />
      <div className={cn('flex flex-wrap gap-x-4 gap-y-1 text-xs', TEXT.caption)}>
        <span className="font-mono">handoff_id={bundle.handoff_id}</span>
        <span>问题总数 {bundle.builds.reduce((n, b) => n + b.issue_count, 0)}</span>
        <span>build 数 {bundle.builds.length}</span>
        <span className="font-mono truncate" title={bundle.manifest_sha256}>
          sha256={bundle.manifest_sha256.slice(0, 12)}…
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid="handoff-download-manifest"
          disabled={downloadingKey === 'manifest'}
          onClick={() => onDownload('manifest')}
        >
          {downloadingKey === 'manifest' ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <FileDown className="mr-1.5 h-3.5 w-3.5" />
          )}
          下载 manifest.json
        </Button>
      </div>

      <div className="space-y-3">
        {bundle.builds.map((build) => {
          const dist = build.baseline_distribution;
          return (
            <div
              key={build.build_slug || build.build}
              className="rounded-md border border-border p-3 space-y-2"
              data-testid={`handoff-build-${build.build_slug || build.build}`}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className={cn('text-sm font-semibold font-mono', TEXT.heading)}>
                  {build.build}
                  {build.product ? (
                    <span className={cn('ml-2 text-xs font-normal', TEXT.caption)}>
                      {build.product}
                    </span>
                  ) : null}
                </div>
                <div className={cn('text-[11px]', TEXT.caption)}>
                  本次 {dist.current_only} · 混合 {dist.mixed} · 仅基线 {dist.baseline_only}
                  {' · '}问题 {build.issue_count}
                  {' · '}过滤 {build.filtered_events}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid={`handoff-download-preview-${build.build_slug || build.build}`}
                  disabled={downloadingKey === `preview_xls:${build.build}`}
                  onClick={() => onDownload('preview_xls', build.build)}
                >
                  <FileDown className="mr-1.5 h-3.5 w-3.5" />
                  预览 .xls
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  data-testid={`handoff-download-review-${build.build_slug || build.build}`}
                  disabled={downloadingKey === `review_workbook:${build.build}`}
                  onClick={() => onDownload('review_workbook', build.build)}
                >
                  <FileDown className="mr-1.5 h-3.5 w-3.5" />
                  复核工作簿
                </Button>
              </div>
              {build.issues.length > 0 && (
                <div className="rounded border border-border/60">
                  <Table className="min-w-[560px] text-[11px]">
                    <TableHeader>
                      <TableRow className="hover:bg-transparent">
                        <TableHead className="h-7 px-2 py-1">#</TableHead>
                        <TableHead className="h-7 px-2 py-1">Count</TableHead>
                        <TableHead className="h-7 px-2 py-1">DeviceCount</TableHead>
                        <TableHead className="h-7 px-2 py-1">基线</TableHead>
                        <TableHead className="h-7 px-2 py-1">代表串号</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {build.issues.map((issue) => (
                        <TableRow key={issue.issue_index}>
                          <TableCell className="px-2 py-1 font-mono">{issue.issue_index}</TableCell>
                          <TableCell className="px-2 py-1 font-mono">{issue.count}</TableCell>
                          <TableCell className="px-2 py-1 font-mono">{issue.device_count}</TableCell>
                          <TableCell className="px-2 py-1">{issue.baseline_class}</TableCell>
                          <TableCell className="px-2 py-1 font-mono">
                            {issue.representative?.serial || '—'}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function HandoffWorkbench({ initialPlanRunIds }: HandoffWorkbenchProps) {
  const qc = useQueryClient();
  const [planRunIdsText, setPlanRunIdsText] = useState(() =>
    formatPlanRunIdsInput(initialPlanRunIds),
  );
  const [snapshotCutoff, setSnapshotCutoff] = useState('');
  const [targetFolderHint, setTargetFolderHint] = useState('');
  const [preflight, setPreflight] = useState<HandoffPreflightOut | null>(null);
  const [activeBundle, setActiveBundle] = useState<HandoffBundleOut | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloadingKey, setDownloadingKey] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const planRunIds = useMemo(() => parsePlanRunIdsInput(planRunIdsText), [planRunIdsText]);
  const coverageOk = preflight?.gates.coverage_complete === true;

  const historyQ = useQuery({
    queryKey: ['jira-handoff-bundles'],
    queryFn: () => api.jiraHandoff.listBundles(20),
    enabled: showHistory,
  });

  const preflightMut = useMutation({
    mutationFn: () =>
      api.jiraHandoff.preflight({
        plan_run_ids: planRunIds,
        platform: 'mtk',
        snapshot_cutoff: snapshotCutoff.trim() ? snapshotCutoff.trim() : null,
      }),
    onSuccess: (data) => {
      setActionError(null);
      setPreflight(data);
      setActiveBundle(null);
    },
    onError: (err) => {
      setActionError(toApiError(err).message);
    },
  });

  const createMut = useMutation({
    mutationFn: () =>
      api.jiraHandoff.createBundle({
        plan_run_ids: planRunIds,
        platform: 'mtk',
        snapshot_cutoff: snapshotCutoff.trim() ? snapshotCutoff.trim() : null,
        target_folder_hint: targetFolderHint.trim() ? targetFolderHint.trim() : null,
      }),
    onSuccess: (data) => {
      setActionError(null);
      setActiveBundle(data);
      void qc.invalidateQueries({ queryKey: ['jira-handoff-bundles'] });
    },
    onError: (err) => {
      setActionError(toApiError(err).message);
    },
  });

  const openHistoryItem = async (item: HandoffBundleSummaryOut) => {
    setActionError(null);
    try {
      const detail = await api.jiraHandoff.getBundle(item.handoff_id);
      setActiveBundle(detail);
    } catch (err) {
      setActionError(toApiError(err).message);
    }
  };

  const handleDownload = async (kind: HandoffArtifactKind, build?: string) => {
    if (!activeBundle) return;
    const key = kind === 'manifest' ? 'manifest' : `${kind}:${build ?? ''}`;
    setDownloadingKey(key);
    setActionError(null);
    try {
      await api.jiraHandoff.downloadArtifact(activeBundle.handoff_id, kind, { build });
    } catch (err) {
      setActionError(toApiError(err).message);
    } finally {
      setDownloadingKey(null);
    }
  };

  const generateBlockedReason = !planRunIds.length
    ? '请先填写至少一个有效的 PlanRun ID'
    : !preflight
      ? '请先运行预检'
      : !coverageOk
        ? '覆盖率门禁未通过，禁止生成'
        : null;

  return (
    <div className="space-y-4" data-testid="handoff-workbench">
      <p className={cn('text-sm', TEXT.subtitle)}>
        从 1..N 个 Monkey PlanRun 生成 A 段提单表单预览（manifest / 预览 .xls / 复核工作簿）。
        产物均为待导出态；终稿导出属后续阶段，本页不写 15.4 共享根。
      </p>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block space-y-1 sm:col-span-2">
          <span className={FORM.label}>PlanRun ID（逗号分隔）</span>
          <input
            className={FORM.input}
            data-testid="handoff-plan-run-ids"
            value={planRunIdsText}
            onChange={(e) => setPlanRunIdsText(e.target.value)}
            placeholder="例如 1594, 1595"
          />
        </label>
        <label className="block space-y-1">
          <span className={FORM.label}>快照截止（可选，ISO-8601）</span>
          <input
            className={FORM.input}
            data-testid="handoff-snapshot-cutoff"
            value={snapshotCutoff}
            onChange={(e) => setSnapshotCutoff(e.target.value)}
            placeholder="留空 = 当前 UTC"
          />
        </label>
        <label className="block space-y-1">
          <span className={FORM.label}>目标目录提示（可选，仅预览占位）</span>
          <input
            className={FORM.input}
            data-testid="handoff-target-folder-hint"
            value={targetFolderHint}
            onChange={(e) => setTargetFolderHint(e.target.value)}
            placeholder="不写 15.4，仅 Path 占位"
          />
        </label>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          data-testid="handoff-preflight-btn"
          disabled={planRunIds.length === 0 || preflightMut.isPending}
          onClick={() => preflightMut.mutate()}
        >
          {preflightMut.isPending ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Search className="mr-1.5 h-3.5 w-3.5" />
          )}
          运行预检
        </Button>
        <Button
          type="button"
          size="sm"
          data-testid="handoff-generate-btn"
          disabled={generateBlockedReason != null || createMut.isPending}
          title={generateBlockedReason ?? '生成 A 段 PENDING_EXPORT 交付包'}
          onClick={() => createMut.mutate()}
        >
          {createMut.isPending ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="mr-1.5 h-3.5 w-3.5" />
          )}
          生成交付包
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid="handoff-history-toggle"
          onClick={() => setShowHistory((v) => !v)}
        >
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          {showHistory ? '隐藏历史' : '历史交付包'}
        </Button>
      </div>

      {generateBlockedReason && preflight && !coverageOk && (
        <div
          className={cn('rounded-md px-3 py-2 text-sm', ALERT_BOX.destructive)}
          data-testid="handoff-generate-blocked"
        >
          {generateBlockedReason}
          {preflight.runs.some((r) => r.missing_hosts.length > 0) && (
            <span className="mt-0.5 block text-xs">
              缺失主机：
              {preflight.runs
                .filter((r) => r.missing_hosts.length > 0)
                .map((r) => `#${r.plan_run_id}[${r.missing_hosts.join(',')}]`)
                .join('；')}
            </span>
          )}
        </div>
      )}

      {actionError && (
        <InlineError message={actionError} onRetry={() => setActionError(null)} />
      )}

      {!preflight && !activeBundle && (
        <InlineEmpty bordered testId="handoff-empty-state">
          填写 PlanRun ID 后先运行预检，确认覆盖率双门禁通过再生成交付包
        </InlineEmpty>
      )}

      {preflight && <CoverageGatePanel preflight={preflight} />}

      {showHistory && (
        <div className="space-y-2" data-testid="handoff-history-panel">
          <div className={cn('text-sm font-medium', TEXT.heading)}>历史交付包</div>
          {historyQ.isError && (
            <InlineError
              message="历史交付包加载失败"
              onRetry={() => void historyQ.refetch()}
            />
          )}
          {historyQ.isPending ? (
            <div className={cn('flex items-center gap-1.5 text-xs', TEXT.subtitle)}>
              <Loader2 className="h-3 w-3 animate-spin" /> 加载中…
            </div>
          ) : (historyQ.data?.length ?? 0) === 0 ? (
            <InlineEmpty bordered>暂无历史交付包</InlineEmpty>
          ) : (
            <ul className="divide-y rounded-md border border-border text-xs">
              {historyQ.data?.map((item) => (
                <li key={item.handoff_id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                  <button
                    type="button"
                    className="font-mono underline-offset-2 hover:underline"
                    data-testid={`handoff-history-${item.handoff_id}`}
                    onClick={() => void openHistoryItem(item)}
                  >
                    {item.handoff_id}
                  </button>
                  <span
                    className={cn(
                      'rounded px-1.5 py-0.5',
                      item.status === 'PENDING_EXPORT' ? STATUS_CHIP.warning : STATUS_CHIP.success,
                    )}
                  >
                    {item.status}
                  </span>
                  <span className={TEXT.caption}>
                    runs [{item.plan_run_ids.join(',')}] · {item.issue_count_total} 问题 ·{' '}
                    {formatLocalDateTime(item.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {activeBundle && (
        <BundleDetail
          bundle={activeBundle}
          onDownload={(kind, build) => void handleDownload(kind, build)}
          downloadingKey={downloadingKey}
        />
      )}
    </div>
  );
}
