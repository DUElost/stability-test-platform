import { useMemo, type ReactNode } from 'react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Skeleton } from '@/components/ui/skeleton';
import { InlineEmpty } from '@/components/ui/empty-state';
import { CHART_COLORS } from '@/design-system/colors';

export interface CompletionTrendPoint {
  date: string;
  passed: number;
  failed: number;
}

interface CompletionTrendChartProps {
  data?: CompletionTrendPoint[];
  isLoading?: boolean;
}

function ChartBody({ children }: { children: ReactNode }) {
  return <div className="min-h-[200px]">{children}</div>;
}

export function CompletionTrendChart({ data, isLoading }: CompletionTrendChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((p) => ({
      ...p,
      label: p.date.slice(5), // "2026-02-16" -> "02-16"
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
          <LineChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
            <XAxis
              dataKey="label"
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
                            {entry.dataKey === 'passed' ? '通过' : '失败'}:
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
            <Line
              type="monotone"
              dataKey="passed"
              name="通过"
              stroke={CHART_COLORS.success}
              strokeWidth={2}
              dot={{ r: 3, fill: CHART_COLORS.success }}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="failed"
              name="失败"
              stroke={CHART_COLORS.error}
              strokeWidth={2}
              dot={{ r: 3, fill: CHART_COLORS.error }}
              activeDot={{ r: 5 }}
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
          </LineChart>
        </ResponsiveContainer>
      </StableResponsiveContainer>
    </ChartBody>
  );
}
