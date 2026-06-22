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
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import SectionHeader from './SectionHeader';
import type {
  AeeBreakdown,
  PackageStat,
  WatcherCategory,
  WatcherSummary,
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

const CATEGORY_TONE: Record<string, string> = {
  AEE: 'border-red-300 bg-red-50',
  VENDOR_AEE: 'border-red-300 bg-red-50',
  ANR: 'border-amber-300 bg-amber-50',
  TOMBSTONE: 'border-purple-300 bg-purple-50',
  MOBILELOG: 'border-blue-300 bg-blue-50',
};

// 类别 → by_package 中用于排序 / Top3 展示的字段
const CATEGORY_BREAKDOWN_FIELD: Record<string, keyof PackageStat> = {
  AEE: 'crash_count',
  VENDOR_AEE: 'vendor_crash_count',
  ANR: 'anr_count',
};

function fmtTime(ts: string | null | undefined): string {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString('zh-CN', { hour12: false });
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
  const tone = CATEGORY_TONE[cat.category] ?? 'border-gray-300 bg-gray-50';
  const trend = cat.trend_change;
  return (
    <div
      data-testid={`watcher-cat-${cat.category}`}
      title={topTitle || undefined}
      className={`grid grid-cols-[1fr_auto_auto] items-center gap-2 rounded-lg border-l-4 px-3 py-2 ${tone}`}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-xs font-semibold text-gray-900">
          <span className="font-mono uppercase tracking-wider">{cat.category}</span>
          <span className="font-normal text-gray-500">
            {CATEGORY_LABEL[cat.category] ?? '—'}
          </span>
        </div>
        <div className="mt-0.5 text-[11px] text-gray-500">
          影响 {cat.affected_device_count} 台设备
          {cat.latest_device_serial && (
            <>
              {' · 最近 '}
              <span className="font-mono text-gray-700">
                {cat.latest_device_serial}
              </span>{' '}
              <span className="text-gray-400">{fmtTime(cat.latest_detected_at)}</span>
            </>
          )}
        </div>
      </div>
      <div className="text-right">
        <div className="font-mono text-lg font-bold tabular-nums text-gray-900">
          {cat.count}
        </div>
        <div className="text-[11px] uppercase tracking-wider text-gray-400">条</div>
      </div>
      <div
        data-testid={`watcher-cat-${cat.category}-trend`}
        className={`flex w-14 items-center justify-end gap-0.5 text-xs font-mono ${
          trend > 0
            ? 'text-red-600'
            : trend < 0
            ? 'text-green-600'
            : 'text-gray-400'
        }`}
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
      className="ml-1 inline-flex items-center rounded border border-orange-300 bg-orange-50 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-orange-800"
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
          className="rounded bg-red-100 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-red-700"
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
          className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-amber-700"
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
      className="flex flex-wrap items-center gap-1 border-b bg-gray-50/60 px-3 py-2"
    >
      <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-gray-500">
        应用
      </span>
      {breakdown.by_package.map((p) => {
        const total = p.crash_count + p.vendor_crash_count + p.anr_count;
        return (
          <span
            key={p.package_name}
            data-testid={`watcher-pkg-${p.package_name}`}
            className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-xs"
            title={`crash ${p.crash_count} · vendor ${p.vendor_crash_count} · anr ${p.anr_count}`}
          >
            <span
              className={
                p.package_name === 'unknown'
                  ? 'text-gray-500'
                  : 'font-medium text-gray-800'
              }
            >
              {p.package_name}
            </span>
            <span className="font-mono text-gray-500">({total})</span>
          </span>
        );
      })}
    </div>
  );
}

export default function WatcherSummaryCard({
  data,
  isLoading = false,
  isError = false,
  windowMinutes = 60,
  onWindowChange,
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
          <div className="flex items-center gap-1 rounded-md border bg-white p-0.5 text-xs">
            {WINDOW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                data-testid={`watcher-window-${opt.value}`}
                onClick={() => onWindowChange?.(opt.value)}
                className={`rounded px-2 py-0.5 ${
                  windowMinutes === opt.value
                    ? 'bg-blue-100 text-blue-700'
                    : 'text-gray-500 hover:bg-gray-100'
                }`}
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
          className={`ml-0.5 rounded p-0.5 transition ${
            showDetails
              ? 'bg-blue-100 text-blue-600'
              : 'text-gray-400 hover:bg-gray-100 hover:text-gray-600'
          }`}
        >
          <Info className="h-3.5 w-3.5" />
        </button>
      </SectionHeader>

      <div className="overflow-hidden rounded-xl border bg-white shadow-sm">
        {/* Threshold banner */}
        {data && exceeded && (
          <div
            data-testid="watcher-threshold-banner"
            className="flex items-center gap-2 border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-800"
          >
            <ShieldAlert className="h-4 w-4 shrink-0" />
            <span className="font-semibold">超过阈值 {(threshold * 100).toFixed(0)}%</span>
            <span className="text-red-700">
              · 当前异常率 <b className="font-mono">{(rate * 100).toFixed(1)}%</b>
              ({affected}/{totalDevices})
            </span>
          </div>
        )}

        {data && !exceeded && total > 0 && (
          <div
            data-testid="watcher-warn-banner"
            className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800"
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
            <AlertCircle className="mb-2 h-6 w-6 text-red-400" />
            <span className="text-xs font-semibold text-red-600">加载失败</span>
            <span className="mt-1 text-[11px] text-red-400">请检查网络连接或稍后重试</span>
          </div>
        ) : isLoading && cats.length === 0 ? (
          <div className="space-y-2 p-3">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : cats.length === 0 ? (
          <div className="py-8 text-center text-xs text-gray-400">
            <Activity className="mx-auto mb-2 h-5 w-5 opacity-30" />
            <div>该窗口内未检测到异常</div>
            <div
              data-testid="watcher-disabled-hint"
              className="mt-1 text-[11px] text-gray-400/80"
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
          <div className="border-t bg-gray-50 px-4 py-2">
            <div className="mb-1 flex items-center justify-between text-[11px] text-gray-500">
              <span>设备异常率</span>
              <span className="font-mono">
                {(rate * 100).toFixed(1)}% · 阈值 {(threshold * 100).toFixed(0)}%
              </span>
            </div>
            <div className="relative h-1.5 overflow-hidden rounded-full bg-gray-200">
              <div
                className={`h-full transition-all ${
                  exceeded ? 'bg-red-500' : rate > 0 ? 'bg-amber-500' : 'bg-green-500'
                }`}
                style={{ width: `${Math.min(100, rate * 100)}%` }}
              />
              {threshold > 0 && (
                <div
                  data-testid="watcher-threshold-marker"
                  className="absolute top-0 h-full w-px bg-gray-700"
                  style={{ left: `${Math.min(100, threshold * 100)}%` }}
                />
              )}
            </div>
          </div>
        )}

        {/* ADR-0025 Sprint 3: 存储运维概览 + scan 占位 */}
        {data?.archive && data.archive.ops_metrics && (
          <div className="border-t bg-gray-50 px-4 py-2" data-testid="watcher-archive-section">
            <div className="mb-1 flex items-center justify-between text-[11px] text-gray-500">
              <span className="flex items-center gap-1">
                <Archive className="h-3 w-3" />
                存储运维
              </span>
              <span className="font-mono text-gray-400" data-testid="hdd-usage">
                HDD {data.archive.ops_metrics.local_disk_usage_pct != null
                  ? `${Math.round(data.archive.ops_metrics.local_disk_usage_pct)}%`
                  : '—'}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-1 text-[10px] text-center text-gray-400">
              <div>SSD清理 {data.archive.ops_metrics.pruned_total}</div>
              <div>溢出 {data.archive.ops_metrics.spill_cycles}</div>
              <div>上送 {data.archive.ops_metrics.spilled_total}</div>
            </div>
            {data.archive.scan_status && (
              <div className="mt-1 text-[10px] text-gray-400">
                Scan: {data.archive.scan_status}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
