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

export interface QuickViewOption {
  key: string;
  label: string;
  count?: number;
  active: boolean;
  tone?: 'default' | 'warning' | 'danger' | 'success' | 'primary';
  onClick: () => void;
}

interface FilterWorkbenchProps {
  quickViews?: QuickViewOption[];
  headerLeft?: ReactNode;
  headerRight?: ReactNode;
  children: ReactNode;
  /** 仅展示收纳在「更多筛选」中的隐藏条件，避免与外露控件重复显示。 */
  hiddenChips?: ActiveFilterChip[];
  hasAnyActiveFilter?: boolean;
  onResetAll: () => void;
  matchSummary?: string;
  chipsTestId?: string;
}

const QUICK_VIEW_ACTIVE_TONE: Record<NonNullable<QuickViewOption['tone']>, string> = {
  default: 'bg-background text-foreground shadow-xs border-border/80',
  primary: 'bg-primary/10 text-primary border-primary/30 shadow-xs',
  success: 'bg-success/10 text-success border-success/30 shadow-xs',
  warning: 'bg-warning/15 text-warning border-warning/30 shadow-xs',
  danger: 'bg-destructive/10 text-destructive border-destructive/30 shadow-xs',
};

/**
 * 统一筛选工作台容器：
 * - 顶部：快捷视图分段条（或自定义 headerLeft） + 命中统计 + 页级主动作（headerRight）
 * - 中部：主筛选控件行（搜索 + 维度下拉 + 更多筛选 + 重置按钮）
 * - 底部：仅在「更多筛选」有隐藏条件生效时展开，避免与外露控件重复显示
 */
export function FilterWorkbench({
  quickViews,
  headerLeft,
  headerRight,
  children,
  hiddenChips = [],
  hasAnyActiveFilter = false,
  onResetAll,
  matchSummary,
  chipsTestId,
}: FilterWorkbenchProps) {
  const hasTopRow = Boolean(
    (quickViews && quickViews.length > 0) || headerLeft || headerRight || matchSummary,
  );

  return (
    <div className="rounded-xl border border-border/90 bg-card shadow-xs">
      {hasTopRow && (
        <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-border/60 px-3.5 py-2.5">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            {quickViews && quickViews.length > 0 ? (
              <div
                role="tablist"
                aria-label="快捷视图"
                className="inline-flex flex-wrap items-center gap-1 rounded-lg bg-muted/60 p-1"
              >
                {quickViews.map((qv) => (
                  <button
                    key={qv.key}
                    type="button"
                    role="tab"
                    aria-selected={qv.active}
                    onClick={qv.onClick}
                    className={cn(
                      'inline-flex items-center gap-1.5 rounded-md border border-transparent px-2.5 py-1 text-xs font-medium transition-all',
                      qv.active
                        ? QUICK_VIEW_ACTIVE_TONE[qv.tone ?? 'default']
                        : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    <span>{qv.label}</span>
                    {qv.count != null && (
                      <span
                        className={cn(
                          'rounded px-1.5 py-0.2 text-[11px] tabular-nums',
                          qv.active
                            ? 'bg-background/80 text-current'
                            : 'bg-background/60 text-muted-foreground',
                        )}
                      >
                        {qv.count}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            ) : (
              headerLeft
            )}
          </div>

          <div className="flex shrink-0 items-center gap-2.5">
            {matchSummary && (
              <span className="inline-flex items-center rounded-full bg-muted/70 px-2.5 py-1 text-xs font-medium tabular-nums text-muted-foreground">
                {matchSummary}
              </span>
            )}
            {hasAnyActiveFilter && (
              <button
                type="button"
                onClick={onResetAll}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <RotateCcw className="h-3 w-3" />
                重置筛选
              </button>
            )}
            {headerRight}
          </div>
        </div>
      )}

      <div className="p-3.5">{children}</div>

      {hiddenChips.length > 0 && (
        <div
          data-testid={chipsTestId}
          className="flex flex-wrap items-center gap-1.5 rounded-b-xl border-t border-border/60 bg-muted/20 px-3.5 py-2 text-xs"
        >
          <span className="mr-0.5 text-[11px] font-medium text-muted-foreground">
            高级筛选:
          </span>
          {hiddenChips.map((chip) => (
            <span
              key={chip.key}
              className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary/10 px-2.5 py-0.5 text-xs text-primary"
            >
              <span className="opacity-75">{chip.label}:</span>
              <span className="font-medium">{chip.value}</span>
              <button
                type="button"
                aria-label={`移除${chip.label}筛选`}
                onClick={chip.onRemove}
                className="ml-0.5 rounded-full p-0.5 text-primary/70 hover:bg-primary/15 hover:text-primary"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
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
        className={cn(
          FORM.inputSm,
          'h-9 w-full rounded-lg bg-muted/30 pl-9 pr-8 text-xs sm:text-sm transition-colors focus:bg-background',
        )}
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
          <kbd className="pointer-events-none absolute right-2.5 top-1/2 hidden -translate-y-1/2 rounded border border-border bg-background px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline-block">
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
      const target = event.target as HTMLElement | null;
      if (target?.closest?.('[role="listbox"]')) return;
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('[role="listbox"]')) {
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
          'inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-xs sm:text-sm transition-all',
          'hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25',
          activeCount > 0
            ? 'border-primary/40 bg-primary/5 font-medium text-primary'
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
            'absolute z-40 mt-1.5 w-80 rounded-xl border border-border bg-popover p-4 text-popover-foreground shadow-lg ring-1 ring-border/50',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          <div className="mb-2.5 flex items-center justify-between border-b border-border/60 pb-2">
            <span className="text-xs font-semibold text-foreground">高级筛选条件</span>
            <span className="text-[11px] text-muted-foreground">Esc 关闭</span>
          </div>
          <div className="space-y-3">{children}</div>
        </div>
      )}
    </div>
  );
}
