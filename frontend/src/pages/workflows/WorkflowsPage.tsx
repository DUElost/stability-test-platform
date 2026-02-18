import { useState, useEffect } from 'react';
import { api, Workflow, WorkflowStep, WorkflowStepCreate } from '@/utils/api';
import { CleanCard } from '@/components/ui/clean-card';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { CreateWorkflowModal } from './CreateWorkflowModal';
import {
  Plus,
  Play,
  XCircle,
  Trash2,
  ChevronDown,
  ChevronRight,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Clock,
  Ban,
} from 'lucide-react';

const STATUS_CONFIG: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  DRAFT:     { label: '草稿',   color: 'bg-gray-100 text-gray-600',   icon: <Clock size={14} /> },
  READY:     { label: '就绪',   color: 'bg-blue-100 text-blue-600',   icon: <Clock size={14} /> },
  RUNNING:   { label: '运行中', color: 'bg-yellow-100 text-yellow-700', icon: <Loader2 size={14} className="animate-spin" /> },
  COMPLETED: { label: '已完成', color: 'bg-green-100 text-green-700',  icon: <CheckCircle2 size={14} /> },
  FAILED:    { label: '失败',   color: 'bg-red-100 text-red-700',     icon: <AlertCircle size={14} /> },
  CANCELED:  { label: '已取消', color: 'bg-gray-100 text-gray-500',   icon: <Ban size={14} /> },
};

