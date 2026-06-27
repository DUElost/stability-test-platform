import React, { ReactNode } from 'react';
import { ChevronRight, Home, MoreHorizontal } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { TEXT, INTERACTIVE } from '@/design-system/tokens';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';

export interface BreadcrumbItem {
  label: string;
  path?: string;
}

export interface PageHeaderV2Props {
  title: ReactNode;
  breadcrumbs?: BreadcrumbItem[];
  actions?: ReactNode;
  secondaryActions?: ReactNode;
  description?: ReactNode;
  sticky?: boolean;
  className?: string;
}

function PageHeaderBreadcrumbs({ breadcrumbs }: { breadcrumbs: BreadcrumbItem[] }) {
  return (
    <nav aria-label="Breadcrumb" className={cn('flex items-center text-xs', TEXT.subtitle)}>
      <Link to="/" className={cn('flex items-center transition-colors', INTERACTIVE.hoverText)}>
        <Home size={12} className="mr-1" />
        首页
      </Link>
      {breadcrumbs.map((item, index) => (
        <React.Fragment key={`${item.label}-${index}`}>
          <ChevronRight size={12} className="mx-1.5 text-muted-foreground/40" />
          {item.path ? (
            <Link to={item.path} className={cn('transition-colors', INTERACTIVE.hoverText)}>
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

export const PageHeaderV2: React.FC<PageHeaderV2Props> = ({
  title,
  breadcrumbs,
  actions,
  secondaryActions,
  description,
  sticky = false,
  className,
}) => {
  const hasSecondary = !!secondaryActions;

  return (
    <div
      className={cn(
        'flex flex-col gap-3 pb-4',
        sticky && 'sticky top-0 z-10 bg-background/95 backdrop-blur-sm',
        className,
      )}
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1 flex flex-col gap-1">
          {breadcrumbs && breadcrumbs.length > 0 && (
            <PageHeaderBreadcrumbs breadcrumbs={breadcrumbs} />
          )}
          <h1 className={cn('text-xl font-semibold tracking-tight', TEXT.heading)}>{title}</h1>
          {description && <div className={cn('text-sm', TEXT.subtitle)}>{description}</div>}
        </div>

        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {/* On small screens, collapse actions into a dropdown if there are many */}
            <div className="hidden sm:flex items-center gap-2">{actions}</div>
            <div className="flex sm:hidden items-center gap-2">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="icon" aria-label="操作菜单">
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">{actions}</DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        )}
      </div>

      {hasSecondary && <div className="flex flex-wrap items-center gap-2">{secondaryActions}</div>}
    </div>
  );
};

export default PageHeaderV2;
