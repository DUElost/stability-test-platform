import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useBeforeUnload, useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Layers,
  Wrench,
  Search,
  Plus,
  Trash2,
  CheckCircle2,
  CircleOff,
  ClipboardList,
  Sparkles,
  RefreshCcw,
} from 'lucide-react';
import {
  api,
  type BuiltinActionEntry,
  type BuiltinActionUpdatePayload,
  type ToolEntry,
} from '@/utils/api';
import { useToast } from '@/components/ui/toast';
import { useConfirm } from '@/hooks/useConfirm';

type CatalogTab = 'builtin' | 'tool';
type BuiltinCategory = 'device' | 'process' | 'file' | 'log' | 'script';

type BuiltinFilterCategory = BuiltinCategory | 'all';

const BUILTIN_CATEGORIES: BuiltinFilterCategory[] = ['all', 'device', 'process', 'file', 'log', 'script'];

interface BuiltinForm {
  name: string;
  label: string;
  category: BuiltinCategory;
  description: string;
  paramSchemaText: string;
  isActive: boolean;
}

interface ToolForm {
  name: string;
  version: string;
  scriptPath: string;
  scriptClass: string;
  description: string;
  paramSchemaText: string;
  isActive: boolean;
}

const EMPTY_TOOL_FORM: ToolForm = {
  name: '',
  version: '',
  scriptPath: '',
  scriptClass: '',
  description: '',
  paramSchemaText: '{\n  \n}',
  isActive: true,
};

function builtinToForm(item: BuiltinActionEntry): BuiltinForm {
  return {
    name: item.name,
    label: item.label,
    category: item.category,
    description: item.description || '',
    paramSchemaText: JSON.stringify(item.param_schema || {}, null, 2),
    isActive: item.is_active,
  };
}

function toolToForm(item: ToolEntry): ToolForm {
  return {
    name: item.name,
    version: item.version,
    scriptPath: item.script_path,
    scriptClass: item.script_class || '',
    description: item.description || '',
    paramSchemaText: JSON.stringify(item.param_schema || {}, null, 2),
    isActive: item.is_active,
  };
}

function parseJson(text: string): Record<string, any> {
  const input = text.trim();
  if (!input) return {};
  const parsed = JSON.parse(input);
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('JSON 必须是对象');
  }
  return parsed;
}

function safeNormalizeSchemaText(text: string): string {
  try {
    return JSON.stringify(parseJson(text));
  } catch {
    return `__INVALID__:${text.trim()}`;
  }
}

function normalizeBuiltinForm(form: BuiltinForm) {
  return JSON.stringify({
    name: form.name,
    label: form.label,
    category: form.category,
    description: form.description,
    isActive: form.isActive,
    schema: safeNormalizeSchemaText(form.paramSchemaText),
  });
}

function normalizeToolForm(form: ToolForm) {
  return JSON.stringify({
    name: form.name,
    version: form.version,
    scriptPath: form.scriptPath,
    scriptClass: form.scriptClass,
    description: form.description,
    isActive: form.isActive,
    schema: safeNormalizeSchemaText(form.paramSchemaText),
  });
}

