import { useMemo, useState } from 'react';
import { LayoutTemplate, Loader2, Search, AlertTriangle } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorState } from '@/components/ui/error-state';
import { FORM, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { PipelineTemplate, Specialty } from '@/utils/api/types';

interface TemplatePickerDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  templates: PipelineTemplate[];
  isLoading?: boolean;
  isError?: boolean;
  errorMessage?: string;
  onRetry?: () => void;
  specialties?: Specialty[];
  initialSpecialtyKey?: string;
  onSelectTemplate: (template: PipelineTemplate) => void;
}

function countTemplateSteps(template: PipelineTemplate) {
  const lc = template.pipeline_def?.lifecycle;
  return {
    init: lc?.init?.length ?? 0,
    patrol: lc?.patrol?.steps?.length ?? 0,
    teardown: lc?.teardown?.length ?? 0,
  };
}

export default function TemplatePickerDialog({
  open,
  onOpenChange,
  templates,
  isLoading = false,
  isError = false,
  errorMessage,
  onRetry,
  specialties,
  initialSpecialtyKey,
  onSelectTemplate,
}: TemplatePickerDialogProps) {
  const [selectedSpecialty, setSelectedSpecialty] = useState<string>(initialSpecialtyKey || 'all');
  const [keyword, setKeyword] = useState('');
  const [prevOpen, setPrevOpen] = useState(open);

  if (open !== prevOpen) {
    setPrevOpen(open);
    if (open) {
      setSelectedSpecialty(initialSpecialtyKey || 'all');
      setKeyword('');
    }
  }

  const specialtyLabel = useMemo(() => {
    const map = new Map((specialties ?? []).map((s) => [s.key, s.display_name]));
    return (key: string) => map.get(key) ?? key;
  }, [specialties]);

  const specialtyTabs = useMemo(() => {
    const present = new Set(templates.map((t) => t.specialty_key));
    const ordered: Array<{ key: string; label: string }> = [];
    for (const s of specialties ?? []) {
      if (present.has(s.key)) {
        ordered.push({ key: s.key, label: s.display_name });
        present.delete(s.key);
      }
    }
    for (const remaining of Array.from(present).sort()) {
      ordered.push({ key: remaining, label: remaining });
    }
    return ordered;
  }, [templates, specialties]);

  const filteredTemplates = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    return templates.filter((tpl) => {
      if (selectedSpecialty !== 'all' && tpl.specialty_key !== selectedSpecialty) {
        return false;
      }
      if (!q) return true;
      return (
        tpl.display_name.toLowerCase().includes(q) ||
        tpl.name.toLowerCase().includes(q) ||
        tpl.description.toLowerCase().includes(q) ||
        specialtyLabel(tpl.specialty_key).toLowerCase().includes(q)
      );
    });
  }, [templates, selectedSpecialty, keyword, specialtyLabel]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <LayoutTemplate className="h-4 w-4 text-primary" />
            从模板创建 Plan
          </DialogTitle>
          <DialogDescription>
            选择专项模板后将自动展开标准步骤、脚本版本与默认时序到草稿中，可继续修改参数后再保存。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索模板名称、标识或说明..."
              aria-label="搜索模板"
              data-testid="template-picker-search"
              className={cn(FORM.input, 'h-8 pl-8 text-xs')}
            />
          </div>

          {specialtyTabs.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="按专项筛选模板">
              <button
                type="button"
                data-testid="template-filter-all"
                onClick={() => setSelectedSpecialty('all')}
                className={cn(
                  'rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors',
                  selectedSpecialty === 'all'
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground',
                )}
              >
                全部（{templates.length}）
              </button>
              {specialtyTabs.map((tab) => {
                const count = templates.filter((t) => t.specialty_key === tab.key).length;
                const active = selectedSpecialty === tab.key;
                return (
                  <button
                    key={tab.key}
                    type="button"
                    data-testid={`template-filter-${tab.key}`}
                    onClick={() => setSelectedSpecialty(tab.key)}
                    className={cn(
                      'rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors',
                      active
                        ? 'border-primary bg-primary text-primary-foreground'
                        : 'border-border bg-background text-muted-foreground hover:bg-muted hover:text-foreground',
                    )}
                  >
                    {tab.label}（{count}）
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="mt-2 min-h-0 flex-1 overflow-y-auto pr-1 custom-scrollbar">
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-xs text-muted-foreground">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              正在加载模板...
            </div>
          ) : isError ? (
            <ErrorState
              title="加载模板失败"
              description={errorMessage || '请检查网络连接或稍后重试'}
              onRetry={onRetry}
            />
          ) : filteredTemplates.length === 0 ? (
            <div className="rounded-md border border-dashed border-border py-10 text-center text-xs text-muted-foreground">
              没有匹配的模板
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              {filteredTemplates.map((tpl) => {
                const counts = countTemplateSteps(tpl);
                const watcherEnabled = Boolean(
                  tpl.plan_defaults?.watcher_policy &&
                    (tpl.plan_defaults.watcher_policy as Record<string, unknown>).enabled === true,
                );
                return (
                  <button
                    key={tpl.name}
                    type="button"
                    data-testid={`template-card-${tpl.name}`}
                    onClick={() => {
                      onSelectTemplate(tpl);
                      onOpenChange(false);
                    }}
                    className="group flex flex-col gap-1.5 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-primary/60 hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className={cn('truncate text-sm font-semibold group-hover:text-primary', TEXT.heading)}>
                          {tpl.display_name}
                        </div>
                        <div className={cn('truncate font-mono text-[11px]', TEXT.caption)}>
                          {tpl.name}
                        </div>
                      </div>
                      <Badge variant="default" className="shrink-0 text-[11px] px-1.5 py-0">
                        {specialtyLabel(tpl.specialty_key)}
                      </Badge>
                    </div>

                    <p className={cn('line-clamp-2 text-xs leading-relaxed', TEXT.subtitle)}>
                      {tpl.description || '无描述'}
                    </p>

                    <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-1 text-[11px]">
                      <span className={cn('font-mono', TEXT.caption)}>
                        Init {counts.init} · Patrol {counts.patrol} · Teardown {counts.teardown}
                      </span>
                      {tpl.requires_suite && (
                        <Badge variant="info" className="text-[10px] px-1.5 py-0">
                          需绑定套件
                        </Badge>
                      )}
                      {watcherEnabled && (
                        <Badge variant="info" className="text-[10px] px-1.5 py-0">
                          默认开启 Watcher
                        </Badge>
                      )}
                      {tpl.status === 'skeleton' && (
                        <Badge variant="warning" className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0">
                          <AlertTriangle className="h-2.5 w-2.5" />
                          骨架模板
                        </Badge>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="flex justify-end pt-2 border-t border-border">
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
