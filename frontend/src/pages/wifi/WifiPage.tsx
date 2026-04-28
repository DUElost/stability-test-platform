import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/utils/api';
import type { ResourcePool, ResourcePoolLoad } from '@/utils/api/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { Plus, Trash2, Wifi, WifiOff, Pencil, X } from 'lucide-react';

const FORM_INITIAL = {
  name: '',
  resource_type: 'wifi',
  config_ssid: '',
  config_password: '',
  config_router_ip: '',
  max_concurrent_devices: 30,
  host_group: '',
};

export default function WifiPage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState(FORM_INITIAL);

  const { data: pools = [], isLoading } = useQuery({
    queryKey: ['resource-pools', 'loads'],
    queryFn: () => api.resourcePools.listLoads(),
    refetchInterval: 15000,
  });

  const createMutation = useMutation({
    mutationFn: () => api.resourcePools.create({
      name: form.name,
      resource_type: form.resource_type,
      config: { ssid: form.config_ssid, password: form.config_password, router_ip: form.config_router_ip },
      max_concurrent_devices: form.max_concurrent_devices,
      host_group: form.host_group || null,
    }),
    onSuccess: () => {
      toast.success('WiFi 池创建成功');
      queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
      resetForm();
    },
    onError: () => toast.error('创建失败'),
  });

  const updateMutation = useMutation({
    mutationFn: (id: number) => api.resourcePools.update(id, {
      name: form.name,
      config: { ssid: form.config_ssid, password: form.config_password, router_ip: form.config_router_ip },
      max_concurrent_devices: form.max_concurrent_devices,
      host_group: form.host_group || null,
    }),
    onSuccess: () => {
      toast.success('WiFi 池更新成功');
      queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
      resetForm();
    },
    onError: () => toast.error('更新失败'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.resourcePools.delete(id),
    onSuccess: () => {
      toast.success('WiFi 池已删除');
      queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
    },
    onError: () => toast.error('删除失败'),
  });

  function resetForm() {
    setForm(FORM_INITIAL);
    setEditingId(null);
    setShowCreate(false);
  }

  function startEdit(pool: ResourcePool | ResourcePoolLoad) {
    setForm({
      name: pool.name,
      resource_type: pool.resource_type,
      config_ssid: pool.config?.ssid || '',
      config_password: pool.config?.password || '',
      config_router_ip: pool.config?.router_ip || '',
      max_concurrent_devices: pool.max_concurrent_devices,
      host_group: pool.host_group || '',
    });
    setEditingId(pool.id);
    setShowCreate(false);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (editingId) {
      updateMutation.mutate(editingId);
    } else {
      createMutation.mutate();
    }
  }

  const pending = createMutation.isLoading || updateMutation.isLoading;

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">WiFi 资源池</h1>
          <p className="mt-1 text-sm text-gray-500">管理 WiFi 路由器池，平台按容量自动为设备分配接入点</p>
        </div>
        <Button
          type="button"
          onClick={() => { resetForm(); setShowCreate(true); }}
          disabled={showCreate}
        >
          <Plus className="mr-2 h-4 w-4" />
          新增 WiFi 池
        </Button>
      </div>

      {(showCreate || editingId) && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">{editingId ? '编辑 WiFi 池' : '新增 WiFi 池'}</CardTitle>
              <button type="button" onClick={resetForm} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
                <X className="h-4 w-4" />
              </button>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">名称</label>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                  placeholder="例: Lab A - 2.4G Router"
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">SSID</label>
                <input
                  value={form.config_ssid}
                  onChange={(e) => setForm({ ...form, config_ssid: e.target.value })}
                  required
                  placeholder="WiFi SSID"
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">密码</label>
                <input
                  value={form.config_password}
                  onChange={(e) => setForm({ ...form, config_password: e.target.value })}
                  required
                  placeholder="WiFi 密码"
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">路由器 IP（可选）</label>
                <input
                  value={form.config_router_ip}
                  onChange={(e) => setForm({ ...form, config_router_ip: e.target.value })}
                  placeholder="172.21.15.1"
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">最大设备数</label>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={form.max_concurrent_devices}
                  onChange={(e) => setForm({ ...form, max_concurrent_devices: parseInt(e.target.value, 10) || 1 })}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-600">主机组（可选）</label>
                <input
                  value={form.host_group}
                  onChange={(e) => setForm({ ...form, host_group: e.target.value })}
                  placeholder="限制分配给指定主机"
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-gray-900/10"
                />
              </div>
              <div className="flex items-end gap-2 md:col-span-2 lg:col-span-3">
                <Button type="submit" disabled={pending}>
                  {pending ? '保存中...' : editingId ? '保存修改' : '创建'}
                </Button>
                <Button type="button" variant="outline" onClick={resetForm}>取消</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">WiFi 池列表</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}
            </div>
          ) : pools.length === 0 ? (
            <div className="rounded-md border border-dashed py-10 text-center text-sm text-gray-500">
              暂无 WiFi 资源池
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {pools.map((pool) => {
                const loadPct = pool.max_concurrent_devices > 0
                  ? Math.round(pool.current_devices / pool.max_concurrent_devices * 100)
                  : 0;
                const isFull = pool.current_devices >= pool.max_concurrent_devices;

                return (
                  <div key={pool.id} className="rounded-md border border-gray-200 bg-white p-4">
                    <div className="flex items-start gap-3">
                      <div className={`rounded-md p-2 ${isFull ? 'bg-red-100' : 'bg-green-100'}`}>
                        {isFull ? <WifiOff className="h-4 w-4 text-red-600" /> : <Wifi className="h-4 w-4 text-green-600" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-medium text-gray-900">{pool.name}</span>
                          {pool.host_group && (
                            <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">{pool.host_group}</span>
                          )}
                        </div>
                        <div className="mt-1 font-mono text-xs text-gray-500">{pool.config?.ssid || '-'}</div>
                      </div>
                    </div>

                    <div className="mt-3">
                      <div className="flex items-center justify-between text-xs text-gray-500">
                        <span>设备负载</span>
                        <span>{pool.current_devices} / {pool.max_concurrent_devices}</span>
                      </div>
                      <div className="mt-1 h-2 w-full rounded-full bg-gray-100">
                        <div
                          className={`h-2 rounded-full transition-all ${loadPct > 80 ? 'bg-red-500' : loadPct > 50 ? 'bg-amber-500' : 'bg-green-500'}`}
                          style={{ width: `${Math.min(loadPct, 100)}%` }}
                        />
                      </div>
                    </div>

                    <div className="mt-3 flex items-center gap-2 text-xs text-gray-400">
                      <span>{pool.resource_type}</span>
                      {pool.config?.router_ip && <span>· {pool.config.router_ip}</span>}
                    </div>

                    <div className="mt-3 flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => startEdit(pool)}
                        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => { if (confirm('确定删除此 WiFi 池?')) deleteMutation.mutate(pool.id); }}
                        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-400 hover:bg-red-50 hover:text-red-600"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                        删除
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
