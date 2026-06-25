import { useState } from 'react';
import {
  AlertTriangle,
  Activity,
  Archive,
  ArrowDown,
  ArrowUp,
  ShieldAlert,
  Minus,
  Info,
  AlertCircle,
  Search,
  Merge,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import SectionHeader from './SectionHeader';
import { triggerDedupScan, triggerDedupMerge } from '@/utils/api/planRuns';
import {
  ALERT_BANNER,
  DEDUP_STATUS_CHIP,
  PANEL,
  SEGMENTED,
  STATUS_CHIP,
  TEXT,
  TREND,
  WATCHER_CATEGORY,
  dedupActionBtnClass,
} from '@/design-system';
import { cn } from '@/lib/utils';
import { formatTimeLabel } from '@/utils/format';
import type {
  AeeBreakdown,
  PackageStat,
  WatcherCategory,
  WatcherSummary,
  DedupScanStatus,
} from '@/utils/api/types';

interface Props {
  data: WatcherSummary | undefined;
  isLoading?: boolean;
  isError?: boolean;
  /** Window minutes filter; lifted up so the parent can sync URL params. */
  windowMinutes?: number;
  onWindowChange?: (minutes: number) => void;
  // ADR-0025 S2: 手动立即归档(grace=0)按钮回调;由父组件 PlanRunDetailPage 注入
  onArchiveNow?: () => Promise<void> | void;
  /** PlanRun id, used for dedup scan/merge API calls. */
  runId?: number;
  /** Callback after scan/merge triggers, so parent can refresh data. */
  onDedupAction?: () => void;
}

const WINDOW_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 15, label: '15 分钟' },
  { value: 60, label: '1 小时' },
  { value: 360, label: '6 小时' },
  { value: 1440, label: '24 小时' },
];

const CATEGORY_LABEL: Record<string, string> = {
  AEE: '主进程崩溃 (AEE)',
  VENDOR_AEE: '厂商进程崩溃',
  ANR: '应用无响应 (ANR)',
  TOMBSTONE: 'Native Tombstone',
  MOBILELOG: 'mobile log 异常',
};

const CATEGORY_TONE = WATCHER_CATEGORY;

// 类别 → by_package 中用于排序 / Top3 展示的字段
const CATEGORY_BREAKDOWN_FIELD: Record<string, keyof PackageStat> = {
  AEE: 'crash_count',
  VENDOR_AEE: 'vendor_crash_count',
  ANR: 'anr_count',
};

function fmtTime(ts: string | null | undefined): string {
  return formatTimeLabel(ts ?? null);
}

function topPackagesTitle(
  category: string,
  by_package: PackageStat[] | undefined,
): string {
  if (!by_package || by_package.length === 0) return '';
  const field = CATEGORY_BREAKDOWN_FIELD[category];
  if (!field) return '';
  const top = [...by_package]
    .filter((p) => Number(p[field]) > 0)
    .sort((a, b) => Number(b[field]) - Number(a[field]))
    .slice(0, 3);
  if (top.length === 0) return '';
  return (
    'Top 3 应用: ' +
    top.map((p) => `${p.package_name} (${p[field]})`).join(', ')
  );
}

function CategoryRow({
  cat,
  topTitle,
}: {
  cat: WatcherCategory;
  topTitle: string;
}) {
  const tone = CATEGORY_TONE[cat.category as keyof typeof WATCHER_CATEGORY] ?? WATCHER_CATEGORY.default;
  const trend = cat.trend_change;
  return (
    <div
      data-testid={`watcher-cat-${cat.category}`}
      title={topTitle || undefined}
      className={cn('grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded-lg border-l-4 px-3 py-2', tone)}
    >
      <div className="min-w-0">
        <div className={cn('flex items-center gap-2 text-xs font-semibold', TEXT.heading)}>
          <span className="font-mono uppercase tracking-wider">{cat.category}</span>
          <span className={cn('font-normal', TEXT.subtitle)}>
            {CATEGORY_LABEL[cat.category] ?? '—'}
          </span>
        </div>
        <div className={cn('mt-0.5 text-[11px]', TEXT.subtitle)}>
          影响 {cat.affected_device_count} 台设备
          {cat.latest_device_serial && (
            <>
              {' · 最近 '}
              <span className={cn('font-mono', TEXT.body)}>
                {cat.latest_device_serial}
              </span>{' '}
              <span className="text-muted-foreground/70">{fmtTime(cat.latest_detected_at)}</span>
            </>
          )}
        </div>
      </div>
      <div className="text-right">
        <div className={cn('font-mono text-lg font-bold tabular-nums', TEXT.heading)}>
          {cat.count}
        </div>
        <div className="text-[11px] uppercase tracking-wider text-muted-foreground/70">条</div>
      </div>
      <div
        data-testid={`watcher-cat-${cat.category}-trend`}
        className={cn(
          'flex w-14 items-center justify-end gap-0.5 text-xs font-mono',
          trend > 0 ? TREND.up : trend < 0 ? TREND.down : TREND.flat,
        )}
      >
        {trend > 0 ? (
          <ArrowUp className="h-3 w-3" />
        ) : trend < 0 ? (
          <ArrowDown className="h-3 w-3" />
        ) : (
          <Minus className="h-3 w-3" />
        )}
        {trend === 0 ? '0' : trend > 0 ? `+${trend}` : `${trend}`}
      </div>
    </div>
  );
}

