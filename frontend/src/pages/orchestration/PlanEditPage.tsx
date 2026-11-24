import { useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Loader2, ArrowLeft, AlertCircle, GitBranch, PanelLeftOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import PlanChainPanel from '@/components/pipeline/PlanChainPanel';
import PlanCanvas from '@/components/pipeline/PlanCanvas';
import PlanStepInspector from '@/components/pipeline/PlanStepInspector';
import { SURFACE, TEXT, FORM, ALERT_BANNER } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { PageContainer } from '@/components/layout';
import { usePlanEditForm } from './usePlanEditForm';
import { usePlanEditHeaderSlot } from './usePlanEditHeaderSlot';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { ErrorState } from '@/components/ui/error-state';
import type { PipelinePhase } from '@/utils/api/types';

export default function PlanEditPage() {
  const { id } = useParams<{ id: string }>();
  // D8：`Number(id) > 0` 对 `/plans/abc` 得到 NaN，比较为假 → 静默当成「新建」，
  // 用户以为在编辑某个 Plan，实际打开的是空白表单。与 PlanRunDetailPage 对齐：
  // 先把非法 id 单独判出来报错，而不是落进新建分支。
  const parsedPlanId = id != null && id !== 'new' ? Number(id) : Number.NaN;
  const invalidPlanId = id != null && id !== 'new' && !Number.isFinite(parsedPlanId);
  const planId = invalidPlanId || !Number.isFinite(parsedPlanId) || parsedPlanId <= 0
    ? null
    : parsedPlanId;
  const navigate = useNavigate();

  const form = usePlanEditForm(planId);
  const [chainPanelOverride, setChainPanelOverride] = useState<{
    planId: number | null;
    open: boolean;
  } | null>(null);

  const hasMultiPlanChain = useMemo(() => {
    if (planId == null || !form.allPlans || form.allPlans.length === 0) return false;
    const current = form.allPlans.find((p) => p.id === planId);
    if (current?.next_plan_id != null) return true;
    return form.allPlans.some((p) => p.next_plan_id === planId);
  }, [form.allPlans, planId]);

  const chainPanelOpen =
    chainPanelOverride && chainPanelOverride.planId === planId
      ? chainPanelOverride.open
      : hasMultiPlanChain;

  const setChainPanelOpen = (open: boolean) => {
    setChainPanelOverride({ planId, open });
  };

  const orderedSteps = useMemo(() => {
    const list: Array<{ key: string; phase: PipelinePhase; index: number }> = [];
    (form.lifecycle.lifecycle.init ?? []).forEach((s, idx) => {
      if (s.step_id) list.push({ key: s.step_id, phase: 'init', index: idx });
    });
    (form.lifecycle.lifecycle.patrol?.steps ?? []).forEach((s, idx) => {
      if (s.step_id) list.push({ key: s.step_id, phase: 'patrol', index: idx });
    });
    (form.lifecycle.lifecycle.teardown ?? []).forEach((s, idx) => {
      if (s.step_id) list.push({ key: s.step_id, phase: 'teardown', index: idx });
    });
    return list;
  }, [form.lifecycle]);

  const currentStepIdx = useMemo(
    () => (form.selectedStepKey ? orderedSteps.findIndex((item) => item.key === form.selectedStepKey) : -1),
    [orderedSteps, form.selectedStepKey],
  );

  const handlePrevStep =
    currentStepIdx > 0
      ? () => form.setSelectedStepKey(orderedSteps[currentStepIdx - 1].key)
      : undefined;
  const handleNextStep =
    currentStepIdx >= 0 && currentStepIdx < orderedSteps.length - 1
      ? () => form.setSelectedStepKey(orderedSteps[currentStepIdx + 1].key)
      : undefined;

  const headerReady = !form.planLoading && !form.planIsError && !form.dependenciesIsError;
  usePlanEditHeaderSlot(form, headerReady);
  useDocumentTitle(form.name || (form.isNew ? '新建 Plan' : '编辑 Plan'));

  if (invalidPlanId) {
    return (
      <PageContainer width="content" className="space-y-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/orchestration/plans')}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回 Plan 列表
        </Button>
        <ErrorState
          title="无效的 Plan ID"
          description={`URL 中的 Plan ID「${id}」不是合法数字，无法编辑。`}
        />
      </PageContainer>
    );
  }

  if (!form.isNew && form.planLoading) {
    return (
      <PageContainer width="content" className="flex min-h-[60vh] items-center justify-center">
        <Loader2 className={cn('w-6 h-6 animate-spin', TEXT.caption)} />
      </PageContainer>
    );
  }

  if (!form.isNew && form.planIsError) {
    return (
      <PageContainer width="content" className="space-y-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/orchestration/plans')}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回 Plan 列表
        </Button>
        <ErrorState
          title="加载 Plan 详情失败"
          description={(form.planError as Error)?.message || '请检查网络连接或稍后重试'}
          onRetry={() => void form.refetchPlan()}
        />
      </PageContainer>
    );
  }

  if (form.dependenciesIsError) {
    return (
      <PageContainer width="content" className="space-y-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/orchestration/plans')}
        >
          <ArrowLeft className="mr-1 h-4 w-4" /> 返回 Plan 列表
        </Button>
        <ErrorState
          title="加载 Plan 编辑依赖失败"
          description={(form.dependenciesError as Error)?.message || '脚本或 Plan 链数据加载失败'}
          onRetry={form.refetchDependencies}
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer width="bleed" scrollable={false} className="flex h-full flex-col bg-muted/40">
      {form.remoteChange && (
        <div
          role="status"
          className={cn('flex items-center gap-3 px-4 py-2 text-[13px]', ALERT_BANNER.warning)}
        >
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span className="min-w-0 flex-1">
            该 Plan 已在其他会话更新；本地草稿已保留，保存时将按乐观锁校验。
          </span>
          <Button size="sm" variant="outline" onClick={form.reloadFromRemote}>
            重新加载远端版本
          </Button>
          <Button size="sm" variant="ghost" onClick={form.dismissRemoteChange}>
            继续编辑
          </Button>
        </div>
      )}

      <div
        className={cn(
          'flex-1 min-h-0 grid grid-cols-1 auto-rows-max lg:grid-rows-1 overflow-y-auto lg:overflow-hidden',
          chainPanelOpen
            ? 'lg:grid-cols-[260px_minmax(0,1fr)]'
            : 'lg:grid-cols-[44px_minmax(0,1fr)]',
        )}
      >
        {chainPanelOpen ? (
          <PlanChainPanel
            plans={form.allPlans || []}
            currentPlanId={planId}
            draftStepCounts={form.isNew ? form.draftStepCounts : null}
            draftPlanName={form.name}
            onSelectPlan={form.handleSelectChainPlan}
            onAppendPlan={form.handleAppendChainPlan}
            onCollapse={() => setChainPanelOpen(false)}
          />
        ) : (
          <div className="flex items-center justify-between border-b border-border bg-card px-3 py-2 lg:flex-col lg:justify-start lg:gap-2 lg:border-b-0 lg:border-r lg:px-1.5 lg:py-3">
            <button
              type="button"
              data-testid="expand-plan-chain-btn"
              onClick={() => setChainPanelOpen(true)}
              aria-label="展开执行链"
              title="展开执行链面板（管理多 Plan 链式触发）"
              className="inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground lg:flex-col lg:px-1 lg:py-2"
            >
              <PanelLeftOpen className="h-4 w-4 shrink-0" />
              <span className="lg:[writing-mode:vertical-rl] lg:text-[11px] lg:tracking-wider">
                执行链
              </span>
            </button>
            <span
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground lg:hidden"
              aria-hidden="true"
            >
              <GitBranch className="h-3 w-3" />
              单 Plan
            </span>
          </div>
        )}

        <PlanCanvas
          settingsMetadata={form.editableProjection}
          projection={form.projection}
          projectionUnavailable={form.projectionUnavailable}
          projectionIsDraft={form.projectionIsDraft}
          planName={form.name}
          onPlanNameChange={form.setName}
          description={form.description}
          onDescriptionChange={form.setDescription}
          planSettings={{
            patrolIntervalSeconds: form.patrolIntervalSeconds,
            timeoutSeconds: form.timeoutSeconds,
            barrierTimeoutSeconds: form.barrierTimeoutSeconds,
            barrierMaxWaitSeconds: form.barrierMaxWaitSeconds,
            autoArchiveIntervalSeconds: form.autoArchiveIntervalSeconds,
            watcherPolicy: form.watcherPolicy,
          }}
          onPlanSettingsChange={form.handlePlanSettingsChange}
          patrolIntervalSeconds={form.patrolIntervalSeconds}
          onPatrolIntervalChange={form.handlePatrolIntervalChange}
          timeoutSeconds={form.timeoutSeconds}
          onTimeoutChange={form.handleTimeoutChange}
          nextPlanName={form.nextPlanName}
          isCurrentEditing
          lifecycle={form.lifecycle}
          onLifecycleChange={form.setLifecycle}
          selectedStepKey={form.selectedStepKey}
          onSelectStep={form.setSelectedStepKey}
          scripts={form.scripts || []}
          projectKey={form.projectKey}
          onProjectKeyChange={form.setProjectKey}
          specialtyKey={form.specialtyKey}
          onSpecialtyKeyChange={form.setSpecialtyKey}
          projects={form.projects || []}
          specialties={form.specialties || []}
          suiteName={form.suiteName}
          onSuiteNameChange={form.setSuiteName}
          suites={form.suites || []}
        />
      </div>

      <Sheet
        open={Boolean(form.selectedStep)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) form.setSelectedStepKey(null);
        }}
      >
        <SheetContent
          side="right"
          size="lg"
          hideCloseButton
          overlayClassName="bg-foreground/15 backdrop-blur-[1px]"
          aria-label="步骤属性编辑抽屉"
        >
          <PlanStepInspector
            embeddedInSheet
            editableProjection={form.editableProjection}
            projection={form.projection}
            projectionUnavailable={form.projectionUnavailable}
            projectionIsDraft={form.projectionIsDraft}
            step={form.selectedStep}
            phase={form.selectedRef.phase}
            index={form.selectedRef.index >= 0 ? form.selectedRef.index : null}
            scripts={form.scripts || []}
            onUpdateStep={form.handleStepUpdate}
            stepPosition={currentStepIdx >= 0 ? currentStepIdx + 1 : undefined}
            totalSteps={orderedSteps.length}
            onPrevStep={handlePrevStep}
            onNextStep={handleNextStep}
            onClose={() => form.setSelectedStepKey(null)}
          />
        </SheetContent>
      </Sheet>

      <AlertDialog open={form.showJson} onOpenChange={form.setShowJson}>
        <AlertDialogContent className="max-w-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle>Plan 安全参数 JSON</AlertDialogTitle>
            <AlertDialogDescription>
              {form.projectionIsDraft ? 'L1草稿，未保存' : 'L1 已保存计划基线'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <pre
            className={cn(
              'max-h-[60vh] overflow-auto border border-border rounded-md p-3 text-xs font-mono leading-relaxed',
              SURFACE.subtle,
            )}
          >
            {form.projection ? JSON.stringify(form.projection.safe_debug, null, 2) : (form.projectionUnavailable ? '安全投影不可用' : '正在读取安全投影')}
          </pre>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => form.setShowJson(false)}>关闭</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={!!form.confirmLeave} onOpenChange={(open) => !open && form.setConfirmLeave(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>有未保存的修改</AlertDialogTitle>
            <AlertDialogDescription>
              {form.confirmLeave?.type === 'execute'
                ? '是否先保存当前 Plan 再发起测试？'
                : '是否先保存当前 Plan 再切换到目标 Plan？'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="ghost" onClick={() => form.setConfirmLeave(null)}>
              取消
            </Button>
            <AlertDialogAction onClick={form.confirmAndProceed}>保存并继续</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={form.chainAppendDialog === 'confirm-save'}
        onOpenChange={(open) => !open && form.setChainAppendDialog(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>先保存再追加链尾？</AlertDialogTitle>
            <AlertDialogDescription>
              当前 Plan 尚未保存，是否先保存再追加链尾？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void form.onChainAppendSaveConfirm()}>
              保存并继续
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={form.chainAppendDialog === 'name'}
        onOpenChange={(open) => !open && form.setChainAppendDialog(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>新 Plan 名称</AlertDialogTitle>
            <AlertDialogDescription>为链尾新 Plan 输入名称。</AlertDialogDescription>
          </AlertDialogHeader>
          <input
            type="text"
            value={form.chainAppendName}
            onChange={(e) => form.setChainAppendName(e.target.value)}
            className={cn(FORM.input, 'mt-1')}
            autoFocus
          />
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={!form.chainAppendName.trim()}
              onClick={(e) => {
                e.preventDefault();
                void form.onChainAppendNameConfirm();
              }}
            >
              创建并追加
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageContainer>
  );
}
