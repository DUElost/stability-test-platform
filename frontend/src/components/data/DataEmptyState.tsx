import React, { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

interface DataEmptyStateProps {
  title: string;
  description?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}

export const DataEmptyState: React.FC<DataEmptyStateProps> = ({
  title,
  description,
  icon,
  action,
  className,
}) => {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      {icon && <div className={cn('mb-4', TEXT.subtitle)}>{icon}</div>}
      <h3 className={cn('text-sm font-medium', TEXT.heading)}>{title}</h3>
      {description && <p className={cn('mt-1 text-sm max-w-sm', TEXT.subtitle)}>{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
};

export default DataEmptyState;
