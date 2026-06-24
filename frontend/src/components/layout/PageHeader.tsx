import React, { ReactNode, useEffect } from 'react';
import { ChevronRight, Home } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { INTERACTIVE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface BreadcrumbItem {
  label: string;
  path?: string;
}

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  breadcrumbs?: BreadcrumbItem[];
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  subtitle,
  action,
  breadcrumbs,
}) => {
  const { setHeaderSlot, isDefault } = useHeaderSlot();

  useEffect(() => {
    if (isDefault) return;
    setHeaderSlot(
      <div className="flex min-w-0 flex-col justify-center gap-1">
        <h1 className={cn('truncate text-lg font-semibold leading-tight tracking-tight', TEXT.heading)}>
          {title}
        </h1>
        {subtitle && (
          <span className={cn('truncate text-xs leading-tight', TEXT.caption)}>
            {subtitle}
          </span>
        )}
      </div>,
    );
    return () => setHeaderSlot(null);
  }, [title, subtitle, isDefault, setHeaderSlot]);

  const breadcrumbsEl =
    breadcrumbs && breadcrumbs.length > 0 ? (
      <nav className={cn('flex items-center text-sm', TEXT.subtitle)}>
        <Link
          to="/"
          className={cn('flex items-center transition-colors', INTERACTIVE.hoverText)}
        >
          <Home size={14} className="mr-1" />
          首页
        </Link>
        {breadcrumbs.map((item, index) => (
          <React.Fragment key={index}>
            <ChevronRight size={14} className="mx-2 text-muted-foreground/40" />
            {item.path ? (
              <Link
                to={item.path}
                className={cn('transition-colors', INTERACTIVE.hoverText)}
              >
                {item.label}
              </Link>
            ) : (
              <span className={cn('font-medium', TEXT.heading)}>{item.label}</span>
            )}
          </React.Fragment>
        ))}
      </nav>
    ) : null;

  if (!isDefault) {
    if (!breadcrumbsEl && !action) return null;
    return (
      <div className="space-y-3">
        {breadcrumbsEl}
        {action && (
          <div className="flex flex-shrink-0 items-center justify-end gap-2">
            {action}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {breadcrumbsEl}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className={cn('text-2xl font-semibold mb-1', TEXT.heading)}>{title}</h2>
          {subtitle && <p className={cn('text-sm', TEXT.caption)}>{subtitle}</p>}
        </div>
        {action && (
          <div className="flex-shrink-0 flex items-center gap-2">{action}</div>
        )}
      </div>
    </div>
  );
};

export default PageHeader;
