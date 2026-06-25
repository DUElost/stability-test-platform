import { useState, useEffect, useCallback } from 'react';
import { Search, Filter, X } from 'lucide-react';
import { FORM, INTERACTIVE, PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface DeviceToolbarProps {
  filterText: string;
  onFilterTextChange: (value: string) => void;
  statusFilter: string;
  onStatusFilterChange: (value: string) => void;
}

const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'idle', label: '空闲' },
  { value: 'testing', label: '测试中' },
  { value: 'offline', label: '离线' },
  { value: 'error', label: '异常' },
];

export function DeviceToolbar({
  filterText,
  onFilterTextChange,
  statusFilter,
  onStatusFilterChange,
}: DeviceToolbarProps) {
  const [localFilterText, setLocalFilterText] = useState(filterText);

  useEffect(() => {
    const timer = setTimeout(() => {
      onFilterTextChange(localFilterText);
    }, 300);
    return () => clearTimeout(timer);
  }, [localFilterText, onFilterTextChange]);

  useEffect(() => {
    setLocalFilterText(filterText);
  }, [filterText]);

  const handleClearFilter = useCallback(() => {
    setLocalFilterText('');
    onFilterTextChange('');
  }, [onFilterTextChange]);

  return (
    <div className={PANEL.root}>
      <div className="flex flex-col gap-4 p-4 sm:flex-row">
        <div className="relative max-w-md flex-1">
          <Search className={cn('absolute left-3 top-1/2 -translate-y-1/2', TEXT.subtitle)} size={18} />
          <input
            type="text"
            placeholder="Search by serial or model..."
            value={localFilterText}
            onChange={(e) => setLocalFilterText(e.target.value)}
            className={cn(FORM.input, 'pl-10 pr-10')}
          />
          {localFilterText && (
            <button
              onClick={handleClearFilter}
              className={cn('absolute right-3 top-1/2 -translate-y-1/2', INTERACTIVE.iconButton)}
            >
              <X size={16} />
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Filter size={18} className={TEXT.subtitle} />
          <select
            value={statusFilter}
            onChange={(e) => onStatusFilterChange(e.target.value)}
            className={FORM.select}
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {(filterText || statusFilter !== 'all') && (
          <div className={cn('flex items-center gap-2 text-sm', TEXT.subtitle)}>
            <span>Active filters:</span>
            {filterText && (
              <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs', STATUS_CHIP.primary)}>
                Search: {filterText.length > 15 ? `${filterText.slice(0, 15)}...` : filterText}
                <button onClick={handleClearFilter} className="hover:text-primary">
                  <X size={12} />
                </button>
              </span>
            )}
            {statusFilter !== 'all' && (
              <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs', STATUS_CHIP.success)}>
                Status: {STATUS_OPTIONS.find((o) => o.value === statusFilter)?.label}
                <button onClick={() => onStatusFilterChange('all')} className="hover:text-success">
                  <X size={12} />
                </button>
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
