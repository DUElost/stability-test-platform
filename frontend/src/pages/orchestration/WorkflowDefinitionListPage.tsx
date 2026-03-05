import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { api, type WorkflowDefinition, type WorkflowDefinitionCreate } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import {
  Plus,
  Play,
  Pencil,
  Trash2,
  ChevronRight,
  Workflow,
  Search,
  List,
  Library,
  ShieldAlert,
  X,
} from 'lucide-react';

function formatThreshold(v: number) {
  return `${Math.round(v * 100)}%`;
}

function formatTime(iso: string) {
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

interface CreateModalProps {
  onClose: () => void;
  onCreated: (wf: WorkflowDefinition) => void;
}

function CreateModal({ onClose, onCreated }: CreateModalProps) {
  const [form, setForm] = useState<WorkflowDefinitionCreate>({
    name: '',
    description: '',
    failure_threshold: 0.05,
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) { setError('工作流名称不能为空'); return; }
    setSubmitting(true);
    try {
      const wf = await api.orchestration.create(form);
      onCreated(wf);
    } catch (err: any) {
      setError(err.message || '创建失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold mb-4">新建工作流</h2>
        {error && (
          <div className="mb-3 p-2 rounded bg-red-50 text-red-600 text-sm">{error}</div>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">名称 *</label>
            <input
              type="text"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
              placeholder="例如：Monkey 稳定性测试"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
            <textarea
              value={form.description}
              onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
              rows={3}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
              placeholder="工作流用途说明"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              失败阈值（{formatThreshold(form.failure_threshold ?? 0.05)}）
            </label>
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={form.failure_threshold}
              onChange={e => setForm(f => ({ ...f, failure_threshold: parseFloat(e.target.value) || 0 }))}
              className="w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
            />
            <p className="text-xs text-gray-400 mt-1">允许失败的设备比例（0.05 = 5%）</p>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              取消
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? '创建中...' : '创建'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function WorkflowDefinitionListPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [showCreate, setShowCreate] = useState(false);
  const [query, setQuery] = useState('');

  const { data: workflows, isLoading } = useQuery({
    queryKey: ['workflow-definitions'],
    queryFn: () => api.orchestration.list(0, 100),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.orchestration.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflow-definitions'] });
      toast.success('工作流已删除');
    },
    onError: (err: any) => toast.error(err.message || '删除失败'),
  });

  const filteredWorkflows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return workflows ?? [];
    return (workflows ?? []).filter((wf) => (
      wf.name.toLowerCase().includes(q) || (wf.description || '').toLowerCase().includes(q)
    ));
  }, [workflows, query]);

  const stats = useMemo(() => {
    const list = workflows ?? [];
    const total = list.length;
    const templateCount = list.reduce((acc, wf) => acc + (wf.task_templates?.length ?? 0), 0);
    const avgFailureThreshold = total > 0
      ? list.reduce((acc, wf) => acc + (wf.failure_threshold ?? 0), 0) / total
      : 0;
    return { total, templateCount, avgFailureThreshold };
  }, [workflows]);

  const handleDelete = async (wf: WorkflowDefinition) => {
    const ok = await confirmDialog({
      title: '删除工作流',
      description: `确认删除「${wf.name}」？此操作不可恢复。`,
      confirmText: '删除',
      cancelText: '取消',
      variant: 'destructive',
    });
    if (!ok) return;
    deleteMutation.mutate(wf.id);
  };

  const handleCreated = (wf: WorkflowDefinition) => {
    queryClient.invalidateQueries({ queryKey: ['workflow-definitions'] });
    setShowCreate(false);
    toast.success(`工作流「${wf.name}」已创建`);
    navigate(`/orchestration/workflows/${wf.id}`);
  };

  return (
    <div className="space-y-6">
      {showCreate && (
        <CreateModal onClose={() => setShowCreate(false)} onCreated={handleCreated} />
      )}

      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">工作流设计</h1>
          <p className="mt-1 text-sm text-gray-500">管理测试蓝图并快速进入编排编辑</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => navigate('/execution/run')}>
            <Play className="w-4 h-4 mr-2" />
            发起测试
          </Button>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="w-4 h-4 mr-2" />
            新建工作流
          </Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="border-gray-200/80">
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100">
              <List className="h-4 w-4 text-slate-600" />
            </div>
            <div>
              <p className="text-xs text-gray-500">工作流总数</p>
              <p className="text-lg font-semibold text-gray-900">{stats.total}</p>
            </div>
          </CardContent>
        </Card>
        <Card className="border-gray-200/80">
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-100">
              <Library className="h-4 w-4 text-emerald-700" />
            </div>
            <div>
              <p className="text-xs text-gray-500">任务模板总数</p>
              <p className="text-lg font-semibold text-gray-900">{stats.templateCount}</p>
            </div>
          </CardContent>
        </Card>
        <Card className="border-gray-200/80">
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-100">
              <ShieldAlert className="h-4 w-4 text-amber-700" />
            </div>
            <div>
              <p className="text-xs text-gray-500">平均失败阈值</p>
              <p className="text-lg font-semibold text-gray-900">{formatThreshold(stats.avgFailureThreshold)}</p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <CardTitle>工作流列表</CardTitle>
            <span className="text-xs text-gray-500">共 {filteredWorkflows.length} 条</span>
          </div>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="按名称或描述搜索工作流"
              className="h-9 pl-9 pr-9"
            />
            {query && (
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600"
                onClick={() => setQuery('')}
                aria-label="清空搜索"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : !workflows?.length ? (
            <div className="text-center py-12 text-gray-500">
              <Workflow className="w-12 h-12 mx-auto mb-4 text-gray-300" />
              <p>暂无工作流</p>
              <p className="text-sm mt-1">点击「新建工作流」创建第一个测试蓝图</p>
            </div>
          ) : !filteredWorkflows.length ? (
            <div className="text-center py-12 text-gray-500">
              <Search className="mx-auto mb-4 h-10 w-10 text-gray-300" />
              <p>未找到匹配的工作流</p>
              <p className="mt-1 text-sm">请调整搜索关键词</p>
            </div>
          ) : (
            <div className="space-y-3">
              {filteredWorkflows.map((wf) => (
                <div
                  key={wf.id}
                  className="group rounded-xl border border-gray-200 bg-white p-4 transition-all hover:border-gray-300 hover:shadow-sm"
                >
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
                    <div className="flex items-center justify-center h-10 w-10 rounded-lg bg-slate-100 flex-shrink-0">
                      <Workflow className="h-5 w-5 text-slate-700" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-semibold text-gray-900">{wf.name}</h3>
                        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                          {formatThreshold(wf.failure_threshold)}
                        </span>
                      </div>
                      <p className="mt-1 truncate text-sm text-gray-500">{wf.description || '无描述'}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-700">
                          {wf.task_templates?.length ?? 0} 个任务模板
                        </span>
                        <span>创建于 {formatTime(wf.created_at)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-1 lg:opacity-0 lg:group-hover:opacity-100 transition-opacity">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => navigate(`/execution/run?workflow=${wf.id}`)}
                        title="发起测试"
                      >
                        <Play className="w-4 h-4 text-slate-600" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => navigate(`/orchestration/workflows/${wf.id}`)}
                        title="编辑"
                      >
                        <Pencil className="w-4 h-4 text-slate-600" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void handleDelete(wf)}
                        title="删除"
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="w-4 h-4 text-red-500" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => navigate(`/orchestration/workflows/${wf.id}`)}
                        title="查看详情"
                      >
                        <ChevronRight className="w-4 h-4 text-gray-400" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
