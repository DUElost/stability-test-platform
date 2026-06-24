import type { ReactNode, KeyboardEvent } from 'react';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { STAT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

export interface DashboardStatCardProps {
  label: string;
  value?: ReactNode;
  suffix?: ReactNode;
  icon?: ReactNode;
  iconWellClassName?: string;
  valueClassName?: string;
  loading?: boolean;
  onClick?: () => void;
  href?: string;
  ariaLabel?: string;
}

function StatCardShell({
  children,
  className,
  onClick,
  onKeyDown,
  tabIndex,
  role,
  ariaLabel,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  onKeyDown?: (e: KeyboardEvent<HTMLDivElement>) => void;
  tabIndex?: number;
  role?: string;
  ariaLabel?: string;
}) {
  return (
    <Card
      className={cn('p-4 transition-shadow hover:shadow-md', className)}
      onClick={onClick}
      onKeyDown={onKeyDown}
      tabIndex={tabIndex}
      role={role}
      aria-label={ariaLabel}
    >
      {children}
    </Card>
  );
}

/**
 * 仪表盘 KPI 卡片 — 统一标签/数值/图标槽样式
 */
export function DashboardStatCard({
  label,
  value,
  suffix,
  icon,
  iconWellClassName = STAT.iconWellMuted,
  valueClassName = STAT.value,
  loading,
  onClick,
  ariaLabel,
}: DashboardStatCardProps) {
  const inner = (
    <div className="flex items-center justify-between">
      <div>
        <p className={STAT.label}>{label}</p>
        <div className="flex items-baseline gap-1 mt-1">
          {loading ? (
            <Skeleton className="h-8 w-12" />
          ) : (
            <>
              <span className={valueClassName}>{value}</span>
              {suffix != null && suffix !== '' && (
                <span className={STAT.suffix}>{suffix}</span>
              )}
            </>
          )}
        </div>
      </div>
      {icon && (
        <div className={cn(STAT.iconWell, iconWellClassName)}>{icon}</div>
      )}
    </div>
  );

  if (onClick) {
    return (
      <StatCardShell
        className="cursor-pointer"
        onClick={onClick}
        tabIndex={0}
        role="button"
        ariaLabel={ariaLabel}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onClick();
          }
        }}
      >
        {inner}
      </StatCardShell>
    );
  }

  return <StatCardShell>{inner}</StatCardShell>;
}
