import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, toApiError } from '@/utils/api';
import type { ResourcePool, ResourcePoolLoad } from '@/utils/api/types';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { Plus, Trash2, Wifi, WifiOff, Pencil, X } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import { InlineEmpty } from '@/components/ui/empty-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { FORM, INTERACTIVE, MODAL, PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { LAYOUT, resourceUsageBgClass } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

const DEFAULT_MAX_DEVICES = 30;
const MAX_DEVICES_LIMIT = 1000;

// 最大设备数不放进 form：数字输入框需要「清空后重填」的中间态，与 form 的
// number 语义直接冲突——`parseInt(v) || 1` 会在清空的那一刻把值弹回 1，
// 用户根本输不进去（#498 D2）。改由单独的字符串 state 承载，提交时再解析。
const FORM_INITIAL = {
  name: '',
  config_ssid: '',
  config_password: '',
  config_router_ip: '',
  host_group: '',
};

function configString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export default function WifiPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState(FORM_INITIAL);
  const [maxDevicesInput, setMaxDevicesInput] = useState(String(DEFAULT_MAX_DEVICES));

  /** 提交时解析；空串/非法输入回落默认值，其余夹到 [1, MAX_DEVICES_LIMIT]。 */
  const maxDevicesValue = () => {
    const parsed = Number.parseInt(maxDevicesInput, 10);
    if (!Number.isFinite(parsed)) return DEFAULT_MAX_DEVICES;
    return Math.min(Math.max(parsed, 1), MAX_DEVICES_LIMIT);
  };

  const { data: pools = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['resource-pools', 'loads'],
    queryFn: () => api.resourcePools.listLoads(),
    refetchInterval: 15000,
  });

  const createMutation = useMutation({
    mutationFn: () => api.resourcePools.create({
      name: form.name,
      // resource_type 由后端按 WiFi 池默认，前端不再携带这个恒为 'wifi' 的残留字段（#499 E4）
      config: { ssid: form.config_ssid, password: form.config_password, router_ip: form.config_router_ip },
      max_concurrent_devices: maxDevicesValue(),
      host_group: form.host_group || null,
    }),
    onSuccess: () => {
      toast.success('WiFi 池创建成功');
      queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
      resetForm();
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const updateMutation = useMutation({
    mutationFn: (id: number) => api.resourcePools.update(id, {
      name: form.name,
      config: { ssid: form.config_ssid, password: form.config_password, router_ip: form.config_router_ip },
      max_concurrent_devices: maxDevicesValue(),
      host_group: form.host_group || null,
    }),
    onSuccess: () => {
      toast.success('WiFi 池更新成功');
      queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
      resetForm();
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.resourcePools.delete(id),
    onSuccess: () => {
      toast.success('WiFi 池已删除');
      queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  function resetForm() {
    setForm(FORM_INITIAL);
    setMaxDevicesInput(String(DEFAULT_MAX_DEVICES));
    setEditingId(null);
    setShowCreate(false);
  }

  function startEdit(pool: ResourcePool | ResourcePoolLoad) {
    setForm({
      name: pool.name,
      config_ssid: configString(pool.config?.ssid),
      config_password: configString(pool.config?.password),
      config_router_ip: configString(pool.config?.router_ip),
      host_group: pool.host_group || '',
    });
    setMaxDevicesInput(String(pool.max_concurrent_devices ?? DEFAULT_MAX_DEVICES));
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

  const pending = createMutation.isPending || updateMutation.isPending;

  const handleDeletePool = async (pool: ResourcePoolLoad) => {
    const ok = await confirmDialog({
      title: '删除 WiFi 池',
      description: `确定删除「${pool.name}」？已分配的设备将不再使用该池。`,
      confirmText: '删除',
      variant: 'destructive',
    });
    if (ok) deleteMutation.mutate(pool.id);
  };

  return (
    <PageContainer width="content" className={LAYOUT.pageGap}>
      <PageHeader
        title="WiFi 资源池"
        subtitle="管理 WiFi 路由器池，平台按容量自动为设备分配接入点"
        action={
          <Button
            type="button"
            onClick={() => { resetForm(); setShowCreate(true); }}
            disabled={showCreate}
          >
            <Plus className="mr-2 h-4 w-4" />
            新增 WiFi 池
          </Button>
        }
      />

      {(showCreate || editingId) && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">{editingId ? '编辑 WiFi 池' : '新增 WiFi 池'}</CardTitle>
              <button
                type="button"
                onClick={resetForm}
                className={cn('rounded p-1', MODAL.closeButton)}
                aria-label="关闭表单"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <div>
                <label htmlFor="wifi-name" className={FORM.label}>名称</label>
                <input
                  id="wifi-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                  placeholder="例: Lab A - 2.4G Router"
                  className={FORM.input}
                />
              </div>
              <div>
                <label htmlFor="wifi-ssid" className={FORM.label}>SSID</label>
                <input
                  id="wifi-ssid"
                  value={form.config_ssid}
                  onChange={(e) => setForm({ ...form, config_ssid: e.target.value })}
                  required
                  placeholder="WiFi SSID"
                  className={FORM.input}
                />
              </div>
              <div>
                <label htmlFor="wifi-password" className={FORM.label}>密码</label>
                <input
                  id="wifi-password"
                  type="password"
                  autoComplete="new-password"
                  value={form.config_password}
                  onChange={(e) => setForm({ ...form, config_password: e.target.value })}
                  required
                  placeholder="WiFi 密码"
                  className={FORM.input}
                />
              </div>
              <div>
                <label htmlFor="wifi-router-ip" className={FORM.label}>路由器 IP（可选）</label>
                <input
                  id="wifi-router-ip"
                  value={form.config_router_ip}
                  onChange={(e) => setForm({ ...form, config_router_ip: e.target.value })}
                  placeholder="192.0.2.1"
                  className={FORM.input}
                />
              </div>
              <div>
                <label htmlFor="wifi-max-devices" className={FORM.label}>最大设备数</label>
                <input
                  id="wifi-max-devices"
                  type="number"
                  min={1}
                  max={MAX_DEVICES_LIMIT}
                  value={maxDevicesInput}
                  onChange={(e) => setMaxDevicesInput(e.target.value)}
                  className={FORM.input}
                />
              </div>
              <div>
                <label htmlFor="wifi-host-group" className={FORM.label}>主机组（可选）</label>
                <input
                  id="wifi-host-group"
                  value={form.host_group}
                  onChange={(e) => setForm({ ...form, host_group: e.target.value })}
                  placeholder="限制分配给指定主机"
                  className={FORM.input}
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
          {isError && (
            <div className="mb-4">
              <InlineError message="WiFi 资源池加载失败，请检查后端服务连接。" onRetry={() => void refetch()} />
            </div>
          )}
          {isLoading ? (
            <PageSkeleton.Cards count={3} layout="grid" />
          ) : pools.length === 0 ? (
            <InlineEmpty bordered>暂无 WiFi 资源池</InlineEmpty>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {pools.map((pool) => {
                const loadPct = pool.max_concurrent_devices > 0
                  ? Math.round(pool.current_devices / pool.max_concurrent_devices * 100)
                  : 0;
                const isFull = pool.current_devices >= pool.max_concurrent_devices;

                return (
                  <div key={pool.id} className={cn(PANEL.root, 'p-4')}>
                    <div className="flex items-start gap-3">
                      <div className={cn('rounded-md p-2', isFull ? 'bg-destructive/10' : 'bg-success/10')}>
                        {isFull ? <WifiOff className="h-4 w-4 text-destructive" /> : <Wifi className="h-4 w-4 text-success" />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className={cn('truncate font-medium', TEXT.heading)}>{pool.name}</span>
                          {pool.host_group && (
                            <span className={cn('rounded px-1.5 py-0.5 text-[11px]', STATUS_CHIP.muted)}>{pool.host_group}</span>
                          )}
                        </div>
                        <div className={cn('mt-1 font-mono text-xs', TEXT.subtitle)}>{configString(pool.config?.ssid) || '-'}</div>
                      </div>
                    </div>

                    <div className="mt-3">
                      <div className={cn('flex items-center justify-between text-xs', TEXT.subtitle)}>
                        <span>设备负载</span>
                        <span>{pool.current_devices} / {pool.max_concurrent_devices}</span>
                      </div>
                      <div className="mt-1 h-2 w-full rounded-full bg-muted">
                        <div
                          className={cn('h-2 rounded-full transition-all', resourceUsageBgClass(loadPct))}
                          style={{ width: `${Math.min(loadPct, 100)}%` }}
                        />
                      </div>
                    </div>

                    <div className={cn('mt-3 flex items-center gap-2 text-xs', TEXT.subtitle)}>
                      <span>{pool.resource_type}</span>
                      {configString(pool.config?.router_ip) && <span>· {configString(pool.config?.router_ip)}</span>}
                    </div>

                    <div className="mt-3 flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => startEdit(pool)}
                        className={cn('flex items-center gap-1 rounded px-2 py-1 text-xs', INTERACTIVE.iconButton, INTERACTIVE.hover)}
                        aria-label={`编辑 ${pool.name}`}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        编辑
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeletePool(pool)}
                        className={cn('flex items-center gap-1 rounded px-2 py-1 text-xs', INTERACTIVE.destructiveMenu)}
                        aria-label={`删除 ${pool.name}`}
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
    </PageContainer>
  );
}
