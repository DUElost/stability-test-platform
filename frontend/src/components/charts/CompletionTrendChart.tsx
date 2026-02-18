import { useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { TrendingUp } from 'lucide-react';

export interface CompletionTrendPoint {
  date: string;
  passed: number;
  failed: number;
}

interface CompletionTrendChartProps {
  data?: CompletionTrendPoint[];
  isLoading?: boolean;
  title?: string;
}

export function CompletionTrendChart({
  data,
  isLoading,
  title = '完成趋势',
}: CompletionTrendChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((p) => ({
      ...p,
      label: p.date.slice(5), // "2026-02-16" -> "02-16"
    }));
  }, [data]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-32" />
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
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-[200px] flex items-center justify-center text-sm text-gray-400">
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
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
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
                        {payload.map((entry) => (
                          <div key={entry.dataKey} className="flex items-center gap-2">
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
                stroke="#22c55e"
                strokeWidth={2}
                dot={{ r: 3, fill: '#22c55e' }}
                activeDot={{ r: 5 }}
                animationDuration={800}
              />
              <Line
                type="monotone"
                dataKey="failed"
                stroke="#ef4444"
                strokeWidth={2}
                dot={{ r: 3, fill: '#ef4444' }}
                activeDot={{ r: 5 }}
                animationDuration={800}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
