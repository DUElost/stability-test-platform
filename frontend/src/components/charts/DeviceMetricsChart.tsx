import { useState, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts';

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
  { key: 'battery_level', label: '电量', color: '#22c55e', unit: '%' },
  { key: 'temperature', label: '温度', color: '#f97316', unit: '°C' },
  { key: 'network_latency', label: '延迟', color: '#3b82f6', unit: 'ms' },
  { key: 'cpu_usage', label: 'CPU', color: '#8b5cf6', unit: '%' },
  { key: 'mem_used', label: '内存', color: '#ec4899', unit: 'MB' },
];

export function DeviceMetricsChart({ data }: DeviceMetricsChartProps) {
  const [activeMetric, setActiveMetric] = useState<MetricKey>('battery_level');
  const tab = METRIC_TABS.find((t) => t.key === activeMetric)!;

  const chartData = useMemo(() => {
    return data.map((p) => ({
      time: p.timestamp.slice(11, 16), // HH:MM
      value: activeMetric === 'mem_used' && p.mem_used != null
        ? Math.round(p.mem_used / (1024 * 1024)) // bytes -> MB
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
            className={`px-2.5 py-1 text-xs rounded-md transition-colors ${
              activeMetric === t.key
                ? 'bg-gray-900 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="h-[220px]">
        {chartData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-400">
            暂无数据
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 5, left: -10, bottom: 5 }}>
              <XAxis
                dataKey="time"
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: '#9ca3af' }}
              />
              <YAxis
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 11, fill: '#9ca3af' }}
                allowDecimals={false}
              />
              <Tooltip
                content={({ active, payload, label }) => {
                  if (active && payload && payload.length && payload[0].value != null) {
                    return (
                      <div className="bg-white border border-gray-200 rounded-lg p-2 shadow-md text-xs">
                        <div className="text-gray-500 mb-1">{label}</div>
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
