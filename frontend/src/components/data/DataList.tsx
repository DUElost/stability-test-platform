import { ReactNode } from 'react';
import { DataEmptyState } from './DataEmptyState';
import { DataErrorState } from './DataErrorState';
import { DataSkeleton } from './DataSkeleton';
import { cn } from '@/lib/utils';

interface DataListRenderContext {
  isSelected: boolean;
  toggleSelect: () => void;
}

interface DataListProps<T> {
  items: T[];
  isLoading?: boolean;
  error?: Error | null;
  emptyState?: ReactNode;
  renderItem: (item: T, ctx: DataListRenderContext) => ReactNode;
  keyExtractor: (item: T) => string;
  selection?: 'none' | 'single' | 'multiple';
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
  header?: ReactNode;
  footer?: ReactNode;
  itemClassName?: string;
  className?: string;
}

export function DataList<T>({
  items,
  isLoading,
  error,
  emptyState,
  renderItem,
  keyExtractor,
  selection: _selection = 'none',
  selectedKeys,
  onSelectionChange,
  header,
  footer,
  itemClassName,
  className,
}: DataListProps<T>) {
  const currentKeys = selectedKeys ?? new Set<string>();

  const toggleSelect = (key: string) => {
    if (!onSelectionChange) return;
    const next = new Set(currentKeys);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onSelectionChange(next);
  };

  if (isLoading) {
    return (
      <div className={className}>
        {header}
        <DataSkeleton rows={5} />
      </div>
    );
  }

  if (error) {
    return (
      <div className={className}>
        {header}
        <DataErrorState description={error.message} />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className={className}>
        {header}
        {emptyState ?? <DataEmptyState title="暂无数据" />}
      </div>
    );
  }

  return (
    <div className={cn('space-y-3', className)}>
      {header}
      <div className="space-y-2">
        {items.map((item) => {
          const key = keyExtractor(item);
          const isSelected = currentKeys.has(key);
          return (
            <div key={key} className={itemClassName}>
              {renderItem(item, {
                isSelected,
                toggleSelect: () => toggleSelect(key),
              })}
            </div>
          );
        })}
      </div>
      {footer}
    </div>
  );
}

export default DataList;
