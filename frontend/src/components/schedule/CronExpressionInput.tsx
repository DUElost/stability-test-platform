import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { FORM, SEGMENTED, TEXT } from '@/design-system/tokens';

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
          className={cn(FORM.input, 'flex-1 font-mono rounded-lg')}
        />
        <button
          type="button"
          onClick={() => setShowPresets(!showPresets)}
          className={cn(
            'px-3 py-2 text-sm border border-border rounded-lg transition-colors',
            SEGMENTED.toggleIdle,
          )}
        >
          预设
        </button>
      </div>

      {value && (
        <p className={cn('text-xs', TEXT.subtitle)}>{description}</p>
      )}

      {showPresets && (
        <div className={cn('grid grid-cols-2 gap-1.5 p-2 rounded-lg border', SEGMENTED.track)}>
          {PRESETS.map((preset) => (
            <button
              key={preset.value}
              type="button"
              onClick={() => {
                onChange(preset.value);
                setShowPresets(false);
              }}
              className={cn(
                'text-left px-3 py-2 rounded-md text-sm transition-colors',
                SEGMENTED.toggleIdle,
                value === preset.value && cn('bg-card shadow-sm', SEGMENTED.toggleActive),
              )}
            >
              <div className={cn('font-medium', TEXT.heading)}>{preset.label}</div>
              <div className={cn('text-xs', TEXT.subtitle)}>{preset.description}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
