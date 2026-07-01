import { useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { TrendingUp } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';
import type { PlanRunPassRatePoint } from '@/utils/api/types';

/** @deprecated use `PlanRunPassRatePoint` from `@/utils/api/types` */
export type PlanRunPassRateTrendPoint = PlanRunPassRatePoint;

interface PlanRunPassRateTrendChartProps {
  data?: PlanRunPassRateTrendPoint[];
  isLoading?: boolean;
}

export function PlanRunPassRateTrendChart({
  data,
  isLoading,
}: PlanRunPassRateTrendChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((p) => ({
      ...p,
      label: p.date.slice(5),
      ratePct: parseFloat((p.avg_pass_rate * 100).toFixed(1)),
    }));
  }, [data]);

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
            <TrendingUp size={16} className="text-muted-foreground" />
            运行通过率趋势
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-[200px] flex items-center justify-center text-sm text-muted-foreground">
            暂无数据
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <TrendingUp size={16} className="text-muted-foreground" />
          运行通过率趋势
        </CardTitle>
      </CardHeader>
      <CardContent>
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
                domain={[0, 100]}
                tickFormatter={(v: number) => `${v}%`}
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const item = payload[0]?.payload as PlanRunPassRateTrendPoint & { label: string; ratePct: number };
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
                        <div className="text-muted-foreground mb-1">{item.date}</div>
                        <div className="flex items-center gap-2">
                          <span
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: CHART_COLORS.primary }}
                          />
                          <span className="text-muted-foreground">平均通过率:</span>
                          <span className="font-medium">{item.ratePct}%</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-muted-foreground">完成运行数:</span>
                          <span className="font-medium">{item.run_count}</span>
                        </div>
                      </div>
                    );
                  }
                  return null;
                }}
              />
              <Line
                type="monotone"
                dataKey="ratePct"
                stroke={CHART_COLORS.primary}
                strokeWidth={2}
                dot={{ r: 3, fill: CHART_COLORS.primary }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </StableResponsiveContainer>
      </CardContent>
    </Card>
  );
}
