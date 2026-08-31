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
      className={cn(
        'p-4 transition-colors hover:bg-muted/30',
        onClick && 'cursor-pointer',
        className,
      )}
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
 * 仪表盘 KPI 卡片 — 稀疏数字优先（标签 → 大数字 → 可选小图标）
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
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <p className={STAT.label}>{label}</p>
        {icon && (
          <div className={cn(STAT.iconWell, iconWellClassName, 'shrink-0')}>{icon}</div>
        )}
      </div>
      <div className="flex items-baseline gap-1.5 min-h-[2rem]">
        {loading ? (
          <Skeleton className="h-8 w-16" />
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
  );

  if (onClick) {
    return (
      <StatCardShell
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
