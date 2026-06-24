import React, { ReactNode } from 'react';
import { STATUS_TEXT_COLORS } from '@/design-system/colors';
import { BORDER, ELEVATION, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

export interface StatItem {
  label: string;
  value: string | number;
  suffix?: string;
  color?: 'default' | 'success' | 'primary' | 'error' | 'warning' | 'muted';
  icon?: ReactNode;
}

interface StatsGridProps {
  stats: StatItem[];
  columns?: 2 | 3 | 4 | 5;
}

const VALUE_COLOR: Record<NonNullable<StatItem['color']>, string> = {
  default: STATUS_TEXT_COLORS.default,
  success: STATUS_TEXT_COLORS.success,
  primary: STATUS_TEXT_COLORS.primary,
  error: STATUS_TEXT_COLORS.error,
  warning: STATUS_TEXT_COLORS.warning,
  muted: STATUS_TEXT_COLORS.muted,
};

/**
 * 统计卡片网格组件
 */
export const StatsGrid: React.FC<StatsGridProps> = ({ stats, columns = 4 }) => {
  const gridCols = {
    2: 'grid-cols-2',
    3: 'grid-cols-2 sm:grid-cols-3',
    4: 'grid-cols-2 sm:grid-cols-4',
    5: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-5',
  };

  return (
    <div className={cn('grid gap-4', gridCols[columns])}>
      {stats.map((stat, index) => (
        <div
          key={index}
          className={cn(
            'p-4 rounded-lg transition-shadow hover:shadow-md',
            SURFACE.elevated,
            BORDER.default,
            'border',
            ELEVATION.sm,
          )}
        >
          <div className="flex items-start justify-between">
            <div>
              <h3 className={cn('text-sm font-medium', TEXT.subtitle)}>{stat.label}</h3>
              <div className="flex items-baseline gap-1 mt-1">
                <p className={cn('text-2xl font-bold', VALUE_COLOR[stat.color || 'default'])}>
                  {stat.value}
                </p>
                {stat.suffix && (
                  <span className={cn('text-sm', TEXT.caption)}>{stat.suffix}</span>
                )}
              </div>
            </div>
            {stat.icon && (
              <div className={TEXT.subtle}>{stat.icon}</div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

export default StatsGrid;
