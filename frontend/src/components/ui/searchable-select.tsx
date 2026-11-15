import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, Search, X } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface SearchableSelectOption {
  value: string;
  label: string;
  count?: number;
}

interface SearchableSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SearchableSelectOption[];
  allValue?: string;
  allLabel?: string;
  searchPlaceholder?: string;
  searchable?: boolean;
  icon?: ReactNode;
  ariaLabel?: string;
  className?: string;
  testId?: string;
}

/**
 * 统一质感单选下拉：支持图标前缀、输入过滤（长列表）、一键清除与 Esc 关闭。
 */
export function SearchableSelect({
  value,
  onChange,
  options,
  allValue = 'all',
  allLabel = '全部',
  searchPlaceholder = '输入关键字过滤...',
  searchable = true,
  icon,
  ariaLabel,
  className,
  testId,
}: SearchableSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedOption = useMemo(
    () => options.find((opt) => opt.value === value),
    [options, value],
  );
  const isFiltered = value !== allValue;
  const displayLabel = isFiltered ? (selectedOption?.label ?? value) : allLabel;

  const filteredOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (opt) =>
        opt.label.toLowerCase().includes(q) ||
        opt.value.toLowerCase().includes(q),
    );
  }, [options, query]);

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

  useEffect(() => {
    if (open && searchable) {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open, searchable]);

  return (
    <div ref={containerRef} className={cn('relative inline-block', className)}>
      <button
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        data-testid={testId}
        onClick={() => {
          const next = !open;
          if (next) setQuery('');
          setOpen(next);
        }}
        className={cn(
          'flex h-9 w-full items-center justify-between gap-1.5 rounded-lg border px-2.5 text-xs sm:text-sm transition-all',
          'hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25',
          isFiltered
            ? 'border-primary/40 bg-primary/5 text-primary font-medium'
            : 'border-input bg-background text-foreground',
        )}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          {icon && (
            <span className={cn('shrink-0 [&_svg]:h-3.5 [&_svg]:w-3.5', isFiltered ? 'text-primary' : 'text-muted-foreground')}>
              {icon}
            </span>
          )}
          <span className="truncate text-left">{displayLabel}</span>
        </span>
        <span className="flex shrink-0 items-center gap-0.5 text-muted-foreground">
          {isFiltered && (
            <span
              role="button"
              tabIndex={-1}
              aria-label={`清除${ariaLabel ?? '筛选'}`}
              onClick={(e) => {
                e.stopPropagation();
                onChange(allValue);
                setOpen(false);
              }}
              className="rounded p-0.5 text-primary/70 hover:bg-primary/10 hover:text-primary"
            >
              <X className="h-3.5 w-3.5" />
            </span>
          )}
          <ChevronDown className={cn('h-3.5 w-3.5 opacity-70 transition-transform', open && 'rotate-180')} />
        </span>
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute left-0 z-50 mt-1.5 w-64 min-w-full rounded-xl border border-border bg-popover p-1.5 text-popover-foreground shadow-lg ring-1 ring-black/5"
        >
          {searchable && (
            <div className="relative mb-1.5">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                className="h-8 w-full rounded-lg border border-input bg-muted/30 pl-8 pr-7 text-xs focus:bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
              />
              {query && (
                <button
                  type="button"
                  aria-label="清空选项搜索"
                  onClick={() => setQuery('')}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
          )}

          <div className="max-h-60 overflow-y-auto space-y-0.5">
            {!query.trim() && (
              <button
                type="button"
                role="option"
                aria-selected={value === allValue}
                onClick={() => {
                  onChange(allValue);
                  setOpen(false);
                }}
                className={cn(
                  'flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors',
                  value === allValue
                    ? 'bg-primary/10 font-medium text-primary'
                    : 'hover:bg-muted/60 text-foreground',
                )}
              >
                <span className="truncate">{allLabel}</span>
                {value === allValue && <Check className="h-3.5 w-3.5 shrink-0 text-primary" />}
              </button>
            )}

            {filteredOptions.length === 0 ? (
              <div className="px-2 py-4 text-center text-xs text-muted-foreground">
                无匹配选项
              </div>
            ) : (
              filteredOptions.map((opt) => {
                const selected = opt.value === value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => {
                      onChange(opt.value);
                      setOpen(false);
                    }}
                    className={cn(
                      'flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors',
                      selected
                        ? 'bg-primary/10 font-medium text-primary'
                        : 'hover:bg-muted/60 text-foreground',
                    )}
                  >
                    <span className="truncate">{opt.label}</span>
                    <span className="flex shrink-0 items-center gap-1.5">
                      {opt.count != null && (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground">
                          {opt.count}
                        </span>
                      )}
                      {selected && <Check className="h-3.5 w-3.5 text-primary" />}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
