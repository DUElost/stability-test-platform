import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type WorkflowDefinition, type WorkflowDefinitionCreate } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { Plus, Play, Pencil, Trash2, ChevronRight, Workflow } from 'lucide-react';

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
  const [showCreate, setShowCreate] = useState(false);

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

  const handleDelete = (wf: WorkflowDefinition) => {
    if (!confirm(`确认删除工作流「${wf.name}」？`)) return;
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

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">工作流设计</h1>
          <p className="text-gray-500 mt-1">管理测试蓝图（WorkflowDefinition）</p>
        </div>
        <div className="flex gap-2">
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

      <Card>
        <CardHeader>
          <CardTitle>工作流列表</CardTitle>
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
          ) : (
            <div className="divide-y">
              {workflows.map(wf => (
                <div key={wf.id} className="flex items-center gap-4 py-4 group">
                  <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-blue-50 flex-shrink-0">
                    <Workflow className="w-5 h-5 text-blue-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-gray-900 truncate">{wf.name}</div>
                    <div className="text-sm text-gray-500 truncate">
                      {wf.description || '无描述'}
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                      <span>失败阈值 {formatThreshold(wf.failure_threshold)}</span>
                      {wf.task_templates && (
                        <span>{wf.task_templates.length} 个任务模板</span>
                      )}
                      <span>{formatTime(wf.created_at)}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(`/execution/run?workflow=${wf.id}`)}
                      title="发起测试"
                    >
                      <Play className="w-4 h-4 text-blue-500" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate(`/orchestration/workflows/${wf.id}`)}
                      title="编辑"
                    >
                      <Pencil className="w-4 h-4 text-gray-500" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(wf)}
                      title="删除"
                    >
                      <Trash2 className="w-4 h-4 text-red-400" />
                    </Button>
                  </div>
                  <ChevronRight
                    className="w-5 h-5 text-gray-300 cursor-pointer"
                    onClick={() => navigate(`/orchestration/workflows/${wf.id}`)}
                  />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
