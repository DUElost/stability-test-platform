import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';

interface CronExpressionInputProps {
  value: string;
  onChange: (value: string) => void;
}

const PRESETS: { label: string; value: string; description: string }[] = [
  { label: '每小时', value: '0 * * * *', description: '每小时整点执行' },
  { label: '每天 2:00', value: '0 2 * * *', description: '每天凌晨 2:00 执行' },
  { label: '每天 8:00', value: '0 8 * * *', description: '每天早上 8:00 执行' },
  { label: '每周一', value: '0 9 * * 1', description: '每周一 9:00 执行' },
  { label: '每月1日', value: '0 0 1 * *', description: '每月1日 0:00 执行' },
];

function describeCron(expr: string): string {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return '无效表达式';

  const [min, hour, dom, month, dow] = parts;

  if (min === '0' && hour === '*' && dom === '*' && month === '*' && dow === '*')
    return '每小时整点';
  if (min === '0' && hour !== '*' && dom === '*' && month === '*' && dow === '*')
    return `每天 ${hour}:00`;
  if (min !== '*' && hour !== '*' && dom === '*' && month === '*' && dow !== '*') {
    const days = ['日', '一', '二', '三', '四', '五', '六'];
    return `每周${days[Number(dow)] || dow} ${hour}:${min.padStart(2, '0')}`;
  }
  if (min !== '*' && hour !== '*' && dom !== '*' && month === '*' && dow === '*')
    return `每月${dom}日 ${hour}:${min.padStart(2, '0')}`;

  return `${min} ${hour} ${dom} ${month} ${dow}`;
}

export function CronExpressionInput({ value, onChange }: CronExpressionInputProps) {
  const [showPresets, setShowPresets] = useState(false);

  const description = useMemo(() => describeCron(value), [value]);

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="0 2 * * *"
          className="flex-1 px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300"
        />
        <button
          type="button"
          onClick={() => setShowPresets(!showPresets)}
          className="px-3 py-2 text-sm border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          预设
        </button>
      </div>

      {value && (
        <p className="text-xs text-gray-500">{description}</p>
      )}

      {showPresets && (
        <div className="grid grid-cols-2 gap-1.5 p-2 bg-gray-50 rounded-lg border border-gray-100">
          {PRESETS.map((preset) => (
            <button
              key={preset.value}
              type="button"
              onClick={() => {
                onChange(preset.value);
                setShowPresets(false);
              }}
              className={cn(
                'text-left px-3 py-2 rounded-md text-sm hover:bg-white transition-colors',
                value === preset.value && 'bg-white shadow-sm'
              )}
            >
              <div className="font-medium text-gray-900">{preset.label}</div>
              <div className="text-xs text-gray-500">{preset.description}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
