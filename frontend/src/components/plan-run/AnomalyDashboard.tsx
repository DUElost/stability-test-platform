import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, X, ChevronRight } from 'lucide-react';
import { Cell, Pie, PieChart, ResponsiveContainer, Sector, Tooltip } from 'recharts';
import type {
  AeeDashboardSection,
  CrashDetailEntry,
  PackageRanking,
  PackageSubtypeCount,
  SubtypeDistribution,
  WatcherSummary,
  WatcherTimeScope,
} from '@/utils/api/types';
import { api } from '@/utils/api';
import { StableResponsiveContainer } from '@/components/charts/StableResponsiveContainer';
import {
  ALERT_BANNER,
  CHART_COLORS,
  DASHBOARD_SUMMARY_CARD,
  DRAWER,
  INTERACTIVE,
  KPI_TONE,
  PACKAGE_ROW,
  SEGMENTED_DARK,
  TEXT,
  packageRankClass,
} from '@/design-system';
import { cn } from '@/lib/utils';
import SectionHeader from './SectionHeader';

interface Props {
  runId: number;
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
  ANR: '#5b74c8',
  JE: '#ffc94d',
  NE: '#f26363',
  SWT: '#67c7df',
  'Fatal NE': '#f08a52',
  'Fatal JE': '#8b68d6',
  'Combo EE': '#4bb5a8',
  'Kernel API Dump': '#7b879b',
  'System API Dump': '#55a8f2',
  HWT: '#8acb69',
  HANG: '#94a3b8',
  KE: '#6b7280',
  'HW Reboot': '#a3cf5b',
  'Modem EE': '#4d87da',
  'OCP Reboot': '#b082ef',
  'Vendor 其他': '#b7c1d4',
  其他: '#d8dee8',
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

function formatSharePercent(share: number): string {
  const value = share * 100;
  const fixed = value.toFixed(1);
  return fixed.replace(/\.?0+$/, '');
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

function packageDominantColor(row: PackageRanking): string {
  if (!row.subtype_breakdown || row.subtype_breakdown.length === 0) {
    return SUBTYPE_COLORS['其他'];
  }
  const dominant = row.subtype_breakdown.reduce((a, b) =>
    b.count > a.count ? b : a,
  );
  return SUBTYPE_COLORS[dominant.subtype] ?? SUBTYPE_COLORS['其他'];
}

function PackageSubtypeDots({ row, active }: { row: PackageRanking; active: boolean }) {
  const items = row.subtype_breakdown.slice(0, 3);
  if (items.length === 0) {
    return <span className={cn('text-xs', TEXT.subtitle)}>无细分类型数据</span>;
  }
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {items.map((item) => (
        <span key={item.subtype} className="inline-flex items-center gap-1">
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: SUBTYPE_COLORS[item.subtype] ?? SUBTYPE_COLORS['其他'] }}
          />
          <span className={cn('text-xs', active ? 'text-muted-foreground/70' : TEXT.subtitle)}>
            {item.subtype} {item.count}
          </span>
        </span>
      ))}
      {row.subtype_breakdown.length > 3 && (
        <span className={cn('text-xs', TEXT.subtitle)}>+{row.subtype_breakdown.length - 3}</span>
      )}
    </div>
  );
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
    <div className={DASHBOARD_SUMMARY_CARD.root}>
      <div className={DASHBOARD_SUMMARY_CARD.label}>{label}</div>
      <div className={cn('mt-2 text-lg font-bold', accent)}>{value}</div>
    </div>
  );
}

