import { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, useNavigate, useBeforeUnload } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { api, type Plan, type PlanUpdate, type PipelineDef, type PlanStepCreate } from '@/utils/api';
import PlanLifecycleEditor from '@/components/pipeline/PlanLifecycleEditor';
import { ArrowLeft, Save, AlertCircle } from 'lucide-react';

const EMPTY_LIFECYCLE: PipelineDef = {
  lifecycle: {
    init: [{ step_id: "new_step", action: "script:check_device", version: "1.0.0", timeout_seconds: 30, retry: 0 }],
    teardown: [],
  },
};

function pipelineSnapshot(def: PipelineDef | null | undefined): string {
  if (!def) return '';
  return JSON.stringify(def);
}

export default function PlanEditPage() {
  const { id } = useParams<{ id: string }>();
  const planId = Number(id);
  const isNew = !planId || planId <= 0;
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();

  // Form state
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [failureThreshold, setFailureThreshold] = useState(0.05);
  const [nextPlanId, setNextPlanId] = useState<number | null>(null);
  const [lifecycle, setLifecycle] = useState<PipelineDef>(EMPTY_LIFECYCLE);
  const [saving, setSaving] = useState(false);

  // Original snapshot for dirty detection
  const [origName, setOrigName] = useState('');
  const [origDesc, setOrigDesc] = useState('');
  const [origThresh, setOrigThresh] = useState(0.05);
  const [origNextPlan, setOrigNextPlan] = useState<number | null>(null);
  const [origLifecycleJson, setOrigLifecycleJson] = useState('');

  // Fetch existing
  const { data: plan, isLoading } = useQuery({
    queryKey: ['plan', planId],
    queryFn: () => api.plans.get(planId),
    enabled: !isNew,
  });

  // Fetch all plans for next_plan_id selector
  const { data: allPlans } = useQuery({
    queryKey: ['plans'],
    queryFn: () => api.plans.list(0, 100),
    enabled: !isNew,
  });

  // Init form
  useEffect(() => {
    if (plan && !isNew) {
      setName(plan.name);
      setDescription(plan.description || '');
      setFailureThreshold(plan.failure_threshold);
      setNextPlanId(plan.next_plan_id ?? null);
      const lc = (plan.lifecycle && typeof plan.lifecycle === 'object') ? plan.lifecycle : {};
      const def: PipelineDef = {
        lifecycle: {
          init: lc.init || [],
          patrol: lc.patrol || undefined,
          teardown: lc.teardown || [],
          timeout_seconds: lc.timeout_seconds,
        },
      };
      setLifecycle(def);

      setOrigName(plan.name);
      setOrigDesc(plan.description || '');
      setOrigThresh(plan.failure_threshold);
      setOrigNextPlan(plan.next_plan_id ?? null);
      setOrigLifecycleJson(pipelineSnapshot(def));
    }
  }, [plan, isNew]);

  // Dirty detection
  const isDirty = useMemo(() => {
    if (isNew) return name !== '' || description !== '' || pipelineSnapshot(lifecycle) !== pipelineSnapshot(EMPTY_LIFECYCLE);
    return name !== origName ||
      description !== origDesc ||
      failureThreshold !== origThresh ||
      nextPlanId !== origNextPlan ||
      pipelineSnapshot(lifecycle) !== origLifecycleJson;
  }, [isNew, name, description, failureThreshold, nextPlanId, lifecycle, origName, origDesc, origThresh, origNextPlan, origLifecycleJson]);

  useBeforeUnload((event) => {
    if (!isDirty) return;
    event.preventDefault();
    event.returnValue = '';
  });

  // Build steps from lifecycle for API payload
  const buildSteps = useCallback((): PlanStepCreate[] => {
    const steps: PlanStepCreate[] = [];
    const lc = lifecycle.lifecycle;
    const addSteps = (phase: string, phaseSteps: any[]) => {
      phaseSteps.forEach((s, i) => {
        const scriptName = (s.action || '').startsWith('script:') ? s.action.slice(7) : '';
        steps.push({
          step_key: s.step_id || `step_${phase}_${i}`,
          script_name: scriptName,
          script_version: s.version || '',
          stage: phase as 'init' | 'patrol' | 'teardown',
          sort_order: i,
          timeout_seconds: s.timeout_seconds,
          retry: s.retry ?? 0,
        });
      });
    };

    if (lc.init) addSteps('init', lc.init);
    if (lc.patrol?.steps) addSteps('patrol', lc.patrol.steps);
    if (lc.teardown) addSteps('teardown', lc.teardown);

    return steps;
  }, [lifecycle]);

  // Save
  const handleSave = async () => {
    if (!name.trim()) { toast.error('请输入 Plan 名称'); return; }

    setSaving(true);
    try {
      const payload: PlanUpdate = {
        name: name.trim(),
        description: description.trim() || undefined,
        failure_threshold: failureThreshold,
        lifecycle: lifecycle.lifecycle as any,
        next_plan_id: nextPlanId ?? undefined as any,
        steps: buildSteps(),
      };

      let saved: Plan;
      if (isNew) {
        saved = await api.plans.create(payload as any);
        toast.success('Plan 已创建');
      } else {
        saved = await api.plans.update(planId, payload);
        toast.success('Plan 已保存');
      }

      queryClient.invalidateQueries({ queryKey: ['plans'] });
      queryClient.setQueryData(['plan', saved.id], saved);

      // Update originals
      if (isNew) {
        navigate(`/orchestration/plans/${saved.id}`, { replace: true });
      } else {
        setOrigName(name.trim());
        setOrigDesc(description.trim() || '');
        setOrigThresh(failureThreshold);
        setOrigNextPlan(nextPlanId);
        setOrigLifecycleJson(pipelineSnapshot(lifecycle));
      }
    } catch (err: any) {
      const detail = err.response?.data?.detail || err.response?.data?.error;
      if (Array.isArray(detail)) {
        // FastAPI Pydantic validation errors
        const msgs = detail.map((d: any) => `${d.loc?.join('.') || ''} ${d.msg}`).join('; ');
        toast.error(msgs || err.message || '保存失败');
      } else if (detail && typeof detail === 'object' && detail.errors) {
        toast.error(`校验失败: ${detail.errors.join('; ')}`);
      } else if (typeof detail === 'string') {
        toast.error(detail);
      } else {
        toast.error(err.message || '保存失败');
      }
    } finally {
      setSaving(false);
    }
  };

  if (!isNew && isLoading) return <Skeleton className="h-96 w-full" />;

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate('/orchestration/plans')}>
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">{isNew ? '新建 Plan' : `编辑 ${name || 'Plan'}`}</h1>
            <p className="text-gray-500 text-sm mt-0.5">
              Plan 定义脚本步骤的执行顺序、超时、重试策略。步骤参数由脚本默认参数自动填充，只读。
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isDirty && (
            <span className="text-xs text-amber-600 bg-amber-50 px-2 py-1 rounded flex items-center gap-1">
              <AlertCircle className="w-3 h-3" /> 未保存
            </span>
          )}
          <Button variant="outline" onClick={() => navigate('/orchestration/plans')}>取消</Button>
          <Button onClick={handleSave} disabled={saving}>
            <Save className="w-4 h-4 mr-1.5" />
            {saving ? '保存中...' : '保存'}
          </Button>
        </div>
      </div>

      {/* Basic Info */}
      <Card>
        <CardHeader><CardTitle className="text-base">基本信息</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">名称 *</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">失败阈值</label>
              <div className="flex items-center gap-2">
                <input type="number" min={0} max={1} step={0.01} value={failureThreshold}
                  onChange={e => setFailureThreshold(parseFloat(e.target.value) || 0)}
                  className="w-24 px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20" />
                <span className="text-xs text-gray-500">= {Math.round(failureThreshold * 100)}%</span>
              </div>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
            <input type="text" value={description} onChange={e => setDescription(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20"
              placeholder="可选描述" />
          </div>
          {!isNew && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">链式下一 Plan (next_plan_id)</label>
              <select value={nextPlanId ?? ''} onChange={e => setNextPlanId(e.target.value ? Number(e.target.value) : null)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/20 bg-white">
                <option value="">— 无 (不链接) —</option>
                {(allPlans || []).filter(p => p.id !== planId).map(p => (
                  <option key={p.id} value={p.id}>{p.name} (#{p.id})</option>
                ))}
              </select>
              <p className="text-xs text-gray-400 mt-1">当前 Plan 执行成功后自动触发链式下一 Plan</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Lifecycle Editor */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">步骤编排 (Lifecycle)</CardTitle>
        </CardHeader>
        <CardContent>
          <PlanLifecycleEditor value={lifecycle} onChange={setLifecycle} />
        </CardContent>
      </Card>

      {/* Step List Preview */}
      {lifecycle.lifecycle.init.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-base">步骤概要</CardTitle></CardHeader>
          <CardContent>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="pb-2 font-medium">阶段</th>
                  <th className="pb-2 font-medium">Step Key</th>
                  <th className="pb-2 font-medium">脚本</th>
                  <th className="pb-2 font-medium">版本</th>
                  <th className="pb-2 font-medium">超时(s)</th>
                  <th className="pb-2 font-medium">重试</th>
                </tr>
              </thead>
              <tbody>
                {buildSteps().map(s => (
                  <tr key={s.step_key} className="border-b last:border-0">
                    <td className="py-1.5">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        s.stage === 'init' ? 'bg-blue-100 text-blue-700' :
                        s.stage === 'patrol' ? 'bg-amber-100 text-amber-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>{s.stage}</span>
                    </td>
                    <td className="py-1.5 font-mono text-xs">{s.step_key}</td>
                    <td className="py-1.5">{s.script_name}</td>
                    <td className="py-1.5 text-xs text-gray-500">{s.script_version}</td>
                    <td className="py-1.5">{s.timeout_seconds ?? '-'}</td>
                    <td className="py-1.5">{s.retry ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
