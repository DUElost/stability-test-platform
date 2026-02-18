// -*- coding: utf-8 -*-
import { useState, useEffect } from 'react';
import { api, Tool, ToolCategory } from '@/utils/api';
import { Wrench, Plus, RefreshCw, Trash2, Edit, AlertCircle } from 'lucide-react';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';

interface ToolPageProps {
  // parent component props
}

export default function ToolsPage({}: ToolPageProps) {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const [categories, setCategories] = useState<ToolCategory[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingTool, setEditingTool] = useState<Tool | null>(null);

  // 加载分类
  const loadCategories = async () => {
    try {
      const response = await api.tools.listCategories(0, 200);
      setCategories(response.data.items);
    } catch (error) {
      console.error('加载分类失败:', error);
    }
  };

  // 加载工具
  const loadTools = async (categoryId?: number) => {
    setLoading(true);
    try {
      const response = await api.tools.list(categoryId || undefined, 0, 200);
      setTools(response.data.items);
    } catch (error) {
      console.error('加载工具失败:', error);
    } finally {
      setLoading(false);
    }
  };

  // 扫描工具
  const handleScan = async () => {
    setScanning(true);
    try {
      const response = await api.tools.scan();
      toast.success(`扫描完成：新增 ${response.data.result.categories} 个分类，${response.data.result.tools} 个工具`);
      loadCategories();
      loadTools(selectedCategory || undefined);
    } catch (error) {
      console.error('扫描失败:', error);
      toast.error('扫描失败');
    } finally {
      setScanning(false);
    }
  };

  // 删除工具
  const handleDelete = async (id: number) => {
    if (!(await confirmDialog({ description: '确定要删除这个工具吗？', variant: 'destructive' }))) return;
    try {
      await api.tools.delete(id);
      loadTools(selectedCategory || undefined);
    } catch (error) {
      console.error('删除失败:', error);
      toast.error('删除失败');
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
    ? tools.filter(t => t.category_id === selectedCategory)
    : tools;

  return (
    <div className="p-6">
      {/* 页面头部 */}
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

      {/* 分类筛选 */}
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
              key={cat.id}
              onClick={() => setSelectedCategory(cat.id)}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
                selectedCategory === cat.id
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
              }`}
            >
              {cat.name} ({cat.tools_count || 0})
            </button>
          ))}
        </div>
      </div>

      {/* 工具列表 */}
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
                  <p className="text-sm text-gray-500">{tool.category_name}</p>
                </div>
                <span className={`px-2 py-1 text-xs font-medium rounded ${
                  tool.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                }`}>
                  {tool.enabled ? '启用' : '禁用'}
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
                <p>超时: {tool.timeout}秒</p>
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
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 创建/编辑弹窗 */}
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

// 工具弹窗组件
interface ToolModalProps {
  tool: Tool | null;
  categories: ToolCategory[];
  onClose: () => void;
  onSave: () => void;
}

function ToolModal({ tool, categories, onClose, onSave }: ToolModalProps) {
  const toast = useToast();
  const [form, setForm] = useState({
    category_id: tool?.category_id || (categories[0]?.id || 0),
    name: tool?.name || '',
    description: tool?.description || '',
    script_path: tool?.script_path || '',
    script_class: tool?.script_class || '',
    script_type: tool?.script_type || 'python',
    timeout: tool?.timeout || 3600,
    need_device: tool?.need_device ?? true,
    enabled: tool?.enabled ?? true,
    default_params: JSON.stringify(tool?.default_params || {}, null, 2),
  });
  const [saving, setSaving] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const data = {
        ...form,
        default_params: JSON.parse(form.default_params || '{}'),
      };
      if (tool) {
        await api.tools.update(tool.id, data);
      } else {
        await api.tools.create(data);
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
              <label className="block text-sm font-medium text-gray-700 mb-1">
                所属分类 *
              </label>
              <select
                value={form.category_id}
                onChange={e => setForm({ ...form, category_id: Number(e.target.value) })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              >
                {categories.map(cat => (
                  <option key={cat.id} value={cat.id}>{cat.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                工具名称 *
              </label>
              <input
                type="text"
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                描述
              </label>
              <textarea
                value={form.description}
                onChange={e => setForm({ ...form, description: e.target.value })}
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                脚本路径 *
              </label>
              <input
                type="text"
                value={form.script_path}
                onChange={e => setForm({ ...form, script_path: e.target.value })}
                placeholder="/home/android/sonic_agent/.../Test_Tool/Monkey/test.py"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                类名 *
              </label>
              <input
                type="text"
                value={form.script_class}
                onChange={e => setForm({ ...form, script_class: e.target.value })}
                placeholder="MtkMonkeyTest"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                required
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  超时时间（秒）
                </label>
                <input
                  type="number"
                  value={form.timeout}
                  onChange={e => setForm({ ...form, timeout: Number(e.target.value) })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  脚本类型
                </label>
                <select
                  value={form.script_type}
                  onChange={e => setForm({ ...form, script_type: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                >
                  <option value="python">Python</option>
                  <option value="shell">Shell</option>
                  <option value="bat">Batch</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                默认参数（JSON）
              </label>
              <textarea
                value={form.default_params}
                onChange={e => setForm({ ...form, default_params: e.target.value })}
                rows={4}
                placeholder='{"event_count": 10000, "throttle": 100}'
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 font-mono text-sm"
              />
            </div>

            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={form.need_device}
                  onChange={e => setForm({ ...form, need_device: e.target.checked })}
                  className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                />
                <span className="text-sm text-gray-700">需要设备</span>
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={e => setForm({ ...form, enabled: e.target.checked })}
                  className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
                />
                <span className="text-sm text-gray-700">启用</span>
              </label>
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
