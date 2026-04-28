import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Device, type ScriptEntry, type ScriptSequenceItem } from '@/utils/api';
import type { ScriptBatchItemIn } from '@/utils/api/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { DynamicToolForm, type ParamSchema, type SchemaField } from '@/components/task/DynamicToolForm';
import { ArrowDown, ArrowUp, History, Play, Plus, Save, Trash2 } from 'lucide-react';

type EditableScriptItem = ScriptSequenceItem & { paramsText: string };

function normalizeField(key: string, raw: any, requiredKeys: string[]): SchemaField {
  const rawType = raw?.type === 'integer' ? 'number' : raw?.type;
  const hasEnum = Array.isArray(raw?.enum);
  return {
    type: hasEnum ? 'select' : ['string', 'number', 'boolean', 'select'].includes(rawType) ? rawType : 'string',
    label: raw?.label || raw?.title || key,
    placeholder: raw?.placeholder,
    default: raw?.default,
    required: Boolean(raw?.required) || requiredKeys.includes(key),
    min: raw?.min ?? raw?.minimum,
    max: raw?.max ?? raw?.maximum,
    description: raw?.description,
    options: raw?.options || (hasEnum ? raw.enum.map((value: any) => ({ label: String(value), value })) : undefined),
  };
}

function toParamSchema(schema: Record<string, any> | undefined | null): ParamSchema | null {
  if (!schema || Object.keys(schema).length === 0) return null;
  const properties = schema.properties && typeof schema.properties === 'object' ? schema.properties : schema;
  const requiredKeys = Array.isArray(schema.required) ? schema.required : [];
  const normalized = Object.fromEntries(
    Object.entries(properties).map(([key, field]) => [key, normalizeField(key, field, requiredKeys)]),
  ) as ParamSchema;
  return Object.keys(normalized).length > 0 ? normalized : null;
}

function defaultParams(script: ScriptEntry): Record<string, any> {
  const schema = toParamSchema(script.param_schema);
  if (!schema) return {};
  return Object.fromEntries(
    Object.entries(schema).map(([key, field]) => [key, field.default ?? (field.type === 'boolean' ? false : '')]),
  );
}

function parseParams(value: string): Record<string, any> {
  const trimmed = value.trim();
  if (!trimmed) return {};
  const parsed = JSON.parse(trimmed);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('params must be a JSON object');
  }
  return parsed;
}

