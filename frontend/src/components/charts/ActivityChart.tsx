import { useMemo, type ReactNode } from 'react';
import { AreaChart, Area, XAxis, YAxis, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Skeleton } from '@/components/ui/skeleton';
import { InlineEmpty } from '@/components/ui/empty-state';
import { CHART_COLORS } from '@/design-system/colors';

export interface ActivityDataPoint {
  hour: string;
  started: number;
  completed: number;
  failed: number;
}

interface ActivityChartProps {
  data?: ActivityDataPoint[];
  isLoading?: boolean;
}

function ChartBody({ children }: { children: ReactNode }) {
  return <div className="min-h-[200px]">{children}</div>;
}

export function ActivityChart({ data, isLoading }: ActivityChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((p) => ({
      ...p,
      time: p.hour.slice(11, 16), // "2026-02-16T08:00:00" -> "08:00"
    }));
  }, [data]);

  if (isLoading) {
    return (
      <ChartBody>
        <Skeleton className="h-[200px] w-full" />
      </ChartBody>
    );
  }

  if (chartData.length === 0) {
    return (
      <ChartBody>
        <InlineEmpty chart>暂无数据</InlineEmpty>
      </ChartBody>
    );
  }

  return (
    <ChartBody>
      <StableResponsiveContainer>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
            <defs>
              <linearGradient id="grad-started" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={CHART_COLORS.primary} stopOpacity={0.3} />
                <stop offset="95%" stopColor={CHART_COLORS.primary} stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="grad-completed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={CHART_COLORS.success} stopOpacity={0.3} />
                <stop offset="95%" stopColor={CHART_COLORS.success} stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="grad-failed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={CHART_COLORS.error} stopOpacity={0.3} />
                <stop offset="95%" stopColor={CHART_COLORS.error} stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="time"
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
            />
            <YAxis
              axisLine={false}
              tickLine={false}
              tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
              allowDecimals={false}
            />
            <Tooltip
              content={({ active, payload, label }) => {
                if (active && payload && payload.length) {
                  return (
                    <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
                      <div className="text-muted-foreground mb-1">{label}</div>
                      {payload.map((entry, index) => (
                        <div key={String(entry.dataKey ?? index)} className="flex items-center gap-2">
                          <span
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: entry.color }}
                          />
                          <span className="text-muted-foreground">
                            {entry.dataKey === 'started' ? '启动' : entry.dataKey === 'completed' ? '完成' : '失败'}:
                          </span>
                          <span className="font-medium">{entry.value}</span>
                        </div>
                      ))}
                    </div>
                  );
                }
                return null;
              }}
            />
            <Area
              type="monotone"
              dataKey="started"
              name="启动"
              stroke={CHART_COLORS.primary}
              strokeWidth={2}
              fill="url(#grad-started)"
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="completed"
              name="完成"
              stroke={CHART_COLORS.success}
              strokeWidth={2}
              fill="url(#grad-completed)"
              isAnimationActive={false}
            />
            <Area
              type="monotone"
              dataKey="failed"
              name="失败"
              stroke={CHART_COLORS.error}
              strokeWidth={2}
              fill="url(#grad-failed)"
              isAnimationActive={false}
            />
            <Legend
              verticalAlign="bottom"
              height={30}
              iconType="circle"
              iconSize={8}
              formatter={(value: string) => (
                <span className="text-xs text-muted-foreground">{value}</span>
              )}
            />
          </AreaChart>
        </ResponsiveContainer>
      </StableResponsiveContainer>
    </ChartBody>
  );
}