// M0/C-6 (§2.4 #5): watcher_capability=unavailable 表示 watcher 未正常启动,
//   AEE reconciler 可能也未运行;勿将 unavailable 误解为 reconciler 单通道兜底。
function CapabilityBadge({ capability }: { capability: string | null | undefined }) {
  if (capability !== 'unavailable') return null;
  return (
    <span
      data-testid="watcher-capability-badge"
      data-capability="unavailable"
      title={
        'watcher_capability=unavailable:Watcher 未正常启动(inotifyd / polling 探测失败)。' +
        'AEE reconciler 可能未运行,勿当作有 reconciler 兜底;请以 signal 侧数据为准。'
      }
      className={cn(
        'ml-1 inline-flex items-center rounded border border-warning/30 px-1.5 py-0.5',
        'font-mono text-[11px] font-semibold',
        STATUS_CHIP.warning,
      )}
    >
      Watcher 不可用
    </span>
  );
}

function AeeBreakdownChips({ breakdown }: { breakdown: AeeBreakdown }) {
  const crash = breakdown.crash_count + breakdown.vendor_crash_count;
  const anr = breakdown.anr_count;
  if (crash === 0 && anr === 0) return null;
  return (
    <span
      data-testid="watcher-aee-summary"
      className="ml-1 inline-flex items-center gap-1"
    >
      {crash > 0 && (
        <span
          data-testid="watcher-crash-chip"
          className={cn('rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold', STATUS_CHIP.destructive)}
          title={
            breakdown.vendor_crash_count > 0
              ? `AEE ${breakdown.crash_count} + Vendor ${breakdown.vendor_crash_count}`
              : undefined
          }
        >
          {crash} Crash
        </span>
      )}
      {anr > 0 && (
        <span
          data-testid="watcher-anr-chip"
          className={cn('rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold', STATUS_CHIP.warning)}
        >
          {anr} ANR
        </span>
      )}
    </span>
  );
}

function PackagesChipRow({ breakdown }: { breakdown: AeeBreakdown }) {
  if (!breakdown.by_package || breakdown.by_package.length === 0) return null;
  return (
    <div
      data-testid="watcher-packages-row"
      className="flex flex-wrap items-center gap-1 border-b bg-muted/50 px-3 py-2"
    >
      <span className={cn('mr-1 text-[11px] font-semibold uppercase tracking-wider', TEXT.subtitle)}>
        应用
      </span>
      {breakdown.by_package.map((p) => {
        const total = p.crash_count + p.vendor_crash_count + p.anr_count;
        return (
          <span
            key={p.package_name}
            data-testid={`watcher-pkg-${p.package_name}`}
            className="inline-flex items-center gap-1 rounded-full border bg-card px-2 py-0.5 text-xs"
            title={`crash ${p.crash_count} · vendor ${p.vendor_crash_count} · anr ${p.anr_count}`}
          >
            <span
              className={
                p.package_name === 'unknown'
                  ? TEXT.subtitle
                  : cn('font-medium', TEXT.body)
              }
            >
              {p.package_name}
            </span>
            <span className={cn('font-mono', TEXT.subtitle)}>({total})</span>
          </span>
        );
      })}
    </div>
  );
}

