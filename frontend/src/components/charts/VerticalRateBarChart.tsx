import { useMemo, type ReactNode } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell, LabelList } from 'recharts';
import type { RenderableText } from 'recharts/types/component/Text';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

/** Row shape after `VerticalRateBarChart` derives display fields from the raw item. */
export type RateBarRow<T> = T & { label: string; rate: number; ratePct: number };

interface VerticalRateBarChartProps<T> {
  /** Card title shown in the header and empty state. */
  title: string;
  /** Leading icon shown next to the title. */
  icon: ReactNode;
  data?: T[];
  isLoading?: boolean;
  emptyText?: string;
  /** Y axis (category) label column width; widen for longer names. */
  yAxisWidth?: number;
  getLabel: (item: T) => string;
  /** Rate as a 0-1 fraction; rendered as a rounded percentage. */
  getRate: (item: T) => number;
  getBarColor: (rate: number) => string;
  renderTooltip: (item: RateBarRow<T>) => ReactNode;
}

/**
 * Shared skeleton/empty/vertical-bar layout for rate-style dashboard charts
 * (e.g. host failure rate, plan success rate). Callers supply field accessors
 * and per-domain rendering (title/icon/color thresholds/tooltip copy).
 */
export function VerticalRateBarChart<T>({
  title,
  icon,
  data,
  isLoading,
  emptyText = '暂无数据',
  yAxisWidth = 130,
  getLabel,
  getRate,
  getBarColor,
  renderTooltip,
}: VerticalRateBarChartProps<T>) {
  const chartData = useMemo<RateBarRow<T>[]>(() => {
    if (!data || data.length === 0) return [];
    return data.map((d) => {
      const rate = getRate(d);
      return {
        ...d,
        label: getLabel(d),
        rate,
        ratePct: Math.round(rate * 100),
      };
    });
  }, [data, getLabel, getRate]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-40" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[200px] w-full" />
        </CardContent>
      </Card>
    );
  }

  if (chartData.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            {icon}
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-[200px] flex items-center justify-center text-sm text-muted-foreground">
            {emptyText}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          {icon}
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <StableResponsiveContainer>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              layout="vertical"
              margin={{ top: 5, right: 40, left: 0, bottom: 5 }}
            >
              <XAxis
                type="number"
                domain={[0, 100]}
                tickFormatter={(v: number) => `${v}%`}
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
              />
              <YAxis
                type="category"
                dataKey="label"
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                width={yAxisWidth}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    return renderTooltip(payload[0]?.payload as RateBarRow<T>);
                  }
                  return null;
                }}
              />
              <Bar
                dataKey="ratePct"
                radius={[0, 4, 4, 0]}
                maxBarSize={20}
                isAnimationActive={false}
              >
                {chartData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={getBarColor(entry.rate)} />
                ))}
                <LabelList
                  dataKey="ratePct"
                  position="right"
                  formatter={(value: RenderableText) => `${value}%`}
                  style={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </StableResponsiveContainer>
      </CardContent>
    </Card>
  );
}
