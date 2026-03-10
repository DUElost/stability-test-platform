import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useNavigate, useBeforeUnload } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { api, type PipelineDef } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import StagesPipelineEditor from '@/components/pipeline/StagesPipelineEditor';
import {
  ArrowLeft,
  Save,
  Play,
  Code2,
  Library,
  Layers3,
  CheckCircle2,
  AlertCircle,
  Clock3,
  Copy,
} from 'lucide-react';

const EMPTY_PIPELINE: PipelineDef = {
  stages: { prepare: [], execute: [], post_process: [] },
};

function normalizePipeline(def?: PipelineDef | null): PipelineDef {
  return {
    stages: {
      prepare: def?.stages?.prepare ?? [],
      execute: def?.stages?.execute ?? [],
      post_process: def?.stages?.post_process ?? [],
    },
  };
}

function pipelineSnapshot(def?: PipelineDef | null) {
  return JSON.stringify(normalizePipeline(def));
}

function formSnapshot(form: { name: string; description: string; failure_threshold: number }) {
  return JSON.stringify({
    name: form.name.trim(),
    description: form.description.trim(),
    failure_threshold: Number(form.failure_threshold ?? 0),
  });
}

function formatTime(iso?: string | null) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function WorkflowDefinitionEditPage() {
  const { id } = useParams<{ id: string }>();
  const workflowId = Number(id);
  const isValidId = Number.isFinite(workflowId) && workflowId > 0;

  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const navPromptingRef = useRef(false);

  const [showJson, setShowJson] = useState(false);
  const [localPipeline, setLocalPipeline] = useState<PipelineDef | null>(null);
  const [basicForm, setBasicForm] = useState<{
    name: string;
    description: string;
    failure_threshold: number;
  } | null>(null);

  useEffect(() => {
    setBasicForm(null);
    setLocalPipeline(null);
  }, [workflowId]);

  const { data: wf, isLoading } = useQuery({
    queryKey: ['workflow-definition', workflowId],
    queryFn: () => api.orchestration.get(workflowId),
    enabled: isValidId,
  });

  useEffect(() => {
    if (!wf) return;
    if (!basicForm) {
      setBasicForm({
        name: wf.name,
        description: wf.description || '',
        failure_threshold: wf.failure_threshold,
      });
    }
    if (!localPipeline) {
      setLocalPipeline(normalizePipeline(wf.task_templates?.[0]?.pipeline_def ?? EMPTY_PIPELINE));
    }
  }, [wf, basicForm, localPipeline]);

  const { data: tools } = useQuery({
    queryKey: ['tool-catalog'],
    queryFn: () => api.toolCatalog.list(true),
  });

  const { data: builtinActions } = useQuery({
    queryKey: ['builtin-catalog'],
    queryFn: () => api.builtinCatalog.list(true),
  });

  const { data: actionTemplates } = useQuery({
    queryKey: ['action-templates'],
    queryFn: () => api.actionTemplates.list(true),
  });

  const effectivePipeline = useMemo(
    () => normalizePipeline(localPipeline ?? wf?.task_templates?.[0]?.pipeline_def ?? EMPTY_PIPELINE),
    [localPipeline, wf],
  );

  const form = basicForm ?? { name: '', description: '', failure_threshold: 0.05 };

  const stageCounts = useMemo(() => {
    const prepare = effectivePipeline.stages.prepare?.length ?? 0;
    const execute = effectivePipeline.stages.execute?.length ?? 0;
    const postProcess = effectivePipeline.stages.post_process?.length ?? 0;
    return {
      prepare,
      execute,
      postProcess,
      total: prepare + execute + postProcess,
    };
  }, [effectivePipeline]);

  const hasUnsavedChanges = useMemo(() => {
    if (!wf || !basicForm || !localPipeline) return false;
    const baseForm = {
      name: wf.name,
      description: wf.description || '',
      failure_threshold: wf.failure_threshold,
    };
    const basePipeline = wf.task_templates?.[0]?.pipeline_def ?? EMPTY_PIPELINE;
    return (
      formSnapshot(basicForm) !== formSnapshot(baseForm)
      || pipelineSnapshot(localPipeline) !== pipelineSnapshot(basePipeline)
    );
  }, [wf, basicForm, localPipeline]);

  const confirmDiscardIfDirty = useCallback(async (): Promise<boolean> => {
    if (!hasUnsavedChanges) return true;
    return confirmDialog({
      title: '离开当前编辑页？',
      description: '当前存在未保存内容，离开后这些修改会丢失。',
      confirmText: '离开并丢弃',
      cancelText: '继续编辑',
      variant: 'destructive',
    });
  }, [confirmDialog, hasUnsavedChanges]);

  useBeforeUnload((event) => {
    if (!hasUnsavedChanges) return;
    event.preventDefault();
    event.returnValue = '';
  });

  useEffect(() => {
    if (!hasUnsavedChanges) return;

    const onLinkClickCapture = async (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const target = event.target as HTMLElement | null;
      const anchor = target?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!anchor) return;
      if (anchor.target && anchor.target !== '_self') return;

      const href = anchor.getAttribute('href');
      if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;

      let toUrl: URL;
      try {
        toUrl = new URL(anchor.href, window.location.origin);
      } catch {
        return;
      }

      const currentUrl = new URL(window.location.href);
      if (toUrl.origin !== currentUrl.origin) return;
      if (
        toUrl.pathname === currentUrl.pathname
        && toUrl.search === currentUrl.search
        && toUrl.hash === currentUrl.hash
      ) {
        return;
      }

      event.preventDefault();
      if (navPromptingRef.current) return;
      navPromptingRef.current = true;
      try {
        const ok = await confirmDiscardIfDirty();
        if (ok) {
          navigate(`${toUrl.pathname}${toUrl.search}${toUrl.hash}`);
        }
      } finally {
        navPromptingRef.current = false;
      }
    };

    document.addEventListener('click', onLinkClickCapture, true);
    return () => document.removeEventListener('click', onLinkClickCapture, true);
  }, [hasUnsavedChanges, confirmDiscardIfDirty, navigate]);

  const navigateWithGuard = useCallback(async (to: string) => {
    const ok = await confirmDiscardIfDirty();
    if (!ok) return;
    navigate(to);
  }, [confirmDiscardIfDirty, navigate]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!isValidId) throw new Error('工作流 ID 无效');
      return api.orchestration.update(workflowId, {
        name: basicForm?.name,
        description: basicForm?.description,
        failure_threshold: basicForm?.failure_threshold,
        task_templates: [{ name: 'default', pipeline_def: effectivePipeline, sort_order: 0 }],
      });
    },
    onSuccess: (updated) => {
      // Directly update the query cache instead of invalidating (which is async)
      queryClient.setQueryData(['workflow-definition', workflowId], updated);
      queryClient.invalidateQueries({ queryKey: ['workflow-definitions'] });
      setBasicForm({
        name: updated.name,
        description: updated.description || '',
        failure_threshold: updated.failure_threshold,
      });
      setLocalPipeline(normalizePipeline(updated.task_templates?.[0]?.pipeline_def ?? EMPTY_PIPELINE));
      toast.success('工作流已保存');
    },
    onError: (err: any) => toast.error(err.message || '保存失败'),
  });

  const handleCopyJson = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(effectivePipeline, null, 2));
      toast.success('Pipeline JSON 已复制');
    } catch {
      toast.error('复制失败，请手动复制');
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!wf && !isLoading) {
    return (
      <div className="py-12 text-center text-gray-500">
        <p>工作流不存在</p>
        <Button className="mt-4" variant="outline" onClick={() => navigate('/orchestration/workflows')}>
          返回列表
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-gray-200 bg-gradient-to-r from-slate-50 to-white p-4 lg:p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void navigateWithGuard('/orchestration/workflows')}
              className="h-8 px-2"
            >
              <ArrowLeft className="mr-1 h-4 w-4" />
              返回列表
            </Button>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-xl font-semibold text-gray-900">{wf?.name}</h1>
                {hasUnsavedChanges ? (
                  <Badge variant="warning" className="gap-1">
                    <AlertCircle className="h-3.5 w-3.5" />
                    未保存
                  </Badge>
                ) : (
                  <Badge variant="success" className="gap-1">
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    已保存
                  </Badge>
                )}
              </div>
              <p className="mt-1 text-sm text-gray-500">工作流 #{workflowId} · 创建于 {formatTime(wf?.created_at)}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowJson((v) => !v)}
            >
              <Code2 className="mr-1 h-4 w-4" />
              {showJson ? '隐藏 JSON' : '查看 JSON'}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void navigateWithGuard('/orchestration/actions')}
            >
              <Library className="mr-1 h-4 w-4" />
              动作目录
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void navigateWithGuard(`/execution/run?workflow=${workflowId}`)}
            >
              <Play className="mr-1 h-4 w-4" />
              发起测试
            </Button>
            <Button
              size="sm"
              onClick={() => saveMutation.mutate()}
              disabled={saveMutation.isPending || !hasUnsavedChanges || !form.name.trim()}
            >
              <Save className="mr-1 h-4 w-4" />
              {saveMutation.isPending ? '保存中...' : '保存修改'}
            </Button>
          </div>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>基本信息</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">名称</label>
                  <Input
                    type="text"
                    value={form.name}
                    onChange={(e) => setBasicForm((f) => (f ? { ...f, name: e.target.value } : null))}
                    placeholder="输入工作流名称"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">
                    失败阈值（{Math.round((form.failure_threshold ?? 0.05) * 100)}%）
                  </label>
                  <Input
                    type="number"
                    min={0}
                    max={1}
                    step={0.01}
                    value={form.failure_threshold}
                    onChange={(e) => setBasicForm((f) => {
                      if (!f) return null;
                      return { ...f, failure_threshold: parseFloat(e.target.value) || 0 };
                    })}
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="mb-1 block text-sm font-medium text-gray-700">描述</label>
                  <textarea
                    value={form.description}
                    onChange={(e) => setBasicForm((f) => (f ? { ...f, description: e.target.value } : null))}
                    rows={3}
                    className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    placeholder="描述该工作流的使用场景和目标"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <CardTitle>Pipeline 定义（Stages）</CardTitle>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <Clock3 className="h-3.5 w-3.5" />
                  总计 {stageCounts.total} 个 Step
                </div>
              </div>
              <div className="flex flex-wrap gap-2 text-xs">
                <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">Prepare {stageCounts.prepare}</span>
                <span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-700">Execute {stageCounts.execute}</span>
                <span className="rounded-full bg-amber-100 px-2 py-1 text-amber-700">Post Process {stageCounts.postProcess}</span>
              </div>
            </CardHeader>
            <CardContent>
              <StagesPipelineEditor
                value={effectivePipeline}
                onChange={setLocalPipeline}
                toolOptions={(tools ?? []).map((t) => ({ id: t.id, name: t.name, version: t.version }))}
                builtinOptions={(builtinActions ?? []).map((a) => ({
                  name: a.name,
                  label: a.label,
                  category: a.category,
                  description: a.description,
                  param_schema: a.param_schema ?? {},
                  is_active: a.is_active,
                }))}
                actionTemplateOptions={(actionTemplates ?? []).map((t) => ({
                  id: t.id,
                  name: t.name,
                  action: t.action,
                  version: t.version,
                  params: t.params ?? {},
                  timeout_seconds: t.timeout_seconds,
                  retry: t.retry,
                  is_active: t.is_active,
                }))}
              />
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Layers3 className="h-4 w-4 text-slate-500" />
                编排摘要
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-gray-500">工作流名称</span>
                <span className="max-w-[180px] truncate font-medium text-gray-900">{form.name || '-'}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">失败阈值</span>
                <span className="font-medium text-gray-900">{Math.round((form.failure_threshold ?? 0.05) * 100)}%</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">任务模板数</span>
                <span className="font-medium text-gray-900">{wf?.task_templates?.length ?? 0}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-gray-500">Step 总数</span>
                <span className="font-medium text-gray-900">{stageCounts.total}</span>
              </div>
              <div className="rounded-lg bg-slate-50 p-3 text-xs text-gray-600">
                建议先在动作目录维护可复用 Action，再回到此处编排 Stage，提高蓝图复用效率。
              </div>
            </CardContent>
          </Card>

          {showJson && (
            <Card>
              <CardHeader className="flex flex-row items-center justify-between space-y-0">
                <CardTitle className="text-base">Pipeline JSON 预览</CardTitle>
                <Button variant="outline" size="sm" onClick={() => void handleCopyJson()}>
                  <Copy className="mr-1 h-3.5 w-3.5" />
                  复制
                </Button>
              </CardHeader>
              <CardContent>
                <pre className="max-h-[420px] overflow-auto rounded-lg bg-gray-50 p-3 text-xs font-mono">
                  {JSON.stringify(effectivePipeline, null, 2)}
                </pre>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