function DedupScanStatusChip({ scanStatus }: { scanStatus: DedupScanStatus }) {
  if (!scanStatus) return null;
  const labelMap: Record<string, string> = { pending: '待扫描', scanned: '已扫描', merged: '已合并' };
  return (
    <span
      data-testid="dedup-scan-status-chip"
      className={cn(
        'inline-flex items-center rounded-full px-1.5 py-0.5 font-mono text-[10px] font-semibold',
        DEDUP_STATUS_CHIP[scanStatus] ?? STATUS_CHIP.muted,
      )}
    >
      {labelMap[scanStatus] ?? scanStatus}
    </span>
  );
}

function DedupActionButtons({
  runId,
  scanStatus,
  onAction,
}: {
  runId: number;
  scanStatus: DedupScanStatus;
  onAction?: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const doScan = async () => {
    setLoading(true);
    try { await triggerDedupScan(runId); } finally { setLoading(false); onAction?.(); }
  };
  const doMerge = async () => {
    setLoading(true);
    try { await triggerDedupMerge(runId); } finally { setLoading(false); onAction?.(); }
  };
  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        data-testid="dedup-scan-btn"
        disabled={loading}
        onClick={doScan}
        title="运行去重扫描"
        className={dedupActionBtnClass('primary')}
      >
        <Search className="mr-0.5 inline h-3 w-3" />扫描
      </button>
      {scanStatus === 'scanned' && (
        <button
          type="button"
          data-testid="dedup-merge-btn"
          disabled={loading}
          onClick={doMerge}
          title="合并去重结果"
          className={dedupActionBtnClass('success')}
        >
          <Merge className="mr-0.5 inline h-3 w-3" />合并
        </button>
      )}
    </span>
  );
}

