import React, { ReactNode } from 'react';
import { ChevronRight, Home } from 'lucide-react';
import { Link } from 'react-router-dom';
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

function PageHeaderBreadcrumbs({ breadcrumbs }: { breadcrumbs: BreadcrumbItem[] }) {
  return (
    <nav className={cn('flex items-center text-xs', TEXT.subtitle)}>
      <Link
        to="/"
        className={cn('flex items-center transition-colors', INTERACTIVE.hoverText)}
      >
        <Home size={12} className="mr-1" />
        首页
      </Link>
      {breadcrumbs.map((item, index) => (
        <React.Fragment key={`${item.label}-${index}`}>
          <ChevronRight size={12} className="mx-1.5 text-muted-foreground/40" />
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
  );
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  subtitle,
  action,
  breadcrumbs,
}) => {
  return (
    <div className="space-y-3">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <PageHeaderBreadcrumbs breadcrumbs={breadcrumbs} />
      )}
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
