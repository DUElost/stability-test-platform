import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Search,
  Smartphone,
  X,
} from 'lucide-react';
import { Cell, Pie, PieChart, ResponsiveContainer, Sector, Tooltip, type PieSectorDataItem } from 'recharts';
import type {
  AeeDashboardSection,
  CrashDetailEntry,
  PackageRanking,
  SubtypeDistribution,
  WatcherSummary,
  WatcherTimeScope,
} from '@/utils/api/types';
import { api } from '@/utils/api';
import { StableResponsiveContainer } from '@/components/charts/StableResponsiveContainer';
import { Button } from '@/components/ui/button';
import { InlineEmpty } from '@/components/ui/empty-state';
import { PaginationBar } from '@/components/ui/pagination-bar';
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet';
import { StateTabs } from '@/components/ui/state-tabs';
import {
  ALERT_BANNER,
  aeeSubtypeChartColor,
  CHART_COLORS,
  DASHBOARD_SUMMARY_CARD,
  DRAWER,
  FORM,
  KPI_TONE,
  PACKAGE_ROW,
  PANEL,
  TEXT,
  packageRankClass,
} from '@/design-system';
import { cn } from '@/lib/utils';
import { formatDateTimeShort } from '@/utils/format';

type OriginTab = 'current' | 'preexisting';

