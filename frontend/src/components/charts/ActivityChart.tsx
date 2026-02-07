import { useMemo } from 'react';
import { AreaChart, Area, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Activity } from 'lucide-react';

interface ActivityDataPoint {
  time: string;
  value: number;
}

interface ActivityChartProps {
  data?: ActivityDataPoint[];
  isLoading?: boolean;
  title?: string;
  color?: string;
}

const defaultData: ActivityDataPoint[] = [
  { time: '00:00', value: 12 },
  { time: '04:00', value: 8 },
  { time: '08:00', value: 45 },
  { time: '12:00', value: 38 },
  { time: '16:00', value: 52 },
  { time: '20:00', value: 28 },
  { time: '23:59', value: 15 },
];

export function ActivityChart({
  data = defaultData,
  isLoading,
  title = 'Activity Trend',
  color = '#3b82f6'
}: ActivityChartProps) {
  const chartData = useMemo(() => data, [data]);

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
                <linearGradient id={`gradient-${color}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={color} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={color} stopOpacity={0.05} />
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
              />
              <Tooltip
                content={({ active, payload, label }) => {
                  if (active && payload && payload.length) {
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md">
                        <div className="text-xs text-muted-foreground mb-1">{label}</div>
                        <div className="text-sm font-medium">
                          {payload[0].value} tasks
                        </div>
                      </div>
                    );
                  }
                  return null;
                }}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke={color}
                strokeWidth={2}
                fill={`url(#gradient-${color})`}
                animationDuration={800}
                activeDot={{
                  r: 5,
                  stroke: color,
                  strokeWidth: 2,
                  fill: '#fff',
                }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
