import { useMemo } from 'react';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/ui/error-state';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDurationSeconds } from '@/utils/format';
import type { Plan, PlanRun } from '@/utils/api';
import { AlertCircle } from 'lucide-react';
import { groupPlansForSelect } from './planExecutePlanOptions';
import { PlanStepList } from './PlanStepList';
import { RecentPlanRunsInline } from './RecentPlanRunsInline';

interface PlanSelectPhaseProps {
  plans: Plan[];
  recentExecutedRuns: PlanRun[];
  plansLoading: boolean;
  plansError: boolean;
  plansErrorMessage?: string;
  onRetryPlans: () => void;
  planSearch: string;
  onPlanSearchChange: (value: string) => void;
  selectedPlanId: number | null;
  onSelectPlan: (planId: number) => void;
  selectedPlan: Plan | null | undefined;
  executableStepCount: number;
  scriptParamsByKey: Map<string, Record<string, unknown>>;
  recentPlanRuns: PlanRun[];
  recentPlanRunsLoading: boolean;
  onOpenRun: (runId: number) => void;
  formatFailureThreshold: (threshold: number | null | undefined) => string;
}

function PlanListButton({
  plan,
  active,
  onSelect,
}: {
  plan: Plan;
  active: boolean;
  onSelect: (planId: number) => void;
}) {
  const steps = plan.steps?.length ?? 0;
  return (
    <button
      type="button"
      onClick={() => onSelect(plan.id)}
      className={cn(
        'w-full rounded-lg border px-3 py-2.5 text-left transition-colors',
        active ? 'border-primary/40 bg-primary/10' : 'border-transparent hover:bg-muted',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold">{plan.name}</span>
        <span className={cn('shrink-0 text-xs', TEXT.subtitle)}>#{plan.id}</span>
      </div>
      <div className={cn('mt-1 text-xs', TEXT.subtitle)}>
        {steps} 步 · 巡检{' '}
        {formatDurationSeconds(plan.patrol_interval_seconds, 'compact', '—')}
        {' · 超时 '}
        {formatDurationSeconds(plan.timeout_seconds, 'compact', '—')}
      </div>
    </button>
  );
}

/** 态 0：左 Plan 列表 + 右详情（对齐 mockup 00-plan-select）。 */
export function PlanSelectPhase({
  plans,
  recentExecutedRuns,
  plansLoading,
  plansError,
  plansErrorMessage,
  onRetryPlans,
  planSearch,
  onPlanSearchChange,
  selectedPlanId,
  onSelectPlan,
  selectedPlan,
  executableStepCount,
  scriptParamsByKey,
  recentPlanRuns,
  recentPlanRunsLoading,
  onOpenRun,
  formatFailureThreshold,
}: PlanSelectPhaseProps) {
  const { recent, all } = useMemo(
    () => groupPlansForSelect(plans, recentExecutedRuns, planSearch, 5),
    [plans, recentExecutedRuns, planSearch],
  );
  const hasPlans = recent.length > 0 || all.length > 0;

  return (
    <div
      className="grid min-h-[min(70vh,720px)] flex-1 gap-3 lg:grid-cols-[minmax(280px,1.1fr)_minmax(0,0.9fr)]"
      data-testid="plan-execute-plan-layout"
    >
      <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm">
        <div className="shrink-0 border-b px-3 py-2.5 text-sm font-semibold">选择测试计划</div>
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3">
          {plansLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : plansError ? (
            <ErrorState
              title="加载 Plan 失败"
              description={plansErrorMessage || '请检查网络连接或稍后重试'}
              onRetry={onRetryPlans}
            />
          ) : (
            <>
              <Input
                value={planSearch}
                onChange={(event) => onPlanSearchChange(event.target.value)}
                placeholder="搜索 Plan 名称…"
                className="h-9 shrink-0"
              />
              <div className="min-h-0 flex-1 space-y-1 overflow-auto">
                {!hasPlans ? (
                  <div className={cn('px-3 py-8 text-center text-sm', TEXT.subtitle)}>无匹配 Plan</div>
                ) : (
                  <>
                    {recent.length > 0 && (
                      <div className="space-y-1" data-testid="plan-select-recent-group">
                        <div className={cn('px-2 pb-0.5 text-[11px] font-medium', TEXT.subtitle)}>
                          最近执行
                        </div>
                        {recent.map((plan) => (
                          <PlanListButton
                            key={plan.id}
                            plan={plan}
                            active={selectedPlanId === plan.id}
                            onSelect={onSelectPlan}
                          />
                        ))}
                      </div>
                    )}
                    {all.length > 0 && (
                      <div className="space-y-1" data-testid="plan-select-all-group">
                        {recent.length > 0 && (
                          <div className={cn('px-2 pt-2 pb-0.5 text-[11px] font-medium', TEXT.subtitle)}>
                            全部 Plan
                          </div>
                        )}
                        {all.map((plan) => (
                          <PlanListButton
                            key={plan.id}
                            plan={plan}
                            active={selectedPlanId === plan.id}
                            onSelect={onSelectPlan}
                          />
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm">
        <div className="flex shrink-0 items-center justify-between gap-2 border-b px-3 py-2.5">
          <span className="text-sm font-semibold">Plan 详情</span>
          {selectedPlan ? (
            <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium">
              {executableStepCount} 启用 / {selectedPlan.steps?.length ?? 0}
            </span>
          ) : (
            <span className={cn('text-xs', TEXT.subtitle)}>选中后展示</span>
          )}
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
          {!selectedPlan ? (
            <div className={cn('flex h-full min-h-[200px] items-center justify-center text-sm', TEXT.subtitle)}>
              从左侧选择一个 Plan
            </div>
          ) : (
            <>
              {selectedPlan.description && (
                <p className={cn('text-sm', TEXT.subtitle)}>{selectedPlan.description}</p>
              )}
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg bg-muted/50 p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>失败阈值</div>
                  <div className="mt-1 font-semibold">
                    {formatFailureThreshold(selectedPlan.failure_threshold)}
                  </div>
                </div>
                <div className="rounded-lg bg-muted/50 p-3">
                  <div className={cn('text-xs', TEXT.subtitle)}>启用步骤</div>
                  <div className="mt-1 font-semibold">
                    {executableStepCount} / {selectedPlan.steps?.length ?? 0}
                  </div>
                </div>
              </div>
              <div className={cn('flex flex-wrap gap-x-5 gap-y-1 text-xs', TEXT.subtitle)}>
                <span>
                  巡检周期：
                  {formatDurationSeconds(selectedPlan.patrol_interval_seconds, 'precise', '未设置')}
                </span>
                <span>
                  超时：{formatDurationSeconds(selectedPlan.timeout_seconds, 'precise', '未设置')}
                </span>
              </div>
              <div className="rounded-lg border p-3">
                <RecentPlanRunsInline
                  runs={recentPlanRuns}
                  loading={recentPlanRunsLoading}
                  onOpenRun={onOpenRun}
                />
              </div>
              {executableStepCount === 0 && (
                <div
                  className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${STATUS_BG_COLORS.warning}`}
                >
                  <AlertCircle className="h-4 w-4" /> 此 Plan 没有已启用步骤，无法执行
                </div>
              )}
              <div className="overflow-hidden rounded-lg border">
                <PlanStepList steps={selectedPlan.steps} scriptParamsByKey={scriptParamsByKey} />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
