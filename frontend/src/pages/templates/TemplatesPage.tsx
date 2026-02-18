import { useState, useEffect } from 'react';
import { api } from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';
import { Plus, Trash2, Edit2, Loader2 } from 'lucide-react';

interface Template {
  id: number;
  name: string;
  type: string;
  description?: string;
  default_params: Record<string, any>;
  enabled: boolean;
  created_at: string;
}

export default function TemplatesPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Template | null>(null);
  const [form, setForm] = useState({
    name: '',
    type: 'MONKEY',
    description: '',
    default_params: '{}',
    enabled: true,
  });

  const loadTemplates = async () => {
    try {
      const res = await api.templates.list(0, 200);
      setTemplates(res.data.items);
    } catch {
      toast.error('加载模板失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTemplates(); }, []);

  const handleSave = async () => {
    try {
      const data = {
        name: form.name,
        type: form.type,
        description: form.description,
        default_params: JSON.parse(form.default_params || '{}'),
        enabled: form.enabled,
      };
      if (editing) {
        await api.templates.update(editing.id, data);
        toast.success('模板更新成功');
      } else {
        await api.templates.create(data);
        toast.success('模板创建成功');
      }
      setShowForm(false);
      setEditing(null);
      loadTemplates();
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '保存失败');
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除此模板吗？', variant: 'destructive' }))) return;
    try {
      await api.templates.delete(id);
      loadTemplates();
    } catch {
      toast.error('删除失败');
    }
  };

  const openEdit = (t: Template) => {
    setEditing(t);
    setForm({
      name: t.name,
      type: t.type,
      description: t.description || '',
      default_params: JSON.stringify(t.default_params || {}, null, 2),
      enabled: t.enabled,
    });
    setShowForm(true);
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ name: '', type: 'MONKEY', description: '', default_params: '{}', enabled: true });
    setShowForm(true);
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">任务模板</h2>
          <p className="text-sm text-gray-400">管理可复用的任务配置模板</p>
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
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">任务模板</h2>
          <p className="text-sm text-gray-400">管理可复用的任务配置模板</p>
        </div>
        <button
          onClick={openCreate}
          className="inline-flex items-center gap-2 bg-gray-900 hover:bg-gray-800 text-white px-4 py-2 rounded-lg font-medium transition-all text-sm"
        >
          <Plus className="w-4 h-4" />
          新建模板
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-xl border border-gray-200 p-6 max-w-lg">
          <h3 className="text-lg font-medium text-gray-900 mb-4">
            {editing ? '编辑模板' : '新建模板'}
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
              <label className="block text-sm font-medium text-gray-700 mb-1">类型</label>
              <select
                value={form.type}
                onChange={(e) => setForm({ ...form, type: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
              >
                {['MONKEY', 'MTBF', 'DDR', 'GPU', 'STANDBY', 'AIMONKEY'].map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
              <input
                type="text"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">默认参数 (JSON)</label>
              <textarea
                value={form.default_params}
                onChange={(e) => setForm({ ...form, default_params: e.target.value })}
                rows={4}
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
              <button onClick={handleSave} className="px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800">
                保存
              </button>
              <button onClick={() => { setShowForm(false); setEditing(null); }} className="px-4 py-2 border border-gray-200 rounded-lg text-sm hover:bg-gray-50">
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {templates.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <h3 className="text-lg font-medium text-gray-900 mb-2">暂无模板</h3>
          <p className="text-sm text-gray-400">创建任务模板以快速配置测试任务</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {templates.map((t) => (
            <div key={t.id} className="bg-white rounded-xl border border-gray-200 p-4">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h4 className="font-medium text-gray-900">{t.name}</h4>
                  <p className="text-xs text-gray-500 mt-0.5">{t.description || '暂无描述'}</p>
                </div>
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
                  {t.type}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className={`text-xs ${t.enabled ? 'text-emerald-600' : 'text-gray-400'}`}>
                  {t.enabled ? '启用' : '禁用'}
                </span>
                <div className="flex gap-1">
                  <button onClick={() => openEdit(t)} className="p-1.5 text-gray-400 hover:text-gray-600 rounded">
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>
                  <button onClick={() => handleDelete(t.id)} className="p-1.5 text-gray-400 hover:text-red-600 rounded">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
