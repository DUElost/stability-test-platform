import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';
import { Check, ChevronDown, X } from 'lucide-react';
import type { ActiveFilterChip } from './planExecuteFilters';
import type { DeviceViewMode } from './types';

interface DeviceFilterBarProps {
  deviceFilter: string;
  onDeviceFilterChange: (value: string) => void;
  deviceVersionFilter: string;
  onVersionChange: (value: string) => void;
  deviceModelFilter: string;
  onModelChange: (value: string) => void;
  deviceTagFilter: string[];
  onTagFilterChange: (value: string[]) => void;
  versionOptions: string[];
  modelOptions: string[];
  tagOptions: string[];
  versionChips: string[];
  modelChips: string[];
  readyOnly: boolean;
  onReadyOnlyChange: (value: boolean) => void;
  view: DeviceViewMode;
  onViewChange: (view: DeviceViewMode) => void;
  allFilteredSelected: boolean;
  filteredAvailableCount: number;
  onToggleAll: () => void;
  readyFilteredCount: number;
  onSelectAllReady: () => void;
  activeFilterChips: ActiveFilterChip[];
  onClearFilterChip: (chipId: ActiveFilterChip['id']) => void;
}

export function DeviceFilterBar({
  deviceFilter,
  onDeviceFilterChange,
  deviceVersionFilter,
  onVersionChange,
  deviceModelFilter,
  onModelChange,
  deviceTagFilter,
  onTagFilterChange,
  versionOptions,
  modelOptions,
  tagOptions,
  versionChips,
  modelChips,
  readyOnly,
  onReadyOnlyChange,
  view,
  onViewChange,
  allFilteredSelected,
  filteredAvailableCount,
  onToggleAll,
  readyFilteredCount,
  onSelectAllReady,
  activeFilterChips,
  onClearFilterChip,
}: DeviceFilterBarProps) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border p-0.5">
          <button
            type="button"
            className={cn('rounded-md px-2.5 py-1 text-xs', view === 'matrix' && 'bg-primary/15 font-medium text-primary')}
            onClick={() => onViewChange('matrix')}
          >
            矩阵
          </button>
          <button
            type="button"
            className={cn('rounded-md px-2.5 py-1 text-xs', view === 'table' && 'bg-primary/15 font-medium text-primary')}
            onClick={() => onViewChange('table')}
          >
            表格
          </button>
        </div>
        <Input
          className="min-w-48 flex-1"
          type="text"
          placeholder="搜索 Serial / 型号"
          value={deviceFilter}
          onChange={(event) => onDeviceFilterChange(event.target.value)}
        />
        <Select value={deviceVersionFilter} onValueChange={onVersionChange}>
          <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部版本</SelectItem>
            {versionOptions.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}
          </SelectContent>
        </Select>
        <Select value={deviceModelFilter} onValueChange={onModelChange}>
          <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部型号</SelectItem>
            {modelOptions.map((value) => <SelectItem key={value} value={value}>{value}</SelectItem>)}
          </SelectContent>
        </Select>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button type="button" variant="outline" className="w-40 justify-between font-normal">
              <span className="truncate">
                {deviceTagFilter.length === 0
                  ? '全部标签'
                  : deviceTagFilter.length === 1
                    ? deviceTagFilter[0]
                    : `已选 ${deviceTagFilter.length} 个标签`}
              </span>
              <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-48">
            <DropdownMenuItem onSelect={() => onTagFilterChange([])}>
              <span className="mr-2 inline-flex w-4 justify-center">
                {deviceTagFilter.length === 0 && <Check className="h-4 w-4" />}
              </span>
              全部标签
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {tagOptions.map((tag) => {
              const active = deviceTagFilter.includes(tag);
              return (
                <DropdownMenuItem
                  key={tag}
                  onSelect={(event) => {
                    event.preventDefault();
                    onTagFilterChange(active ? deviceTagFilter.filter((t) => t !== tag) : [...deviceTagFilter, tag]);
                  }}
                >
                  <span className="mr-2 inline-flex w-4 justify-center">
                    {active && <Check className="h-4 w-4" />}
                  </span>
                  <span className="truncate">{tag}</span>
                </DropdownMenuItem>
              );
            })}
            {tagOptions.length === 0 && <DropdownMenuItem disabled>暂无标签</DropdownMenuItem>}
          </DropdownMenuContent>
        </DropdownMenu>
        <Button type="button" variant={readyOnly ? 'default' : 'outline'} onClick={() => onReadyOnlyChange(!readyOnly)}>
          仅显示就绪
        </Button>
        <Button type="button" variant="outline" onClick={onToggleAll}>
          {allFilteredSelected
            ? `取消选择筛选结果 (${filteredAvailableCount})`
            : `全选筛选结果 (${filteredAvailableCount})`}
        </Button>
        <Button type="button" variant="outline" onClick={onSelectAllReady} disabled={readyFilteredCount === 0}>
          全选就绪 ({readyFilteredCount})
        </Button>
      </div>
      {(versionChips.length > 0 || modelChips.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {versionChips.map((v) => (
            <button
              key={`v-${v}`}
              type="button"
              onClick={() => onVersionChange(deviceVersionFilter === v ? 'all' : v)}
              className={cn(
                'rounded-full border px-2.5 py-0.5 text-xs transition-colors',
                deviceVersionFilter === v ? 'border-primary bg-primary/15 text-primary' : 'hover:border-primary/50',
              )}
            >
              版本:{v}
            </button>
          ))}
          {modelChips.map((m) => (
            <button
              key={`m-${m}`}
              type="button"
              onClick={() => onModelChange(deviceModelFilter === m ? 'all' : m)}
              className={cn(
                'rounded-full border px-2.5 py-0.5 text-xs transition-colors',
                deviceModelFilter === m ? 'border-primary bg-primary/15 text-primary' : 'hover:border-primary/50',
              )}
            >
              型号:{m}
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1.5" data-testid="active-filter-chips">
        {activeFilterChips.length === 0 ? (
          <span className={cn('text-[11px]', TEXT.subtitle)}>无激活筛选 · 条件会写入 URL 便于分享</span>
        ) : (
          activeFilterChips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() => onClearFilterChip(chip.id)}
              className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary/10 px-2.5 py-0.5 text-xs text-primary"
              aria-label={`清除筛选 ${chip.label}`}
            >
              {chip.label}
              <X className="h-3 w-3 opacity-70" aria-hidden />
            </button>
          ))
        )}
      </div>
    </div>
  );
}
