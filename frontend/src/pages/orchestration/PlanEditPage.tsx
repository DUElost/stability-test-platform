import { useState, useEffect, useMemo, useCallback } from 'react';

import { useParams, useNavigate, useBeforeUnload } from 'react-router-dom';

import { useQuery, useQueryClient } from '@tanstack/react-query';

import { Loader2, ArrowLeft, Code2, Play, Save, AlertCircle, ChevronRight } from 'lucide-react';

import { Button } from '@/components/ui/button';

import { useToast } from '@/components/ui/toast';

import { useHeaderSlot } from '@/contexts/HeaderSlotContext';

import { planKeys } from '@/utils/api/queryKeys';

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

import { api, type Plan, type PlanCreate, type PlanUpdate, type PipelineDef, type PipelineStep, type PipelinePhase, type PlanStepCreate } from '@/utils/api';

import PlanChainPanel from '@/components/pipeline/PlanChainPanel';

import PlanCanvas from '@/components/pipeline/PlanCanvas';

import PlanStepInspector from '@/components/pipeline/PlanStepInspector';

import { STATUS_BG_COLORS } from '@/design-system/colors';

import { SURFACE, TEXT, FORM } from '@/design-system/tokens';

import { cn } from '@/lib/utils';



const EMPTY_LIFECYCLE: PipelineDef = {

  lifecycle: {

    init: [

      {

        step_id: 'step_init_1',

        action: 'script:check_device',

        version: '1.0.0',

        params: {},

        timeout_seconds: 30,

        retry: 0,

        enabled: true,

      },

    ],

    teardown: [],

  },

};



function snapshot(value: unknown): string {

  return JSON.stringify(value);

}



function findStepInLifecycle(lc: PipelineDef, stepKey: string | null) {

  if (!stepKey) return { phase: null as PipelinePhase | null, index: -1 as number };

  const lifecycle = lc.lifecycle;

  const sources: Array<[PipelinePhase, PipelineStep[]]> = [

    ['init', lifecycle.init ?? []],

    ['patrol', lifecycle.patrol?.steps ?? []],

    ['teardown', lifecycle.teardown ?? []],

  ];

  for (const [phase, steps] of sources) {

    const idx = steps.findIndex(s => s.step_id === stepKey);

    if (idx >= 0) return { phase, index: idx };

  }

  return { phase: null as PipelinePhase | null, index: -1 };

}



function rebuildLifecycleFromPlan(plan: Plan): PipelineDef {

  const init: PipelineStep[] = [];

  const patrol: PipelineStep[] = [];

  const teardown: PipelineStep[] = [];



  const sorted = [...(plan.steps || [])].sort(

    (a, b) => a.stage.localeCompare(b.stage) || a.sort_order - b.sort_order,

  );



  for (const s of sorted) {

    const stepDef: PipelineStep = {

      step_id: s.step_key,

      action: `script:${s.script_name}`,

      version: s.script_version,

      params: {},

      timeout_seconds: s.timeout_seconds ?? 30,

      retry: s.retry ?? 0,

      enabled: s.enabled !== false,

    };

    if (s.stage === 'init') init.push(stepDef);

    else if (s.stage === 'patrol') patrol.push(stepDef);

    else teardown.push(stepDef);

  }



  return {

    lifecycle: {

      init,

      patrol: patrol.length

        ? { interval_seconds: plan.patrol_interval_seconds ?? 60, steps: patrol }

        : undefined,

      teardown,

      timeout_seconds: plan.timeout_seconds ?? undefined,

    },

  };

}



function buildStepsForApi(lifecycle: PipelineDef): PlanStepCreate[] {

  const out: PlanStepCreate[] = [];

  const lc = lifecycle.lifecycle;

  const append = (phase: 'init' | 'patrol' | 'teardown', steps: PipelineStep[]) => {

    steps.forEach((s, i) => {

      const action = s.action || '';

      const scriptName = action.startsWith('script:') ? action.slice(7) : '';

      out.push({

        step_key: s.step_id || `step_${phase}_${i}`,

        script_name: scriptName,

        script_version: s.version || '',

        stage: phase,

        sort_order: i,

        timeout_seconds: s.timeout_seconds ?? null,

        retry: s.retry ?? 0,

        enabled: s.enabled !== false,

      });

    });

  };

  if (lc.init) append('init', lc.init);

  if (lc.patrol?.steps) append('patrol', lc.patrol.steps);

  if (lc.teardown) append('teardown', lc.teardown);

  return out;

}



