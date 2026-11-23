import { type ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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

interface MenuCoords {
  top: number;
  left: number;
  minWidth: number;
  openUpward: boolean;
}

const ESTIMATED_MENU_HEIGHT = 296;
const VIEWPORT_MARGIN = 8;

/**
 * 统一质感单选下拉（D1 筛选栏与动态/长列表标准控件）：
 * - Portal 浮层定位，防止被 `overflow-hidden` 容器裁切；
 * - 完整键盘导航（ArrowDown / ArrowUp / Home / End / Enter / Escape 归位焦点）；
 * - 图标前缀、输入过滤、一键清除。
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
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [menuCoords, setMenuCoords] = useState<MenuCoords | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
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

  const navigableOptions = useMemo<SearchableSelectOption[]>(() => {
    if (!query.trim()) {
      return [{ value: allValue, label: allLabel }, ...filteredOptions];
    }
    return filteredOptions;
  }, [allLabel, allValue, filteredOptions, query]);

  const clampedHighlight =
    navigableOptions.length === 0
      ? -1
      : Math.min(Math.max(highlightedIndex, 0), navigableOptions.length - 1);

  const updateMenuPosition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const menuWidth = Math.max(256, rect.width);
    const spaceBelow = window.innerHeight - rect.bottom;
    const openUpward = spaceBelow < ESTIMATED_MENU_HEIGHT && rect.top > spaceBelow;
    const maxLeft = Math.max(VIEWPORT_MARGIN, window.innerWidth - menuWidth - VIEWPORT_MARGIN);
    const left = Math.min(Math.max(VIEWPORT_MARGIN, rect.left), maxLeft);
    setMenuCoords({
      top: openUpward ? Math.max(VIEWPORT_MARGIN, rect.top - 6) : rect.bottom + 6,
      left,
      minWidth: Math.max(256, Math.round(rect.width)),
      openUpward,
    });
  }, []);

  const closeMenu = useCallback((restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) {
      requestAnimationFrame(() => triggerRef.current?.focus());
    }
  }, []);

  const selectValue = useCallback(
    (nextValue: string) => {
      onChange(nextValue);
      closeMenu(true);
    },
    [closeMenu, onChange],
  );

  useLayoutEffect(() => {
    if (!open) return;
    updateMenuPosition();
  }, [open, updateMenuPosition]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        !containerRef.current?.contains(target) &&
        !menuRef.current?.contains(target)
      ) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeMenu(true);
      }
    };
    const handleReposition = () => updateMenuPosition();

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    window.addEventListener('resize', handleReposition);
    window.addEventListener('scroll', handleReposition, true);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
      window.removeEventListener('resize', handleReposition);
      window.removeEventListener('scroll', handleReposition, true);
    };
  }, [open, closeMenu, updateMenuPosition]);

  useEffect(() => {
    if (open && searchable) {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open, searchable]);

  const handleNavigationKey = (event: React.KeyboardEvent) => {
    if (!open) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        setQuery('');
        const initialIdx = Math.max(0, navigableOptions.findIndex((opt) => opt.value === value));
        setHighlightedIndex(initialIdx);
        setOpen(true);
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (navigableOptions.length > 0) {
        setHighlightedIndex((prev) => (prev + 1) % navigableOptions.length);
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (navigableOptions.length > 0) {
        setHighlightedIndex((prev) => (prev - 1 + navigableOptions.length) % navigableOptions.length);
      }
    } else if (event.key === 'Home') {
      event.preventDefault();
      setHighlightedIndex(0);
    } else if (event.key === 'End') {
      event.preventDefault();
      if (navigableOptions.length > 0) {
        setHighlightedIndex(navigableOptions.length - 1);
      }
    } else if (event.key === 'Enter') {
      if (clampedHighlight >= 0 && navigableOptions[clampedHighlight]) {
        event.preventDefault();
        selectValue(navigableOptions[clampedHighlight].value);
      }
    }
  };

  const menuContent = open ? (
    <div
      ref={menuRef}
      role="listbox"
      aria-label={ariaLabel}
      onKeyDown={handleNavigationKey}
      style={
        menuCoords
          ? {
              position: 'fixed',
              top: menuCoords.top,
              left: menuCoords.left,
              minWidth: menuCoords.minWidth,
              transform: menuCoords.openUpward ? 'translateY(-100%)' : undefined,
            }
          : undefined
      }
      className={cn(
        'z-50 w-64 rounded-xl border border-border bg-popover p-1.5 text-popover-foreground shadow-lg ring-1 ring-border/50',
        !menuCoords && 'absolute left-0 mt-1.5 min-w-full',
      )}
    >
      {searchable && (
        <div className="relative mb-1.5">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setHighlightedIndex(0);
            }}
            onKeyDown={handleNavigationKey}
            placeholder={searchPlaceholder}
            className="h-8 w-full rounded-lg border border-input bg-muted/30 pl-8 pr-7 text-xs focus:bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
          {query && (
            <button
              type="button"
              aria-label="清空选项搜索"
              onClick={() => {
                setQuery('');
                setHighlightedIndex(0);
              }}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      )}

      <div className="max-h-60 space-y-0.5 overflow-y-auto">
        {navigableOptions.length === 0 ? (
          <div className="px-2 py-4 text-center text-xs text-muted-foreground">
            无匹配选项
          </div>
        ) : (
          navigableOptions.map((opt, idx) => {
            const selected = opt.value === value;
            const highlighted = idx === clampedHighlight;
            return (
              <button
                key={opt.value}
                type="button"
                role="option"
                aria-selected={selected}
                data-highlighted={highlighted ? 'true' : undefined}
                onMouseEnter={() => setHighlightedIndex(idx)}
                onClick={() => selectValue(opt.value)}
                className={cn(
                  'flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors',
                  selected
                    ? 'bg-primary/10 font-medium text-primary'
                    : highlighted
                      ? 'bg-muted/80 text-foreground'
                      : 'text-foreground hover:bg-muted/60',
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
  ) : null;

  return (
    <div ref={containerRef} className={cn('relative inline-block', className)}>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        data-testid={testId}
        onKeyDown={handleNavigationKey}
        onClick={() => {
          const next = !open;
          if (next) {
            setQuery('');
            const initialIdx = Math.max(0, options.findIndex((opt) => opt.value === value) + 1);
            setHighlightedIndex(value === allValue ? 0 : initialIdx);
          }
          setOpen(next);
        }}
        className={cn(
          'flex h-9 w-full items-center justify-between gap-1.5 rounded-lg border px-2.5 text-xs sm:text-sm transition-all',
          'hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/25',
          isFiltered
            ? 'border-primary/40 bg-primary/5 font-medium text-primary'
            : 'border-input bg-background text-foreground',
        )}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          {icon && (
            <span
              className={cn(
                'shrink-0 [&_svg]:h-3.5 [&_svg]:w-3.5',
                isFiltered ? 'text-primary' : 'text-muted-foreground',
              )}
            >
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
          <ChevronDown
            className={cn('h-3.5 w-3.5 opacity-70 transition-transform', open && 'rotate-180')}
          />
        </span>
      </button>

      {menuContent && (typeof document !== 'undefined' ? createPortal(menuContent, document.body) : menuContent)}
    </div>
  );
}
