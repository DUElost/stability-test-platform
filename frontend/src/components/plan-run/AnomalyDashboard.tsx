import { TrendingDown, TrendingUp, Minus, AlertTriangle } from 'lucide-react';
import type { WatcherSummary } from '@/utils/api/types';
import SectionHeader from './SectionHeader';

interface Props {
  data?: WatcherSummary;
  isLoading?: boolean;
  isError?: boolean;
  windowMinutes?: number;
  onWindowChange?: (minutes: number) => void;
}

const WINDOWS = [15, 60, 360, 1440] as const;
const WINDOW_LABELS: Record<number, string> = { 15: '15m', 60: '1h', 360: '6h', 1440: '24h' };

const CATEGORY_LABEL: Record<string, string> = {
  AEE:              'AEE 崩溃',
  VENDOR_AEE:       'Vendor AEE',
  ANR:              'ANR',
  LOW_BATTERY:      '低电量',
  HIGH_TEMPERATURE: '高温',
  DISCONNECT:       '断连',
};

function TrendIcon({ value }: { value: number }) {
  if (value > 0) return <TrendingUp  className="h-3 w-3 text-red-500"   />;
  if (value < 0) return <TrendingDown className="h-3 w-3 text-green-500" />;
  return <Minus className="h-3 w-3 text-gray-400" />;
}

function GaugeRing({ rate, exceeded }: { rate: number; exceeded: boolean }) {
  const r = 22;
  const circ = 2 * Math.PI * r;
  const stroke = Math.min(1, Math.max(0, rate)) * circ;
  const color = exceeded ? '#ef4444' : rate > 0.5 ? '#f97316' : '#22c55e';
  return (
    <svg viewBox="0 0 60 60" className="w-14 h-14 -rotate-90">
      <circle cx="30" cy="30" r={r} strokeWidth="5" stroke="#f3f4f6" fill="none" />
      <circle
        cx="30" cy="30" r={r} strokeWidth="5" fill="none"
        stroke={color}
        strokeDasharray={`${stroke} ${circ}`}
        strokeLinecap="round"
      />
      <text
        x="30" y="34"
        textAnchor="middle"
        style={{ fontSize: 11, fill: color, fontWeight: 700, transform: 'rotate(90deg)', transformOrigin: '30px 30px' }}
      >
        {Math.round(rate * 100)}%
      </text>
    </svg>
  );
}

export default function AnomalyDashboard({
  data,
  isLoading = false,
  isError = false,
  windowMinutes = 60,
  onWindowChange,
}: Props) {
  const categories = data?.categories ?? [];
  const abnormalRate = data?.abnormal_rate ?? 0;
  const exceeded = data?.exceeded ?? false;
  const totalCount = categories.reduce((sum, c) => sum + c.count, 0);

  return (
    <div className="space-y-2.5">
      <SectionHeader
        title="异常仪表盘"
        color={exceeded ? 'red' : totalCount > 0 ? 'amber' : 'green'}
        extra={
          <div className="flex gap-1">
            {WINDOWS.map((w) => (
              <button
                key={w}
                onClick={() => onWindowChange?.(w)}
                className={`rounded px-1.5 py-0.5 text-[11px] ${
                  windowMinutes === w
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                }`}
              >
                {WINDOW_LABELS[w]}
              </button>
            ))}
          </div>
        }
      />

      {isLoading && (
        <div className="flex h-20 items-center justify-center text-xs text-gray-400">加载中…</div>
      )}
      {isError && (
        <div className="flex h-20 items-center justify-center gap-1 text-xs text-red-500">
          <AlertTriangle className="h-3 w-3" /> 加载失败
        </div>
      )}

      {!isLoading && !isError && (
        <>
          {exceeded && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span>异常率超阈值，巡检风险较高，请及时介入</span>
            </div>
          )}

          <div className="flex items-center gap-3 rounded-lg border bg-white px-3 py-2 shadow-sm">
            <GaugeRing rate={abnormalRate} exceeded={exceeded} />
            <div>
              <div className="text-[11px] text-gray-500">整体异常率</div>
              <div className="text-lg font-bold text-gray-800">
                {Math.round(abnormalRate * 100)}%
              </div>
              <div className="text-[11px] text-gray-400">
                {totalCount === 0 ? '暂无异常事件' : `共 ${totalCount} 次异常`}
              </div>
            </div>
          </div>

          {categories.length > 0 && (
            <div className="space-y-1.5">
              {categories.map((cat) => {
                const barWidth = totalCount > 0 ? Math.round((cat.count / totalCount) * 100) : 0;
                return (
                  <div key={cat.category} className="rounded-lg border bg-white px-3 py-2 shadow-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-gray-700">
                        {CATEGORY_LABEL[cat.category] ?? cat.category}
                      </span>
                      <div className="flex items-center gap-1">
                        <TrendIcon value={cat.trend_change ?? 0} />
                        <span className="text-xs font-bold text-gray-800">{cat.count}</span>
                      </div>
                    </div>
                    {cat.latest_device_serial && (
                      <div className="mt-0.5 text-[11px] text-gray-400">
                        最近：{cat.latest_device_serial}
                      </div>
                    )}
                    <div className="mt-1.5 h-1 rounded-full bg-gray-100">
                      <div
                        className="h-1 rounded-full bg-orange-400"
                        style={{ width: `${barWidth}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {categories.length === 0 && (
            <div className="flex h-10 items-center justify-center text-[11px] text-gray-400">
              当前时间窗内暂无异常事件
            </div>
          )}
        </>
      )}
    </div>
  );
}
