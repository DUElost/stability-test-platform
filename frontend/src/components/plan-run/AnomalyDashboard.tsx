import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import type {
  AeeDashboardSection,
  PackageRanking,
  PackageSubtypeCount,
  SubtypeDistribution,
  WatcherSummary,
  WatcherTimeScope,
} from '@/utils/api/types';
import SectionHeader from './SectionHeader';

interface Props {
  data?: WatcherSummary;
  isLoading?: boolean;
  isError?: boolean;
  timeScope?: WatcherTimeScope;
  onTimeScopeChange?: (scope: WatcherTimeScope) => void;
}

const TIME_SCOPE_OPTIONS: Array<{ value: WatcherTimeScope; label: string }> = [
  { value: 'all', label: '全量' },
  { value: '15m', label: '15m' },
  { value: '1h', label: '1h' },
  { value: '6h', label: '6h' },
  { value: '24h', label: '24h' },
];

const EMPTY_SECTION: AeeDashboardSection = {
  total_events: 0,
  affected_device_count: 0,
  top_package_name: null,
  top_subtype: null,
  subtype_distribution: [],
  package_ranking: [],
};

const VENDOR_SUBTYPES = new Set([
  'System API Dump',
  'HWT',
  'HANG',
  'KE',
  'HW Reboot',
  'Modem EE',
  'OCP Reboot',
]);

const SUBTYPE_COLORS: Record<string, string> = {
  ANR: '#ef4444',
  JE: '#2563eb',
  NE: '#f59e0b',
  SWT: '#06b6d4',
  'Fatal NE': '#ea580c',
  'Fatal JE': '#7c3aed',
  'Combo EE': '#0f766e',
  'Kernel API Dump': '#475569',
  'System API Dump': '#0ea5e9',
  HWT: '#14b8a6',
  HANG: '#64748b',
  KE: '#334155',
  'HW Reboot': '#84cc16',
  'Modem EE': '#1d4ed8',
  'OCP Reboot': '#9333ea',
  'Vendor 其他': '#94a3b8',
  其他: '#cbd5e1',
};

function subtypeLabel(item: Pick<SubtypeDistribution, 'subtype' | 'group'>): string {
  if (item.subtype === '其他' && item.group === 'VENDOR_AEE') return 'Vendor 其他';
  return item.subtype;
}

function subtypeColor(item: Pick<SubtypeDistribution, 'subtype' | 'group'>): string {
  return SUBTYPE_COLORS[subtypeLabel(item)] ?? SUBTYPE_COLORS['其他'];
}

function formatCompactValue(value: string | null | undefined): string {
  if (!value) return '无';
  return value;
}

function inferSubtypeGroup(subtype: string): 'AEE' | 'VENDOR_AEE' {
  return VENDOR_SUBTYPES.has(subtype) ? 'VENDOR_AEE' : 'AEE';
}

function collapseDistribution(items: SubtypeDistribution[]): SubtypeDistribution[] {
  if (items.length <= 6) return items;
  const total = items.reduce((sum, item) => sum + item.count, 0);
  const leading = items.slice(0, 5);
  const trailingCount = items.slice(5).reduce((sum, item) => sum + item.count, 0);
  if (trailingCount <= 0) return leading;
  return [
    ...leading,
    {
      subtype: '其他',
      group: 'AEE',
      count: trailingCount,
      share: total > 0 ? trailingCount / total : 0,
    },
  ];
}

function buildPackageDistribution(breakdown: PackageSubtypeCount[]): SubtypeDistribution[] {
  const total = breakdown.reduce((sum, item) => sum + item.count, 0);
  return breakdown
    .filter((item) => item.count > 0)
    .map((item) => ({
      subtype: item.subtype,
      group: inferSubtypeGroup(item.subtype),
      count: item.count,
      share: total > 0 ? item.count / total : 0,
    }))
    .sort((a, b) => b.count - a.count || subtypeLabel(a).localeCompare(subtypeLabel(b), 'zh-CN'));
}

function packageSubtypeSummary(row: PackageRanking): string {
  return row.subtype_breakdown
    .slice(0, 2)
    .map((item) => `${item.subtype} ${item.count}`)
    .join(' · ');
}

function SummaryCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[11px] uppercase tracking-[0.16em] text-slate-400">{label}</div>
      <div className={`mt-2 text-lg font-bold ${accent}`}>{value}</div>
    </div>
  );
}

function DonutChart({
  items,
  total,
  tone,
}: {
  items: SubtypeDistribution[];
  total: number;
  tone: string;
}) {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="flex items-center gap-4">
      <div className="relative h-28 w-28 shrink-0">
        <svg viewBox="0 0 100 100" className="-rotate-90">
          <circle cx="50" cy="50" r={radius} stroke="#e2e8f0" strokeWidth="12" fill="none" />
          {items.map((item) => {
            const dash = total > 0 ? (item.count / total) * circumference : 0;
            const node = (
              <circle
                key={`${item.group}-${item.subtype}`}
                cx="50"
                cy="50"
                r={radius}
                fill="none"
                stroke={subtypeColor(item)}
                strokeWidth="12"
                strokeLinecap="round"
                strokeDasharray={`${dash} ${circumference}`}
                strokeDashoffset={-offset}
              />
            );
            offset += dash;
            return node;
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <div className={`text-xl font-bold ${tone}`}>{total}</div>
          <div className="text-[11px] text-slate-400">事件</div>
        </div>
      </div>

      <div className="min-w-0 flex-1 space-y-2">
        {items.length > 0 ? (
          items.map((item) => (
            <div
              key={`${item.group}-${item.subtype}`}
              className="flex items-center justify-between gap-3 text-sm"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ backgroundColor: subtypeColor(item) }}
                />
                <span className="truncate text-slate-700">{subtypeLabel(item)}</span>
              </div>
              <div className="shrink-0 font-mono text-xs text-slate-500">
                {Math.round(item.share * 100)}%
              </div>
            </div>
          ))
        ) : (
          <div className="text-sm text-slate-400">当前范围内暂无细分类型数据</div>
        )}
      </div>
    </div>
  );
}

