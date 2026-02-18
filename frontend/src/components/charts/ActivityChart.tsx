import { useMemo } from 'react';
import { AreaChart, Area, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Activity } from 'lucide-react';

export interface ActivityDataPoint {
  hour: string;
  started: number;
  completed: number;
  failed: number;
}

interface ActivityChartProps {
  data?: ActivityDataPoint[];
  isLoading?: boolean;
  title?: string;
}

export function ActivityChart({
  data,
  isLoading,
  title = '任务活动趋势',
}: ActivityChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((p) => ({
      ...p,
      time: p.hour.slice(11, 16), // "2026-02-16T08:00:00" -> "08:00"
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
            <Activity size={16} className="text-muted-foreground" />
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
          <Activity size={16} className="text-muted-foreground" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
              <defs>
                <linearGradient id="grad-started" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.05} />
                </linearGradient>
                <linearGradient id="grad-completed" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#22c55e" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#22c55e" stopOpacity={0.05} />
                </linearGradient>
                <linearGradient id="grad-failed" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0.05} />
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
                        {payload.map((entry) => (
                          <div key={entry.dataKey} className="flex items-center gap-2">
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
                stroke="#3b82f6"
                strokeWidth={2}
                fill="url(#grad-started)"
                animationDuration={800}
              />
              <Area
                type="monotone"
                dataKey="completed"
                stroke="#22c55e"
                strokeWidth={2}
                fill="url(#grad-completed)"
                animationDuration={800}
              />
              <Area
                type="monotone"
                dataKey="failed"
                stroke="#ef4444"
                strokeWidth={2}
                fill="url(#grad-failed)"
                animationDuration={800}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