function PackageRankingDrawer({
  open,
  onClose,
  rankings,
  selectedPackageName,
  onSelectPackage,
}: {
  open: boolean;
  onClose: () => void;
  rankings: PackageRanking[];
  selectedPackageName: string | null;
  onSelectPackage: (pkg: string | null) => void;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const [drawerPage, setDrawerPage] = useState(0);
  const DRAWER_PAGE_SIZE = 20;

  useEffect(() => {
    if (open) setDrawerPage(0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    drawerRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <>
      <div onClick={onClose} className={DRAWER.overlay} />
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="包名榜完整列表"
        tabIndex={-1}
        className={DRAWER.panel}
      >
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div className="min-w-0">
            <p className={cn('truncate text-xs', TEXT.subtitle)}>当前范围</p>
            <h2 className={cn('truncate text-base font-semibold', TEXT.heading)}>
              包名榜 · 全部 ({rankings.length})
            </h2>
          </div>
          <button type="button" onClick={onClose} className={DRAWER.closeBtn}>
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-3">
          <div className="space-y-2">
            {rankings.slice(drawerPage * DRAWER_PAGE_SIZE, (drawerPage + 1) * DRAWER_PAGE_SIZE).map((row, index) => {
              const actualIndex = drawerPage * DRAWER_PAGE_SIZE + index;
              const active = selectedPackageName === row.package_name;
              const isUnknown = row.package_name === 'unknown';
              const dominantColor = isUnknown
                ? CHART_COLORS.muted
                : packageDominantColor(row);
              const rankCls = packageRankClass(actualIndex);

              return (
                <button
                  key={row.package_name}
                  type="button"
                  aria-pressed={active}
                  onClick={() => {
                    onSelectPackage(
                      row.package_name === selectedPackageName ? null : row.package_name,
                    );
                    onClose();
                  }}
                  className={cn(
                    'group flex w-full items-stretch rounded-xl border text-left transition-all duration-200',
                    active ? PACKAGE_ROW.active
                      : isUnknown ? PACKAGE_ROW.unknown
                      : PACKAGE_ROW.default,
                  )}
                >
                  <div
                    className={`shrink-0 w-1 rounded-l-xl transition-all duration-200 ${
                      active ? 'w-1.5' : 'group-hover:w-1.5'
                    }`}
                    style={{ backgroundColor: dominantColor }}
                  />
                  <div className="flex-1 min-w-0 px-3 py-2 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className={`font-mono tabular-nums ${rankCls}`}>
                          #{index + 1}
                        </span>
                        <span
                          className={cn(
                            'truncate text-sm',
                            isUnknown
                              ? cn('italic', TEXT.subtitle)
                              : active
                                ? cn('font-semibold', TEXT.heading)
                                : cn('font-medium', TEXT.body),
                          )}
                        >
                          {isUnknown ? '未知进程' : row.package_name}
                        </span>
                      </div>
                      <div className="mt-1">
                        <PackageSubtypeDots row={row} active={active} />
                      </div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div
                        className={cn(
                          'text-lg font-bold transition-colors duration-200',
                          active ? TEXT.heading
                            : isUnknown ? TEXT.subtitle
                            : TEXT.body,
                        )}
                      >
                        {row.total_count}
                      </div>
                      <div className={cn('text-[11px]', active ? TEXT.subtitle : 'text-muted-foreground/70')}>
                        {row.affected_device_count} 台设备
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
          {rankings.length > DRAWER_PAGE_SIZE && (
            <div className={cn('flex items-center justify-between border-t px-4 py-2 text-xs', TEXT.subtitle)}>
              <button
                type="button"
                onClick={() => setDrawerPage((p) => Math.max(0, p - 1))}
                disabled={drawerPage === 0}
                className={cn('rounded px-2 py-1 disabled:opacity-30', INTERACTIVE.hover)}
              >
                上一页
              </button>
              <span>
                {drawerPage + 1}/{Math.ceil(rankings.length / DRAWER_PAGE_SIZE)}
              </span>
              <button
                type="button"
                onClick={() => setDrawerPage((p) => Math.min(Math.ceil(rankings.length / DRAWER_PAGE_SIZE) - 1, p + 1))}
                disabled={(drawerPage + 1) * DRAWER_PAGE_SIZE >= rankings.length}
                className={cn('rounded px-2 py-1 disabled:opacity-30', INTERACTIVE.hover)}
              >
                下一页
              </button>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

const DonutChart = memo(function DonutChart({
  items,
  total,
  tone,
  chartTestId,
}: {
  items: SubtypeDistribution[];
  total: number;
  tone: string;
  chartTestId: string;
}) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);

  const chartData = useMemo(
    () =>
      items.map((item, index) => ({
        ...item,
        color: subtypeColor(item),
        fullLabel: subtypeLabel(item),
        key: `${item.group}-${item.subtype}-${index}`,
      })),
    [items],
  );

  if (chartData.length === 0) {
    return (
      <div
        data-testid={chartTestId}
        className={cn('flex h-32 items-center justify-center text-sm', TEXT.subtitle)}
      >
        当前范围内暂无细分类型数据
      </div>
    );
  }

  const renderActiveShape = (props: any) => {
    const {
      cx, cy, innerRadius, outerRadius,
      startAngle, endAngle, fill,
    } = props;
    return (
      <Sector
        cx={cx}
        cy={cy}
        innerRadius={innerRadius}
        outerRadius={outerRadius + 8}
        startAngle={startAngle}
        endAngle={endAngle}
        fill={fill}
        cornerRadius={3}
      />
    );
  };

  return (
    <div
      data-testid={chartTestId}
      data-chart-type="recharts-donut"
      aria-label="异常细分类型占比饼图"
    >
      {/* Donut ring + center total */}
      <div className="relative mx-auto w-full max-w-[320px]">
        <StableResponsiveContainer className="h-[290px] min-h-[290px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Tooltip
                wrapperStyle={{ outline: 'none', zIndex: 50 }}
                content={({ active, payload }) => {
                  if (!active || !payload || payload.length === 0) return null;
                  const item = payload[0].payload as (typeof chartData)[number];
                  return (
                    <div className="rounded-xl border bg-card/95 px-3 py-2 shadow-lg backdrop-blur">
                      <div className={cn('flex items-center gap-2 text-sm font-semibold', TEXT.body)}>
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: item.color }}
                        />
                        <span>{item.fullLabel}</span>
                      </div>
                      <div className={cn('mt-1 text-xs', TEXT.subtitle)}>
                        {`${item.count} 次 · ${formatSharePercent(item.share)}%`}
                      </div>
                    </div>
                  );
                }}
              />
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={55}
                outerRadius={90}
                startAngle={90}
                endAngle={-270}
                paddingAngle={chartData.length > 1 ? 2 : 0}
                dataKey="count"
                stroke="hsl(var(--card))"
                strokeWidth={1}
                cornerRadius={3}
                animationBegin={0}
                animationDuration={600}
                animationEasing="ease-out"
                isAnimationActive
                activeShape={renderActiveShape}
                onMouseEnter={(_data, index) => setActiveIndex(index)}
                onMouseLeave={() => setActiveIndex(null)}
                label={false}
              >
                {chartData.map((item) => (
                  <Cell
                    key={item.key}
                    fill={item.color}
                    style={{
                      filter: 'drop-shadow(0 2px 4px rgba(15, 23, 42, 0.12))',
                    }}
                  />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </StableResponsiveContainer>
        {/* Center total */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <div className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
            异常总数
          </div>
          <div
            data-center-total="true"
            className={`mt-0.5 text-[26px] font-bold leading-none ${tone}`}
          >
            {total}
          </div>
        </div>
      </div>

      {/* Compact legend below */}
      <div
        data-testid={`${chartTestId}-legend`}
        data-legend-position="below"
        className="mt-4 flex flex-wrap justify-center gap-x-3 gap-y-1"
      >
        {chartData.map((item, index) => {
          const isActive = index === activeIndex;
          return (
            <span
              key={`legend-${item.key}`}
              className={cn(
                'inline-flex items-center gap-1 text-xs cursor-pointer transition rounded px-1.5 py-0.5',
                isActive ? 'bg-muted text-foreground' : cn(TEXT.subtitle, 'hover:text-foreground'),
              )}
              onMouseEnter={() => setActiveIndex(index)}
              onMouseLeave={() => setActiveIndex(null)}
            >
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: item.color }}
              />
              <span className="font-medium">{item.fullLabel}</span>
              <span className="tabular-nums">{formatSharePercent(item.share)}%</span>
            </span>
          );
        })}
      </div>
    </div>
  );
});

export default function AnomalyDashboard({
  runId,
  data,
  isLoading = false,
  isError = false,
  timeScope = 'all',
  onTimeScopeChange,
}: Props) {
  const [selectedPackage, setSelectedPackage] = useState<string | null>(null);
  const [isPackageDrawerOpen, setPackageDrawerOpen] = useState(false);
  const [crashDetailPackage, setCrashDetailPackage] = useState<string | null>(null);

  const crashDetailsQ = useQuery({
    queryKey: ['crash-details', runId, crashDetailPackage],
    queryFn: () => api.planRuns.getCrashDetails(runId, crashDetailPackage || undefined),
    enabled: !!crashDetailPackage,
    staleTime: 30_000,
  });

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
    <>
      <section
        data-testid="watcher-summary"
        className="space-y-4 rounded-[28px] border bg-gradient-to-b from-muted/40 to-card p-4 shadow-sm"
    >
      <SectionHeader
        title="异常仪表盘"
        meta="聚焦 AEE / Vendor AEE 细分异常与高风险包名"
        color={currentRun.total_events > 0 ? 'amber' : 'green'}
        extra={
          <div className={SEGMENTED_DARK.track}>
            {TIME_SCOPE_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => onTimeScopeChange?.(option.value)}
                className={timeScope === option.value ? SEGMENTED_DARK.itemActive : SEGMENTED_DARK.item}
              >
                {option.label}
              </button>
            ))}
          </div>
        }
      />

      {isLoading && (
        <div className={cn('flex h-28 items-center justify-center text-sm', TEXT.subtitle)}>加载中…</div>
      )}

      {isError && (
        <div className={cn('flex h-28 items-center justify-center gap-2 rounded-2xl text-sm', ALERT_BANNER.destructive)}>
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
              accent={KPI_TONE.default.value}
            />
            <SummaryCard
              label="影响设备数"
              value={String(currentRun.affected_device_count)}
              accent={KPI_TONE.info.value}
            />
            <SummaryCard
              label="Top 包名"
              value={formatCompactValue(currentRun.top_package_name)}
              accent={KPI_TONE.warning.value}
            />
            <SummaryCard
              label="Top 类型"
              value={formatCompactValue(currentRun.top_subtype)}
              accent={KPI_TONE.destructive.value}
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
            <div className={DASHBOARD_SUMMARY_CARD.panel}>
              <div className={cn('mb-4 text-sm font-semibold', TEXT.heading)}>
                {`${primaryLabel} · 细分类型占比`}
              </div>
              {currentRun.total_events > 0 ? (
                <DonutChart
                  items={chartDistribution}
                  total={chartTotal}
                  tone={KPI_TONE.default.value}
                  chartTestId="current-run-pie-chart"
                />
              ) : (
                <div className={cn('flex h-32 items-center justify-center text-sm', TEXT.subtitle)}>
                  {supportsOriginSplit
                    ? '当前范围内未发现新增 AEE / Vendor AEE 异常'
                    : '当前范围内未发现 AEE / Vendor AEE 异常'}
                </div>
              )}
            </div>

            <div className={DASHBOARD_SUMMARY_CARD.panel}>
              <div className={cn('mb-1 text-sm font-semibold', TEXT.heading)}>
                {`${primaryLabel} · 包名榜`}
              </div>
              <div className={cn('mb-4 text-[11px]', TEXT.subtitle)}>
                点击包名筛选饼图
              </div>
              {currentRun.package_ranking.length > 0 ? (
                <div className="space-y-2">
                  {currentRun.package_ranking.slice(0, 5).map((row, index) => {
                    const active = selectedPackageRow?.package_name === row.package_name;
                    const isUnknown = row.package_name === 'unknown';
                    const dominantColor = isUnknown
                      ? CHART_COLORS.muted
                      : packageDominantColor(row);
                    const rankCls = packageRankClass(index);

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
                        className={cn(
                          'group flex w-full items-stretch rounded-xl border text-left transition-all duration-200',
                          active ? PACKAGE_ROW.active
                            : isUnknown ? PACKAGE_ROW.unknown
                            : PACKAGE_ROW.default,
                        )}
                      >
                        <div
                          className={cn(
                            'shrink-0 w-1 rounded-l-xl transition-all duration-200',
                            active ? 'w-1.5' : 'group-hover:w-1.5',
                          )}
                          style={{ backgroundColor: dominantColor }}
                        />
                        <div className="flex-1 min-w-0 px-3 py-2 flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className={cn('font-mono tabular-nums', rankCls)}>
                                #{index + 1}
                              </span>
                              <span
                                className={cn(
                                  'truncate text-sm',
                                  isUnknown
                                    ? cn('italic', TEXT.subtitle)
                                    : active
                                      ? cn('font-semibold', TEXT.heading)
                                      : cn('font-medium', TEXT.body),
                                )}
                              >
                                {isUnknown ? '未知进程' : row.package_name}
                              </span>
                            </div>
                            <div className="mt-1">
                              <PackageSubtypeDots row={row} active={active} />
                            </div>
                          </div>
                          <div className="shrink-0 text-right transition-all duration-300">
                            <div
                              className={cn(
                                'text-lg font-bold transition-colors duration-200',
                                active ? TEXT.heading
                                  : isUnknown ? TEXT.subtitle
                                  : TEXT.body,
                              )}
                            >
                              {row.total_count}
                            </div>
                            <div className={cn('text-[11px]', active ? TEXT.subtitle : 'text-muted-foreground/70')}>
                              {row.affected_device_count} 台设备
                            </div>
                            <button
                              type="button"
                              data-testid={`crash-detail-btn-${row.package_name}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setCrashDetailPackage(
                                  crashDetailPackage === row.package_name ? null : row.package_name,
                                );
                              }}
                              className="mt-1 inline-flex items-center gap-0.5 text-[10px] font-medium text-info hover:text-info/80"
                            >
                              查看 {row.total_count} 条详情
                              <ChevronRight className="h-2.5 w-2.5" />
                            </button>
                          </div>
                        </div>
                      </button>
                    );
                  })}

                  {crashDetailPackage && (
                    <CrashDetailPanel
                      packageName={crashDetailPackage}
                      details={crashDetailsQ.data}
                      isLoading={crashDetailsQ.isLoading}
                      onClose={() => setCrashDetailPackage(null)}
                    />
                  )}

                  {currentRun.package_ranking.length > 5 && (
                    <button
                      type="button"
                      onClick={() => setPackageDrawerOpen(true)}
                      className={cn(
                        'w-full rounded-xl border border-dashed py-2 text-xs font-medium transition',
                        TEXT.subtitle,
                        'hover:border-border hover:text-foreground',
                      )}
                    >
                      查看全部 ({currentRun.package_ranking.length})
                    </button>
                  )}
                </div>
              ) : (
                <div className={cn('flex h-32 items-center justify-center text-sm', TEXT.subtitle)}>
                  当前范围内暂无异常包名数据
                </div>
              )}
            </div>
          </div>

          <div className={DASHBOARD_SUMMARY_CARD.sectionMuted}>
            <div className={cn('mb-4 text-sm font-semibold', TEXT.body)}>运行前遗留</div>
            {supportsOriginSplit ? (
              preexisting.total_events > 0 ? (
                <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:items-center">
                  <DonutChart
                    items={preexistingDistribution}
                    total={preexistingTotal}
                    tone={KPI_TONE.default.label}
                    chartTestId="preexisting-pie-chart"
                  />
                  <div className="grid gap-3 sm:grid-cols-3">
                    <SummaryCard
                      label="遗留总量"
                      value={String(preexisting.total_events)}
                      accent={KPI_TONE.default.value}
                    />
                    <SummaryCard
                      label="Top 包名"
                      value={formatCompactValue(preexisting.top_package_name)}
                      accent={KPI_TONE.default.label}
                    />
                    <SummaryCard
                      label="Top 类型"
                      value={formatCompactValue(preexisting.top_subtype)}
                      accent={KPI_TONE.default.label}
                    />
                  </div>
                </div>
              ) : (
                <div className={cn('text-sm', TEXT.subtitle)}>运行开始前无遗留异常记录</div>
              )
            ) : (
              <div className={cn('rounded-2xl border border-dashed bg-card/70 px-4 py-3 text-sm', TEXT.subtitle)}>
                该计划运行未记录新增/遗留来源标记，无法拆分运行前遗留
              </div>
            )}
          </div>
        </div>
      )}
    </section>

      <PackageRankingDrawer
        open={isPackageDrawerOpen}
        onClose={() => setPackageDrawerOpen(false)}
        rankings={currentRun.package_ranking}
        selectedPackageName={selectedPackage}
        onSelectPackage={setSelectedPackage}
      />
    </>
  );
}

function CrashDetailPanel({
  packageName,
  details,
  isLoading,
  onClose,
}: {
  packageName: string;
  details?: CrashDetailEntry[];
  isLoading: boolean;
  onClose: () => void;
}) {
  return (
    <div
      data-testid="crash-detail-panel"
      className="rounded-xl border border-info/25 bg-info/5 p-3"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className={cn('text-xs font-semibold', TEXT.body)}>
          {packageName === 'unknown' ? '未知进程' : packageName} · Crash 详情
        </span>
        <button type="button" onClick={onClose} className={DRAWER.closeBtn}>
          <X className="h-3 w-3" />
        </button>
      </div>
      {isLoading ? (
        <div className={cn('py-3 text-center text-xs', TEXT.subtitle)}>加载中…</div>
      ) : !details || details.length === 0 ? (
        <div className={cn('py-3 text-center text-xs', TEXT.subtitle)}>暂无详情数据</div>
      ) : (
        <div className="max-h-48 space-y-1 overflow-y-auto">
          {details.map((d, i) => (
            <div
              key={i}
              className="flex items-center gap-2 rounded border bg-card px-2 py-1 text-[11px]"
            >
              <span className={cn('font-mono', TEXT.subtitle)}>{d.subtype}</span>
              <span className="text-muted-foreground/70">{d.device_serial}</span>
              <span className="text-muted-foreground/50">{d.detected_at}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