const STEP_STATUS_CONFIG: Record<string, { color: string; dot: string }> = {
  PENDING:   { color: 'text-gray-500', dot: 'bg-gray-300' },
  RUNNING:   { color: 'text-yellow-600', dot: 'bg-yellow-400 animate-pulse' },
  COMPLETED: { color: 'text-green-600', dot: 'bg-green-500' },
  FAILED:    { color: 'text-red-600', dot: 'bg-red-500' },
  SKIPPED:   { color: 'text-gray-400', dot: 'bg-gray-300' },
};

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.DRAFT;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.color}`}>
      {cfg.icon} {cfg.label}
    </span>
  );
}

function StepTimeline({ steps }: { steps: WorkflowStep[] }) {
  const sorted = [...steps].sort((a, b) => a.order - b.order);
  return (
    <div className="pl-4 py-2 space-y-2">
      {sorted.map((step) => {
        const cfg = STEP_STATUS_CONFIG[step.status] || STEP_STATUS_CONFIG.PENDING;
        return (
          <div key={step.id} className="flex items-start gap-3">
            <div className="flex flex-col items-center mt-1">
              <div className={`w-2.5 h-2.5 rounded-full ${cfg.dot}`} />
              {step.order < sorted.length && <div className="w-px h-4 bg-gray-200 mt-0.5" />}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className={`text-sm font-medium ${cfg.color}`}>{step.name}</span>
                <span className="text-xs text-gray-400">#{step.order}</span>
              </div>
              {step.error_message && (
                <p className="text-xs text-red-500 mt-0.5 truncate">{step.error_message}</p>
              )}
              {step.started_at && (
                <p className="text-xs text-gray-400 mt-0.5">
                  {new Date(step.started_at).toLocaleString()}
                  {step.finished_at && ` → ${new Date(step.finished_at).toLocaleString()}`}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function WorkflowsPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [actionLoading, setActionLoading] = useState<number | null>(null);

  const loadWorkflows = async () => {
    try {
      const resp = await api.workflows.list(0, 200);
      setWorkflows(resp.data.items);
    } catch (err) {
      console.error('加载工作流失败:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWorkflows();
    // Auto-refresh every 10s
    const interval = setInterval(loadWorkflows, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleCreate = async (data: { name: string; description: string; steps: WorkflowStepCreate[] }) => {
    setCreating(true);
    try {
      await api.workflows.create(data);
      setShowCreate(false);
      loadWorkflows();
    } catch (err) {
      console.error('创建工作流失败:', err);
      toast.error('创建工作流失败');
    } finally {
      setCreating(false);
    }
  };

  const handleStart = async (id: number) => {
    setActionLoading(id);
    try {
      await api.workflows.start(id);
      loadWorkflows();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '启动失败');
    } finally {
      setActionLoading(null);
    }
  };

  const handleCancel = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要取消此工作流吗？', variant: 'destructive' }))) return;
    setActionLoading(id);
    try {
      await api.workflows.cancel(id);
      loadWorkflows();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '取消失败');
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此工作流吗？仅草稿状态可删除。', variant: 'destructive' }))) return;
    setActionLoading(id);
    try {
      await api.workflows.delete(id);
      loadWorkflows();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '删除失败');
    } finally {
      setActionLoading(null);
    }
  };

  const toggleExpand = (id: number) => {
    setExpandedId(prev => (prev === id ? null : id));
  };

  const handleClone = async (id: number) => {
    setActionLoading(id);
    try {
      await api.workflows.clone(id);
      toast.success('工作流克隆成功');
      loadWorkflows();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '克隆失败');
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">工作流管理</h2>
          <p className="text-sm text-gray-400">创建和管理多步骤自动化测试工作流</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
        >
          <Plus size={18} /> 创建工作流
        </button>
      </div>

      {loading ? (
        <CleanCard className="p-8 text-center">
          <Loader2 className="w-8 h-8 mx-auto animate-spin text-gray-400" />
          <p className="mt-2 text-sm text-gray-400">加载中...</p>
        </CleanCard>
      ) : workflows.length === 0 ? (
        <CleanCard className="p-8 text-center">
          <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gray-50 flex items-center justify-center">
            <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2" />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无工作流</h3>
          <p className="text-sm text-gray-400 mb-4">点击上方按钮创建第一个工作流</p>
        </CleanCard>
      ) : (
        <div className="space-y-3">
          {workflows.map(wf => {
            const isExpanded = expandedId === wf.id;
            const isActing = actionLoading === wf.id;
            const canStart = wf.status === 'DRAFT' || wf.status === 'READY';
            const canCancel = wf.status === 'RUNNING';
            const canDelete = wf.status === 'DRAFT';
            const completedSteps = wf.steps.filter(s => s.status === 'COMPLETED').length;

            return (
              <CleanCard key={wf.id} className="overflow-hidden">
                {/* Row header */}
                <div
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-gray-50/50 transition-colors"
                  onClick={() => toggleExpand(wf.id)}
                >
                  <button className="text-gray-400 shrink-0">
                    {isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                  </button>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <span className="font-medium text-gray-900 truncate">{wf.name}</span>
                      <StatusBadge status={wf.status} />
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-gray-400">
                      <span>{wf.steps.length} 个步骤</span>
                      {wf.steps.length > 0 && (
                        <span>完成 {completedSteps}/{wf.steps.length}</span>
                      )}
                      <span>{new Date(wf.created_at).toLocaleDateString()}</span>
                      {wf.description && (
                        <span className="truncate max-w-[200px]">{wf.description}</span>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 shrink-0" onClick={e => e.stopPropagation()}>
                    {canStart && (
                      <button
                        onClick={() => handleStart(wf.id)}
                        disabled={isActing}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm text-green-700 bg-green-50 hover:bg-green-100 rounded-md transition-colors disabled:opacity-50"
                      >
                        {isActing ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                        启动
                      </button>
                    )}
                    {canCancel && (
                      <button
                        onClick={() => handleCancel(wf.id)}
                        disabled={isActing}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm text-yellow-700 bg-yellow-50 hover:bg-yellow-100 rounded-md transition-colors disabled:opacity-50"
                      >
                        {isActing ? <Loader2 size={14} className="animate-spin" /> : <XCircle size={14} />}
                        取消
                      </button>
                    )}
                    {canDelete && (
                      <button
                        onClick={() => handleDelete(wf.id)}
                        disabled={isActing}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm text-red-600 bg-red-50 hover:bg-red-100 rounded-md transition-colors disabled:opacity-50"
                      >
                        {isActing ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        删除
                      </button>
                    )}
                    <button
                      onClick={() => handleClone(wf.id)}
                      disabled={isActing}
                      className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-600 bg-gray-50 hover:bg-gray-100 rounded-md transition-colors disabled:opacity-50"
                    >
                      克隆
                    </button>
                  </div>
                </div>

                {/* Expanded: step timeline */}
                {isExpanded && wf.steps.length > 0 && (
                  <div className="border-t border-gray-100 px-5 py-3 bg-gray-50/30">
                    <StepTimeline steps={wf.steps} />
                  </div>
                )}
              </CleanCard>
            );
          })}
        </div>
      )}

      <CreateWorkflowModal
        isOpen={showCreate}
        onClose={() => setShowCreate(false)}
        onSubmit={handleCreate}
        isSubmitting={creating}
      />
    </div>
  );
}
