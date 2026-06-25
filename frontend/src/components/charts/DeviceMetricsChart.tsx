import { useState, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts';
import { CHART_COLORS } from '@/design-system/colors';
import { SEGMENTED, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

export interface DeviceMetricPoint {
  timestamp: string;
  battery_level: number | null;
  temperature: number | null;
  network_latency: number | null;
  cpu_usage: number | null;
  mem_used: number | null;
}

interface DeviceMetricsChartProps {
  data: DeviceMetricPoint[];
}

type MetricKey = 'battery_level' | 'temperature' | 'network_latency' | 'cpu_usage' | 'mem_used';

const METRIC_TABS: { key: MetricKey; label: string; color: string; unit: string }[] = [
  { key: 'battery_level', label: '电量', color: CHART_COLORS.success, unit: '%' },
  { key: 'temperature', label: '温度', color: CHART_COLORS.warning, unit: '°C' },
  { key: 'network_latency', label: '延迟', color: CHART_COLORS.primary, unit: 'ms' },
  { key: 'cpu_usage', label: 'CPU', color: CHART_COLORS.palette[5], unit: '%' },
  { key: 'mem_used', label: '内存', color: CHART_COLORS.palette[4], unit: 'MB' },
];

export function DeviceMetricsChart({ data }: DeviceMetricsChartProps) {
  const [activeMetric, setActiveMetric] = useState<MetricKey>('battery_level');
  const tab = METRIC_TABS.find((t) => t.key === activeMetric)!;

  const chartData = useMemo(() => {
    return data.map((p) => ({
      time: p.timestamp.slice(11, 16),
      value: activeMetric === 'mem_used' && p.mem_used != null
        ? Math.round(p.mem_used / (1024 * 1024))
        : p[activeMetric],
    }));
  }, [data, activeMetric]);

  return (
    <div>
      <div className="flex gap-1 mb-3 flex-wrap">
        {METRIC_TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveMetric(t.key)}
            className={cn(
              'px-2.5 py-1 text-xs rounded-md transition-colors',
              activeMetric === t.key
                ? 'bg-primary text-primary-foreground'
                : cn(SEGMENTED.toggleIdle, 'bg-muted'),
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="h-[220px]">
        {chartData.length === 0 ? (
          <div className={cn('h-full flex items-center justify-center text-sm', TEXT.subtitle)}>
            暂无数据
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 5, left: -10, bottom: 5 }}>
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
                  if (active && payload && payload.length && payload[0].value != null) {
                    return (
                      <div className="bg-popover border border-border rounded-lg p-2 shadow-md text-xs">
                        <div className={cn('mb-1', TEXT.subtitle)}>{label}</div>
                        <div className="font-medium" style={{ color: tab.color }}>
                          {payload[0].value} {tab.unit}
                        </div>
                      </div>
                    );
                  }
                  return null;
                }}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke={tab.color}
                strokeWidth={2}
                dot={false}
                animationDuration={600}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
