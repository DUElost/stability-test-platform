import React, { ReactNode } from 'react';

export interface StatItem {
  label: string;
  value: string | number;
  suffix?: string;
  color?: 'default' | 'green' | 'blue' | 'red' | 'amber' | 'slate';
  icon?: ReactNode;
}

interface StatsGridProps {
  stats: StatItem[];
  columns?: 2 | 3 | 4 | 5;
}

/**
 * 统计卡片网格组件
 * 统一的统计数字展示
 */
export const StatsGrid: React.FC<StatsGridProps> = ({
  stats,
  columns = 4,
}) => {
  const colorClasses: Record<string, { bg: string; text: string }> = {
    default: { bg: 'bg-white', text: 'text-slate-900' },
    green: { bg: 'bg-white', text: 'text-green-600' },
    blue: { bg: 'bg-white', text: 'text-blue-600' },
    red: { bg: 'bg-white', text: 'text-red-600' },
    amber: { bg: 'bg-white', text: 'text-amber-500' },
    slate: { bg: 'bg-white', text: 'text-slate-400' },
  };

  const gridCols = {
    2: 'grid-cols-2',
    3: 'grid-cols-2 sm:grid-cols-3',
    4: 'grid-cols-2 sm:grid-cols-4',
    5: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-5',
  };

  return (
    <div className={`grid ${gridCols[columns]} gap-4`}>
      {stats.map((stat, index) => (
        <div
          key={index}
          className={`${colorClasses[stat.color || 'default'].bg} p-4 rounded-lg shadow-sm border border-slate-200 hover:shadow-md transition-shadow`}
        >
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-sm font-medium text-slate-500">{stat.label}</h3>
              <div className="flex items-baseline gap-1 mt-1">
                <p className={`text-2xl font-bold ${colorClasses[stat.color || 'default'].text}`}>
                  {stat.value}
                </p>
                {stat.suffix && (
                  <span className="text-sm text-slate-400">{stat.suffix}</span>
                )}
              </div>
            </div>
            {stat.icon && (
              <div className="text-slate-300">
                {stat.icon}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};

export default StatsGrid;
