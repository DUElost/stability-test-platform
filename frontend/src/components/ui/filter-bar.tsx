import { type ReactNode, useEffect, useRef, useState } from 'react';
import { RotateCcw, Search, SlidersHorizontal, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { FORM, TEXT } from '@/design-system/tokens';

export interface ActiveFilterChip {
  key: string;
  label: string;
  value: string;
  onRemove: () => void;
}

interface FilterSearchInputProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  ariaLabel: string;
  testId?: string;
  className?: string;
  enableSlashShortcut?: boolean;
}

/**
 * 统一搜索输入框：内置左搜索图标、右侧一键清空按钮、可选 `/` 快捷键聚焦。
 */
export function FilterSearchInput({
  id,
  value,
  onChange,
  placeholder,
  ariaLabel,
  testId,
  className,
  enableSlashShortcut = true,
}: FilterSearchInputProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!enableSlashShortcut) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || target?.isContentEditable) {
        return;
      }
      event.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [enableSlashShortcut]);

  return (
    <div className={cn('relative min-w-0', className)}>
      <Search
        className={cn(
          'pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2',
          TEXT.subtitle,
        )}
      />
      <input
        ref={inputRef}
        id={id}
        name={id}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
        data-testid={testId}
        className={cn('h-9 w-full pl-9 pr-8', FORM.inputSm)}
      />
      {value ? (
        <button
          type="button"
          aria-label="清空搜索"
          onClick={() => onChange('')}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      ) : (
        enableSlashShortcut && (
          <kbd className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 rounded border border-border bg-muted/50 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
            /
          </kbd>
        )
      )}
    </div>
  );
}

interface MoreFiltersPopoverProps {
  activeCount: number;
  children: ReactNode;
  align?: 'left' | 'right';
  testId?: string;
}

/**
 * 「更多筛选」弹出面板：收纳低频条件与布尔开关，保持主筛选条单行整洁。
 */
export function MoreFiltersPopover({
  activeCount,
  children,
  align = 'right',
  testId,
}: MoreFiltersPopoverProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative inline-block">
      <button
        type="button"
        aria-expanded={open}
        data-testid={testId}
        onClick={() => setOpen((prev) => !prev)}
        className={cn(
          'inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm transition-colors',
          'hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          activeCount > 0
            ? 'border-primary/50 bg-primary/5 font-medium text-foreground'
            : 'border-input bg-background text-muted-foreground hover:text-foreground',
        )}
      >
        <SlidersHorizontal className="h-3.5 w-3.5" />
        <span>更多筛选</span>
        {activeCount > 0 && (
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[11px] font-semibold tabular-nums text-primary-foreground">
            {activeCount}
          </span>
        )}
      </button>

      {open && (
        <div
          className={cn(
            'absolute z-40 mt-1.5 w-80 rounded-lg border border-border bg-popover p-3.5 text-popover-foreground shadow-lg',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          <div className="space-y-3">{children}</div>
        </div>
      )}
    </div>
  );
}

interface FilterChipsProps {
  chips: ActiveFilterChip[];
  onResetAll: () => void;
  matchSummary?: string;
  testId?: string;
}

/**
 * 已生效筛选条件条：展示可单独移除的 Chip + 一键清空全部 + 当前命中计数。
 */
export function FilterChips({
  chips,
  onResetAll,
  matchSummary,
  testId,
}: FilterChipsProps) {
  if (chips.length === 0 && !matchSummary) return null;

  return (
    <div
      data-testid={testId}
      className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border/70 bg-muted/20 px-3 py-2 text-xs"
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {chips.length === 0 ? (
          <span className="text-muted-foreground">当前显示全部数据</span>
        ) : (
          <>
            <span className="mr-1 text-muted-foreground">已选筛选:</span>
            {chips.map((chip) => (
              <span
                key={chip.key}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-0.5 text-xs text-foreground"
              >
                <span className="text-muted-foreground">{chip.label}:</span>
                <span className="font-medium">{chip.value}</span>
                <button
                  type="button"
                  aria-label={`移除${chip.label}筛选`}
                  onClick={chip.onRemove}
                  className="ml-0.5 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
            <button
              type="button"
              onClick={onResetAll}
              className="ml-1 inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/10"
            >
              <RotateCcw className="h-3 w-3" />
              清空全部
            </button>
          </>
        )}
      </div>

      {matchSummary && (
        <span className="shrink-0 tabular-nums text-muted-foreground">
          {matchSummary}
        </span>
      )}
    </div>
  );
}
