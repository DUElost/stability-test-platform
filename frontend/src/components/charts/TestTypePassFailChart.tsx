import { useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { BarChart3 } from 'lucide-react';

interface TestTypeStat {
  type: string;
  finished: number;
  failed: number;
  total: number;
}

interface TestTypePassFailChartProps {
  data: TestTypeStat[];
  isLoading?: boolean;
}

export function TestTypePassFailChart({ data, isLoading }: TestTypePassFailChartProps) {
  const chartData = useMemo(() => {
    return data.map(item => ({
      name: item.type,
      finished: item.finished,
      failed: item.failed,
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
            <BarChart3 size={16} className="text-muted-foreground" />
            Pass / Fail by Test Type
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-[200px] flex flex-col items-center justify-center text-muted-foreground">
            <BarChart3 size={32} className="mb-2 opacity-50" />
            <span className="text-sm">No test data</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <BarChart3 size={16} className="text-muted-foreground" />
          Pass / Fail by Test Type
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
              <XAxis
                dataKey="name"
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
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md">
                        <div className="text-sm font-medium mb-1">{label}</div>
                        {payload.map((entry) => (
                          <div key={entry.name} className="flex items-center gap-2 text-xs">
                            <div
                              className="w-2 h-2 rounded-full"
                              style={{ backgroundColor: entry.color as string }}
                            />
                            <span className="capitalize">{entry.name}:</span>
                            <span className="font-medium">{entry.value}</span>
                          </div>
                        ))}
                      </div>
                    );
                  }
                  return null;
                }}
              />
              <Legend
                verticalAlign="bottom"
                height={30}
                iconType="circle"
                iconSize={8}
                formatter={(value: string) => (
                  <span className="text-xs text-muted-foreground capitalize">{value}</span>
                )}
              />
              <Bar
                dataKey="finished"
                name="Finished"
                stackId="a"
                fill="#22c55e"
                radius={[0, 0, 0, 0]}
                maxBarSize={40}
              />
              <Bar
                dataKey="failed"
                name="Failed"
                stackId="a"
                fill="#ef4444"
                radius={[4, 4, 0, 0]}
                maxBarSize={40}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
