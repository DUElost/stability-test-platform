import { useMemo, useState } from 'react';
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
  filteredCount: number;
  onToggleAll: () => void;
  readyFilteredCount: number;
  onSelectAllReady: () => void;
  activeFilterChips: ActiveFilterChip[];
  onClearFilterChip: (chipId: ActiveFilterChip['id']) => void;
}

/**
 * 舞台筛选条 — 对齐 mockup `.filter-bar` + `.quick-chips`。
 * 主行：视图 / 搜索 / 仅就绪 / 全选就绪；次行：快捷 chips + 激活条件。
 */
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
  filteredCount,
  onToggleAll,
  readyFilteredCount,
  onSelectAllReady,
  activeFilterChips,
  onClearFilterChip,
}: DeviceFilterBarProps) {
  const [tagSearch, setTagSearch] = useState('');
  const filteredTagOptions = useMemo(() => {
    const keyword = tagSearch.trim().toLowerCase();
    if (!keyword) return tagOptions;
    return tagOptions.filter((tag) => tag.toLowerCase().includes(keyword));
  }, [tagOptions, tagSearch]);

  return (
    <div className="shrink-0" data-testid="device-filter-bar">
      <div className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-3 py-2.5">
        <div className="inline-flex overflow-hidden rounded-lg border bg-card">
          <button
            type="button"
            className={cn(
              'h-8 px-3 text-xs',
              view === 'matrix' ? 'bg-primary/15 font-semibold text-primary' : 'text-muted-foreground',
            )}
            onClick={() => onViewChange('matrix')}
          >
            矩阵
          </button>
          <button
            type="button"
            className={cn(
              'h-8 border-l px-3 text-xs',
              view === 'table' ? 'bg-primary/15 font-semibold text-primary' : 'text-muted-foreground',
            )}
            onClick={() => onViewChange('table')}
          >
            表格
          </button>
        </div>

        <Input
          className="h-8 w-40 bg-card sm:w-48"
          type="text"
          placeholder="搜索 serial…"
          value={deviceFilter}
          onChange={(event) => onDeviceFilterChange(event.target.value)}
        />

        <label
          className={cn(
            'inline-flex h-8 cursor-pointer select-none items-center gap-1.5 rounded-lg border bg-card px-2.5 text-xs',
            readyOnly && 'border-primary/40 bg-primary/10 text-primary',
          )}
        >
          <input
            type="checkbox"
            className="accent-primary"
            checked={readyOnly}
            onChange={(event) => onReadyOnlyChange(event.target.checked)}
          />
          仅显示就绪
        </label>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8"
          onClick={onSelectAllReady}
          disabled={readyFilteredCount === 0}
        >
          全选就绪
        </Button>

        <Button type="button" variant="ghost" size="sm" className="h-8" onClick={onToggleAll}>
          {allFilteredSelected
            ? `取消全选 (${filteredAvailableCount})`
            : `全选筛选 (${filteredAvailableCount})`}
        </Button>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Select value={deviceVersionFilter} onValueChange={onVersionChange}>
            <SelectTrigger className="h-8 w-[7.5rem] bg-card text-xs">
              <SelectValue placeholder="版本" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部版本</SelectItem>
              {versionOptions.map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={deviceModelFilter} onValueChange={onModelChange}>
            <SelectTrigger className="h-8 w-28 bg-card text-xs">
              <SelectValue placeholder="型号" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部型号</SelectItem>
              {modelOptions.map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8 w-28 justify-between bg-card px-2 font-normal text-xs"
              >
                <span className="truncate">
                  {deviceTagFilter.length === 0
                    ? '全部标签'
                    : deviceTagFilter.length === 1
                      ? deviceTagFilter[0]
                      : `${deviceTagFilter.length} 标签`}
                </span>
                <ChevronDown className="ml-1 h-3.5 w-3.5 shrink-0 opacity-50" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <div className="p-2 pb-1" onKeyDown={(event) => event.stopPropagation()}>
                <Input
                  className="h-8 text-xs"
                  placeholder="搜索标签…"
                  value={tagSearch}
                  onChange={(event) => setTagSearch(event.target.value)}
                />
              </div>
              <DropdownMenuItem onSelect={() => { setTagSearch(''); onTagFilterChange([]); }}>
                <span className="mr-2 inline-flex w-4 justify-center">
                  {deviceTagFilter.length === 0 && <Check className="h-4 w-4" />}
                </span>
                全部标签
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {filteredTagOptions.map((tag) => {
                const active = deviceTagFilter.includes(tag);
                return (
                  <DropdownMenuItem
                    key={tag}
                    onSelect={(event) => {
                      event.preventDefault();
                      onTagFilterChange(
                        active
                          ? deviceTagFilter.filter((t) => t !== tag)
                          : [...deviceTagFilter, tag],
                      );
                    }}
                  >
                    <span className="mr-2 inline-flex w-4 justify-center">
                      {active && <Check className="h-4 w-4" />}
                    </span>
                    <span className="truncate">{tag}</span>
                  </DropdownMenuItem>
                );
              })}
              {filteredTagOptions.length === 0 && (
                <DropdownMenuItem disabled>{tagOptions.length === 0 ? '暂无标签' : '无匹配标签'}</DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
          <span className="rounded-md bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
            筛选 {filteredCount}
          </span>
        </div>
      </div>

      {(versionChips.length > 0 || modelChips.length > 0) && (
        <div className="flex flex-wrap items-center gap-1.5 px-3 pt-2">
          <span className={cn('self-center text-[11px]', TEXT.subtitle)}>快捷：</span>
          {versionChips.map((v) => (
            <button
              key={`v-${v}`}
              type="button"
              onClick={() => onVersionChange(deviceVersionFilter === v ? 'all' : v)}
              className={cn(
                'h-6 rounded-md border px-2 text-xs transition-colors',
                deviceVersionFilter === v
                  ? 'border-primary bg-primary/15 font-medium text-primary'
                  : 'bg-card hover:border-primary/50',
              )}
            >
              {v}
            </button>
          ))}
          {modelChips.map((m) => (
            <button
              key={`m-${m}`}
              type="button"
              onClick={() => onModelChange(deviceModelFilter === m ? 'all' : m)}
              className={cn(
                'h-6 rounded-md border px-2 text-xs transition-colors',
                deviceModelFilter === m
                  ? 'border-primary bg-primary/15 font-medium text-primary'
                  : 'bg-card hover:border-primary/50',
              )}
            >
              {m}
            </button>
          ))}
        </div>
      )}

      <div
        className="flex flex-wrap items-center gap-1.5 px-3 py-2"
        data-testid="active-filter-chips"
      >
        {activeFilterChips.length === 0 ? (
          <span className={cn('text-[11px]', TEXT.subtitle)}>
            无激活筛选 · 条件会写入 URL 便于分享
          </span>
        ) : (
          activeFilterChips.map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() => onClearFilterChip(chip.id)}
              className="inline-flex h-6 items-center gap-1 rounded-md border border-primary/25 bg-primary/10 px-2 text-xs text-primary"
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
