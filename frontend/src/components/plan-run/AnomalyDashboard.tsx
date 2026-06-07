import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Cell, Pie, PieChart, ResponsiveContainer, Sector, Tooltip } from 'recharts';
import type {
  AeeDashboardSection,
  PackageRanking,
  PackageSubtypeCount,
  SubtypeDistribution,
  WatcherSummary,
  WatcherTimeScope,
} from '@/utils/api/types';
import { StableResponsiveContainer } from '@/components/charts/StableResponsiveContainer';
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
        className="flex h-32 items-center justify-center text-sm text-slate-400"
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
                    <div className="rounded-xl border border-slate-200 bg-white/95 px-3 py-2 shadow-lg backdrop-blur">
                      <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                        <span
                          className="h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: item.color }}
                        />
                        <span>{item.fullLabel}</span>
                      </div>
                      <div className="mt-1 text-xs text-slate-500">
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
                stroke="#f8fafc"
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
          <div className="text-[11px] uppercase tracking-[0.16em] text-slate-400">
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
              className={`inline-flex items-center gap-1 text-xs cursor-pointer transition rounded px-1.5 py-0.5 ${
                isActive
                  ? 'bg-slate-100 text-slate-900'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
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
                <DonutChart
                  items={chartDistribution}
                  total={chartTotal}
                  tone="text-slate-900"
                  chartTestId="current-run-pie-chart"
                />
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
                    chartTestId="preexisting-pie-chart"
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
