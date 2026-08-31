import { useMemo, type ReactNode } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell } from 'recharts';
import { StableResponsiveContainer } from './StableResponsiveContainer';
import { Skeleton } from '@/components/ui/skeleton';
import { Server } from 'lucide-react';
import { CHART_COLORS } from '@/design-system/colors';

interface HostResourceData {
  name: string;
  cpu: number;
  ram: number;
  disk: number;
}

interface HostResourceChartProps {
  hosts: Array<{
    ip: string | null;
    cpu_load: number;
    ram_usage: number;
    disk_usage: number | null;
  }>;
  isLoading?: boolean;
}

function ChartBody({ children }: { children: ReactNode }) {
  return <div className="min-h-[200px]">{children}</div>;
}

export function HostResourceChart({ hosts, isLoading }: HostResourceChartProps) {
  const data: HostResourceData[] = useMemo(() => {
    return hosts.map(host => ({
      name: host.ip?.split('.').pop() || host.ip || 'unknown',
      cpu: host.cpu_load,
      ram: host.ram_usage,
      disk: host.disk_usage ?? 0,
    }));
  }, [hosts]);

  const getBarColor = (value: number) => {
    if (value >= 90) return CHART_COLORS.error;
    if (value >= 70) return CHART_COLORS.warning;
    return CHART_COLORS.success;
  };

  if (isLoading) {
    return (
      <ChartBody>
        <Skeleton className="h-[200px] w-full" />
      </ChartBody>
    );
  }

  if (data.length === 0) {
    return (
      <ChartBody>
        <div className="flex h-[200px] flex-col items-center justify-center text-muted-foreground">
          <Server size={32} className="mb-2 opacity-50" />
          <span className="text-sm">暂无主机</span>
        </div>
      </ChartBody>
    );
  }

  return (
    <ChartBody>
      <StableResponsiveContainer>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 5, right: 5, left: -20, bottom: 5 }}>
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
              domain={[0, 100]}
              tickFormatter={(v: number) => `${v}%`}
            />
            <Tooltip
              content={({ active, payload, label }) => {
                if (active && payload && payload.length) {
                  return (
                    <div className="bg-popover border border-border rounded-lg p-2 shadow-md">
                      <div className="text-sm font-medium mb-1">主机 {label}</div>
                      {payload.map((entry) => (
                        <div key={entry.name} className="flex items-center gap-2 text-xs">
                          <div
                            className="w-2 h-2 rounded-full"
                            style={{ backgroundColor: entry.color }}
                          />
                          <span className="capitalize">{entry.name}:</span>
                          <span className="font-medium">{entry.value}%</span>
                        </div>
                      ))}
                    </div>
                  );
                }
                return null;
              }}
            />
            <Bar dataKey="cpu" name="CPU" radius={[4, 4, 0, 0]} maxBarSize={30} isAnimationActive={false} fill={CHART_COLORS.primary} />
            <Bar dataKey="ram" name="RAM" radius={[4, 4, 0, 0]} maxBarSize={30} isAnimationActive={false} fill={CHART_COLORS.success} />
            <Bar dataKey="disk" name="磁盘" radius={[4, 4, 0, 0]} maxBarSize={30} isAnimationActive={false}>
              {data.map((entry, index) => (
                <Cell key={`disk-${index}`} fill={getBarColor(entry.disk)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </StableResponsiveContainer>
    </ChartBody>
  );
}
