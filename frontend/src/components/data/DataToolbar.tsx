import React, { ReactNode } from 'react';
import { Search } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

interface DataToolbarProps {
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  searchPlaceholder?: string;
  children?: ReactNode;
  className?: string;
}

export const DataToolbar: React.FC<DataToolbarProps> = ({
  searchValue,
  onSearchChange,
  searchPlaceholder = '搜索...',
  children,
  className,
}) => {
  return (
    <div className={cn('flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between', className)}>
      {onSearchChange && (
        <div className="relative flex-1 max-w-md">
          <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4', TEXT.subtitle)} />
          <Input
            type="text"
            placeholder={searchPlaceholder}
            value={searchValue ?? ''}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-9"
          />
        </div>
      )}
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
};

export default DataToolbar;
