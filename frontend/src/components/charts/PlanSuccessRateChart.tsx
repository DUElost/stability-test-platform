import { useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell, LabelList } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { CheckCircle } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';

export interface PlanSuccessRateData {
  plan_id: number;
  plan_name: string;
  total_jobs: number;
  passed: number;
  failed: number;
  pass_rate: number;
}

interface PlanSuccessRateChartProps {
  data?: PlanSuccessRateData[];
  isLoading?: boolean;
}

export function PlanSuccessRateChart({ data, isLoading }: PlanSuccessRateChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((d) => ({
      ...d,
      label: d.plan_name.length > 20
        ? d.plan_name.slice(0, 19) + '...'
        : d.plan_name,
      ratePct: Math.round(d.pass_rate * 100),
    }));
  }, [data]);

  const getBarColor = (rate: number) => {
    if (rate >= 0.95) return CHART_COLORS.success;
    if (rate >= 0.8) return CHART_COLORS.warning;
    return CHART_COLORS.error;
  };

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
            <CheckCircle size={16} className="text-muted-foreground" />
            方案成功率
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
          <CheckCircle size={16} className="text-muted-foreground" />
          方案成功率
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
                width={150}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const item = payload[0]?.payload as PlanSuccessRateData & { ratePct: number; label: string };
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
                        <div className="text-muted-foreground mb-1">{item.plan_name}</div>
                        <div className="flex items-center gap-2">
                          <span className="text-muted-foreground">成功率:</span>
                          <span className="font-medium">{item.ratePct}%</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-muted-foreground">通过/总计:</span>
                          <span className="font-medium">{item.passed}/{item.total_jobs}</span>
                        </div>
                      </div>
                    );
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
                  <Cell key={`cell-${index}`} fill={getBarColor(entry.pass_rate)} />
                ))}
                <LabelList dataKey="ratePct" position="right" formatter={(label: any) => `${label}%`} style={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </StableResponsiveContainer>
      </CardContent>
    </Card>
  );
}
