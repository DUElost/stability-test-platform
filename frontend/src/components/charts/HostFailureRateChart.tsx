import { useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell, LabelList } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { AlertTriangle } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';

export interface HostFailureRateData {
  host_id: string;
  hostname: string;
  ip_address: string | null;
  total_jobs: number;
  failed: number;
  failure_rate: number;
}

interface HostFailureRateChartProps {
  data?: HostFailureRateData[];
  isLoading?: boolean;
}

export function HostFailureRateChart({ data, isLoading }: HostFailureRateChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((d) => ({
      ...d,
      label: d.hostname
        ? d.hostname.length > 16
          ? d.hostname.slice(0, 15) + '...'
          : d.hostname
        : d.host_id.slice(0, 12),
      ratePct: Math.round(d.failure_rate * 100),
    }));
  }, [data]);

  const getBarColor = (rate: number) => {
    if (rate >= 0.3) return CHART_COLORS.error;
    if (rate >= 0.1) return CHART_COLORS.warning;
    return CHART_COLORS.primary;
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
            <AlertTriangle size={16} className="text-muted-foreground" />
            节点失败率排行
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
          <AlertTriangle size={16} className="text-muted-foreground" />
          节点失败率排行
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
                width={130}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const item = payload[0]?.payload as HostFailureRateData & { ratePct: number; label: string };
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
                        <div className="text-muted-foreground mb-1">{item.hostname || item.host_id}</div>
                        <div className="flex items-center gap-2">
                          <span className="text-muted-foreground">失败率:</span>
                          <span className="font-medium">{item.ratePct}%</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-muted-foreground">失败/总计:</span>
                          <span className="font-medium">{item.failed}/{item.total_jobs}</span>
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
                  <Cell key={`cell-${index}`} fill={getBarColor(entry.failure_rate)} />
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
