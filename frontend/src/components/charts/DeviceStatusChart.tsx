import { useMemo } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PieChart as PieChartIcon } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';

interface DeviceStatusData {
  name: string;
  value: number;
  color: string;
}

interface DeviceStatusChartProps {
  data: {
    idle: number;
    testing: number;
    offline: number;
    error: number;
  };
  isLoading?: boolean;
}

const COLORS = {
  idle: CHART_COLORS.success,
  testing: CHART_COLORS.primary,
  offline: CHART_COLORS.muted,
  error: CHART_COLORS.error,
};

const LABELS = {
  idle: 'Idle',
  testing: 'Testing',
  offline: 'Offline',
  error: 'Error',
};

export function DeviceStatusChart({ data, isLoading }: DeviceStatusChartProps) {
  const chartData: DeviceStatusData[] = useMemo(() => {
    return [
      { name: LABELS.idle, value: data.idle, color: COLORS.idle },
      { name: LABELS.testing, value: data.testing, color: COLORS.testing },
      { name: LABELS.offline, value: data.offline, color: COLORS.offline },
      { name: LABELS.error, value: data.error, color: COLORS.error },
    ].filter(item => item.value > 0);
  }, [data]);

  const total = useMemo(() =>
    data.idle + data.testing + data.offline + data.error,
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
            <PieChartIcon size={16} className="text-muted-foreground" />
            Device Status
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-[200px] flex flex-col items-center justify-center text-muted-foreground">
            <PieChartIcon size={32} className="mb-2 opacity-50" />
            <span className="text-sm">No devices</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <PieChartIcon size={16} className="text-muted-foreground" />
          Device Status
        </CardTitle>
      </CardHeader>
      <CardContent>
        <StableResponsiveContainer>
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
                isAnimationActive={false}
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
                    const data = payload[0].payload as DeviceStatusData;
                    const percentage = ((data.value / total) * 100).toFixed(1);
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md">
                        <div className="flex items-center gap-2 mb-1">
                          <div
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: data.color }}
                          />
                          <span className="text-sm font-medium">{data.name}</span>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {data.value} devices ({percentage}%)
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
        </StableResponsiveContainer>
      </CardContent>
    </Card>
  );
}
