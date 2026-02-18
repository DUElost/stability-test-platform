import { useMemo } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ShieldAlert } from 'lucide-react';

interface RiskData {
  name: string;
  value: number;
  color: string;
}

interface RiskDistributionChartProps {
  data: {
    high: number;
    medium: number;
    low: number;
    unknown: number;
  };
  isLoading?: boolean;
}

const COLORS = {
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#22c55e',
  unknown: '#6b7280',
};

const LABELS = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  unknown: 'Unknown',
};

export function RiskDistributionChart({ data, isLoading }: RiskDistributionChartProps) {
  const chartData: RiskData[] = useMemo(() => {
    return [
      { name: LABELS.high, value: data.high, color: COLORS.high },
      { name: LABELS.medium, value: data.medium, color: COLORS.medium },
      { name: LABELS.low, value: data.low, color: COLORS.low },
      { name: LABELS.unknown, value: data.unknown, color: COLORS.unknown },
    ].filter(item => item.value > 0);
  }, [data]);

  const total = useMemo(() =>
    data.high + data.medium + data.low + data.unknown,
    [data]
  );

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

  if (total === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <ShieldAlert size={16} className="text-muted-foreground" />
            Risk Distribution
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-[200px] flex flex-col items-center justify-center text-muted-foreground">
            <ShieldAlert size={32} className="mb-2 opacity-50" />
            <span className="text-sm">No risk data</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <ShieldAlert size={16} className="text-muted-foreground" />
          Risk Distribution
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={70}
                paddingAngle={2}
                dataKey="value"
                animationBegin={0}
                animationDuration={800}
              >
                {chartData.map((entry, index) => (
                  <Cell
                    key={`cell-${index}`}
                    fill={entry.color}
                    strokeWidth={0}
                    style={{
                      filter: 'drop-shadow(0 2px 4px rgba(0,0,0,0.1))',
                    }}
                  />
                ))}
              </Pie>
              <Tooltip
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const d = payload[0].payload as RiskData;
                    const pct = ((d.value / total) * 100).toFixed(1);
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md">
                        <div className="flex items-center gap-2 mb-1">
                          <div
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: d.color }}
                          />
                          <span className="text-sm font-medium">{d.name}</span>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {d.value} runs ({pct}%)
                        </div>
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
                  <span className="text-xs text-muted-foreground">{value}</span>
                )}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
