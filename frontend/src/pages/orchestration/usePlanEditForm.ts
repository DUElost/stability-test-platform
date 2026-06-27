import { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate, useBeforeUnload } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToast } from '@/hooks/useToast';
import { api, type Plan, type PlanCreate, type PlanUpdate, type PipelineDef, type PipelineStep } from '@/utils/api';
import { planKeys } from '@/utils/api/queryKeys';
import {
  EMPTY_LIFECYCLE,
  buildStepsForApi,
  findStepInLifecycle,
  rebuildLifecycleFromPlan,
  snapshot,
} from './planEditUtils';

export type ConfirmLeaveState = null | { type: 'switch' | 'execute'; targetPlanId?: number };

export function usePlanEditForm(planId: number | null) {
  const isNew = planId == null;
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [failureThreshold, setFailureThreshold] = useState(0.05);
  const [nextPlanId, setNextPlanId] = useState<number | null>(null);
  const [lifecycle, setLifecycle] = useState<PipelineDef>(EMPTY_LIFECYCLE);
  const [selectedStepKey, setSelectedStepKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showJson, setShowJson] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState<ConfirmLeaveState>(null);
  const [chainAppendDialog, setChainAppendDialog] = useState<'confirm-save' | 'name' | null>(null);
  const [chainAppendName, setChainAppendName] = useState('');
  const [origSnapshot, setOrigSnapshot] = useState('');

  const { data: plan, isLoading: planLoading } = useQuery({
    queryKey: planKeys.detail(planId!),
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
    const target = (allPlans || []).find((p) => p.id === nextPlanId);
    return target?.name ?? `Plan #${nextPlanId}`;
  }, [nextPlanId, allPlans]);

  const selectedRef = useMemo(
    () => findStepInLifecycle(lifecycle, selectedStepKey),
    [lifecycle, selectedStepKey],
  );

  const selectedStep: PipelineStep | null = useMemo(() => {
    if (!selectedStepKey || selectedRef.phase == null) return null;
    const lc = lifecycle.lifecycle;
    const arr =
      selectedRef.phase === 'patrol'
        ? lc.patrol?.steps
        : (lc as { init?: PipelineStep[]; teardown?: PipelineStep[] })[selectedRef.phase];
    return arr?.[selectedRef.index] ?? null;
  }, [lifecycle, selectedRef, selectedStepKey]);

  useBeforeUnload(
    useCallback(
      (event) => {
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
        const phaseKey = phase as 'init' | 'teardown';
        const steps = [...(lc[phaseKey] ?? [])];
        steps[selectedRef.index] = next;
        lc[phaseKey] = steps;
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
      queryClient.setQueryData(planKeys.detail(saved.id), saved);
      setOrigSnapshot(currentSnapshot);
      if (isNew) {
        navigate(`/orchestration/plans/${saved.id}`, { replace: true });
      }
      return saved;
    } catch (err: unknown) {
      const ax = err as { response?: { data?: { detail?: unknown } }; message?: string };
      const detail = ax.response?.data?.detail;
      if (Array.isArray(detail)) {
        toast.error(
          detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc || []).join('.')} ${d.msg}`).join('; '),
        );
      } else if (typeof detail === 'string') {
        toast.error(detail);
      } else if (detail && typeof detail === 'object' && 'errors' in detail) {
        toast.error(`校验失败: ${(detail as { errors: string[] }).errors.join('; ')}`);
      } else {
        toast.error(ax.message || '保存失败');
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
      const byId = new Map(plansList.map((p) => [p.id, p]));
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
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '追加失败';
      toast.error(msg);
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

  return {
    isNew,
    planId,
    planLoading,
    name,
    setName,
    description,
    setDescription,
    failureThreshold,
    setFailureThreshold,
    lifecycle,
    setLifecycle,
    selectedStepKey,
    setSelectedStepKey,
    saving,
    showJson,
    setShowJson,
    confirmLeave,
    setConfirmLeave,
    chainAppendDialog,
    setChainAppendDialog,
    chainAppendName,
    setChainAppendName,
    isDirty,
    draftStepCounts,
    nextPlanName,
    selectedRef,
    selectedStep,
    allPlans,
    scripts,
    handleStepUpdate,
    handlePatrolIntervalChange,
    handleTimeoutChange,
    handleSave,
    handleAppendChainPlan,
    onChainAppendSaveConfirm,
    onChainAppendNameConfirm,
    handleSelectChainPlan,
    handleExecute,
    confirmAndProceed,
  };
}
