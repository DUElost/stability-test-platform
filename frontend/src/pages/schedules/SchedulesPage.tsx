import { useState, useEffect } from 'react';
import { api } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { CronExpressionInput } from '@/components/schedule/CronExpressionInput';
import { Plus, Trash2, Edit2, Play, Loader2, Power } from 'lucide-react';

interface Schedule {
  id: number;
  name: string;
  cron_expression: string;
  task_type: string;
  tool_id?: number;
  target_device_id?: number;
  params: Record<string, any>;
  enabled: boolean;
  last_run_at?: string;
  next_run_at?: string;
  created_at: string;
}

export default function SchedulesPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [form, setForm] = useState({
    name: '',
    cron_expression: '0 2 * * *',
    task_type: 'MONKEY',
    params: '{}',
    enabled: true,
  });

  const loadSchedules = async () => {
    try {
      const res = await api.schedules.list(0, 200);
      setSchedules(res.data.items);
    } catch {
      toast.error('加载定时任务失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadSchedules(); }, []);

  const handleSave = async () => {
    try {
      const data = {
        name: form.name,
        cron_expression: form.cron_expression,
        task_type: form.task_type,
        params: JSON.parse(form.params || '{}'),
        enabled: form.enabled,
      };
      if (editing) {
        await api.schedules.update(editing.id, data);
        toast.success('定时任务更新成功');
      } else {
        await api.schedules.create(data);
        toast.success('定时任务创建成功');
      }
      setShowForm(false);
      setEditing(null);
      loadSchedules();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '保存失败');
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此定时任务吗？', variant: 'destructive' }))) return;
    try {
      await api.schedules.delete(id);
      loadSchedules();
    } catch {
      toast.error('删除失败');
    }
  };

  const handleToggle = async (id: number) => {
    try {
      await api.schedules.toggle(id);
      loadSchedules();
    } catch {
      toast.error('切换失败');
    }
  };

  const handleRunNow = async (id: number) => {
    try {
      const res = await api.schedules.runNow(id);
      toast.success(`任务已创建，ID: ${res.data.task_id}`);
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '执行失败');
    }
  };

  const openEdit = (s: Schedule) => {
    setEditing(s);
    setForm({
      name: s.name,
      cron_expression: s.cron_expression,
      task_type: s.task_type,
      params: JSON.stringify(s.params || {}, null, 2),
      enabled: s.enabled,
    });
    setShowForm(true);
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', cron_expression: '0 2 * * *', task_type: 'MONKEY', params: '{}', enabled: true });
    setShowForm(true);
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">定时任务</h2>
          <p className="text-sm text-gray-400">管理 Cron 定时执行的测试任务</p>
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
          <p className="text-sm text-gray-400">管理 Cron 定时执行的测试任务</p>
        </div>
        <button
          onClick={openCreate}
          className="inline-flex items-center gap-2 bg-gray-900 hover:bg-gray-800 text-white px-4 py-2 rounded-lg font-medium transition-all text-sm"
        >
          <Plus className="w-4 h-4" />
          新建定时任务
        </button>
      </div>

      {/* Form Modal */}
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
              <label className="block text-sm font-medium text-gray-700 mb-1">任务类型</label>
              <select
                value={form.task_type}
                onChange={(e) => setForm({ ...form, task_type: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
              >
                {['MONKEY', 'MTBF', 'DDR', 'GPU', 'STANDBY', 'AIMONKEY'].map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">参数 (JSON)</label>
              <textarea
                value={form.params}
                onChange={(e) => setForm({ ...form, params: e.target.value })}
                rows={3}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono"
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
                onClick={() => { setShowForm(false); setEditing(null); }}
                className="px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Schedules Table */}
      {schedules.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无定时任务</h3>
          <p className="text-sm text-gray-400">创建定时任务以自动执行测试</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left px-4 py-3 font-medium text-gray-500">名称</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500">Cron</th>
                <th className="text-left px-4 py-3 font-medium text-gray-500">类型</th>
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
                  <td className="px-4 py-3 text-gray-600">{s.task_type}</td>
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