export default function AnomalyDashboard({
  data,
  isLoading = false,
  isError = false,
  timeScope = 'all',
  onTimeScopeChange,
}: Props) {
  const [selectedPackage, setSelectedPackage] = useState<string | null>(null);

  const supportsOriginSplit = data?.supports_origin_split ?? false;
  const currentRun = data?.current_run ?? EMPTY_SECTION;
  const preexisting = data?.preexisting ?? EMPTY_SECTION;
  const primaryLabel = supportsOriginSplit ? '本次新增' : '当前范围';

  const selectedPackageRow = useMemo(
    () => currentRun.package_ranking.find((row) => row.package_name === selectedPackage) ?? null,
    [currentRun.package_ranking, selectedPackage],
  );
  const focusedDistribution = useMemo(() => {
    if (!selectedPackageRow) return currentRun.subtype_distribution;
    return buildPackageDistribution(selectedPackageRow.subtype_breakdown);
  }, [currentRun.subtype_distribution, selectedPackageRow]);
  const chartDistribution = collapseDistribution(focusedDistribution);
  const chartTotal = chartDistribution.reduce((sum, item) => sum + item.count, 0);
  const preexistingDistribution = collapseDistribution(preexisting.subtype_distribution);
  const preexistingTotal = preexistingDistribution.reduce((sum, item) => sum + item.count, 0);

  return (
    <section
      data-testid="watcher-summary"
      className="space-y-4 rounded-[28px] border border-slate-200 bg-[linear-gradient(180deg,#f8fafc_0%,#ffffff_100%)] p-4 shadow-sm"
    >
      <SectionHeader
        title="异常仪表盘"
        meta="聚焦 AEE / Vendor AEE 细分异常与高风险包名"
        color={currentRun.total_events > 0 ? 'amber' : 'green'}
        extra={
          <div className="flex flex-wrap gap-1">
            {TIME_SCOPE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => onTimeScopeChange?.(option.value)}
                className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
                  timeScope === option.value
                    ? 'border-slate-900 bg-slate-900 text-white'
                    : 'border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:text-slate-700'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        }
      />

      {isLoading && (
        <div className="flex h-28 items-center justify-center text-sm text-slate-400">加载中…</div>
      )}

      {isError && (
        <div className="flex h-28 items-center justify-center gap-2 rounded-2xl border border-rose-200 bg-rose-50 text-sm text-rose-700">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>异常数据加载失败，请稍后重试</span>
        </div>
      )}

      {!isLoading && !isError && (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <SummaryCard
              label={`${primaryLabel}异常总量`}
              value={String(currentRun.total_events)}
              accent="text-slate-900"
            />
            <SummaryCard
              label="影响设备数"
              value={String(currentRun.affected_device_count)}
              accent="text-sky-700"
            />
            <SummaryCard
              label="Top 包名"
              value={formatCompactValue(currentRun.top_package_name)}
              accent="text-amber-700"
            />
            <SummaryCard
              label="Top 类型"
              value={formatCompactValue(currentRun.top_subtype)}
              accent="text-rose-700"
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
            <div className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-4 text-sm font-semibold text-slate-900">
                {`${primaryLabel} · 细分类型占比`}
              </div>
              {currentRun.total_events > 0 ? (
                <DonutChart items={chartDistribution} total={chartTotal} tone="text-slate-900" />
              ) : (
                <div className="flex h-32 items-center justify-center text-sm text-slate-400">
                  {supportsOriginSplit
                    ? '当前范围内未发现新增 AEE / Vendor AEE 异常'
                    : '当前范围内未发现 AEE / Vendor AEE 异常'}
                </div>
              )}
            </div>

            <div className="rounded-[24px] border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-4 text-sm font-semibold text-slate-900">
                {`${primaryLabel} · 包名榜`}
              </div>
              {currentRun.package_ranking.length > 0 ? (
                <div className="space-y-2">
                  {currentRun.package_ranking.slice(0, 5).map((row, index) => {
                    const active = selectedPackageRow?.package_name === row.package_name;
                    return (
                      <button
                        key={row.package_name}
                        type="button"
                        aria-pressed={active}
                        onClick={() =>
                          setSelectedPackage((current) =>
                            current === row.package_name ? null : row.package_name,
                          )
                        }
                        className={`flex w-full items-start justify-between gap-3 rounded-2xl border px-3 py-2 text-left transition ${
                          active
                            ? 'border-slate-900 bg-slate-900 text-white'
                            : 'border-slate-200 bg-slate-50 text-slate-800 hover:border-slate-300 hover:bg-white'
                        }`}
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-semibold text-slate-400">
                              #{index + 1}
                            </span>
                            <span className="truncate font-mono text-sm">{row.package_name}</span>
                          </div>
                          <div
                            className={`mt-1 text-xs ${
                              active ? 'text-slate-200' : 'text-slate-500'
                            }`}
                          >
                            {packageSubtypeSummary(row) || '无细分类型数据'}
                          </div>
                        </div>
                        <div className="shrink-0 text-right">
                          <div className="text-lg font-bold">{row.total_count}</div>
                          <div
                            className={`text-[11px] ${
                              active ? 'text-slate-300' : 'text-slate-400'
                            }`}
                          >
                            {row.affected_device_count} 台设备
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <div className="flex h-32 items-center justify-center text-sm text-slate-400">
                  当前范围内暂无异常包名数据
                </div>
              )}
            </div>
          </div>

          <div className="rounded-[24px] border border-slate-200 bg-slate-100/80 p-4">
            <div className="mb-4 text-sm font-semibold text-slate-700">运行前遗留</div>
            {supportsOriginSplit ? (
              preexisting.total_events > 0 ? (
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:items-center">
                  <DonutChart
                    items={preexistingDistribution}
                    total={preexistingTotal}
                    tone="text-slate-700"
                  />
                  <div className="grid gap-3 sm:grid-cols-3">
                    <SummaryCard
                      label="遗留总量"
                      value={String(preexisting.total_events)}
                      accent="text-slate-800"
                    />
                    <SummaryCard
                      label="Top 包名"
                      value={formatCompactValue(preexisting.top_package_name)}
                      accent="text-slate-700"
                    />
                    <SummaryCard
                      label="Top 类型"
                      value={formatCompactValue(preexisting.top_subtype)}
                      accent="text-slate-700"
                    />
                  </div>
                </div>
              ) : (
                <div className="text-sm text-slate-500">运行开始前无遗留异常记录</div>
              )
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-300 bg-white/70 px-4 py-3 text-sm text-slate-500">
                该计划运行未记录新增/遗留来源标记，无法拆分运行前遗留
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
