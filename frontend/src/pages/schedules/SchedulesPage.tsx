import { useEffect, useState } from 'react';
import { api, type TaskSchedule, type TaskScheduleCreatePayload, type WorkflowDefinition } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { CronExpressionInput } from '@/components/schedule/CronExpressionInput';
import { Plus, Trash2, Edit2, Play, Loader2, Power } from 'lucide-react';

interface ScheduleForm {
  name: string;
  cron_expression: string;
  workflow_definition_id: string;
  device_ids: string;
  enabled: boolean;
  legacy_task_type: string;
}

const DEFAULT_FORM: ScheduleForm = {
  name: '',
  cron_expression: '0 2 * * *',
  workflow_definition_id: '',
  device_ids: '',
  enabled: true,
  legacy_task_type: 'MONKEY',
};

function parseDeviceIds(input: string): number[] {
  const values = (input || '')
    .split(',')
    .map(v => Number(v.trim()))
    .filter(v => Number.isInteger(v) && v > 0);
  return Array.from(new Set(values));
}

export default function SchedulesPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [schedules, setSchedules] = useState<TaskSchedule[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<TaskSchedule | null>(null);
  const [form, setForm] = useState<ScheduleForm>(DEFAULT_FORM);

  const loadSchedules = async () => {
    const res = await api.schedules.list(0, 200);
    setSchedules(res.data.items || []);
  };

  const loadWorkflows = async () => {
    const list = await api.orchestration.list(0, 200);
    setWorkflows(list || []);
  };

  const loadAll = async () => {
    try {
      await Promise.all([loadSchedules(), loadWorkflows()]);
    } catch {
      toast.error('加载定时任务失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleSave = async () => {
    try {
      const workflowId = form.workflow_definition_id ? Number(form.workflow_definition_id) : null;
      const deviceIds = parseDeviceIds(form.device_ids);

      if (!editing && !workflowId) {
        toast.error('请选择工作流蓝图');
        return;
      }
      if (workflowId && deviceIds.length === 0) {
        toast.error('请至少填写一个设备 ID');
        return;
      }

      const payload: TaskScheduleCreatePayload = {
        name: form.name,
        cron_expression: form.cron_expression,
        enabled: form.enabled,
      };

      if (workflowId) {
        payload.workflow_definition_id = workflowId;
        payload.device_ids = deviceIds;
        payload.task_type = 'WORKFLOW';
        payload.params = {};
      } else {
        payload.workflow_definition_id = null;
        payload.device_ids = [];
        payload.task_type = form.legacy_task_type || 'MONKEY';
        payload.params = editing?.params || {};
      }

      if (editing) {
        await api.schedules.update(editing.id, payload);
        toast.success('定时任务更新成功');
      } else {
        await api.schedules.create(payload);
        toast.success('定时任务创建成功');
      }

      setShowForm(false);
      setEditing(null);
      setForm(DEFAULT_FORM);
      await loadSchedules();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || err.message || '保存失败');
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此定时任务吗？', variant: 'destructive' }))) return;
    try {
      await api.schedules.delete(id);
      await loadSchedules();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (id: number) => {
    try {
      await api.schedules.toggle(id);
      await loadSchedules();
    } catch {
      toast.error('切换失败');
    }
  };

  const handleRunNow = async (id: number) => {
    try {
      const res = await api.schedules.runNow(id);
      const workflowRunId = res.data.workflow_run_id;
      const taskId = res.data.task_id;
      if (workflowRunId) {
        toast.success(`工作流已触发，Run ID: ${workflowRunId}`);
      } else {
        toast.success(`任务已创建，ID: ${taskId}`);
      }
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '执行失败');
    }
  };

  const openEdit = (s: TaskSchedule) => {
    setEditing(s);
    setForm({
      name: s.name,
      cron_expression: s.cron_expression,
      workflow_definition_id: s.workflow_definition_id ? String(s.workflow_definition_id) : '',
      device_ids: (s.device_ids || []).join(','),
      enabled: s.enabled,
      legacy_task_type: s.task_type || 'MONKEY',
    });
    setShowForm(true);
  };

  const openCreate = () => {
    setEditing(null);
    setForm(DEFAULT_FORM);
    setShowForm(true);
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">定时任务</h2>
          <p className="text-sm text-gray-400">管理 Cron 定时执行的工作流</p>
        </div>
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">定时任务</h2>
          <p className="text-sm text-gray-400">管理 Cron 定时执行的工作流</p>
        </div>
        <button
          onClick={openCreate}
          className="inline-flex items-center gap-2 bg-gray-900 hover:bg-gray-800 text-white px-4 py-2 rounded-lg font-medium transition-all text-sm"
        >
          <Plus className="w-4 h-4" />
          新建定时任务
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-xl border border-gray-200 p-6 max-w-lg">
          <h3 className="text-lg font-medium text-gray-900 mb-4">
            {editing ? '编辑定时任务' : '新建定时任务'}
          </h3>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">名称</label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Cron 表达式</label>
              <CronExpressionInput
                value={form.cron_expression}
                onChange={(v) => setForm({ ...form, cron_expression: v })}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">工作流蓝图</label>
              <select
                value={form.workflow_definition_id}
                onChange={(e) => setForm({ ...form, workflow_definition_id: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
              >
                <option value="">请选择工作流</option>
                {workflows.map(wf => (
                  <option key={wf.id} value={String(wf.id)}>{wf.name} (#{wf.id})</option>
                ))}
              </select>
              {editing && !editing.workflow_definition_id && (
                <p className="text-xs text-amber-600 mt-1">该记录为旧链路定时任务，建议迁移为工作流定时任务。</p>
              )}
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">设备 IDs（逗号分隔）</label>
              <input
                type="text"
                value={form.device_ids}
                onChange={(e) => setForm({ ...form, device_ids: e.target.value })}
                placeholder="例如: 1,2,3"
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                className="rounded"
              />
              <span className="text-sm text-gray-700">启用</span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleSave}
                className="px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800"
              >
                保存
              </button>
              <button
                onClick={() => { setShowForm(false); setEditing(null); setForm(DEFAULT_FORM); }}
                className="px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {schedules.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无定时任务</h3>
          <p className="text-sm text-gray-400">创建定时任务以自动执行工作流</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500">名称</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500">Cron</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500">执行对象</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500">状态</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500">下次执行</th>
                <th className="text-right px-4 py-3 font-medium text-gray-500">操作</th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id} className="border-b border-gray-50 hover:bg-gray-50/50">
                  <td className="px-4 py-3 font-medium text-gray-900">{s.name}</td>
                  <td className="px-4 py-3 font-mono text-gray-600">{s.cron_expression}</td>
                  <td className="px-4 py-3 text-gray-600">
                    {s.workflow_definition_id
                      ? `Workflow #${s.workflow_definition_id} (${(s.device_ids || []).length} devices)`
                      : `Legacy/${s.task_type}`}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                      s.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'
                    }`}>
                      {s.enabled ? '启用' : '禁用'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {s.next_run_at ? new Date(s.next_run_at).toLocaleString() : '-'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-1">
                      <button onClick={() => handleRunNow(s.id)} title="立即执行" className="p-1.5 text-gray-400 hover:text-blue-600 rounded">
                        <Play className="w-4 h-4" />
                      </button>
                      <button onClick={() => handleToggle(s.id)} title="切换状态" className="p-1.5 text-gray-400 hover:text-amber-600 rounded">
                        <Power className="w-4 h-4" />
                      </button>
                      <button onClick={() => openEdit(s)} title="编辑" className="p-1.5 text-gray-400 hover:text-gray-600 rounded">
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button onClick={() => handleDelete(s.id)} title="删除" className="p-1.5 text-gray-400 hover:text-red-600 rounded">
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