export default function ScriptExecutePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [items, setItems] = useState<EditableScriptItem[]>([]);
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<string | null>(null);
  const [deviceSearch, setDeviceSearch] = useState('');
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);

  const { data: scripts = [], isLoading: scriptsLoading } = useQuery({
    queryKey: ['scripts', 'execute'],
    queryFn: () => api.scripts.list(true),
  });
  const { data: sequenceList } = useQuery({
    queryKey: ['script-sequences', 'execute'],
    queryFn: () => api.scriptSequences.list(0, 100),
  });
  const { data: devicesResp, isLoading: devicesLoading } = useQuery({
    queryKey: ['devices', 'execute'],
    queryFn: () => api.devices.list(0, 200),
  });
  const { data: hostsResp } = useQuery({
    queryKey: ['hosts', 'execute'],
    queryFn: () => api.hosts.list(0, 200),
  });

  const devices = devicesResp?.data.items ?? [];
  const hosts = Array.isArray(hostsResp?.data) ? hostsResp?.data : hostsResp?.data.items ?? [];
  const selectedTemplate = sequenceList?.items.find((item) => item.id === selectedTemplateId) ?? null;
  const hostNameById = useMemo(() => new Map(hosts.map((host) => [String(host.id), host.name || host.ip || String(host.id)])), [hosts]);
  const scriptByKey = useMemo(
    () => new Map(scripts.map((script) => [`${script.name}:${script.version}`, script])),
    [scripts],
  );
  const categories = useMemo(
    () => Array.from(new Set(scripts.map((script) => script.category).filter(Boolean))) as string[],
    [scripts],
  );
  const filteredScripts = useMemo(() => {
    const q = search.trim().toLowerCase();
    return scripts.filter((script) => {
      const inCategory = !category || script.category === category;
      const inQuery = !q
        || script.name.toLowerCase().includes(q)
        || (script.description ?? '').toLowerCase().includes(q);
      return inCategory && inQuery;
    });
  }, [scripts, search, category]);
  const filteredDevices = useMemo(() => {
    const q = deviceSearch.trim().toLowerCase();
    if (!q) return devices;
    return devices.filter(
      (device) =>
        device.serial.toLowerCase().includes(q) ||
        (device.model ?? '').toLowerCase().includes(q),
    );
  }, [devices, deviceSearch]);

  const buildSequenceItems = () =>
    items.map((item) => {
      const script = scriptByKey.get(`${item.script_name}:${item.version}`);
      const params = toParamSchema(script?.param_schema) ? (item.params || {}) : parseParams(item.paramsText);
      return {
        script_name: item.script_name,
        version: item.version,
        params,
        timeout_seconds: item.timeout_seconds,
        retry: item.retry,
      };
    });

  const buildBatchItems = (): ScriptBatchItemIn[] =>
    items.map((item) => {
      const script = scriptByKey.get(`${item.script_name}:${item.version}`);
      const params = toParamSchema(script?.param_schema) ? (item.params || {}) : parseParams(item.paramsText);
      return {
        script_name: item.script_name,
        version: item.version,
        params,
        timeout_seconds: item.timeout_seconds,
      };
    });

  const executeMutation = useMutation({
    mutationFn: () =>
      api.scriptBatches.create({
        name: selectedTemplate?.name || null,
        sequence_id: selectedTemplateId,
        items: buildBatchItems(),
        device_ids: Array.from(selectedDeviceIds),
        on_failure: 'stop',
      }),
    onSuccess: (result) => {
      const firstId = result[0]?.id;
      toast.success(`已下发 ${result.length} 个批次到 ${result.length} 台设备`);
      if (firstId) navigate(`/history?batch=${firstId}`);
    },
    onError: () => toast.error('创建脚本执行失败，请检查参数 JSON 和设备选择'),
  });

  const saveMutation = useMutation({
    mutationFn: (mode: 'update' | 'create') => {
      const payload = {
        name: selectedTemplate?.name || `脚本序列 ${new Date().toLocaleString('zh-CN')}`,
        items: buildSequenceItems(),
        on_failure: 'stop' as const,
      };
      return mode === 'update' && selectedTemplateId
        ? api.scriptSequences.update(selectedTemplateId, payload)
        : api.scriptSequences.create(payload);
    },
    onSuccess: (_, mode) => {
      toast.success(mode === 'update' ? '序列模板已更新' : '序列已保存');
      queryClient.invalidateQueries({ queryKey: ['script-sequences'] });
    },
    onError: () => toast.error('保存序列失败'),
  });

  const addScript = (script: ScriptEntry) => {
    const params = defaultParams(script);
    setItems((prev) => [
      ...prev,
      {
        script_name: script.name,
        version: script.version,
        params,
        paramsText: JSON.stringify(params, null, 2),
        timeout_seconds: 3600,
        retry: 0,
      },
    ]);
  };

  const loadTemplate = (templateId: number) => {
    const sequence = sequenceList?.items.find((item) => item.id === templateId);
    if (!sequence) return;
    setSelectedTemplateId(templateId);
    setItems(sequence.items.map((item) => ({
      ...item,
      paramsText: JSON.stringify(item.params || {}, null, 2),
    })));
  };

  const moveItem = (index: number, direction: -1 | 1) => {
    setItems((prev) => {
      const target = index + direction;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const updateItem = (index: number, patch: Partial<EditableScriptItem>) => {
    setItems((prev) => prev.map((item, idx) => (idx === index ? { ...item, ...patch } : item)));
  };

  const updateItemParam = (index: number, key: string, value: any) => {
    setItems((prev) => prev.map((item, idx) => {
      if (idx !== index) return item;
      const params = { ...(item.params || {}), [key]: value };
      return { ...item, params, paramsText: JSON.stringify(params, null, 2) };
    }));
  };

  const toggleDevice = (device: Device) => {
    setSelectedDeviceIds((prev) => {
      const next = new Set(prev);
      if (next.has(device.id)) next.delete(device.id);
      else next.add(device.id);
      return next;
    });
  };

  const deviceHostLabel = (device: Device) => {
    if (!device.host_id) return '未分配主机';
    return hostNameById.get(String(device.host_id)) || `Host ${device.host_id}`;
  };

  const busyReason = (device: Device) => {
    const extra = device.extra || {};
    return String(extra.busy_reason || extra.lock_reason || extra.active_job || '设备正在执行其他任务');
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">执行任务</h1>
          <p className="mt-1 text-sm text-gray-500">选择脚本、填写参数并下发到目标设备</p>
        </div>
        <Button asChild variant="outline">
          <Link to="/history">
            <History className="mr-2 h-4 w-4" />
            查看历史
          </Link>
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">脚本选择</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="block">
              <span className="sr-only">搜索脚本</span>
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                placeholder="搜索脚本"
              />
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setCategory(null)}
                className={`rounded-md px-2 py-1 text-xs ${category === null ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600'}`}
              >
                全部
              </button>
              {categories.map((item) => (
                <button
                  type="button"
                  key={item}
                  onClick={() => setCategory(item)}
                  className={`rounded-md px-2 py-1 text-xs ${category === item ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600'}`}
                >
                  {item}
                </button>
              ))}
            </div>
            {scriptsLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (
              <div className="max-h-[520px] space-y-2 overflow-y-auto">
                {filteredScripts.map((script) => (
                  <div key={script.id} className="rounded-md border border-gray-200 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-gray-900">{script.name}</div>
                        <div className="mt-1 text-xs text-gray-500">v{script.version} · {script.category || '未分类'}</div>
                      </div>
                      <Button type="button" size="sm" variant="outline" onClick={() => addScript(script)}>
                        <Plus className="mr-1 h-3.5 w-3.5" />
                        加入
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                <CardTitle className="text-base">执行序列</CardTitle>
                <label className="block text-sm">
                  <span className="mb-1 block text-xs text-gray-500">序列模板</span>
                  <select
                    value={selectedTemplateId ?? ''}
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      if (Number.isFinite(value) && value > 0) loadTemplate(value);
                      else setSelectedTemplateId(null);
                    }}
                    className="w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm md:w-56"
                  >
                    <option value="">选择模板</option>
                    {sequenceList?.items.map((sequence) => (
                      <option key={sequence.id} value={sequence.id}>{sequence.name}</option>
                    ))}
                  </select>
                </label>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {items.length === 0 ? (
                <div className="rounded-md border border-dashed py-8 text-center text-sm text-gray-500">请从左侧加入脚本</div>
              ) : (
                items.map((item, index) => (
                  <div key={`${item.script_name}-${index}`} className="rounded-md border border-gray-200 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <div className="font-medium text-gray-900">{index + 1}. {item.script_name}</div>
                        <div className="mt-1 text-xs text-gray-500">
                          v{item.version} · {scriptByKey.get(`${item.script_name}:${item.version}`)?.script_type || 'script'}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button type="button" size="sm" variant="ghost" disabled={index === 0} title="上移" onClick={() => moveItem(index, -1)}>
                          <ArrowUp className="h-4 w-4" />
                        </Button>
                        <Button type="button" size="sm" variant="ghost" disabled={index === items.length - 1} title="下移" onClick={() => moveItem(index, 1)}>
                          <ArrowDown className="h-4 w-4" />
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          title="移除"
                          onClick={() => setItems((prev) => prev.filter((_, idx) => idx !== index))}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-3 md:grid-cols-[120px_120px_1fr]">
                      <label className="block text-sm">
                        <span className="mb-1 block text-xs text-gray-500">超时秒数</span>
                        <input
                          type="number"
                          min={1}
                          value={item.timeout_seconds ?? 3600}
                          onChange={(event) => updateItem(index, { timeout_seconds: Number(event.target.value) || 1 })}
                          className="w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm"
                        />
                      </label>
                      <label className="block text-sm">
                        <span className="mb-1 block text-xs text-gray-500">重试次数</span>
                        <input
                          type="number"
                          min={0}
                          max={10}
                          value={item.retry ?? 0}
                          onChange={(event) => updateItem(index, { retry: Number(event.target.value) || 0 })}
                          className="w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm"
                        />
                      </label>
                      <div className="block text-sm">
                        <span className="mb-1 block text-xs text-gray-500">
                          {toParamSchema(scriptByKey.get(`${item.script_name}:${item.version}`)?.param_schema) ? '参数' : '参数 JSON'}
                        </span>
                        {toParamSchema(scriptByKey.get(`${item.script_name}:${item.version}`)?.param_schema) ? (
                          <div className="rounded-md border border-gray-200 bg-gray-50 p-3">
                            <DynamicToolForm
                              schema={toParamSchema(scriptByKey.get(`${item.script_name}:${item.version}`)?.param_schema)!}
                              values={item.params || {}}
                              onChange={(key, value) => updateItemParam(index, key, value)}
                            />
                          </div>
                        ) : (
                          <textarea
                            value={item.paramsText}
                            onChange={(event) => updateItem(index, { paramsText: event.target.value })}
                            className="min-h-[72px] w-full rounded-md border border-gray-200 px-2 py-1.5 font-mono text-xs"
                          />
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
          {selectedTemplate && (
            <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-600">
              基于模板：{selectedTemplate.name}
            </div>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">设备选择</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <label className="block">
                <span className="mb-1 block text-xs text-gray-500">搜索设备</span>
                <input
                  value={deviceSearch}
                  onChange={(event) => setDeviceSearch(event.target.value)}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                  placeholder="serial / model"
                />
              </label>
              {devicesLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <div className="grid gap-2 md:grid-cols-2">
                  {filteredDevices.map((device) => (
                    <label
                      key={device.id}
                      title={device.status === 'BUSY' ? busyReason(device) : undefined}
                      className="flex cursor-pointer items-center gap-3 rounded-md border border-gray-200 px-3 py-2 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={selectedDeviceIds.has(device.id)}
                        disabled={device.status === 'OFFLINE' || device.status === 'BUSY'}
                        onChange={() => toggleDevice(device)}
                      />
                      <span className="font-mono text-gray-900">{device.serial}</span>
                      <span className="truncate text-gray-500">{device.model || '-'}</span>
                      <span className="ml-auto text-xs text-gray-400">{deviceHostLabel(device)}</span>
                    </label>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={!items.length || saveMutation.isLoading} onClick={() => saveMutation.mutate(selectedTemplateId ? 'update' : 'create')}>
              <Save className="mr-2 h-4 w-4" />
              {selectedTemplateId ? '更新模板' : '保存为模板'}
            </Button>
            {selectedTemplateId && (
              <Button type="button" variant="outline" disabled={!items.length || saveMutation.isLoading} onClick={() => saveMutation.mutate('create')}>
                <Save className="mr-2 h-4 w-4" />
                另存为新模板
              </Button>
            )}
            <Button
              type="button"
              disabled={!items.length || selectedDeviceIds.size === 0 || executeMutation.isLoading}
              onClick={() => executeMutation.mutate()}
            >
              <Play className="mr-2 h-4 w-4" />
              执行
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