export default function WatcherSummaryCard({
  data,
  isLoading = false,
  isError = false,
  windowMinutes = 60,
  onWindowChange,
  runId,
  onDedupAction,
}: Props) {
  const total = data?.total ?? 0;
  const affected = data?.affected_device_count ?? 0;
  const totalDevices = data?.total_devices ?? 0;
  const rate = data?.abnormal_rate ?? 0;
  const threshold = data?.threshold ?? 0;
  const exceeded = data?.exceeded ?? false;
  const cats = data?.categories ?? [];
  const breakdown = data?.aee_breakdown ?? null;
  const showPackagesRow =
    !!breakdown && breakdown.by_package && breakdown.by_package.length > 0;
  const [showDetails, setShowDetails] = useState(false);

  return (
    <section data-testid="watcher-summary" className="space-y-2">
      <SectionHeader
        title="Watcher 异常聚合"
        meta={
          data
            ? `窗口 ${data.window_minutes} 分钟 · 共 ${total} 条 · 影响 ${affected}/${totalDevices} 台`
            : undefined
        }
        extra={
          <div className={SEGMENTED.track}>
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                data-testid={`watcher-window-${opt.value}`}
                onClick={() => onWindowChange?.(opt.value)}
                className={cn(
                  windowMinutes === opt.value ? SEGMENTED.itemActive : SEGMENTED.item,
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        }
      >
        {breakdown && <AeeBreakdownChips breakdown={breakdown} />}
        {showDetails && data && <CapabilityBadge capability={data.watcher_capability} />}
        <button
          type="button"
          data-testid="watcher-details-toggle"
          onClick={() => setShowDetails((v) => !v)}
          title={showDetails ? '隐藏技术详情' : '显示技术详情'}
          className={cn(
            'ml-0.5 rounded p-0.5 transition',
            showDetails ? SEGMENTED.toggleActive : cn(SEGMENTED.toggleIdle),
          )}
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </SectionHeader>

      <div className={PANEL.root}>
        {/* Threshold banner */}
        {data && exceeded && (
          <div
            data-testid="watcher-threshold-banner"
            className={cn('flex items-center gap-2 px-4 py-2 text-xs', ALERT_BANNER.destructive)}
          >
            <ShieldAlert className="h-4 w-4 shrink-0" />
            <span className="font-semibold">超过阈值 {(threshold * 100).toFixed(0)}%</span>
            <span>
              · 当前异常率 <b className="font-mono">{(rate * 100).toFixed(1)}%</b>
              ({affected}/{totalDevices})
            </span>
          </div>
        )}

        {data && !exceeded && total > 0 && (
          <div
            data-testid="watcher-warn-banner"
            className={cn('flex items-center gap-2 px-4 py-2 text-xs', ALERT_BANNER.warning)}
          >
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span>异常率 <b className="font-mono">{(rate * 100).toFixed(1)}%</b> · 阈值 {(threshold * 100).toFixed(0)}%</span>
          </div>
        )}

        {/* Packages chip row (M0/PR #2 — reconciler signal extra.package_name 聚合) */}
        {showPackagesRow && <PackagesChipRow breakdown={breakdown!} />}

        {/* Body */}
        {isError ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <AlertCircle className="mb-2 h-6 w-6 text-destructive/60" />
            <span className="text-xs font-semibold text-destructive">加载失败</span>
            <span className="mt-1 text-[11px] text-destructive/70">请检查网络连接或稍后重试</span>
          </div>
        ) : isLoading && cats.length === 0 ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : cats.length === 0 ? (
          <div className={cn('py-8 text-center text-xs', TEXT.subtitle)}>
            <Activity className="mx-auto mb-2 h-5 w-5 opacity-30" />
            <div>该窗口内未检测到异常</div>
            <div
              data-testid="watcher-disabled-hint"
              className="mt-1 text-[11px] text-muted-foreground/70"
            >
              如长期为空，请确认 Agent 侧 Watcher 已启用
            </div>
          </div>
        ) : (
          <div className="space-y-2 p-3">
            {cats.map((c) => (
              <CategoryRow
                key={c.category}
                cat={c}
                topTitle={topPackagesTitle(c.category, breakdown?.by_package)}
              />
            ))}
          </div>
        )}

        {/* Bottom progress bar */}
        {data && totalDevices > 0 && (
          <div className={PANEL.footer}>
            <div className={cn('mb-1 flex items-center justify-between text-[11px]', TEXT.subtitle)}>
              <span>设备异常率</span>
              <span className="font-mono">
                {(rate * 100).toFixed(1)}% · 阈值 {(threshold * 100).toFixed(0)}%
              </span>
            </div>
            <div className="relative h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  'h-full transition-all',
                  exceeded ? 'bg-destructive' : rate > 0 ? 'bg-warning' : 'bg-success',
                )}
                style={{ width: `${Math.min(100, rate * 100)}%` }}
              />
              {threshold > 0 && (
                <div
                  data-testid="watcher-threshold-marker"
                  className="absolute top-0 h-full w-px bg-foreground/60"
                  style={{ left: `${Math.min(100, threshold * 100)}%` }}
                />
              )}
            </div>
          </div>
        )}

        {/* ADR-0025 Sprint 3: 存储运维概览 + dedup scan/merge */}
        {data?.archive && data.archive.ops_metrics && (
          <div className={PANEL.footer} data-testid="watcher-archive-section">
            <div className={cn('mb-1 flex items-center justify-between text-[11px]', TEXT.subtitle)}>
              <span className="flex items-center gap-1">
                <Archive className="h-3 w-3" />
                存储运维
              </span>
              <div className="flex items-center gap-2">
                <DedupScanStatusChip scanStatus={data.archive.scan_status ?? null} />
                {runId != null && (
                  <DedupActionButtons
                    runId={runId}
                    scanStatus={data.archive.scan_status ?? null}
                    onAction={onDedupAction}
                  />
                )}
                <span className="font-mono text-muted-foreground/70" data-testid="hdd-usage">
                  HDD {data.archive.ops_metrics.local_disk_usage_pct != null
                    ? `${Math.round(data.archive.ops_metrics.local_disk_usage_pct)}%`
                    : '—'}
                </span>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-1 text-center text-[10px] text-muted-foreground/70">
              <div>SSD清理 {data.archive.ops_metrics.pruned_total}</div>
              <div>溢出 {data.archive.ops_metrics.spill_cycles}</div>
              <div>上送 {data.archive.ops_metrics.spilled_total}</div>
            </div>
            {(data.archive.archived_jobs || data.archive.pending_jobs || data.archive.failed_jobs) ? (
              <div className={cn('mt-1 flex items-center gap-2 text-[10px]', TEXT.subtitle)}>
                <span className="text-success">{data.archive.archived_jobs} 归档</span>
                {data.archive.pending_jobs ? (
                  <span className="text-muted-foreground/70">{data.archive.pending_jobs} 归档中</span>
                ) : null}
                {data.archive.failed_jobs ? (
                  <span className="text-warning">{data.archive.failed_jobs} 归档失败</span>
                ) : null}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}