export default function ActionTemplatePage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const navPromptingRef = useRef(false);

  const [tab, setTab] = useState<CatalogTab>('builtin');

  const [builtinQuery, setBuiltinQuery] = useState('');
  const [builtinCategory, setBuiltinCategory] = useState<BuiltinFilterCategory>('all');
  const [selectedBuiltinName, setSelectedBuiltinName] = useState<string | null>(null);
  const [builtinForm, setBuiltinForm] = useState<BuiltinForm | null>(null);
  const [builtinBaseline, setBuiltinBaseline] = useState<BuiltinForm | null>(null);
  const [builtinSchemaError, setBuiltinSchemaError] = useState('');

  const [toolQuery, setToolQuery] = useState('');
  const [selectedToolKey, setSelectedToolKey] = useState<string | null>(null);
  const [toolForm, setToolForm] = useState<ToolForm>(EMPTY_TOOL_FORM);
  const [toolBaseline, setToolBaseline] = useState<ToolForm>(EMPTY_TOOL_FORM);
  const [toolSchemaError, setToolSchemaError] = useState('');

  const { data: builtins = [] } = useQuery({
    queryKey: ['builtin-catalog'],
    queryFn: () => api.builtinCatalog.list(),
  });

  const { data: tools = [] } = useQuery({
    queryKey: ['tool-catalog'],
    queryFn: () => api.toolCatalog.list(),
  });

  const sortedBuiltins = useMemo(
    () => [...builtins].sort((a, b) => a.name.localeCompare(b.name)),
    [builtins],
  );

  const sortedTools = useMemo(
    () => [...tools].sort((a, b) => a.name.localeCompare(b.name)),
    [tools],
  );

  const filteredBuiltins = useMemo(() => {
    const q = builtinQuery.trim().toLowerCase();
    return sortedBuiltins.filter((item) => {
      if (builtinCategory !== 'all' && item.category !== builtinCategory) return false;
      if (!q) return true;
      return (
        item.name.toLowerCase().includes(q)
        || item.label.toLowerCase().includes(q)
        || (item.description || '').toLowerCase().includes(q)
      );
    });
  }, [sortedBuiltins, builtinCategory, builtinQuery]);

  const filteredTools = useMemo(() => {
    const q = toolQuery.trim().toLowerCase();
    if (!q) return sortedTools;
    return sortedTools.filter((item) => (
      item.name.toLowerCase().includes(q)
      || item.version.toLowerCase().includes(q)
      || (item.description || '').toLowerCase().includes(q)
      || item.script_path.toLowerCase().includes(q)
    ));
  }, [sortedTools, toolQuery]);

  const selectedTool = useMemo(() => {
    if (!selectedToolKey || selectedToolKey === 'new') return null;
    const id = Number(selectedToolKey);
    if (Number.isNaN(id)) return null;
    return sortedTools.find((x) => x.id === id) || null;
  }, [selectedToolKey, sortedTools]);

  const builtinActiveCount = useMemo(() => sortedBuiltins.filter((x) => x.is_active).length, [sortedBuiltins]);
  const toolActiveCount = useMemo(() => sortedTools.filter((x) => x.is_active).length, [sortedTools]);

  const builtinDirty = useMemo(() => {
    if (!builtinForm || !builtinBaseline) return false;
    return normalizeBuiltinForm(builtinForm) !== normalizeBuiltinForm(builtinBaseline);
  }, [builtinForm, builtinBaseline]);

  const toolDirty = useMemo(() => {
    return normalizeToolForm(toolForm) !== normalizeToolForm(toolBaseline);
  }, [toolForm, toolBaseline]);

  const hasUnsavedChanges = builtinDirty || toolDirty;

  useBeforeUnload((event) => {
    if (!hasUnsavedChanges) return;
    event.preventDefault();
    event.returnValue = '';
  });

  const confirmDiscardIfDirty = useCallback(async (): Promise<boolean> => {
    if (!hasUnsavedChanges) return true;
    const ok = await confirmDialog({
      title: '放弃未保存修改？',
      description: '当前有未保存内容，继续操作将丢失这些修改。',
      confirmText: '放弃修改',
      cancelText: '继续编辑',
      variant: 'destructive',
    });
    if (ok) {
      if (builtinBaseline) {
        setBuiltinForm({ ...builtinBaseline });
        setBuiltinSchemaError('');
      }
      setToolForm({ ...toolBaseline });
      setToolSchemaError('');
    }
    return ok;
  }, [hasUnsavedChanges, confirmDialog, builtinBaseline, toolBaseline]);

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
      if (toUrl.origin !== window.location.origin) return;

      const to = `${toUrl.pathname}${toUrl.search}${toUrl.hash}`;
      const from = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      if (to === from) return;

      event.preventDefault();
      event.stopPropagation();
      if (navPromptingRef.current) return;

      navPromptingRef.current = true;
      try {
        const ok = await confirmDiscardIfDirty();
        if (ok) navigate(to);
      } finally {
        navPromptingRef.current = false;
      }
    };

    document.addEventListener('click', onLinkClickCapture, true);
    return () => document.removeEventListener('click', onLinkClickCapture, true);
  }, [hasUnsavedChanges, confirmDiscardIfDirty, navigate]);

  const selectBuiltin = async (item: BuiltinActionEntry) => {
    if (item.name === selectedBuiltinName) return;
    if (!(await confirmDiscardIfDirty())) return;
    const next = builtinToForm(item);
    setSelectedBuiltinName(item.name);
    setBuiltinForm(next);
    setBuiltinBaseline(next);
    setBuiltinSchemaError('');
  };

  const selectTool = async (item: ToolEntry) => {
    if (selectedToolKey === String(item.id)) return;
    if (!(await confirmDiscardIfDirty())) return;
    const next = toolToForm(item);
    setSelectedToolKey(String(item.id));
    setToolForm(next);
    setToolBaseline(next);
    setToolSchemaError('');
  };

  useEffect(() => {
    if (tab !== 'builtin') return;
    if (selectedBuiltinName && sortedBuiltins.some((x) => x.name === selectedBuiltinName)) return;
    if (hasUnsavedChanges) return;
    if (filteredBuiltins.length > 0) {
      const next = builtinToForm(filteredBuiltins[0]);
      setSelectedBuiltinName(filteredBuiltins[0].name);
      setBuiltinForm(next);
      setBuiltinBaseline(next);
      setBuiltinSchemaError('');
    }
  }, [tab, selectedBuiltinName, sortedBuiltins, filteredBuiltins, hasUnsavedChanges]);

  useEffect(() => {
    if (tab !== 'tool') return;
    if (selectedToolKey === 'new') return;
    if (selectedToolKey && sortedTools.some((x) => String(x.id) === selectedToolKey)) return;
    if (hasUnsavedChanges) return;
    if (filteredTools.length > 0) {
      const next = toolToForm(filteredTools[0]);
      setSelectedToolKey(String(filteredTools[0].id));
      setToolForm(next);
      setToolBaseline(next);
      setToolSchemaError('');
    }
  }, [tab, selectedToolKey, sortedTools, filteredTools, hasUnsavedChanges]);

  const saveBuiltinMutation = useMutation({
    mutationFn: async () => {
      if (!builtinForm) throw new Error('请选择要编辑的 builtin');
      const parsedSchema = parseJson(builtinForm.paramSchemaText);
      setBuiltinSchemaError('');
      const payload: BuiltinActionUpdatePayload = {
        label: builtinForm.label,
        category: builtinForm.category,
        description: builtinForm.description,
        param_schema: parsedSchema,
        is_active: builtinForm.isActive,
      };
      return api.builtinCatalog.update(builtinForm.name, payload);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['builtin-catalog'] });
      const next = builtinToForm(data);
      setBuiltinForm(next);
      setBuiltinBaseline(next);
      toast.success('Builtin 配置已保存');
    },
    onError: (error: any) => {
      const message = error?.message || '保存失败';
      if (message.includes('JSON')) setBuiltinSchemaError(message);
      toast.error(message);
    },
  });

  const saveToolMutation = useMutation({
    mutationFn: async () => {
      const parsedSchema = parseJson(toolForm.paramSchemaText);
      setToolSchemaError('');

      if (!toolForm.name.trim()) throw new Error('Tool 名称不能为空');
      if (!toolForm.version.trim()) throw new Error('Tool 版本不能为空');
      if (!toolForm.scriptPath.trim()) throw new Error('script_path 不能为空');
      if (!toolForm.scriptClass.trim()) throw new Error('script_class 不能为空');

      const payload = {
        name: toolForm.name.trim(),
        version: toolForm.version.trim(),
        script_path: toolForm.scriptPath.trim(),
        script_class: toolForm.scriptClass.trim(),
        description: toolForm.description.trim() || undefined,
        param_schema: parsedSchema,
        is_active: toolForm.isActive,
      };

      if (selectedToolKey && selectedToolKey !== 'new') {
        return api.toolCatalog.update(Number(selectedToolKey), payload);
      }
      return api.toolCatalog.create(payload as any);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['tool-catalog'] });
      const next = toolToForm(data);
      setSelectedToolKey(String(data.id));
      setToolForm(next);
      setToolBaseline(next);
      setToolSchemaError('');
      toast.success(selectedToolKey === 'new' ? 'Tool 已创建' : 'Tool 已更新');
    },
    onError: (error: any) => {
      const message = error?.message || '保存失败';
      if (message.includes('JSON')) setToolSchemaError(message);
      toast.error(message);
    },
  });

  const deactivateToolMutation = useMutation({
    mutationFn: (id: number) => api.toolCatalog.remove(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tool-catalog'] });
      toast.success('Tool 已下线');
    },
    onError: (error: any) => {
      toast.error(error?.message || '下线失败');
    },
  });

  const handleCreateTool = async () => {
    if (!(await confirmDiscardIfDirty())) return;
    setSelectedToolKey('new');
    setToolForm(EMPTY_TOOL_FORM);
    setToolBaseline(EMPTY_TOOL_FORM);
    setToolSchemaError('');
  };

  const handleDeactivateTool = async (item: ToolEntry) => {
    if (!(await confirmDialog({
      description: `确认下线 Tool「${item.name} v${item.version}」？`,
      variant: 'destructive',
    }))) return;
    deactivateToolMutation.mutate(item.id);
  };

  const formatBuiltinSchema = () => {
    try {
      if (!builtinForm) return;
      const parsed = parseJson(builtinForm.paramSchemaText);
      setBuiltinForm({ ...builtinForm, paramSchemaText: JSON.stringify(parsed, null, 2) });
      setBuiltinSchemaError('');
    } catch (e: any) {
      setBuiltinSchemaError(e?.message || 'JSON 格式错误');
    }
  };

  const formatToolSchema = () => {
    try {
      const parsed = parseJson(toolForm.paramSchemaText);
      setToolForm((prev) => ({ ...prev, paramSchemaText: JSON.stringify(parsed, null, 2) }));
      setToolSchemaError('');
    } catch (e: any) {
      setToolSchemaError(e?.message || 'JSON 格式错误');
    }
  };

  return (
    <div className="space-y-6">
      <Card className="border-gray-200 bg-gradient-to-r from-gray-50 to-white">
        <CardContent className="pt-6">
          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold text-gray-900">动作目录编写</h1>
              <p className="text-sm text-gray-600 mt-1">统一维护 builtin 与 tool，工作流蓝图直接选择目录项，无需硬编码</p>
            </div>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Sparkles className="w-4 h-4" />
              <span>流程建议：选择目录项 → 编辑配置 → 回到工作流使用</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="pt-5">
            <p className="text-xs text-gray-500">Builtin（启用/总数）</p>
            <p className="mt-2 text-2xl font-semibold text-gray-900">{builtinActiveCount}/{sortedBuiltins.length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-xs text-gray-500">Tool（启用/总数）</p>
            <p className="mt-2 text-2xl font-semibold text-gray-900">{toolActiveCount}/{sortedTools.length}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-5">
            <p className="text-xs text-gray-500">当前目录可见项</p>
            <p className="mt-2 text-2xl font-semibold text-gray-900">{tab === 'builtin' ? filteredBuiltins.length : filteredTools.length}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={tab === 'builtin' ? 'default' : 'outline'}
              size="sm"
              onClick={async () => {
                if (tab === 'builtin') return;
                if (!(await confirmDiscardIfDirty())) return;
                setTab('builtin');
              }}
            >
              <Layers className="w-4 h-4 mr-1" />
              Builtin 目录
            </Button>
            <Button
              variant={tab === 'tool' ? 'default' : 'outline'}
              size="sm"
              onClick={async () => {
                if (tab === 'tool') return;
                if (!(await confirmDiscardIfDirty())) return;
                setTab('tool');
              }}
            >
              <Wrench className="w-4 h-4 mr-1" />
              Tool 目录
            </Button>
          </div>
        </CardContent>
      </Card>

      {tab === 'builtin' && (
        <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_1fr] gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <ClipboardList className="w-4 h-4" />
                Builtin 列表
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={builtinQuery}
                  onChange={(e) => setBuiltinQuery(e.target.value)}
                  placeholder="搜索名称 / 显示名 / 描述"
                  className="w-full pl-9 pr-3 py-2 border rounded-lg text-sm"
                />
              </div>

              <div className="flex flex-wrap gap-2">
                {BUILTIN_CATEGORIES.map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setBuiltinCategory(cat)}
                    className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${
                      builtinCategory === cat
                        ? 'border-gray-900 bg-gray-900 text-white'
                        : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>

              <div className="space-y-2 max-h-[540px] overflow-auto pr-1">
                {filteredBuiltins.length === 0 && (
                  <div className="text-sm text-gray-500 py-10 text-center border rounded-lg">没有匹配的 builtin</div>
                )}
                {filteredBuiltins.map((item) => {
                  const selected = selectedBuiltinName === item.name;
                  return (
                    <button
                      key={item.name}
                      onClick={async () => { await selectBuiltin(item); }}
                      className={`w-full text-left border rounded-lg p-3 transition-colors ${
                        selected
                          ? 'border-gray-900 bg-gray-50'
                          : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">{item.label}</p>
                          <p className="text-xs text-gray-500 font-mono truncate">{item.name}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] px-2 py-0.5 rounded bg-gray-100 text-gray-600">{item.category}</span>
                          {item.is_active ? (
                            <CheckCircle2 className="w-4 h-4 text-green-600" />
                          ) : (
                            <CircleOff className="w-4 h-4 text-gray-400" />
                          )}
                        </div>
                      </div>
                      <p className="mt-1 text-xs text-gray-600 line-clamp-2">{item.description || '无描述'}</p>
                    </button>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Builtin 编辑区</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {!builtinForm && (
                <div className="text-sm text-gray-500 py-16 text-center border rounded-lg">
                  请选择左侧 builtin 进行编辑
                </div>
              )}

              {builtinForm && (
                <>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">name（只读）</label>
                      <input
                        type="text"
                        value={builtinForm.name}
                        disabled
                        className="w-full px-3 py-2 border rounded-lg text-sm bg-gray-50 text-gray-600 font-mono"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">显示名</label>
                      <input
                        type="text"
                        value={builtinForm.label}
                        onChange={(e) => setBuiltinForm((prev) => prev ? { ...prev, label: e.target.value } : prev)}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">分类</label>
                      <select
                        value={builtinForm.category}
                        onChange={(e) => setBuiltinForm((prev) => prev ? { ...prev, category: e.target.value as BuiltinCategory } : prev)}
                        className="w-full px-3 py-2 border rounded-lg text-sm bg-white"
                      >
                        <option value="device">device</option>
                        <option value="process">process</option>
                        <option value="file">file</option>
                        <option value="log">log</option>
                        <option value="script">script</option>
                      </select>
                    </div>
                    <div className="flex items-end">
                      <label className="flex items-center gap-2 text-sm text-gray-700">
                        <input
                          type="checkbox"
                          checked={builtinForm.isActive}
                          onChange={(e) => setBuiltinForm((prev) => prev ? { ...prev, isActive: e.target.checked } : prev)}
                        />
                        启用
                      </label>
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs text-gray-600 mb-1">描述</label>
                    <input
                      type="text"
                      value={builtinForm.description}
                      onChange={(e) => setBuiltinForm((prev) => prev ? { ...prev, description: e.target.value } : prev)}
                      className="w-full px-3 py-2 border rounded-lg text-sm"
                    />
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="block text-xs text-gray-600">param_schema（JSON）</label>
                      <Button variant="outline" size="sm" onClick={formatBuiltinSchema}>
                        <RefreshCcw className="w-3.5 h-3.5 mr-1" />
                        格式化
                      </Button>
                    </div>
                    <textarea
                      rows={12}
                      value={builtinForm.paramSchemaText}
                      onChange={(e) => setBuiltinForm((prev) => prev ? { ...prev, paramSchemaText: e.target.value } : prev)}
                      className="w-full px-3 py-2 border rounded-lg text-sm font-mono"
                    />
                    {builtinSchemaError && <p className="text-xs text-red-500 mt-1">{builtinSchemaError}</p>}
                  </div>

                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="outline"
                      onClick={() => {
                        if (!builtinBaseline) return;
                        setBuiltinForm({ ...builtinBaseline });
                        setBuiltinSchemaError('');
                      }}
                    >
                      还原
                    </Button>
                    <Button onClick={() => saveBuiltinMutation.mutate()} disabled={saveBuiltinMutation.isPending}>
                      {saveBuiltinMutation.isPending ? '保存中...' : '保存 Builtin'}
                    </Button>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {tab === 'tool' && (
        <div className="grid grid-cols-1 xl:grid-cols-[1.15fr_1fr] gap-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-3">
                <CardTitle className="text-base flex items-center gap-2">
                  <Wrench className="w-4 h-4" />
                  Tool 列表
                </CardTitle>
                <Button size="sm" onClick={handleCreateTool}>
                  <Plus className="w-4 h-4 mr-1" />
                  新建 Tool
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={toolQuery}
                  onChange={(e) => setToolQuery(e.target.value)}
                  placeholder="搜索 name / version / path"
                  className="w-full pl-9 pr-3 py-2 border rounded-lg text-sm"
                />
              </div>

              <div className="space-y-2 max-h-[540px] overflow-auto pr-1">
                {filteredTools.length === 0 && (
                  <div className="text-sm text-gray-500 py-10 text-center border rounded-lg">没有匹配的 tool</div>
                )}
                {filteredTools.map((item) => {
                  const selected = selectedToolKey === String(item.id);
                  return (
                    <button
                      key={item.id}
                      onClick={async () => { await selectTool(item); }}
                      className={`w-full text-left border rounded-lg p-3 transition-colors ${
                        selected
                          ? 'border-gray-900 bg-gray-50'
                          : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">{item.name}</p>
                          <p className="text-xs text-gray-500">v{item.version}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] px-2 py-0.5 rounded bg-gray-100 text-gray-600">#{item.id}</span>
                          {item.is_active ? (
                            <CheckCircle2 className="w-4 h-4 text-green-600" />
                          ) : (
                            <CircleOff className="w-4 h-4 text-gray-400" />
                          )}
                        </div>
                      </div>
                      <p className="mt-1 text-xs text-gray-600 line-clamp-1 font-mono">{item.script_path}</p>
                    </button>
                  );
                })}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">{selectedToolKey === 'new' ? '新建 Tool' : 'Tool 编辑区'}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {selectedToolKey !== 'new' && !selectedTool && (
                <div className="text-sm text-gray-500 py-16 text-center border rounded-lg">
                  请选择左侧 tool 进行编辑，或新建 tool
                </div>
              )}

              {(selectedToolKey === 'new' || selectedTool) && (
                <>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">name</label>
                      <input
                        type="text"
                        value={toolForm.name}
                        onChange={(e) => setToolForm((prev) => ({ ...prev, name: e.target.value }))}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">version</label>
                      <input
                        type="text"
                        value={toolForm.version}
                        onChange={(e) => setToolForm((prev) => ({ ...prev, version: e.target.value }))}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">script_path</label>
                      <input
                        type="text"
                        value={toolForm.scriptPath}
                        onChange={(e) => setToolForm((prev) => ({ ...prev, scriptPath: e.target.value }))}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">script_class</label>
                      <input
                        type="text"
                        value={toolForm.scriptClass}
                        onChange={(e) => setToolForm((prev) => ({ ...prev, scriptClass: e.target.value }))}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs text-gray-600 mb-1">description</label>
                      <input
                        type="text"
                        value={toolForm.description}
                        onChange={(e) => setToolForm((prev) => ({ ...prev, description: e.target.value }))}
                        className="w-full px-3 py-2 border rounded-lg text-sm"
                      />
                    </div>
                    <div className="flex items-end">
                      <label className="flex items-center gap-2 text-sm text-gray-700">
                        <input
                          type="checkbox"
                          checked={toolForm.isActive}
                          onChange={(e) => setToolForm((prev) => ({ ...prev, isActive: e.target.checked }))}
                        />
                        启用
                      </label>
                    </div>
                  </div>

                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="block text-xs text-gray-600">param_schema（JSON）</label>
                      <Button variant="outline" size="sm" onClick={formatToolSchema}>
                        <RefreshCcw className="w-3.5 h-3.5 mr-1" />
                        格式化
                      </Button>
                    </div>
                    <textarea
                      rows={12}
                      value={toolForm.paramSchemaText}
                      onChange={(e) => setToolForm((prev) => ({ ...prev, paramSchemaText: e.target.value }))}
                      className="w-full px-3 py-2 border rounded-lg text-sm font-mono"
                    />
                    {toolSchemaError && <p className="text-xs text-red-500 mt-1">{toolSchemaError}</p>}
                  </div>

                  <div className="flex items-center justify-between gap-2">
                    <div>
                      {selectedTool && selectedTool.is_active && (
                        <Button variant="outline" size="sm" onClick={() => handleDeactivateTool(selectedTool)}>
                          <Trash2 className="w-4 h-4 mr-1" />
                          下线 Tool
                        </Button>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        onClick={() => {
                          if (selectedToolKey === 'new') {
                            setToolForm(EMPTY_TOOL_FORM);
                            setToolSchemaError('');
                            return;
                          }
                          setToolForm({ ...toolBaseline });
                          setToolSchemaError('');
                        }}
                      >
                        还原
                      </Button>
                      <Button onClick={() => saveToolMutation.mutate()} disabled={saveToolMutation.isPending}>
                        {saveToolMutation.isPending ? '保存中...' : (selectedToolKey === 'new' ? '创建 Tool' : '保存 Tool')}
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
