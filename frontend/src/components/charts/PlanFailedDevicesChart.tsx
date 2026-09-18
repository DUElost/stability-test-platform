import { useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell, LabelList } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ShieldAlert } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';
import type { PlanFailedDevicesItem } from '@/utils/api/types';

interface PlanFailedDevicesChartProps {
  data?: PlanFailedDevicesItem[];
  isLoading?: boolean;
}

function getLabel(d: PlanFailedDevicesItem): string {
  return d.plan_name.length > 20 ? d.plan_name.slice(0, 19) + '...' : d.plan_name;
}

/**
 * ADR-0048：「方案成功率排行」的后继——设备失败台数是事实指标，不做通过率评判。
 */
export function PlanFailedDevicesChart({ data, isLoading }: PlanFailedDevicesChartProps) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data
      .map((d) => ({ ...d, label: getLabel(d) }))
      .sort((a, b) => b.failed - a.failed)
      .slice(0, 10);
  }, [data]);

  const skeleton = (
    <Card>
      <CardHeader className="pb-2">
        <Skeleton className="h-5 w-40" />
      </CardHeader>
      <CardContent>
        <Skeleton className="h-[200px] w-full" />
      </CardContent>
    </Card>
  );

  const empty = (
    <div className="h-[200px] flex items-center justify-center text-sm text-muted-foreground">
      近 30 天无失败设备记录
    </div>
  );

  if (isLoading) return skeleton;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <ShieldAlert size={16} className="text-muted-foreground" />
          方案失败设备数排行 (30d)
        </CardTitle>
      </CardHeader>
      <CardContent>
        {chartData.length === 0 ? empty : (
          <StableResponsiveContainer>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical" margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
                <XAxis type="number" axisLine={false} tickLine={false} allowDecimals={false}
                  tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={150}
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                />
                <Tooltip
                  content={({ active, payload }) => {
                    if (active && payload && payload.length) {
                      const item = payload[0]?.payload as PlanFailedDevicesItem & { label: string };
                      return (
                        <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
                          <div className="text-muted-foreground mb-1">{item.plan_name}</div>
                          <div className="flex items-center gap-2">
                            <span className="text-muted-foreground">失败设备数:</span>
                            <span className="font-medium">{item.failed}</span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-muted-foreground">总 job 数:</span>
                            <span className="font-medium">{item.total_jobs}</span>
                          </div>
                        </div>
                      );
                    }
                    return null;
                  }}
                />
                <Bar dataKey="failed" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  {chartData.map((entry) => (
                    <Cell key={entry.plan_id} fill={CHART_COLORS.error} />
                  ))}
                  <LabelList dataKey="failed" position="right" style={{ fontSize: 11 }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </StableResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
