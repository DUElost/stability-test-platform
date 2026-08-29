import { useState, useMemo, useCallback } from 'react';
import { useNavigate, useBeforeUnload } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useToast } from '@/hooks/useToast';
import {
  api,
  ApiError,
  type Plan,
  type PlanCreate,
  type PlanUpdate,
  type PipelineDef,
  type PipelineStep,
} from '@/utils/api';
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
  // ADR-0029（#405）：归属项目/专项；orig* 用于「仅变更字段进 update payload」
  const [projectKey, setProjectKey] = useState('');
  const [specialtyKey, setSpecialtyKey] = useState('');
  const [origProjectKey, setOrigProjectKey] = useState('');
  const [origSpecialtyKey, setOrigSpecialtyKey] = useState('');
  // ADR-0030 v1.4（#430）：套件绑定；空 = P0 文件真源模式
  const [suiteName, setSuiteName] = useState('');
  const [origSuiteName, setOrigSuiteName] = useState('');
  const [lifecycle, setLifecycle] = useState<PipelineDef>(EMPTY_LIFECYCLE);
  const [selectedStepKey, setSelectedStepKey] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showJson, setShowJson] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState<ConfirmLeaveState>(null);
  const [chainAppendDialog, setChainAppendDialog] = useState<'confirm-save' | 'name' | null>(null);
  const [chainAppendName, setChainAppendName] = useState('');
  const [origSnapshot, setOrigSnapshot] = useState('');

  const {
    data: plan,
    isLoading: planLoading,
    isError: planIsError,
    error: planError,
    refetch: refetchPlan,
  } = useQuery({
    queryKey: planKeys.detail(planId!),
    queryFn: () => api.plans.get(planId!),
    enabled: planId != null,
  });

  const {
    data: allPlans,
    isError: allPlansIsError,
    error: allPlansError,
    refetch: refetchAllPlans,
  } = useQuery({
    queryKey: planKeys.list(200),
    queryFn: () => api.plans.list(0, 200),
  });

  const {
    data: scripts,
    isError: scriptsIsError,
    error: scriptsError,
    refetch: refetchScripts,
  } = useQuery({
    queryKey: ['scripts-active'],
    queryFn: () => api.scripts.list(true),
    staleTime: 60_000,
  });

  // ADR-0029（#405）：归属选择的数据源。字典失败不阻塞编辑（非依赖项）。
  const { data: projects } = useQuery({
    queryKey: ['projects-for-plan-editor'],
    queryFn: () => api.projects.list(),
    staleTime: 60_000,
  });
  const { data: specialties } = useQuery({
    queryKey: ['specialties'],
    queryFn: () => api.plans.listSpecialties(),
    staleTime: 300_000,
  });
  // ADR-0030（#430）：套件下拉；失败不阻塞编辑（与 projects/specialties 同口径）
  const { data: suites } = useQuery({
    queryKey: ['suites-for-plan-editor'],
    queryFn: () => api.suites.list({ is_active: true }),
    staleTime: 60_000,
  });

  const [prevPlanState, setPrevPlanState] = useState<{ plan: typeof plan; isNew: boolean } | null>(null);
  if (prevPlanState?.plan !== plan || prevPlanState?.isNew !== isNew) {
    setPrevPlanState({ plan, isNew });
    if (plan && !isNew) {
      setName(plan.name);
      setDescription(plan.description || '');
      setFailureThreshold(plan.failure_threshold);
      setNextPlanId(plan.next_plan_id ?? null);
      setProjectKey(plan.project_key || '');
      setSpecialtyKey(plan.specialty_key || '');
      setSuiteName(plan.suite_name || '');
      setOrigProjectKey(plan.project_key || '');
      setOrigSpecialtyKey(plan.specialty_key || '');
      setOrigSuiteName(plan.suite_name || '');
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
  }

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
    // ADR-0029 P1-B：项目 + 专项双必填（后端 schema 也强制；GENERIC = 显式「不限」）
    if (!projectKey || !specialtyKey) {
      toast.error('请选择归属项目与专项（运维型 Plan 选「通用（不限项目）」）');
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
        // 乐观锁令牌:后端据此拒绝"基于旧版本的保存"(409),防跨端互相覆盖
        expected_updated_at: plan?.updated_at,
      };
      // #405：归属字段只在变更时进 payload——后端 update 语义按 fields_set，
      // 恒发会让每次无关保存都在审计里记归属变更。新建则恒带。
      if (isNew) {
        (payload as PlanCreate).project_key = projectKey;
        (payload as PlanCreate).specialty_key = specialtyKey;
        (payload as PlanCreate).suite_name = suiteName || undefined;
      } else {
        if (projectKey !== origProjectKey) {
          payload.project_key = projectKey;
        }
        if (specialtyKey !== origSpecialtyKey) {
          payload.specialty_key = specialtyKey;
        }
        if (suiteName !== origSuiteName) {
          payload.suite_name = suiteName || null;
        }
      }
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
      const detail = err instanceof ApiError
        ? err.details
        : ax.response?.data?.detail;
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
      // 链尾追加改走后端原子接口(#281 P1):单事务内锁定链尾、校验版本、
      // 创建新 Plan、更新 next_plan_id,冲突整体回滚——不再产生孤立 Plan。
      // 链尾在最近 200 条内时携带其版本令牌;链尾不可见/超出窗口时省略,
      // 服务端仍以行锁保证原子追加(旧实现此时会静默跳过连接)。
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
      const reachedTail = cursor != null && cursor.next_plan_id == null;

      const tail = await api.plans.appendChainTail(planId!, {
        name: proposedName.trim(),
        description: '',
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
        expected_updated_at: reachedTail && cursor ? cursor.updated_at : null,
      });

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
    planIsError,
    planError,
    refetchPlan,
    dependenciesError: allPlansError ?? scriptsError,
    dependenciesIsError: allPlansIsError || scriptsIsError,
    refetchDependencies: () => {
      void refetchAllPlans();
      void refetchScripts();
    },
    name,
    setName,
    description,
    setDescription,
    failureThreshold,
    setFailureThreshold,
    projectKey,
    setProjectKey,
    specialtyKey,
    setSpecialtyKey,
    suiteName,
    setSuiteName,
    projects,
    specialties,
    suites,
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
