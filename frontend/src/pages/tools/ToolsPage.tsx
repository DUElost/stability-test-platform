import { useState, useEffect } from 'react';
import { api, ToolEntry } from '@/utils/api';
import { Wrench, Plus, RefreshCw, Trash2, Edit, AlertCircle } from 'lucide-react';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';

export default function ToolsPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [categories, setCategories] = useState<string[]>([]);
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingTool, setEditingTool] = useState<ToolEntry | null>(null);

  const loadCategories = async () => {
    try {
      const cats = await api.tools.listCategories();
      setCategories(cats);
    } catch (error) {
      console.error('加载分类失败:', error);
    }
  };

  const loadTools = async (category?: string) => {
    setLoading(true);
    try {
      const list = await api.tools.list(category || undefined);
      setTools(list);
    } catch (error) {
      console.error('加载工具失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleScan = async () => {
    setScanning(true);
    try {
      const result = await api.tools.scan();
      toast.success(`扫描完成：新增 ${result.created} 个，更新 ${result.updated} 个`);
      loadCategories();
      loadTools(selectedCategory || undefined);
    } catch (error) {
      console.error('扫描失败:', error);
      toast.error('扫描失败');
    } finally {
      setScanning(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要停用这个工具吗？', variant: 'destructive' }))) return;
    try {
      await api.tools.delete(id);
      loadTools(selectedCategory || undefined);
    } catch (error) {
      console.error('停用失败:', error);
      toast.error('停用失败');
    }
  };

  useEffect(() => {
    loadCategories();
    loadTools();
  }, []);

  useEffect(() => {
    loadTools(selectedCategory || undefined);
  }, [selectedCategory]);

  const filteredTools = selectedCategory
    ? tools.filter(t => t.category === selectedCategory)
    : tools;

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-blue-50 rounded-lg">
            <Wrench className="w-6 h-6 text-blue-600" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">工具专项</h1>
            <p className="text-sm text-gray-500">管理测试工具脚本</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={handleScan}
            disabled={scanning}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${scanning ? 'animate-spin' : ''}`} />
            {scanning ? '扫描中...' : '扫描工具'}
          </button>
          <button
            onClick={() => setShowCreateModal(true)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700"
          >
            <Plus className="w-4 h-4" />
            添加工具
          </button>
        </div>
      </div>

      <div className="mb-6">
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => setSelectedCategory(null)}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
              selectedCategory === null
                ? 'bg-blue-600 text-white'
                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }`}
          >
            全部 ({tools.length})
          </button>
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setSelectedCategory(cat)}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                selectedCategory === cat
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <RefreshCw className="w-8 h-8 animate-spin text-gray-400" />
        </div>
      ) : filteredTools.length === 0 ? (
        <div className="flex flex-col items-center justify-center h-64 bg-gray-50 rounded-lg">
          <AlertCircle className="w-12 h-12 text-gray-400 mb-4" />
          <p className="text-gray-500">暂无工具</p>
          <p className="text-sm text-gray-400 mt-1">点击"扫描工具"自动发现脚本</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredTools.map(tool => (
            <div
              key={tool.id}
              className="bg-white border border-gray-200 rounded-lg p-4 hover:shadow-md transition-shadow"
            >
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="font-medium text-gray-900">{tool.name}</h3>
                  <p className="text-sm text-gray-500">{tool.category || '未分类'}</p>
                </div>
                <span className={`px-2 py-1 text-xs font-medium rounded ${
                  tool.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                }`}>
                  {tool.is_active ? '启用' : '禁用'}
                </span>
              </div>

              {tool.description && (
                <p className="text-sm text-gray-600 mb-3 line-clamp-2">{tool.description}</p>
              )}

              <div className="text-xs text-gray-400 mb-3 space-y-1">
                <p className="truncate" title={tool.script_path}>
                  脚本: {tool.script_path}
                </p>
                <p>类名: {tool.script_class}</p>
                <p>版本: {tool.version}</p>
              </div>

              <div className="flex items-center gap-2 pt-3 border-t border-gray-100">
                <button
                  onClick={() => setEditingTool(tool)}
                  className="flex-1 flex items-center justify-center gap-1 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 rounded"
                >
                  <Edit className="w-3.5 h-3.5" />
                  编辑
                </button>
                <button
                  onClick={() => handleDelete(tool.id)}
                  className="flex-1 flex items-center justify-center gap-1 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  停用
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {(showCreateModal || editingTool) && (
        <ToolModal
          tool={editingTool}
          categories={categories}
          onClose={() => {
            setShowCreateModal(false);
            setEditingTool(null);
          }}
          onSave={() => {
            setShowCreateModal(false);
            setEditingTool(null);
            loadCategories();
            loadTools(selectedCategory || undefined);
          }}
        />
      )}
    </div>
  );
}

interface ToolModalProps {
  tool: ToolEntry | null;
  categories: string[];
  onClose: () => void;
  onSave: () => void;
}

function ToolModal({ tool, categories, onClose, onSave }: ToolModalProps) {
  const toast = useToast();
  const [form, setForm] = useState({
    category: tool?.category || (categories[0] || ''),
    name: tool?.name || '',
    version: tool?.version || '1.0.0',
    description: tool?.description || '',
    script_path: tool?.script_path || '',
    script_class: tool?.script_class || '',
    is_active: tool?.is_active ?? true,
    param_schema: JSON.stringify(tool?.param_schema || {}, null, 2),
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const data = {
        ...form,
        param_schema: JSON.parse(form.param_schema || '{}'),
      };
      if (tool) {
        await api.tools.update(tool.id, data);
      } else {
        await api.tools.create(data as any);
      }
      onSave();
    } catch (error) {
      console.error('保存失败:', error);
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <h2 className="text-lg font-semibold mb-4">
            {tool ? '编辑工具' : '添加工具'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">分类</label>
              <input
                type="text"
                list="tool-categories"
                value={form.category}
                onChange={e => setForm({ ...form, category: e.target.value })}
                placeholder="Monkey, GPU, DDR..."
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
              <datalist id="tool-categories">
                {categories.map(cat => (
                  <option key={cat} value={cat} />
                ))}
              </datalist>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">工具名称 *</label>
              <input
                type="text"
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">版本 *</label>
                <input
                  type="text"
                  value={form.version}
                  onChange={e => setForm({ ...form, version: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  required
                />
              </div>
              <div className="flex items-end pb-1">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.is_active}
                    onChange={e => setForm({ ...form, is_active: e.target.checked })}
                    className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                  />
                  <span className="text-sm text-gray-700">启用</span>
                </label>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">描述</label>
              <textarea
                value={form.description}
                onChange={e => setForm({ ...form, description: e.target.value })}
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">脚本路径 *</label>
              <input
                type="text"
                value={form.script_path}
                onChange={e => setForm({ ...form, script_path: e.target.value })}
                placeholder="/home/android/.../test.py"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">类名 *</label>
              <input
                type="text"
                value={form.script_class}
                onChange={e => setForm({ ...form, script_class: e.target.value })}
                placeholder="MonkeyAEEAction"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">参数 Schema（JSON）</label>
              <textarea
                value={form.param_schema}
                onChange={e => setForm({ ...form, param_schema: e.target.value })}
                rows={4}
                placeholder='{"event_count": {"type": "integer", "default": 10000}}'
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 font-mono text-sm"
              />
            </div>

            <div className="flex justify-end gap-3 pt-4">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={saving}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
