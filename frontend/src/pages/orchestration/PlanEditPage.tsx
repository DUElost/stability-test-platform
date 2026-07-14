import { useParams, useNavigate } from 'react-router-dom';
import { Loader2, ArrowLeft, Code2, Play, Save, AlertCircle, ChevronRight } from 'lucide-react';
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
import PlanChainPanel from '@/components/pipeline/PlanChainPanel';
import PlanCanvas from '@/components/pipeline/PlanCanvas';
import PlanStepInspector from '@/components/pipeline/PlanStepInspector';
import { STATUS_BG_COLORS } from '@/design-system/colors';
import { SURFACE, TEXT, FORM } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { usePlanEditForm } from './usePlanEditForm';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { ErrorState } from '@/components/ui/error-state';

export default function PlanEditPage() {
  const { id } = useParams<{ id: string }>();
  const planId = id && id !== 'new' && Number(id) > 0 ? Number(id) : null;
  const navigate = useNavigate();

  const form = usePlanEditForm(planId);
  useDocumentTitle(form.name || (form.isNew ? '新建 Plan' : '编辑 Plan'));

  if (!form.isNew && form.planLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className={cn('w-6 h-6 animate-spin', TEXT.caption)} />
      </div>
    );
  }

  if (!form.isNew && form.planIsError) {
    return (
      <div className="space-y-3 p-6">
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
      </div>
    );
  }

  if (form.dependenciesIsError) {
    return (
      <div className="space-y-3 p-6">
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
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-muted/40">
      <header className="h-16 shrink-0 px-6 flex items-center justify-between gap-4 bg-card/95 backdrop-blur border-b border-border">
        <div className="flex items-center gap-2.5 min-w-0">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-muted-foreground"
            onClick={() => navigate('/orchestration/plans')}
          >
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground min-w-0">
            <span>测试计划</span>
            <ChevronRight className="w-3.5 h-3.5 text-border" />
            <strong className="text-foreground font-bold text-base truncate">
              {form.name || (form.isNew ? '新建 Plan' : '未命名 Plan')}
            </strong>
            {form.isDirty ? (
              <span
                className={`ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.warning} border border-warning`}
              >
                <AlertCircle className="w-3 h-3" /> 未保存
              </span>
            ) : (
              <span
                className={`ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.success} border border-success`}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-success" /> 已保存
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => form.setShowJson(true)}
          >
            <Code2 className="w-4 h-4 mr-1.5" />
            查看 JSON
          </Button>
          <Button variant="default" size="sm" onClick={form.handleExecute} disabled={form.saving}>
            <Play className="w-4 h-4 mr-1.5" />
            发起测试
          </Button>
          <Button size="sm" onClick={form.handleSave} disabled={form.saving || !form.isDirty}>
            <Save className="w-4 h-4 mr-1.5" />
            {form.saving ? '保存中…' : form.isNew ? '创建' : '保存修改'}
          </Button>
        </div>
      </header>

      <div className="flex-1 min-h-0 grid grid-cols-1 grid-rows-1 lg:grid-cols-[260px_minmax(0,1fr)_320px] overflow-hidden">
        <PlanChainPanel
          plans={form.allPlans || []}
          currentPlanId={planId}
          draftStepCounts={form.isNew ? form.draftStepCounts : null}
          draftPlanName={form.name}
          onSelectPlan={form.handleSelectChainPlan}
          onAppendPlan={form.handleAppendChainPlan}
        />

        <PlanCanvas
          planName={form.name}
          onPlanNameChange={form.setName}
          description={form.description}
          onDescriptionChange={form.setDescription}
          failureThreshold={form.failureThreshold}
          onFailureThresholdChange={form.setFailureThreshold}
          patrolIntervalSeconds={form.lifecycle.lifecycle.patrol?.interval_seconds ?? null}
          onPatrolIntervalChange={form.handlePatrolIntervalChange}
          timeoutSeconds={form.lifecycle.lifecycle.timeout_seconds ?? null}
          onTimeoutChange={form.handleTimeoutChange}
          nextPlanName={form.nextPlanName}
          isCurrentEditing
          lifecycle={form.lifecycle}
          onLifecycleChange={form.setLifecycle}
          selectedStepKey={form.selectedStepKey}
          onSelectStep={form.setSelectedStepKey}
          scripts={form.scripts || []}
        />

        <PlanStepInspector
          step={form.selectedStep}
          phase={form.selectedRef.phase}
          index={form.selectedRef.index >= 0 ? form.selectedRef.index : null}
          scripts={form.scripts || []}
          onUpdateStep={form.handleStepUpdate}
        />
      </div>

      <AlertDialog open={form.showJson} onOpenChange={form.setShowJson}>
        <AlertDialogContent className="max-w-3xl">
          <AlertDialogHeader>
            <AlertDialogTitle>Plan Lifecycle JSON</AlertDialogTitle>
            <AlertDialogDescription>
              当前 Plan 的 lifecycle 是从 PlanStep 行 + Plan 直列字段实时装配的，仅供 pipeline_engine 校验视图。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <pre
            className={cn(
              'max-h-[60vh] overflow-auto border border-border rounded-md p-3 text-xs font-mono leading-relaxed',
              SURFACE.subtle,
            )}
          >
            {JSON.stringify(form.lifecycle, null, 2)}
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
    </div>
  );
}