export default function PlanEditPage() {

  const { id } = useParams<{ id: string }>();

  const planId = id && id !== 'new' && Number(id) > 0 ? Number(id) : null;

  const isNew = planId == null;



  const navigate = useNavigate();

  const toast = useToast();

  const queryClient = useQueryClient();

  const { setHeaderSlot, setFullBleed } = useHeaderSlot();

  // 全出血模式 — 与 PlanRunDetailPage 相同；AppShell 去掉内边距，页面自管布局

  useEffect(() => {

    setFullBleed(true);

    return () => { setFullBleed(false); setHeaderSlot(null); };

  }, [setFullBleed, setHeaderSlot]);

  // ── Form state ────────────────────────────────────────────────────────────

  const [name, setName] = useState('');

  const [description, setDescription] = useState('');

  const [failureThreshold, setFailureThreshold] = useState(0.05);

  const [nextPlanId, setNextPlanId] = useState<number | null>(null);

  const [lifecycle, setLifecycle] = useState<PipelineDef>(EMPTY_LIFECYCLE);

  const [selectedStepKey, setSelectedStepKey] = useState<string | null>(null);



  const [saving, setSaving] = useState(false);

  const [showJson, setShowJson] = useState(false);

  const [confirmLeave, setConfirmLeave] = useState<null | { type: 'switch' | 'execute'; targetPlanId?: number }>(null);

  const [chainAppendDialog, setChainAppendDialog] = useState<'confirm-save' | 'name' | null>(null);

  const [chainAppendName, setChainAppendName] = useState('');



  // Original snapshot for dirty detection

  const [origSnapshot, setOrigSnapshot] = useState<string>('');



  // ── Queries ───────────────────────────────────────────────────────────────

  const { data: plan, isLoading: planLoading } = useQuery({

    queryKey: ['plan', planId],

    queryFn: () => api.plans.get(planId!),

    enabled: planId != null,

  });



  const { data: allPlans } = useQuery({

    queryKey: planKeys.list(200),
    queryFn: () => api.plans.list(0, 200),

  });



  const { data: scripts } = useQuery({

    queryKey: ['scripts-active'],

    queryFn: () => api.scripts.list(true),

    staleTime: 60_000,

  });



  // ── Hydrate form from server ─────────────────────────────────────────────

  useEffect(() => {

    if (plan && !isNew) {

      setName(plan.name);

      setDescription(plan.description || '');

      setFailureThreshold(plan.failure_threshold);

      setNextPlanId(plan.next_plan_id ?? null);

      const lc = rebuildLifecycleFromPlan(plan);

      setLifecycle(lc);

      setSelectedStepKey(null);

      setOrigSnapshot(

        snapshot({

          name: plan.name,

          description: plan.description || '',

          failureThreshold: plan.failure_threshold,

          nextPlanId: plan.next_plan_id ?? null,

          lifecycle: lc,

        }),

      );

    }

  }, [plan, isNew]);



  useEffect(() => {

    if (isNew) {

      setOrigSnapshot(

        snapshot({

          name: '',

          description: '',

          failureThreshold: 0.05,

          nextPlanId: null,

          lifecycle: EMPTY_LIFECYCLE,

        }),

      );

    }

  }, [isNew]);



  // ── Derived state ─────────────────────────────────────────────────────────

  const currentSnapshot = useMemo(

    () => snapshot({ name, description, failureThreshold, nextPlanId, lifecycle }),

    [name, description, failureThreshold, nextPlanId, lifecycle],

  );

  const isDirty = currentSnapshot !== origSnapshot;



  const draftStepCounts = useMemo(() => {

    const lc = lifecycle.lifecycle;

    return {

      init: lc.init?.length ?? 0,

      patrol: lc.patrol?.steps?.length ?? 0,

      teardown: lc.teardown?.length ?? 0,

    };

  }, [lifecycle]);



  const nextPlanName = useMemo(() => {

    if (nextPlanId == null) return null;

    const target = (allPlans || []).find(p => p.id === nextPlanId);

    return target?.name ?? `Plan #${nextPlanId}`;

  }, [nextPlanId, allPlans]);



  const selectedRef = useMemo(() => findStepInLifecycle(lifecycle, selectedStepKey), [lifecycle, selectedStepKey]);

  const selectedStep: PipelineStep | null = useMemo(() => {

    if (!selectedStepKey || selectedRef.phase == null) return null;

    const lc = lifecycle.lifecycle;

    const arr = selectedRef.phase === 'patrol' ? lc.patrol?.steps : (lc as any)[selectedRef.phase];

    return arr?.[selectedRef.index] ?? null;

  }, [lifecycle, selectedRef, selectedStepKey]);



  // ── Handlers ──────────────────────────────────────────────────────────────

  useBeforeUnload(

    useCallback(

      event => {

        if (!isDirty) return;

        event.preventDefault();

        event.returnValue = '';

      },

      [isDirty],

    ),

  );



  const handleStepUpdate = useCallback(

    (next: PipelineStep) => {

      if (!selectedStep || selectedRef.phase == null) return;

      const phase = selectedRef.phase;

      const lc = { ...lifecycle.lifecycle };

      if (phase === 'patrol') {

        const steps = [...(lc.patrol?.steps ?? [])];

        steps[selectedRef.index] = next;

        lc.patrol = { interval_seconds: lc.patrol?.interval_seconds ?? 60, steps };

      } else {

        const steps = [...((lc as any)[phase] ?? [])];

        steps[selectedRef.index] = next;

        (lc as any)[phase] = steps;

      }

      setLifecycle({ lifecycle: lc });

      if (next.step_id !== selectedStep.step_id) setSelectedStepKey(next.step_id);

    },

    [selectedRef, selectedStep, lifecycle],

  );



  const handlePatrolIntervalChange = (seconds: number | null) => {

    const lc = { ...lifecycle.lifecycle };

    if (seconds == null) {

      if (lc.patrol) lc.patrol = { ...lc.patrol, interval_seconds: 60 };

    } else if (lc.patrol) {

      lc.patrol = { ...lc.patrol, interval_seconds: Math.max(5, seconds) };

    }

    setLifecycle({ lifecycle: lc });

  };



  const handleTimeoutChange = (seconds: number | null) => {

    setLifecycle({

      lifecycle: {

        ...lifecycle.lifecycle,

        timeout_seconds: seconds ?? undefined,

      },

    });

  };



  const handleSave = async (): Promise<Plan | null> => {

    if (!name.trim()) {

      toast.error('请输入 Plan 名称');

      return null;

    }

    setSaving(true);

    try {

      const payload: PlanUpdate = {

        name: name.trim(),

        description: description.trim() || undefined,

        failure_threshold: failureThreshold,

        patrol_interval_seconds: lifecycle.lifecycle.patrol?.interval_seconds ?? null,

        timeout_seconds: lifecycle.lifecycle.timeout_seconds ?? null,

        next_plan_id: nextPlanId,

        steps: buildStepsForApi(lifecycle),

      };

      let saved: Plan;

      if (isNew) {

        saved = await api.plans.create(payload as PlanCreate);

        toast.success('Plan 已创建');

      } else {

        saved = await api.plans.update(planId!, payload);

        toast.success('已保存');

      }

      queryClient.invalidateQueries({ queryKey: planKeys.allLists() });

      queryClient.setQueryData(['plan', saved.id], saved);

      setOrigSnapshot(currentSnapshot);

      if (isNew) {

        navigate(`/orchestration/plans/${saved.id}`, { replace: true });

      }

      return saved;

    } catch (err: any) {

      const detail = err.response?.data?.detail;

      if (Array.isArray(detail)) {

        toast.error(detail.map((d: any) => `${(d.loc || []).join('.')} ${d.msg}`).join('; '));

      } else if (typeof detail === 'string') {

        toast.error(detail);

      } else if (detail && typeof detail === 'object' && (detail as any).errors) {

        toast.error(`校验失败: ${(detail as any).errors.join('; ')}`);

      } else {

        toast.error(err.message || '保存失败');

      }

      return null;

    } finally {

      setSaving(false);

    }

  };



  const createChainTailPlan = async (proposedName: string) => {

    try {

      const tail = await api.plans.create({

        name: proposedName.trim(),

        description: '',

        failure_threshold: 0.05,

        steps: [

          {

            step_key: 'step_init_1',

            script_name: 'check_device',

            script_version: '1.0.0',

            stage: 'init',

            sort_order: 0,

            timeout_seconds: 30,

            retry: 0,

            enabled: true,

          },

        ],

      });



      const plansList = await api.plans.list(0, 200);

      const byId = new Map(plansList.map(p => [p.id, p]));

      let cursor: Plan | undefined = byId.get(planId!);

      const seen = new Set<number>();

      while (cursor && cursor.next_plan_id != null && !seen.has(cursor.id)) {

        seen.add(cursor.id);

        const nextNode = byId.get(cursor.next_plan_id);

        if (!nextNode) break;

        cursor = nextNode;

      }

      if (cursor && cursor.id !== tail.id) {

        await api.plans.update(cursor.id, { next_plan_id: tail.id });

      }



      queryClient.invalidateQueries({ queryKey: planKeys.allLists() });

      toast.success('已追加新 Plan');

      navigate(`/orchestration/plans/${tail.id}`);

    } catch (err: any) {

      toast.error(err.message || '追加失败');

    }

  };



  const handleAppendChainPlan = () => {

    if (planId == null) {

      toast.info('保存当前 Plan 后再追加链尾');

      return;

    }

    if (isDirty) {

      setChainAppendDialog('confirm-save');

      return;

    }

    setChainAppendName(`${name || 'Plan'} - 后续`);

    setChainAppendDialog('name');

  };



  const onChainAppendSaveConfirm = async () => {

    const saved = await handleSave();

    if (!saved) return;

    setChainAppendName(`${name || 'Plan'} - 后续`);

    setChainAppendDialog('name');

  };



  const onChainAppendNameConfirm = async () => {

    const trimmed = chainAppendName.trim();

    if (!trimmed) return;

    setChainAppendDialog(null);

    await createChainTailPlan(trimmed);

  };



  const handleSelectChainPlan = (targetId: number) => {

    if (targetId === planId) return;

    if (isDirty) {

      setConfirmLeave({ type: 'switch', targetPlanId: targetId });

      return;

    }

    navigate(`/orchestration/plans/${targetId}`);

  };



  const handleExecute = async () => {

    if (planId == null) {

      const saved = await handleSave();

      if (!saved) return;

      navigate(`/execution/plan-execute?plan=${saved.id}`);

      return;

    }

    if (isDirty) {

      setConfirmLeave({ type: 'execute' });

      return;

    }

    navigate(`/execution/plan-execute?plan=${planId}`);

  };



  const confirmAndProceed = async () => {

    const target = confirmLeave;

    setConfirmLeave(null);

    if (!target) return;

    const saved = await handleSave();

    if (!saved) return;

    if (target.type === 'switch' && target.targetPlanId != null) {

      navigate(`/orchestration/plans/${target.targetPlanId}`);

    } else if (target.type === 'execute') {

      navigate(`/execution/plan-execute?plan=${saved.id}`);

    }

  };



  // ── Render ────────────────────────────────────────────────────────────────

  if (!isNew && planLoading) {

    return (

      <div className="flex items-center justify-center min-h-[60vh]">

        <Loader2 className={cn('w-6 h-6 animate-spin', TEXT.caption)} />

      </div>

    );

  }



  return (

    <div className="h-full flex flex-col bg-muted/40">

      {/* ── Top Bar ─────────────────────────────────────────────── */}

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

              {name || (isNew ? '新建 Plan' : '未命名 Plan')}

            </strong>

            {isDirty ? (

              <span className={`ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.warning} border border-warning`}>

                <AlertCircle className="w-3 h-3" /> 未保存

              </span>

            ) : (

              <span className={`ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold ${STATUS_BG_COLORS.success} border border-success`}>

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

            onClick={() => setShowJson(true)}

          >

            <Code2 className="w-4 h-4 mr-1.5" />

            查看 JSON

          </Button>

          <Button

            variant="default"

            size="sm"

            className="bg-emerald-600 hover:bg-emerald-700 text-white"

            onClick={handleExecute}

            disabled={saving}

          >

            <Play className="w-4 h-4 mr-1.5" />

            发起测试

          </Button>

          <Button size="sm" onClick={handleSave} disabled={saving || !isDirty}>

            <Save className="w-4 h-4 mr-1.5" />

            {saving ? '保存中…' : isNew ? '创建' : '保存修改'}

          </Button>

        </div>

      </header>



      {/* ── Workspace ──────────────────────────────────────────── */}

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)_320px] overflow-hidden">

        <PlanChainPanel

          plans={allPlans || []}

          currentPlanId={planId}

          draftStepCounts={isNew ? draftStepCounts : null}

          draftPlanName={name}

          onSelectPlan={handleSelectChainPlan}

          onAppendPlan={handleAppendChainPlan}

        />



        <PlanCanvas

          planName={name}

          onPlanNameChange={setName}

          description={description}

          onDescriptionChange={setDescription}

          failureThreshold={failureThreshold}

          onFailureThresholdChange={setFailureThreshold}

          patrolIntervalSeconds={lifecycle.lifecycle.patrol?.interval_seconds ?? null}

          onPatrolIntervalChange={handlePatrolIntervalChange}

          timeoutSeconds={lifecycle.lifecycle.timeout_seconds ?? null}

          onTimeoutChange={handleTimeoutChange}

          nextPlanName={nextPlanName}

          isCurrentEditing={true}

          lifecycle={lifecycle}

          onLifecycleChange={setLifecycle}

          selectedStepKey={selectedStepKey}

          onSelectStep={setSelectedStepKey}

          scripts={scripts || []}

        />



        <PlanStepInspector

          step={selectedStep}

          phase={selectedRef.phase}

          index={selectedRef.index >= 0 ? selectedRef.index : null}

          scripts={scripts || []}

          onUpdateStep={handleStepUpdate}

        />

      </div>



      {/* ── JSON Dialog ───────────────────────────────────────── */}

      <AlertDialog open={showJson} onOpenChange={setShowJson}>

        <AlertDialogContent className="max-w-3xl">

          <AlertDialogHeader>

            <AlertDialogTitle>Plan Lifecycle JSON</AlertDialogTitle>

            <AlertDialogDescription>

              当前 Plan 的 lifecycle 是从 PlanStep 行 + Plan 直列字段实时装配的，仅供 pipeline_engine 校验视图。

            </AlertDialogDescription>

          </AlertDialogHeader>

          <pre className={cn('max-h-[60vh] overflow-auto border border-border rounded-md p-3 text-xs font-mono leading-relaxed', SURFACE.subtle)}>

            {JSON.stringify(lifecycle, null, 2)}

          </pre>

          <AlertDialogFooter>

            <AlertDialogAction onClick={() => setShowJson(false)}>关闭</AlertDialogAction>

          </AlertDialogFooter>

        </AlertDialogContent>

      </AlertDialog>



      {/* ── Confirm-leave Dialog (dirty switch / execute) ─────── */}

      <AlertDialog open={!!confirmLeave} onOpenChange={open => !open && setConfirmLeave(null)}>

        <AlertDialogContent>

          <AlertDialogHeader>

            <AlertDialogTitle>有未保存的修改</AlertDialogTitle>

            <AlertDialogDescription>

              {confirmLeave?.type === 'execute'

                ? '是否先保存当前 Plan 再发起测试？'

                : '是否先保存当前 Plan 再切换到目标 Plan？'}

            </AlertDialogDescription>

          </AlertDialogHeader>

          <AlertDialogFooter>

            <Button variant="ghost" onClick={() => setConfirmLeave(null)}>

              取消

            </Button>

            <AlertDialogAction onClick={confirmAndProceed}>保存并继续</AlertDialogAction>

          </AlertDialogFooter>

        </AlertDialogContent>

      </AlertDialog>



      {/* ── Chain append: save-before-confirm ─────────────────── */}

      <AlertDialog
        open={chainAppendDialog === 'confirm-save'}
        onOpenChange={(open) => !open && setChainAppendDialog(null)}
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

            <AlertDialogAction onClick={() => void onChainAppendSaveConfirm()}>

              保存并继续

            </AlertDialogAction>

          </AlertDialogFooter>

        </AlertDialogContent>

      </AlertDialog>



      {/* ── Chain append: new Plan name ───────────────────────── */}

      <AlertDialog
        open={chainAppendDialog === 'name'}
        onOpenChange={(open) => !open && setChainAppendDialog(null)}
      >

        <AlertDialogContent>

          <AlertDialogHeader>

            <AlertDialogTitle>新 Plan 名称</AlertDialogTitle>

            <AlertDialogDescription>

              为链尾新 Plan 输入名称。

            </AlertDialogDescription>

          </AlertDialogHeader>

          <input
            type="text"
            value={chainAppendName}
            onChange={(e) => setChainAppendName(e.target.value)}
            className={cn(FORM.input, 'mt-1')}
            autoFocus
          />

          <AlertDialogFooter>

            <AlertDialogCancel>取消</AlertDialogCancel>

            <AlertDialogAction
              disabled={!chainAppendName.trim()}
              onClick={(e) => {
                e.preventDefault();
                void onChainAppendNameConfirm();
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

