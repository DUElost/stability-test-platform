import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type PipelineDef } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import StagesPipelineEditor from '@/components/pipeline/StagesPipelineEditor';
import { ArrowLeft, Save, Play, Code2 } from 'lucide-react';

const EMPTY_PIPELINE: PipelineDef = {
  stages: { prepare: [], execute: [], post_process: [] },
};

export default function WorkflowDefinitionEditPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [showJson, setShowJson] = useState(false);
  const [localPipeline, setLocalPipeline] = useState<PipelineDef | null>(null);
  const [basicForm, setBasicForm] = useState<{
    name: string;
    description: string;
    failure_threshold: number;
  } | null>(null);

  const { data: wf, isLoading } = useQuery({
    queryKey: ['workflow-definition', id],
    queryFn: () => api.orchestration.get(Number(id)),
    enabled: !!id,
  });

  // Initialise local form state from fetched data (only on first load)
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
      setLocalPipeline(wf.task_templates?.length
        ? wf.task_templates[0].pipeline_def ?? EMPTY_PIPELINE
        : EMPTY_PIPELINE
      );
    }
  }, [wf?.id]);

  const { data: tools } = useQuery({
    queryKey: ['tool-catalog'],
    queryFn: () => api.toolCatalog.list(true),
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      api.orchestration.update(Number(id), {
        name: basicForm?.name,
        description: basicForm?.description,
        failure_threshold: basicForm?.failure_threshold,
        task_templates: localPipeline
          ? [{ name: 'default', pipeline_def: localPipeline, sort_order: 0 }]
          : undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflow-definition', id] });
      queryClient.invalidateQueries({ queryKey: ['workflow-definitions'] });
      toast.success('工作流已保存');
    },
    onError: (err: any) => toast.error(err.message || '保存失败'),
  });

  const currentPipeline = localPipeline ?? EMPTY_PIPELINE;
  const form = basicForm ?? { name: '', description: '', failure_threshold: 0.05 };

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
      <div className="text-center py-12 text-gray-500">
        工作流不存在
        <Button className="mt-4" variant="outline" onClick={() => navigate('/orchestration/workflows')}>
          返回列表
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate('/orchestration/workflows')}>
            <ArrowLeft className="w-4 h-4 mr-1" />
            返回
          </Button>
          <div>
            <h1 className="text-xl font-semibold text-gray-900">{wf?.name}</h1>
            <p className="text-sm text-gray-500">工作流 #{id}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowJson(v => !v)}
          >
            <Code2 className="w-4 h-4 mr-1" />
            {showJson ? '隐藏 JSON' : '查看 JSON'}
          </Button>
          <Button
            variant="outline"
            onClick={() => navigate(`/execution/run?workflow=${id}`)}
          >
            <Play className="w-4 h-4 mr-2" />
            发起测试
          </Button>
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
          >
            <Save className="w-4 h-4 mr-2" />
            {saveMutation.isPending ? '保存中...' : '保存'}
          </Button>
        </div>
      </div>

      {/* Basic Info */}
      <Card>
        <CardHeader>
          <CardTitle>基本信息</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">名称</label>
              <input
                type="text"
                value={form.name}
                onChange={e => setBasicForm(f => f ? { ...f, name: e.target.value } : null)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                失败阈值（{Math.round((form.failure_threshold ?? 0.05) * 100)}%）
              </label>
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={form.failure_threshold}
                onChange={e => setBasicForm(f => f ? { ...f, failure_threshold: parseFloat(e.target.value) || 0 } : null)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
              <input
                type="text"
                value={form.description}
                onChange={e => setBasicForm(f => f ? { ...f, description: e.target.value } : null)}
                className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Pipeline Editor */}
      <Card>
        <CardHeader>
          <CardTitle>Pipeline 定义（Stages 格式）</CardTitle>
        </CardHeader>
        <CardContent>
          <StagesPipelineEditor
            value={currentPipeline}
            onChange={setLocalPipeline}
            toolOptions={(tools ?? []).map(t => ({ id: t.id, name: t.name, version: t.version }))}
          />
        </CardContent>
      </Card>

      {/* JSON Preview */}
      {showJson && (
        <Card>
          <CardHeader>
            <CardTitle>Pipeline JSON 预览</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="bg-gray-50 rounded-lg p-4 text-xs font-mono overflow-auto max-h-80">
              {JSON.stringify(currentPipeline, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
