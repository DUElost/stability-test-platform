import { useMemo, useState } from 'react';
import { ChevronDown, Search, X } from 'lucide-react';
import type { Plan } from '@/utils/api/types';
import { FORM, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

interface PlanSelectProps {
  plans: Plan[];
  /** '' = 未选择（与表单 plan_id 的 string 形态一致） */
  selectedId: string;
  onChange: (id: string) => void;
  loading?: boolean;
  /** 供外层 <label htmlFor> 关联 */
  id?: string;
}

/**
 * Plan 单选（定时任务表单）——#627：替代原生 <select>，按 name / id 过滤；
 * 选中后 chip 呈现并可单独清除。数据由父级传入（复用页面 plansQ 缓存，
 * 避免组件内二次请求与两份 loading 语义）。
 */
export function PlanSelect({
  plans,
  selectedId,
  onChange,
  loading = false,
  id,
}: PlanSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');

  const selected = useMemo(
    () => plans.find((p) => String(p.id) === selectedId) ?? null,
    [plans, selectedId],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return plans;
    return plans.filter(
      (p) => p.name.toLowerCase().includes(q) || String(p.id).includes(q),
    );
  }, [plans, search]);

  const pick = (plan: Plan) => {
    onChange(String(plan.id));
    setOpen(false);
    setSearch('');
  };

  return (
    <div data-testid="plan-select">
      {selected && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
            {selected.name} (#{selected.id})
            <button
              type="button"
              onClick={() => onChange('')}
              aria-label={`清除已选 Plan ${selected.name}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        </div>
      )}

      <button
        type="button"
        id={id}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(FORM.input, 'flex items-center justify-between text-left')}
      >
        <span className={selected ? '' : 'text-muted-foreground'}>
          {selected ? `${selected.name} (#${selected.id})` : '点击选择 Plan…'}
        </span>
        <ChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="mt-1 rounded-md border border-border bg-background">
          <div className="relative border-b border-border p-2">
            <Search className={cn('absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2', TEXT.subtitle)} />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索 Plan 名称 / ID…"
              aria-label="搜索 Plan"
              className={cn(FORM.input, 'py-1.5 pl-8 text-xs')}
            />
          </div>
          <div className="max-h-48 overflow-y-auto p-1">
            {loading ? (
              <p className={cn('px-3 py-2 text-xs', TEXT.subtitle)}>Plan 列表加载中…</p>
            ) : filtered.length === 0 ? (
              <p className={cn('px-3 py-2 text-xs', TEXT.subtitle)}>
                {plans.length === 0 ? '暂无 Plan' : '无匹配 Plan'}
              </p>
            ) : (
              filtered.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => pick(p)}
                  aria-label={`选择 Plan ${p.name}`}
                  className={cn(
                    'flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-accent',
                    String(p.id) === selectedId && 'bg-accent',
                  )}
                >
                  <span>{p.name}</span>
                  <span className={cn('ml-auto', TEXT.subtitle)}>#{p.id}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
