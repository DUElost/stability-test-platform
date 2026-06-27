import { useState, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { Code2, Play, Save, PanelLeft, PanelRight } from 'lucide-react';
import type { PanelImperativeHandle } from 'react-resizable-panels';
import { Button } from '@/components/ui/button';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';
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
import { PageContainer, PageHeader } from '@/components/layout';

export default function PlanEditPage() {
  const { id } = useParams<{ id: string }>();
  const planId = id && id !== 'new' && Number(id) > 0 ? Number(id) : null;

  const form = usePlanEditForm(planId);
  useDocumentTitle(form.name || (form.isNew ? '新建 Plan' : '编辑 Plan'));
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const leftPanelRef = useRef<PanelImperativeHandle | null>(null);
  const rightPanelRef = useRef<PanelImperativeHandle | null>(null);

  if (!form.isNew && form.planLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className={cn('w-6 h-6 animate-spin border-2 border-current border-t-transparent rounded-full', TEXT.caption)} />
      </div>
    );
  }

  return (
    <PageContainer scrollable={false} className="p-0">
      <PageHeader
        title={form.name || (form.isNew ? '新建 Plan' : '未命名 Plan')}
        breadcrumbs={[
          { label: 'Plans', path: '/orchestration/plans' },
          { label: form.isNew ? 'Create' : 'Edit' },
        ]}
        action={
          <>
            <Button variant="ghost" size="sm" onClick={() => form.setShowJson(true)}>
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
          </>
        }
      />

      <div className="flex items-center justify-between px-4 py-2 border-b bg-card">
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              const panel = leftPanelRef.current;
              if (panel) {
                if (panel.isCollapsed()) panel.expand();
                else panel.collapse();
              }
              setLeftCollapsed((v) => !v);
            }}
            aria-label={leftCollapsed ? '展开左栏' : '折叠左栏'}
          >
            <PanelLeft className="w-4 h-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              const panel = rightPanelRef.current;
              if (panel) {
                if (panel.isCollapsed()) panel.expand();
                else panel.collapse();
              }
              setRightCollapsed((v) => !v);
            }}
            aria-label={rightCollapsed ? '展开右栏' : '折叠右栏'}
          >
            <PanelRight className="w-4 h-4" />
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn('text-xs', TEXT.caption)}>
            {form.isDirty ? (
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.warning} border border-warning`}>
                未保存
              </span>
            ) : (
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.success} border border-success`}>
                已保存
              </span>
            )}
          </span>
        </div>
      </div>

      <ResizablePanelGroup direction="horizontal" className="flex-1 min-h-0">
        <ResizablePanel
          panelRef={leftPanelRef}
          defaultSize={20}
          minSize={15}
          maxSize={30}
          collapsible
          collapsedSize={0}
        >
          <div className="h-full overflow-auto">
            <PlanChainPanel
              plans={form.allPlans || []}
              currentPlanId={planId}
              draftStepCounts={form.isNew ? form.draftStepCounts : null}
              draftPlanName={form.name}
              onSelectPlan={form.handleSelectChainPlan}
              onAppendPlan={form.handleAppendChainPlan}
            />
          </div>
        </ResizablePanel>

        <ResizableHandle withHandle />

        <ResizablePanel defaultSize={60} minSize={40}>
          <div className="h-full overflow-auto bg-muted/40">
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
          </div>
        </ResizablePanel>

        <ResizableHandle withHandle />

        <ResizablePanel
          panelRef={rightPanelRef}
          defaultSize={20}
          minSize={15}
          maxSize={30}
          collapsible
          collapsedSize={0}
        >
          <div className="h-full overflow-auto">
            <PlanStepInspector
              step={form.selectedStep}
              phase={form.selectedRef.phase}
              index={form.selectedRef.index >= 0 ? form.selectedRef.index : null}
              scripts={form.scripts || []}
              onUpdateStep={form.handleStepUpdate}
            />
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>

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
    </PageContainer>
  );
}
