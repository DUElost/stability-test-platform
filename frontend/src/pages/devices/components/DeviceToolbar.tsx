import { useState, useEffect, useCallback } from 'react';
import { Search, Filter, X } from 'lucide-react';

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
  // Local state for immediate input feedback
  const [localFilterText, setLocalFilterText] = useState(filterText);

  // Debounce filter text changes (300ms)
  useEffect(() => {
    const timer = setTimeout(() => {
      onFilterTextChange(localFilterText);
    }, 300);

    return () => clearTimeout(timer);
  }, [localFilterText, onFilterTextChange]);

  // Sync local state when prop changes (e.g., from parent reset)
  useEffect(() => {
    setLocalFilterText(filterText);
  }, [filterText]);

  const handleClearFilter = useCallback(() => {
    setLocalFilterText('');
    onFilterTextChange('');
  }, [onFilterTextChange]);

  return (
    <div className="bg-white p-4 rounded-lg border border-slate-200 shadow-sm">
      <div className="flex flex-col sm:flex-row gap-4">
        {/* Search Input */}
        <div className="relative flex-1 max-w-md">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
            size={18}
          />
          <input
            type="text"
            placeholder="Search by serial or model..."
            value={localFilterText}
            onChange={(e) => setLocalFilterText(e.target.value)}
            className="w-full pl-10 pr-10 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-all"
          />
          {localFilterText && (
            <button
              onClick={handleClearFilter}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
            >
              <X size={16} />
            </button>
          )}
        </div>

        {/* Status Filter */}
        <div className="flex items-center gap-2">
          <Filter size={18} className="text-slate-400" />
          <select
            value={statusFilter}
            onChange={(e) => onStatusFilterChange(e.target.value)}
            className="border border-slate-300 rounded-md py-2 pl-3 pr-8 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white text-sm min-w-[140px]"
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {/* Active Filters Badge */}
        {(filterText || statusFilter !== 'all') && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-slate-500">Active filters:</span>
            {filterText && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-100 text-blue-700 rounded-full text-xs">
                Search: {filterText.length > 15 ? `${filterText.slice(0, 15)}...` : filterText}
                <button
                  onClick={handleClearFilter}
                  className="hover:text-blue-900"
                >
                  <X size={12} />
                </button>
              </span>
            )}
            {statusFilter !== 'all' && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-100 text-green-700 rounded-full text-xs">
                Status: {STATUS_OPTIONS.find(o => o.value === statusFilter)?.label}
                <button
                  onClick={() => onStatusFilterChange('all')}
                  className="hover:text-green-900"
                >
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
