import { useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell } from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Server } from 'lucide-react';

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
    disk_usage: number;
  }>;
  isLoading?: boolean;
}

export function HostResourceChart({ hosts, isLoading }: HostResourceChartProps) {
  const data: HostResourceData[] = useMemo(() => {
    return hosts.map(host => ({
      name: host.ip?.split('.').pop() || host.ip || 'unknown',
      cpu: host.cpu_load,
      ram: host.ram_usage,
      disk: host.disk_usage,
    }));
  }, [hosts]);

  const getBarColor = (value: number) => {
    if (value >= 80) return '#ef4444'; // red
    if (value >= 60) return '#f59e0b'; // amber
    return '#3b82f6'; // blue
  };

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

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Server size={16} className="text-muted-foreground" />
            Host Resources
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-[200px] flex flex-col items-center justify-center text-muted-foreground">
            <Server size={32} className="mb-2 opacity-50" />
            <span className="text-sm">No hosts available</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Server size={16} className="text-muted-foreground" />
          Host Resources
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
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
              />
              <Tooltip
                content={({ active, payload, label }) => {
                  if (active && payload && payload.length) {
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md">
                        <div className="text-sm font-medium mb-1">Host {label}</div>
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
              <Bar dataKey="cpu" name="CPU" radius={[4, 4, 0, 0]} maxBarSize={30}>
                {data.map((entry, index) => (
                  <Cell key={`cpu-${index}`} fill={getBarColor(entry.cpu)} />
                ))}
              </Bar>
              <Bar dataKey="ram" name="RAM" radius={[4, 4, 0, 0]} maxBarSize={30}>
                {data.map((entry, index) => (
                  <Cell key={`ram-${index}`} fill={getBarColor(entry.ram)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