interface Props {
  runId: number;
  data?: WatcherSummary;
  isLoading?: boolean;
  isError?: boolean;
  timeScope?: WatcherTimeScope;
  onTimeScopeChange?: (scope: WatcherTimeScope) => void;
  /** 点击 Crash 明细中的设备串号时回调，供 PlanRunDetailPage 直接打开单机详情抽屉。 */
  onSelectDeviceSerial?: (serial: string) => void;
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

/** #1956：展锐（UNIVIEW）与 AEE 家族会出现同名 subtype（如 ANR），标签带来源前缀以区分。 */
function subtypeLabel(item: Pick<SubtypeDistribution, 'subtype' | 'group'>): string {
  if (item.group === 'UNIVIEW') return `UNIVIEW · ${item.subtype}`;
  if (item.subtype === '其他' && item.group === 'VENDOR_AEE') return 'Vendor 其他';
  return item.subtype;
}

function subtypeColor(item: Pick<SubtypeDistribution, 'subtype' | 'group'>): string {
  // 色板键必须用原始 subtype：标签可能带 UNIVIEW 前缀，拿去查色板会落回「其他」灰。
  const paletteKey =
    item.subtype === '其他' && item.group === 'VENDOR_AEE' ? 'Vendor 其他' : item.subtype;
  return aeeSubtypeChartColor(paletteKey);
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

function packageDominantColor(row: PackageRanking): string {
  if (!row.subtype_breakdown || row.subtype_breakdown.length === 0) {
    return aeeSubtypeChartColor('其他');
  }
  const dominant = row.subtype_breakdown.reduce((a, b) =>
    b.count > a.count ? b : a,
  );
  return aeeSubtypeChartColor(dominant.subtype);
}

function PackageSubtypeDots({ row, active }: { row: PackageRanking; active: boolean }) {
  const items = row.subtype_breakdown.slice(0, 3);
  if (items.length === 0) {
    return <span className={cn('text-xs', TEXT.subtitle)}>无细分类型数据</span>;
  }
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {items.map((item) => (
        <span
          key={`${item.group ?? 'AEE'}-${item.subtype}`}
          className="inline-flex items-center gap-1"
        >
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: aeeSubtypeChartColor(item.subtype) }}
          />
          <span className={cn('text-xs', active ? 'text-muted-foreground/70' : TEXT.subtitle)}>
            {item.group === 'UNIVIEW' ? `UNIVIEW · ${item.subtype}` : item.subtype} {item.count}
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
  wrap = false,
}: {
  label: string;
  value: string;
  accent: string;
  /** 长包名等允许换行，避免半宽栅格里截断成「不全」 */
  wrap?: boolean;
}) {
  return (
    <div className={DASHBOARD_SUMMARY_CARD.root}>
      <div className={DASHBOARD_SUMMARY_CARD.label}>{label}</div>
      <div
        className={cn(
          'mt-2 text-lg font-bold',
          accent,
          wrap ? 'break-all text-base leading-snug' : 'truncate',
        )}
        title={value}
      >
        {value}
      </div>
    </div>
  );
}

function filterCrashDetailsByOrigin(
  details: CrashDetailEntry[] | undefined,
  supportsOriginSplit: boolean,
  origin: OriginTab,
): CrashDetailEntry[] {
  if (!Array.isArray(details)) return [];
  if (!supportsOriginSplit) return details;
  const expected = origin === 'current' ? 'runtime' : 'baseline';
  return details.filter((item) => !item.entry_origin || item.entry_origin === expected);
}

function formatCrashDetectedAt(value: string | null | undefined): string {
  if (!value) return '—';
  return formatDateTimeShort(value) || value;
}

function PackageRankingDrawer({
  open,
  onClose,
  runId,
  scopeLabel,
  timeScope,
  origin,
  supportsOriginSplit,
  rankings,
  detailPackageName,
  onSelectDetailPackage,
  onSelectDeviceSerial,
}: {
  open: boolean;
  onClose: () => void;
  runId: number;
  scopeLabel: string;
  timeScope: WatcherTimeScope;
  origin: OriginTab;
  supportsOriginSplit: boolean;
  rankings: PackageRanking[];
  detailPackageName: string | null;
  onSelectDetailPackage: (pkg: string | null) => void;
  onSelectDeviceSerial?: (serial: string) => void;
}) {
  const [drawerPage, setDrawerPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [searchQuery, setSearchQuery] = useState('');
  const activeRowRef = useRef<HTMLDivElement | null>(null);

  const [prevOpen, setPrevOpen] = useState(open);
  if (prevOpen !== open) {
    setPrevOpen(open);
    if (open) {
      setSearchQuery('');
      const targetIdx = detailPackageName
        ? rankings.findIndex((r) => r.package_name === detailPackageName)
        : -1;
      setDrawerPage(targetIdx >= 0 ? Math.floor(targetIdx / pageSize) + 1 : 1);
    }
  }

  useEffect(() => {
    if (!open || !detailPackageName) return;
    const el = activeRowRef.current;
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [open, detailPackageName]);

  const normalizedQuery = searchQuery.trim().toLowerCase();
  const filteredRankings = useMemo(() => {
    const indexed = rankings.map((row, idx) => ({ row, rank: idx + 1 }));
    if (!normalizedQuery) return indexed;
    return indexed.filter(({ row }) => {
      const displayName = row.package_name === 'unknown' ? '未知进程 unknown' : row.package_name;
      return displayName.toLowerCase().includes(normalizedQuery);
    });
  }, [rankings, normalizedQuery]);

  const totalPages = Math.max(1, Math.ceil(filteredRankings.length / pageSize));
  const safePage = Math.min(drawerPage, totalPages);
  const visibleRows = filteredRankings.slice((safePage - 1) * pageSize, safePage * pageSize);

  const crashDetailsQ = useQuery({
    queryKey: ['crash-details', runId, detailPackageName, timeScope],
    queryFn: () => api.planRuns.getCrashDetails(runId, detailPackageName || undefined, timeScope),
    enabled: open && Boolean(detailPackageName),
    staleTime: 30_000,
  });

  const scopedCrashDetails = useMemo(
    () => filterCrashDetailsByOrigin(crashDetailsQ.data, supportsOriginSplit, origin),
    [crashDetailsQ.data, supportsOriginSplit, origin],
  );

  const timeScopeText = TIME_SCOPE_OPTIONS.find((o) => o.value === timeScope)?.label ?? '全量';

  return (
    <Sheet
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose();
      }}
    >
      <SheetContent
        side="right"
        size="lg"
        hideCloseButton
        aria-label="包名榜完整列表"
      >
        <header className="flex items-center justify-between border-b px-5 py-3.5">
          <div className="min-w-0">
            <SheetDescription className={cn('truncate text-xs', TEXT.subtitle)}>
              {scopeLabel} · 时间范围：{timeScopeText}
            </SheetDescription>
            <SheetTitle className={cn('truncate text-base font-semibold', TEXT.heading)}>
              包名榜 · 全部 ({rankings.length})
            </SheetTitle>
          </div>
          <button type="button" onClick={onClose} className={DRAWER.closeBtn} aria-label="关闭包名榜">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="border-b bg-muted/20 px-5 py-2.5">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setDrawerPage(1);
              }}
              placeholder="搜索包名或未知进程..."
              aria-label="搜索包名"
              className={cn(FORM.inputSm, 'h-8 pl-8 pr-8')}
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => {
                  setSearchQuery('');
                  setDrawerPage(1);
                }}
                aria-label="清空包名搜索"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <p className={cn('mt-1.5 text-[11px]', TEXT.subtitle)}>
            点击任意包名在当前行原地展开受影响设备与 Crash 事件明细；支持一键跳转单机详情或日志。
          </p>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-3.5">
          {visibleRows.length === 0 ? (
            <InlineEmpty className="py-8">未找到匹配「{searchQuery}」的异常包名</InlineEmpty>
          ) : (
            <div className="space-y-2">
              {visibleRows.map(({ row, rank }) => {
                const isDetailActive = detailPackageName === row.package_name;
                const isUnknown = row.package_name === 'unknown';
                const dominantColor = isUnknown
                  ? CHART_COLORS.muted
                  : packageDominantColor(row);
                const rankCls = packageRankClass(rank - 1);

                return (
                  <div
                    key={row.package_name}
                    ref={isDetailActive ? activeRowRef : undefined}
                    data-testid={`drawer-row-${row.package_name}`}
                    className={cn(
                      'group overflow-hidden rounded-xl border transition-all duration-200',
                      isDetailActive
                        ? 'border-info/40 bg-card shadow-sm'
                        : isUnknown
                          ? PACKAGE_ROW.unknown
                          : PACKAGE_ROW.default,
                    )}
                  >
                    <div className="flex w-full items-stretch">
                      <div
                        className={cn(
                          'shrink-0 w-1 transition-all duration-200',
                          isDetailActive ? 'w-1.5' : 'group-hover:w-1.5',
                        )}
                        style={{ backgroundColor: dominantColor }}
                      />
                      <button
                        type="button"
                        data-testid={`drawer-pkg-${row.package_name}`}
                        aria-expanded={isDetailActive}
                        aria-pressed={isDetailActive}
                        onClick={() => {
                          onSelectDetailPackage(isDetailActive ? null : row.package_name);
                        }}
                        className="flex min-w-0 flex-1 items-start justify-between gap-3 px-3 py-2 text-left"
                      >
                        <div className="min-w-0 flex-1">
                          <div className="flex items-start gap-2">
                            <span className={cn('shrink-0 font-mono tabular-nums', rankCls)}>
                              #{rank}
                            </span>
                            <span
                              className={cn(
                                'break-all text-sm leading-snug',
                                isUnknown
                                  ? cn('italic', TEXT.subtitle)
                                  : isDetailActive
                                    ? cn('font-semibold', TEXT.heading)
                                    : cn('font-medium', TEXT.body),
                              )}
                            >
                              {isUnknown ? '未知进程' : row.package_name}
                            </span>
                          </div>
                          <div className="mt-1">
                            <PackageSubtypeDots row={row} active={isDetailActive} />
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <div className="text-right">
                            <div
                              className={cn(
                                'text-base font-bold tabular-nums transition-colors duration-200',
                                isDetailActive
                                  ? TEXT.heading
                                  : isUnknown
                                    ? TEXT.subtitle
                                    : TEXT.body,
                              )}
                            >
                              {row.total_count}
                            </div>
                            <div className={cn('text-[11px] tabular-nums', isDetailActive ? TEXT.subtitle : 'text-muted-foreground/70')}>
                              {row.affected_device_count} 台设备
                            </div>
                          </div>
                          <ChevronDown
                            className={cn(
                              'h-4 w-4 text-muted-foreground/60 transition-transform duration-200',
                              isDetailActive && 'rotate-180 text-info',
                            )}
                          />
                        </div>
                      </button>
                    </div>

                    {isDetailActive && (
                      <CrashDetailPanel
                        runId={runId}
                        packageName={row.package_name}
                        rankingRow={row}
                        details={scopedCrashDetails}
                        isLoading={crashDetailsQ.isLoading}
                        isError={crashDetailsQ.isError}
                        onRetry={() => void crashDetailsQ.refetch()}
                        onSelectDeviceSerial={(serial) => {
                          if (onSelectDeviceSerial) {
                            onClose();
                            onSelectDeviceSerial(serial);
                          }
                        }}
                        onClose={() => onSelectDetailPackage(null)}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {filteredRankings.length > pageSize && (
          <footer className="border-t bg-card px-5 py-2.5">
            <PaginationBar
              page={safePage}
              totalPages={totalPages}
              total={filteredRankings.length}
              pageSize={pageSize}
              canPreviousPage={safePage > 1}
              canNextPage={safePage < totalPages}
              onGoToPage={setDrawerPage}
              onNextPage={() => setDrawerPage((p) => Math.min(totalPages, p + 1))}
              onPrevPage={() => setDrawerPage((p) => Math.max(1, p - 1))}
              onChangePageSize={(size) => {
                setPageSize(size);
                setDrawerPage(1);
              }}
              pageSizeOptions={[10, 20, 50]}
            />
          </footer>
        )}
      </SheetContent>
    </Sheet>
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
      <InlineEmpty chart testId={chartTestId}>
        当前范围内暂无细分类型数据
      </InlineEmpty>
    );
  }

  const renderActiveShape = (props: PieSectorDataItem) => {
    const {
      cx = 0, cy = 0, innerRadius = 0, outerRadius = 0,
      startAngle = 0, endAngle = 0, fill = '',
    } = props;
    return (
      <Sector
        cx={cx}
        cy={cy}
        innerRadius={innerRadius}
        outerRadius={outerRadius + 6}
        startAngle={startAngle}
        endAngle={endAngle}
        fill={fill}
        cornerRadius={3}
      />
    );
  };

  const activeItem = activeIndex != null ? chartData[activeIndex] ?? null : null;

  return (
    <div
      data-testid={chartTestId}
      data-chart-type="recharts-donut"
      aria-label="异常细分类型占比饼图"
      className="grid w-full grid-cols-1 items-center gap-6 sm:grid-cols-[216px_minmax(0,1fr)]"
    >
      {/* 左侧紧凑环形图 + 动态圆心数值 */}
      <div className="relative mx-auto h-[216px] w-[216px] shrink-0">
        <StableResponsiveContainer className="h-[216px] min-h-[216px] w-[216px]">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Tooltip
                wrapperStyle={{ outline: 'none', zIndex: 50 }}
                content={({ active, payload }) => {
                  if (!active || !payload || payload.length === 0) return null;
                  const item = payload[0].payload as (typeof chartData)[number];
                  return (
                    <div className="rounded-xl border bg-card/95 px-3 py-2 shadow-lg backdrop-blur">
                      <div className={cn('flex items-center gap-2 text-xs font-semibold', TEXT.body)}>
                        <span
                          className="h-2.5 w-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: item.color }}
                        />
                        <span>{item.fullLabel}</span>
                      </div>
                      <div className={cn('mt-1 text-xs tabular-nums', TEXT.subtitle)}>
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
                innerRadius={64}
                outerRadius={92}
                startAngle={90}
                endAngle={-270}
                paddingAngle={chartData.length > 1 ? 2 : 0}
                dataKey="count"
                stroke="hsl(var(--card))"
                strokeWidth={1.5}
                cornerRadius={3}
                animationBegin={0}
                animationDuration={450}
                animationEasing="ease-out"
                isAnimationActive
                activeShape={renderActiveShape}
                onMouseEnter={(_data, index) => setActiveIndex(index)}
                onMouseLeave={() => setActiveIndex(null)}
                label={false}
              >
                {chartData.map((item, index) => (
                  <Cell
                    key={item.key}
                    fill={item.color}
                    fillOpacity={activeIndex === null || activeIndex === index ? 1 : 0.35}
                    style={{ transition: 'fill-opacity 150ms ease' }}
                  />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </StableResponsiveContainer>
        {/* 圆心：默认显示总计，悬浮某类型时平滑展示当前项 */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-4 text-center">
          {activeItem ? (
            <>
              <div
                className="max-w-[96px] truncate text-[11px] font-medium text-muted-foreground"
                title={activeItem.fullLabel}
              >
                {activeItem.fullLabel}
              </div>
              <div
                data-center-total="true"
                className="mt-0.5 text-[24px] font-bold leading-none tabular-nums"
                style={{ color: activeItem.color }}
              >
                {activeItem.count}
              </div>
              <div className="mt-1 text-[11px] font-medium tabular-nums text-muted-foreground">
                {formatSharePercent(activeItem.share)}%
              </div>
            </>
          ) : (
            <>
              <div className="text-[11px] tracking-[0.12em] text-muted-foreground">
                异常总数
              </div>
              <div
                data-center-total="true"
                className={`mt-1 text-[26px] font-bold leading-none tabular-nums ${tone}`}
              >
                {total}
              </div>
            </>
          )}
        </div>
      </div>

      {/* 右侧结构化明细排行表（色点 + 类型 + 次数 + 百分比 + 迷你占比条） */}
      <div
        data-testid={`${chartTestId}-legend`}
        data-legend-position="right"
        className="min-w-0 space-y-1.5"
      >
        {chartData.map((item, index) => {
          const isActive = index === activeIndex;
          const pct = Math.max(0, Math.min(100, item.share * 100));
          return (
            <div
              key={`legend-${item.key}`}
              onMouseEnter={() => setActiveIndex(index)}
              onMouseLeave={() => setActiveIndex(null)}
              className={cn(
                'cursor-default rounded-lg border px-2.5 py-1.5 transition-colors',
                isActive
                  ? 'border-border bg-muted/60'
                  : 'border-transparent hover:border-border/60 hover:bg-muted/35',
              )}
            >
              <div className="flex items-center justify-between gap-2 text-xs">
                <div className="flex min-w-0 items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: item.color }}
                  />
                  <span
                    className={cn(
                      'truncate font-medium',
                      isActive ? TEXT.heading : TEXT.body,
                    )}
                    title={item.fullLabel}
                  >
                    {item.fullLabel}
                  </span>
                </div>
                <div className="flex shrink-0 items-baseline gap-2 tabular-nums">
                  <span className={cn('font-semibold', TEXT.heading)}>
                    {item.count}
                  </span>
                  <span className={cn('w-11 text-right text-[11px]', TEXT.subtitle)}>
                    {formatSharePercent(item.share)}%
                  </span>
                </div>
              </div>
              <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-muted/70">
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${Math.max(pct, pct > 0 ? 3 : 0)}%`,
                    backgroundColor: item.color,
                    opacity: activeIndex === null || isActive ? 1 : 0.4,
                  }}
                />
              </div>
            </div>
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
  onSelectDeviceSerial,
}: Props) {
  const [isPackageDrawerOpen, setPackageDrawerOpen] = useState(false);
  const [currentDetailPackage, setCurrentDetailPackage] = useState<string | null>(null);
  const [isPreexistingDrawerOpen, setPreexistingDrawerOpen] = useState(false);
  const [preexistingDetailPackage, setPreexistingDetailPackage] = useState<string | null>(null);
  /** 新计划主看本次新增；遗留降为次级 Tab，默认不占首屏 */
  const [originTab, setOriginTab] = useState<OriginTab>('current');

  const supportsOriginSplit = data?.supports_origin_split ?? false;
  const currentRun = data?.current_run ?? EMPTY_SECTION;
  const preexisting = data?.preexisting ?? EMPTY_SECTION;
  const platformBuckets = data?.platform_buckets ?? [];
  const primaryLabel = supportsOriginSplit ? '本次新增' : '当前范围';

  const openCurrentPackageDetail = (pkg: string) => {
    setCurrentDetailPackage(pkg);
    setPackageDrawerOpen(true);
  };

  const openPreexistingPackageDetail = (pkg: string) => {
    setPreexistingDetailPackage(pkg);
    setPreexistingDrawerOpen(true);
  };

  const chartDistribution = collapseDistribution(currentRun.subtype_distribution);
  const chartTotal = chartDistribution.reduce((sum, item) => sum + item.count, 0);
  const preexistingDistribution = collapseDistribution(preexisting.subtype_distribution);
  const preexistingTotal = preexistingDistribution.reduce((sum, item) => sum + item.count, 0);

  return (
    <>
      <section
        data-testid="watcher-summary"
        className={cn(PANEL.root, 'space-y-3 p-3')}
      >
        {/* OpenRouter Activity 式页头：标题 + 副文 | 右侧时间窗 */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h2 className={cn('truncate text-xl font-semibold leading-9', TEXT.heading)}>
              异常仪表盘
            </h2>
            <p className={cn('mt-1 text-sm', TEXT.subtitle)}>
              聚焦 AEE / Vendor AEE / UNIVIEW 细分异常与高风险包名
            </p>
          </div>
          <div
            className="flex h-9 w-fit shrink-0 items-center overflow-hidden rounded-md border border-border"
            role="group"
            aria-label="时间范围"
          >
            {TIME_SCOPE_OPTIONS.map((option) => {
              const active = timeScope === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => onTimeScopeChange?.(option.value)}
                  className={cn(
                    'h-full px-2.5 text-xs font-medium transition-colors',
                    active
                      ? 'bg-muted text-foreground'
                      : cn(TEXT.subtitle, 'hover:bg-muted/60 hover:text-foreground'),
                  )}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>

      {isLoading && (
        <div className={cn('flex h-28 items-center justify-center text-sm', TEXT.subtitle)}>加载中…</div>
      )}

      {isError && (
        <div className={cn('flex h-28 items-center justify-center gap-2 rounded-lg text-sm', ALERT_BANNER.destructive)}>
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>异常数据加载失败，请稍后重试</span>
        </div>
      )}

      {!isLoading && !isError && (
        <div className="space-y-3">
          {/* watcher_capability = 本次运行各 Job 中最降级的一档（后端聚合）。unavailable =
              探测全失败、补采也可能未运行：此时下面的「0」可能是没采到而不是没崩溃。
              该提示曾在 WatcherSummaryCard 里，随 08-21 死组件清理（a58e45e3）丢失。 */}
          {data?.watcher_capability === 'unavailable' && (
            <div
              role="status"
              data-testid="watcher-capability-degraded"
              title="watcher_capability=unavailable（取本次运行各 Job 中最降级的一档）"
              className={cn('flex items-start gap-2 rounded-lg px-3 py-2 text-xs', ALERT_BANNER.warning)}
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                <strong className="font-semibold">异常采集降级：</strong>
                {'本次运行中有设备的 Watcher 未能正常启动，后台补采也可能没有运行。'
                  + '对这些设备来说，「无异常」不等于没有崩溃，请以已上报的信号为准。'}
              </span>
            </div>
          )}

          {supportsOriginSplit && (
            <StateTabs
              variant="underline"
              ariaLabel="异常来源"
              testId="anomaly-origin-tabs"
              activeKey={originTab}
              onChange={(key) => setOriginTab(key as OriginTab)}
              items={[
                {
                  key: 'current',
                  label: '本次新增',
                  panelId: 'anomaly-panel-current',
                  testId: 'anomaly-tab-current',
                },
                {
                  key: 'preexisting',
                  label:
                    preexisting.total_events > 0
                      ? `运行前遗留 (${preexisting.total_events})`
                      : '运行前遗留',
                  panelId: 'anomaly-panel-preexisting',
                  testId: 'anomaly-tab-preexisting',
                },
              ]}
            />
          )}

          {(!supportsOriginSplit || originTab === 'current') && (
            <div
              role={supportsOriginSplit ? 'tabpanel' : undefined}
              id={supportsOriginSplit ? 'anomaly-panel-current' : undefined}
              className="space-y-3"
            >
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
                  accent={currentRun.top_package_name ? KPI_TONE.warning.value : KPI_TONE.default.value}
                  wrap
                />
                <SummaryCard
                  label="Top 类型"
                  value={formatCompactValue(currentRun.top_subtype)}
                  accent={currentRun.top_subtype ? KPI_TONE.destructive.value : KPI_TONE.default.value}
                />
              </div>

              {platformBuckets.length > 0 && (
                <div data-testid="watcher-platform-buckets" className="rounded-lg border border-border/60 bg-card/60 p-3">
                  <div className={cn('mb-2 text-sm font-medium', TEXT.heading)}>按平台分桶</div>
                  <div className="flex flex-wrap gap-2">
                    {platformBuckets.map((bucket) => {
                      // R4-b b1（ADR-0032 v0.9 裁决 2026-09-15）：无采集实现的平台
                      // 不能显示成「信号 0 · 设备 0」——那是「没有异常」的语义。
                      const unsupported = bucket.reconciler_supported === false;
                      return (
                        <div key={bucket.platform} className="rounded-lg border border-border/50 bg-background/80 px-3 py-2 text-sm">
                          <span className="font-medium">{bucket.platform}</span>
                          <span className={cn('ml-2', unsupported ? TEXT.destructive : TEXT.subtitle)}>
                            {unsupported
                              ? '平台未支持'
                              : `信号 ${bucket.total}${
                                  bucket.affected_device_count > 0
                                    ? ` · 异常设备 ${bucket.affected_device_count}`
                                    : (bucket.running_device_count ?? 0) > 0
                                      ? ` · 运行中 ${bucket.running_device_count}`
                                      : (bucket.participating_device_count ?? 0) > 0
                                        ? ` · 参与 ${bucket.participating_device_count}`
                                        : ' · 设备 0'
                                }`}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
                <div
                  data-testid="current-run-pie-panel"
                  className={cn(DASHBOARD_SUMMARY_CARD.panel, 'flex flex-col')}
                >
                  <div className={cn('mb-3 text-sm font-semibold', TEXT.heading)}>
                    {`${primaryLabel} · 细分类型占比`}
                  </div>
                  <div className="flex flex-1 items-center justify-center py-2">
                    {currentRun.total_events > 0 ? (
                      <DonutChart
                        items={chartDistribution}
                        total={chartTotal}
                        tone={KPI_TONE.default.value}
                        chartTestId="current-run-pie-chart"
                      />
                    ) : (
                      <InlineEmpty chart className="w-full">
                        {supportsOriginSplit
                          ? '当前范围内未发现新增 AEE / Vendor AEE / UNIVIEW 异常'
                          : '当前范围内未发现 AEE / Vendor AEE / UNIVIEW 异常'}
                      </InlineEmpty>
                    )}
                  </div>
                </div>

                <div className={DASHBOARD_SUMMARY_CARD.panel}>
                  <div className={cn('mb-1 text-sm font-semibold', TEXT.heading)}>
                    {`${primaryLabel} · 包名榜`}
                  </div>
                  <div className={cn('mb-3 text-[11px]', TEXT.subtitle)}>
                    点击任意包名查看受影响设备与 Crash 明细
                  </div>
                  {currentRun.package_ranking.length > 0 ? (
                    <div className="space-y-2">
                      {currentRun.package_ranking.slice(0, 5).map((row, index) => {
                        const isUnknown = row.package_name === 'unknown';
                        const dominantColor = isUnknown
                          ? CHART_COLORS.muted
                          : packageDominantColor(row);
                        const rankCls = packageRankClass(index);

                        return (
                          <button
                            key={row.package_name}
                            type="button"
                            data-testid={`crash-detail-btn-${row.package_name}`}
                            onClick={() => openCurrentPackageDetail(row.package_name)}
                            className={cn(
                              'group flex w-full items-stretch rounded-xl border text-left transition-all duration-200',
                              isUnknown ? PACKAGE_ROW.unknown : PACKAGE_ROW.default,
                            )}
                          >
                            <div
                              className="w-1 shrink-0 rounded-l-xl transition-all duration-200 group-hover:w-1.5"
                              style={{ backgroundColor: dominantColor }}
                            />
                            <div className="flex min-w-0 flex-1 items-start justify-between gap-3 px-3 py-2">
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                  <span className={cn('font-mono tabular-nums', rankCls)}>
                                    #{index + 1}
                                  </span>
                                  <span
                                    className={cn(
                                      'truncate text-sm',
                                      isUnknown
                                        ? cn('italic', TEXT.subtitle)
                                        : cn('font-medium', TEXT.body),
                                    )}
                                  >
                                    {isUnknown ? '未知进程' : row.package_name}
                                  </span>
                                </div>
                                <div className="mt-1">
                                  <PackageSubtypeDots row={row} active={false} />
                                </div>
                              </div>
                              <div className="flex shrink-0 items-center gap-2">
                                <div className="text-right">
                                  <div
                                    className={cn(
                                      'text-lg font-bold tabular-nums transition-colors duration-200',
                                      isUnknown ? TEXT.subtitle : TEXT.body,
                                    )}
                                  >
                                    {row.total_count}
                                  </div>
                                  <div className="text-[11px] tabular-nums text-muted-foreground/70">
                                    {row.affected_device_count} 台设备
                                  </div>
                                </div>
                                <ChevronRight className="h-4 w-4 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-info" />
                              </div>
                            </div>
                          </button>
                        );
                      })}

                      {currentRun.package_ranking.length > 5 && (
                        <button
                          type="button"
                          onClick={() => {
                            setCurrentDetailPackage(null);
                            setPackageDrawerOpen(true);
                          }}
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
                    <InlineEmpty chart>当前范围内暂无异常包名数据</InlineEmpty>
                  )}
                </div>
              </div>

              {!supportsOriginSplit && (
                <div className={cn('rounded-lg border border-dashed bg-card/70 px-4 py-3 text-sm', TEXT.subtitle)}>
                  该计划运行未记录新增/遗留来源标记，无法拆分运行前遗留
                </div>
              )}
            </div>
          )}

          {supportsOriginSplit && originTab === 'preexisting' && (
            <div
              role="tabpanel"
              id="anomaly-panel-preexisting"
              className="space-y-3"
            >
              {preexisting.total_events > 0 ? (
                <>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <SummaryCard
                      label="遗留总量"
                      value={String(preexisting.total_events)}
                      accent={KPI_TONE.default.value}
                    />
                    <SummaryCard
                      label="Top 包名"
                      value={formatCompactValue(preexisting.top_package_name)}
                      accent={KPI_TONE.default.value}
                      wrap
                    />
                    <SummaryCard
                      label="Top 类型"
                      value={formatCompactValue(preexisting.top_subtype)}
                      accent={KPI_TONE.default.value}
                    />
                  </div>

                  <div className="grid gap-3 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
                    <div
                      data-testid="preexisting-pie-panel"
                      className={cn(DASHBOARD_SUMMARY_CARD.panel, 'flex flex-col')}
                    >
                      <div className={cn('mb-3 text-sm font-semibold', TEXT.heading)}>
                        运行前遗留 · 细分类型占比
                      </div>
                      <div className="flex flex-1 items-center justify-center py-2">
                        <DonutChart
                          items={preexistingDistribution}
                          total={preexistingTotal}
                          tone={KPI_TONE.default.value}
                          chartTestId="preexisting-pie-chart"
                        />
                      </div>
                    </div>

                    <div className={DASHBOARD_SUMMARY_CARD.panel}>
                      <div className={cn('mb-1 text-sm font-semibold', TEXT.heading)}>
                        运行前遗留 · 包名榜
                      </div>
                      <div className={cn('mb-3 text-[11px]', TEXT.subtitle)}>
                        点击任意包名查看受影响设备与遗留 Crash 明细
                      </div>
                      {preexisting.package_ranking.length > 0 ? (
                        <div className="space-y-2">
                          {preexisting.package_ranking.slice(0, 5).map((row, index) => {
                            const isUnknown = row.package_name === 'unknown';
                            const dominantColor = isUnknown
                              ? CHART_COLORS.muted
                              : packageDominantColor(row);
                            const rankCls = packageRankClass(index);

                            return (
                              <button
                                key={row.package_name}
                                type="button"
                                data-testid={`preexisting-pkg-${row.package_name}`}
                                onClick={() => openPreexistingPackageDetail(row.package_name)}
                                className={cn(
                                  'group flex w-full items-stretch rounded-lg border text-left transition-all duration-200',
                                  isUnknown ? PACKAGE_ROW.unknown : PACKAGE_ROW.default,
                                )}
                              >
                                <div
                                  className="w-1 shrink-0 rounded-l-lg transition-all duration-200 group-hover:w-1.5"
                                  style={{ backgroundColor: dominantColor }}
                                />
                                <div className="flex min-w-0 flex-1 items-start justify-between gap-3 px-3 py-2">
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-start gap-2">
                                      <span className={cn('shrink-0 font-mono tabular-nums', rankCls)}>
                                        #{index + 1}
                                      </span>
                                      <span
                                        className={cn(
                                          'break-all text-sm leading-snug',
                                          isUnknown
                                            ? cn('italic', TEXT.subtitle)
                                            : cn('font-medium', TEXT.body),
                                        )}
                                        title={isUnknown ? '未知进程' : row.package_name}
                                      >
                                        {isUnknown ? '未知进程' : row.package_name}
                                      </span>
                                    </div>
                                    <div className="mt-1">
                                      <PackageSubtypeDots row={row} active={false} />
                                    </div>
                                  </div>
                                  <div className="flex shrink-0 items-center gap-2">
                                    <div className="text-right">
                                      <div
                                        className={cn(
                                          'text-lg font-bold tabular-nums transition-colors duration-200',
                                          isUnknown ? TEXT.subtitle : TEXT.body,
                                        )}
                                      >
                                        {row.total_count}
                                      </div>
                                      <div className="text-[11px] tabular-nums text-muted-foreground/70">
                                        {row.affected_device_count} 台设备
                                      </div>
                                    </div>
                                    <ChevronRight className="h-4 w-4 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-info" />
                                  </div>
                                </div>
                              </button>
                            );
                          })}
                          {preexisting.package_ranking.length > 5 && (
                            <button
                              type="button"
                              onClick={() => {
                                setPreexistingDetailPackage(null);
                                setPreexistingDrawerOpen(true);
                              }}
                              className={cn(
                                'w-full rounded-lg border border-dashed py-2 text-xs font-medium transition',
                                TEXT.subtitle,
                                'hover:border-border hover:text-foreground',
                              )}
                            >
                              查看全部 ({preexisting.package_ranking.length})
                            </button>
                          )}
                        </div>
                      ) : (
                        <InlineEmpty chart>运行前遗留无包名数据</InlineEmpty>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <InlineEmpty className="py-6">运行开始前无遗留异常记录</InlineEmpty>
              )}
            </div>
          )}
        </div>
      )}
    </section>

      <PackageRankingDrawer
        open={isPackageDrawerOpen}
        onClose={() => setPackageDrawerOpen(false)}
        runId={runId}
        scopeLabel={primaryLabel}
        timeScope={timeScope}
        origin="current"
        supportsOriginSplit={supportsOriginSplit}
        rankings={currentRun.package_ranking}
        detailPackageName={currentDetailPackage}
        onSelectDetailPackage={setCurrentDetailPackage}
        onSelectDeviceSerial={onSelectDeviceSerial}
      />
      <PackageRankingDrawer
        open={isPreexistingDrawerOpen}
        onClose={() => setPreexistingDrawerOpen(false)}
        runId={runId}
        scopeLabel="运行前遗留"
        timeScope={timeScope}
        origin="preexisting"
        supportsOriginSplit={supportsOriginSplit}
        rankings={preexisting.package_ranking}
        detailPackageName={preexistingDetailPackage}
        onSelectDetailPackage={setPreexistingDetailPackage}
        onSelectDeviceSerial={onSelectDeviceSerial}
      />
    </>
  );
}

function CrashDetailPanel({
  runId,
  packageName,
  rankingRow,
  details,
  isLoading,
  isError,
  onRetry,
  onSelectDeviceSerial,
  onClose,
}: {
  runId: number;
  packageName: string;
  rankingRow: PackageRanking | null;
  details: CrashDetailEntry[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  onSelectDeviceSerial?: (serial: string) => void;
  onClose: () => void;
}) {
  const deviceSummary = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of details) {
      const serial = item.device_serial || '未知设备';
      counts.set(serial, (counts.get(serial) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [details]);

  return (
    <div
      data-testid="crash-detail-panel"
      className="border-t border-info/25 bg-info/5 px-3.5 py-3"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className={cn('text-[11px] font-medium tabular-nums', TEXT.subtitle)}>
            Crash 详情 · 命中 {details.length} 条记录
            {deviceSummary.length > 0 ? ` · ${deviceSummary.length} 台设备` : ''}
            {rankingRow && rankingRow.total_count !== details.length && !isLoading && !isError
              ? `（榜计 ${rankingRow.total_count}）`
              : ''}
            <span className="sr-only">
              {packageName === 'unknown' ? '未知进程' : packageName}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className={cn(DRAWER.closeBtn, 'h-6 w-6')}
          aria-label="收起 Crash 详情"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {isLoading ? (
        <div className={cn('py-4 text-center text-xs', TEXT.subtitle)}>加载 Crash 详情中…</div>
      ) : isError ? (
        <div className="mt-2.5 flex items-center justify-between gap-2 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive-text">
          <span>加载 Crash 详情失败，请稍后重试</span>
          <Button type="button" variant="outline" size="sm" className="h-7 px-2 text-[11px]" onClick={onRetry}>
            重试
          </Button>
        </div>
      ) : details.length === 0 ? (
        <div className={cn('py-4 text-center text-xs', TEXT.subtitle)}>当前时间与来源范围内暂无 Crash 明细</div>
      ) : (
        <div className="mt-3 space-y-3">
          {deviceSummary.length > 0 && (
            <div data-testid="crash-detail-devices">
              <div className={cn('mb-1.5 flex items-center gap-1 text-[11px] font-medium', TEXT.subtitle)}>
                <Smartphone className="h-3 w-3" />
                <span>受影响设备（点击查看单机详情）</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {deviceSummary.map(([serial, count]) => (
                  <button
                    key={serial}
                    type="button"
                    data-testid={`crash-device-chip-${serial}`}
                    onClick={() => onSelectDeviceSerial?.(serial)}
                    className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 font-mono text-[11px] text-foreground transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-primary"
                  >
                    <span>{serial}</span>
                    <span className="rounded bg-muted px-1 text-[10px] tabular-nums text-muted-foreground">
                      ×{count}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="max-h-60 space-y-1.5 overflow-y-auto pr-0.5">
            {details.map((d, i) => {
              const color = aeeSubtypeChartColor(d.subtype || '其他');
              const label = d.group === 'UNIVIEW' ? `UNIVIEW · ${d.subtype}` : d.subtype;
              const logsHref = `/execution/plan-runs/${runId}/logs?q=${encodeURIComponent(d.device_serial)}`;
              return (
                <div
                  key={`${d.device_serial}-${d.subtype}-${d.detected_at ?? i}-${i}`}
                  className="flex items-center justify-between gap-2 rounded-lg border bg-card px-2.5 py-1.5 text-xs"
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                    <span className={cn('shrink-0 font-mono text-[11px] font-medium', TEXT.body)}>
                      {label}
                    </span>
                    <button
                      type="button"
                      onClick={() => onSelectDeviceSerial?.(d.device_serial)}
                      className="truncate font-mono text-[11px] text-primary hover:underline"
                      title={`查看设备 ${d.device_serial} 详情`}
                    >
                      {d.device_serial}
                    </button>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
                      {formatCrashDetectedAt(d.detected_at)}
                    </span>
                    <a
                      href={logsHref}
                      className="inline-flex items-center gap-0.5 text-[11px] text-muted-foreground hover:text-primary"
                      title={`在执行日志中检索 ${d.device_serial}`}
                    >
                      日志
                      <ExternalLink className="h-2.5 w-2.5" />
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
