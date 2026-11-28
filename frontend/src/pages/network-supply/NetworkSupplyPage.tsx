import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, classifyApiError, toApiError } from '@/utils/api';
import type {
  Device,
  NetworkEgressType,
  NetworkResourceType,
  NetworkSession,
  ResourcePoolLoad,
} from '@/utils/api/types';
import { readNamedValues } from '@/utils/forms';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import {
  Cable,
  Clock3,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  Wifi,
  WifiOff,
  Pencil,
  X,
} from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import { InlineEmpty } from '@/components/ui/empty-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import {
  ALERT_BOX,
  FORM,
  INTERACTIVE,
  MODAL,
  PANEL,
  STATUS_CHIP,
  TEXT,
  tabLinkClass,
} from '@/design-system';
import { LAYOUT, resourceUsageBgClass } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import {
  formatNetworkEgressLabel,
  formatNetworkPoolTypeLabel,
  poolEgressOf,
} from '@/components/execution/plan-execute/networkPoolRequirements';

type ActiveTab = 'pools' | 'sessions';

const DEFAULT_MAX_DEVICES = 30;
const DEFAULT_USBNET_MAX_PER_HOST = 4;
const MAX_DEVICES_LIMIT = 1000;
const DEFAULT_MANUAL_TTL_MINUTES = 240;

const FORM_INITIAL = {
  name: '',
  resource_type: 'wifi' as NetworkResourceType,
  egress: 'direct' as NetworkEgressType,
  config_ssid: '',
  config_password: '',
  config_router_ip: '',
  config_bridge_name: 'br-stpusb-dir',
  config_subnet_cidr: '10.200.0.0/22',
  config_max_devices_per_host: String(DEFAULT_USBNET_MAX_PER_HOST),
  host_group: '',
};

function configString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function configIntString(value: unknown, fallback: string): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : fallback;
}

const SESSION_STATUS_META: Record<string, { label: string; chipClass: string }> = {
  RESERVED: { label: 'RESERVED（待认领）', chipClass: STATUS_CHIP.muted },
  PROVISIONING: { label: 'PROVISIONING（准备中）', chipClass: STATUS_CHIP.primary },
  ACTIVE: { label: 'ACTIVE（供网中）', chipClass: STATUS_CHIP.success },
  RELEASING: { label: 'RELEASING（回收中）', chipClass: STATUS_CHIP.warning },
  RELEASE_FAILED: { label: 'RELEASE_FAILED（回收失败）', chipClass: STATUS_CHIP.destructive },
  RELEASED: { label: 'RELEASED（已释放）', chipClass: STATUS_CHIP.muted },
};

const OVERRIDE_CATEGORIES = [
  { value: 'device_unreachable', label: '设备长期失联 / 物理下架' },
  { value: 'manual_recovery', label: '已人工现场切回 / 摘桥确认' },
  { value: 'ops_cleanup', label: '运维排障强收口' },
  { value: 'other', label: '其它（需在证据中说明）' },
] as const;

function formatRemainingTtl(expiresAt: string | null): string {
  if (!expiresAt) return '跟随任务';
  const diffMs = new Date(expiresAt).getTime() - Date.now();
  if (!Number.isFinite(diffMs)) return '-';
  if (diffMs <= 0) return '已到期（等待回收）';
  const mins = Math.ceil(diffMs / 60_000);
  if (mins >= 60) {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return m > 0 ? `剩余 ${h}h ${m}m` : `剩余 ${h}h`;
  }
  return `剩余 ${mins}m`;
}

export default function NetworkSupplyPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<ActiveTab>('pools');

  // ── Tab 1: 供给池管理状态 ────────────────────────────────────────────────────
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState(FORM_INITIAL);
  const [maxDevicesInput, setMaxDevicesInput] = useState(String(DEFAULT_MAX_DEVICES));

  // ── Tab 2: 受管会话视图状态 ─────────────────────────────────────────────────
  const [includeReleased, setIncludeReleased] = useState(false);
  const [sessionTypeFilter, setSessionTypeFilter] = useState<'all' | NetworkResourceType>('all');
  const [enableModalOpen, setEnableModalOpen] = useState(false);
  const [enableDeviceId, setEnableDeviceId] = useState<string>('');
  const [enablePoolId, setEnablePoolId] = useState<string>('');
  const [enableTtlMinutes, setEnableTtlMinutes] = useState(String(DEFAULT_MANUAL_TTL_MINUTES));
  const [overrideTarget, setOverrideTarget] = useState<NetworkSession | null>(null);
  const [overrideCategory, setOverrideCategory] = useState<string>('device_unreachable');
  const [overrideEvidenceRef, setOverrideEvidenceRef] = useState<string>('');

  const maxDevicesFrom = (raw: string) => {
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isFinite(parsed)) return DEFAULT_MAX_DEVICES;
    return Math.min(Math.max(parsed, 1), MAX_DEVICES_LIMIT);
  };

  const {
    data: pools = [],
    isLoading: poolsLoading,
    isError: poolsIsError,
    error: poolsError,
    refetch: refetchPools,
  } = useQuery({
    queryKey: ['resource-pools', 'loads'],
    queryFn: () => api.resourcePools.listLoads(),
    refetchInterval: 15_000,
  });

  const {
    data: sessions = [],
    isLoading: sessionsLoading,
    isError: sessionsIsError,
    error: sessionsError,
    refetch: refetchSessions,
  } = useQuery({
    queryKey: ['network-supply', 'sessions', includeReleased, sessionTypeFilter],
    queryFn: () =>
      api.networkSupply.listSessions({
        include_released: includeReleased,
        ...(sessionTypeFilter !== 'all' ? { resource_type: sessionTypeFilter } : {}),
      }),
    refetchInterval: 10_000,
  });

  const { data: onlineDevicesData } = useQuery({
    queryKey: ['network-supply', 'online-devices'],
    queryFn: () => api.devices.list(0, 200, 'ONLINE'),
    enabled: enableModalOpen,
  });

  const onlineDevices: Device[] = useMemo(
    () => (onlineDevicesData?.items ?? []).filter((d) => !d.retired_at),
    [onlineDevicesData],
  );

  const activePools = useMemo(
    () => pools.filter((p) => p.is_active),
    [pools],
  );

  /** #2456：提交载荷由提交那一刻从表单 DOM 读到的值构造（支持密码管理器 autofill）。 */
  const buildPoolPayload = (values: Record<string, string>) => {
    const resourceType: NetworkResourceType =
      form.resource_type === 'usb_net' ? 'usb_net' : 'wifi';
    const egress: NetworkEgressType =
      resourceType === 'usb_net' ? 'direct' : form.egress === 'vpn' ? 'vpn' : 'direct';

    const config =
      resourceType === 'usb_net'
        ? {
            egress: 'direct',
            bridge_name: (values.config_bridge_name || 'br-stpusb-dir').trim(),
            subnet_cidr: (values.config_subnet_cidr || '10.200.0.0/22').trim(),
            max_devices_per_host: Math.min(
              32,
              Math.max(1, Number.parseInt(values.config_max_devices_per_host || '4', 10) || 4),
            ),
          }
        : {
            egress,
            ssid: values.config_ssid,
            password: values.config_password,
            router_ip: values.config_router_ip,
          };

    return {
      name: values.name,
      resource_type: resourceType,
      config,
      max_concurrent_devices: maxDevicesFrom(values.max_devices),
      host_group: values.host_group || null,
    };
  };

  const createMutation = useMutation({
    mutationFn: (values: Record<string, string>) => api.resourcePools.create(buildPoolPayload(values)),
    onSuccess: () => {
      toast.success('网络供给池创建成功');
      void queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
      resetForm();
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, values }: { id: number; values: Record<string, string> }) =>
      api.resourcePools.update(id, buildPoolPayload(values)),
    onSuccess: () => {
      toast.success('网络供给池更新成功');
      void queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
      resetForm();
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.resourcePools.delete(id),
    onSuccess: () => {
      toast.success('网络供给池已删除');
      void queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const enableManualMutation = useMutation({
    mutationFn: (payload: { device_id: number; resource_pool_id: number; ttl_seconds: number }) =>
      api.networkSupply.enableManual(payload),
    onSuccess: () => {
      toast.success('已发起手动调试供网');
      setEnableModalOpen(false);
      setEnableDeviceId('');
      setEnablePoolId('');
      void queryClient.invalidateQueries({ queryKey: ['network-supply'] });
      void queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const renewManualMutation = useMutation({
    mutationFn: (sessionId: number) => api.networkSupply.renewManual(sessionId, 3600),
    onSuccess: () => {
      toast.success('手动调试会话已续期 1 小时');
      void queryClient.invalidateQueries({ queryKey: ['network-supply'] });
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const releaseSessionMutation = useMutation({
    mutationFn: (sessionId: number) => api.networkSupply.releaseSession(sessionId),
    onSuccess: () => {
      toast.success('已下发强制释放（等待 Agent 回读确认）');
      void queryClient.invalidateQueries({ queryKey: ['network-supply'] });
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  const auditOverrideMutation = useMutation({
    mutationFn: ({
      sessionId,
      reason_category,
      evidence_ref,
    }: {
      sessionId: number;
      reason_category: string;
      evidence_ref: string;
    }) => api.networkSupply.auditOverride(sessionId, { reason_category, evidence_ref }),
    onSuccess: (res) => {
      toast.success(
        res.usbnet_revoke_directive
          ? '审计放行已完成，已收口会话并下发 usbnet-revoke 指令'
          : '审计放行已完成，会话与租约已收口',
      );
      setOverrideTarget(null);
      setOverrideEvidenceRef('');
      void queryClient.invalidateQueries({ queryKey: ['network-supply'] });
      void queryClient.invalidateQueries({ queryKey: ['resource-pools'] });
    },
    onError: (err: unknown) => toast.error(toApiError(err).message),
  });

  function resetForm() {
    setForm(FORM_INITIAL);
    setMaxDevicesInput(String(DEFAULT_MAX_DEVICES));
    setEditingId(null);
    setShowCreate(false);
  }

  async function startEdit(pool: ResourcePoolLoad) {
    try {
      const full = await api.resourcePools.get(pool.id);
      const rType: NetworkResourceType = full.resource_type === 'usb_net' ? 'usb_net' : 'wifi';
      const egress = poolEgressOf(full.config);
      setForm({
        name: full.name,
        resource_type: rType,
        egress,
        config_ssid: configString(full.config?.ssid),
        config_password: configString(full.config?.password),
        config_router_ip: configString(full.config?.router_ip),
        config_bridge_name: configString(full.config?.bridge_name) || 'br-stpusb-dir',
        config_subnet_cidr: configString(full.config?.subnet_cidr) || '10.200.0.0/22',
        config_max_devices_per_host: configIntString(
          full.config?.max_devices_per_host,
          String(DEFAULT_USBNET_MAX_PER_HOST),
        ),
        host_group: full.host_group || '',
      });
      setMaxDevicesInput(String(full.max_concurrent_devices ?? DEFAULT_MAX_DEVICES));
      setEditingId(pool.id);
      setShowCreate(false);
    } catch (err: unknown) {
      toast.error(`加载网络供给池详情失败: ${toApiError(err).message}`);
    }
  }

  function handlePoolSubmit(e: React.FormEvent) {
    e.preventDefault();
    const values = readNamedValues(e.currentTarget as HTMLFormElement, {
      ...form,
      max_devices: maxDevicesInput,
    });
    if (editingId) {
      updateMutation.mutate({ id: editingId, values });
    } else {
      createMutation.mutate(values);
    }
  }

  const handleDeletePool = async (pool: ResourcePoolLoad) => {
    const ok = await confirmDialog({
      title: '删除网络供给池',
      description: `确定删除「${pool.name}」？已产生受管会话或分配记录的资源池不可硬删除。`,
      confirmText: '删除',
      variant: 'destructive',
    });
    if (ok) deleteMutation.mutate(pool.id);
  };

  const handleForceRelease = async (sess: NetworkSession) => {
    const ok = await confirmDialog({
      title: '强制释放手动供网会话',
      description: `确认强制断开设备 ${sess.device_serial ?? sess.device_id} 的「${sess.pool_name ?? sess.resource_type}」调试供网？Agent 确认回收前租约将保持占用。`,
      confirmText: '确认强释',
      variant: 'destructive',
    });
    if (ok) releaseSessionMutation.mutate(sess.id);
  };

  const handleEnableSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const did = Number.parseInt(enableDeviceId, 10);
    const pid = Number.parseInt(enablePoolId, 10);
    const mins = Math.max(5, Number.parseInt(enableTtlMinutes, 10) || DEFAULT_MANUAL_TTL_MINUTES);
    if (!Number.isFinite(did) || !Number.isFinite(pid)) {
      toast.error('请选择目标设备与供给池');
      return;
    }
    enableManualMutation.mutate({
      device_id: did,
      resource_pool_id: pid,
      ttl_seconds: mins * 60,
    });
  };

  const handleAuditOverrideSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!overrideTarget) return;
    const trimmedRef = overrideEvidenceRef.trim();
    if (!trimmedRef) {
      toast.error('请填写审计放行凭据/工单号（evidence_ref）');
      return;
    }
    auditOverrideMutation.mutate({
      sessionId: overrideTarget.id,
      reason_category: overrideCategory,
      evidence_ref: trimmedRef,
    });
  };

  const poolFormPending = createMutation.isPending || updateMutation.isPending;

  return (
    <PageContainer width="content" className={LAYOUT.pageGap}>
      <PageHeader
        title="网络供给"
        subtitle="统一管理 WiFi 与 USB 反向供网资源池、出口策略及受管供网设备会话"
        action={
          activeTab === 'pools' ? (
            <Button
              type="button"
              onClick={() => {
                resetForm();
                setShowCreate(true);
              }}
              disabled={showCreate}
            >
              <Plus className="mr-2 h-4 w-4" />
              新增供给池
            </Button>
          ) : (
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => void refetchSessions()}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                刷新会话
              </Button>
              <Button
                type="button"
                onClick={() => {
                  setEnableDeviceId(onlineDevices[0]?.id ? String(onlineDevices[0].id) : '');
                  setEnablePoolId(activePools[0]?.id ? String(activePools[0].id) : '');
                  setEnableTtlMinutes(String(DEFAULT_MANUAL_TTL_MINUTES));
                  setEnableModalOpen(true);
                }}
              >
                <Plus className="mr-2 h-4 w-4" />
                手动开启调试供网
              </Button>
            </div>
          )
        }
      />

      {/* 双 Tab 导航（ADR-0066 D7.2） */}
      <div className="flex items-center border-b border-border" role="tablist" aria-label="网络供给视图">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'pools'}
          className={tabLinkClass(activeTab === 'pools')}
          onClick={() => setActiveTab('pools')}
        >
          供给池管理（{pools.length}）
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'sessions'}
          className={tabLinkClass(activeTab === 'sessions')}
          onClick={() => setActiveTab('sessions')}
        >
          已启用供网设备（受管会话视图 · {sessions.length}）
        </button>
      </div>

      {activeTab === 'pools' ? (
        <>
          {(showCreate || editingId) && (
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base">
                    {editingId ? '编辑供给池' : '新增供给池'}
                  </CardTitle>
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
                <form onSubmit={handlePoolSubmit} className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                  <div className="md:col-span-2 lg:col-span-3 flex flex-wrap items-center gap-6">
                    <div>
                      <div className={FORM.label}>供网方式</div>
                      <div className="mt-1 flex items-center gap-4 text-sm">
                        <label className="flex items-center gap-1.5">
                          <input
                            type="radio"
                            name="resource_type"
                            value="wifi"
                            checked={form.resource_type === 'wifi'}
                            onChange={() => setForm((prev) => ({ ...prev, resource_type: 'wifi' }))}
                          />
                          <span>WiFi</span>
                        </label>
                        <label className="flex items-center gap-1.5">
                          <input
                            type="radio"
                            name="resource_type"
                            value="usb_net"
                            checked={form.resource_type === 'usb_net'}
                            onChange={() =>
                              setForm((prev) => ({
                                ...prev,
                                resource_type: 'usb_net',
                                egress: 'direct',
                              }))
                            }
                          />
                          <span>USB 反向供网</span>
                        </label>
                      </div>
                    </div>

                    <div>
                      <div className={FORM.label}>出口策略</div>
                      <div className="mt-1 flex items-center gap-4 text-sm">
                        <label className="flex items-center gap-1.5">
                          <input
                            type="radio"
                            name="egress"
                            value="direct"
                            checked={form.egress === 'direct'}
                            onChange={() => setForm((prev) => ({ ...prev, egress: 'direct' }))}
                          />
                          <span>普通直连（direct）</span>
                        </label>
                        <label className="flex items-center gap-1.5">
                          <input
                            type="radio"
                            name="egress"
                            value="vpn"
                            disabled={form.resource_type === 'usb_net'}
                            checked={form.egress === 'vpn'}
                            onChange={() => setForm((prev) => ({ ...prev, egress: 'vpn' }))}
                          />
                          <span className={form.resource_type === 'usb_net' ? TEXT.subtitle : undefined}>
                            VPN 出口（vpn{form.resource_type === 'usb_net' ? ' · Phase 1 仅开放 WiFi' : ''}）
                          </span>
                        </label>
                      </div>
                    </div>
                  </div>

                  <div>
                    <label htmlFor="pool-name" className={FORM.label}>名称</label>
                    <input
                      id="pool-name"
                      name="name"
                      value={form.name}
                      onChange={(e) => setForm({ ...form, name: e.target.value })}
                      required
                      placeholder="例: Lab A - 2.4G / USB-Net Direct"
                      className={FORM.input}
                    />
                  </div>

                  {form.resource_type === 'wifi' ? (
                    <>
                      <div>
                        <label htmlFor="wifi-ssid" className={FORM.label}>SSID</label>
                        <input
                          id="wifi-ssid"
                          name="config_ssid"
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
                          name="config_password"
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
                          name="config_router_ip"
                          value={form.config_router_ip}
                          onChange={(e) => setForm({ ...form, config_router_ip: e.target.value })}
                          placeholder="192.0.2.1"
                          className={FORM.input}
                        />
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <label htmlFor="usbnet-bridge" className={FORM.label}>网桥名</label>
                        <input
                          id="usbnet-bridge"
                          name="config_bridge_name"
                          value={form.config_bridge_name}
                          onChange={(e) => setForm({ ...form, config_bridge_name: e.target.value })}
                          required
                          placeholder="br-stpusb-dir"
                          className={FORM.input}
                        />
                      </div>
                      <div>
                        <label htmlFor="usbnet-subnet" className={FORM.label}>网桥子网 CIDR</label>
                        <input
                          id="usbnet-subnet"
                          name="config_subnet_cidr"
                          value={form.config_subnet_cidr}
                          onChange={(e) => setForm({ ...form, config_subnet_cidr: e.target.value })}
                          required
                          placeholder="10.200.0.0/22"
                          className={FORM.input}
                        />
                      </div>
                      <div>
                        <label htmlFor="usbnet-per-host" className={FORM.label}>单宿主最大设备数</label>
                        <input
                          id="usbnet-per-host"
                          name="config_max_devices_per_host"
                          type="number"
                          min={1}
                          max={32}
                          value={form.config_max_devices_per_host}
                          onChange={(e) =>
                            setForm({ ...form, config_max_devices_per_host: e.target.value })
                          }
                          className={FORM.input}
                        />
                      </div>
                    </>
                  )}

                  <div>
                    <label htmlFor="pool-max-devices" className={FORM.label}>全局最大并发设备数</label>
                    <input
                      id="pool-max-devices"
                      name="max_devices"
                      type="number"
                      min={1}
                      max={MAX_DEVICES_LIMIT}
                      value={maxDevicesInput}
                      onChange={(e) => setMaxDevicesInput(e.target.value)}
                      className={FORM.input}
                    />
                  </div>
                  <div>
                    <label htmlFor="pool-host-group" className={FORM.label}>主机组作用域（可选）</label>
                    <input
                      id="pool-host-group"
                      name="host_group"
                      value={form.host_group}
                      onChange={(e) => setForm({ ...form, host_group: e.target.value })}
                      placeholder="留空 = 全部授权主机"
                      className={FORM.input}
                    />
                  </div>
                  <div className="flex items-end gap-2 md:col-span-2 lg:col-span-3">
                    <Button type="submit" disabled={poolFormPending}>
                      {poolFormPending ? '保存中...' : editingId ? '保存修改' : '创建'}
                    </Button>
                    <Button type="button" variant="outline" onClick={resetForm}>取消</Button>
                  </div>
                </form>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">供给池列表（按设备去重负载口径）</CardTitle>
            </CardHeader>
            <CardContent>
              {poolsLoading ? (
                <PageSkeleton.Cards count={3} layout="grid" />
              ) : poolsIsError ? (
                <InlineError
                  // #2359：403/401 是权限错误，不是连接问题——不得把排障引导至检查后端服务连接。
                  message={
                    classifyApiError(poolsError) === 'permission'
                      ? '无权限访问网络供给资源池（该功能需要管理员权限）。'
                      : '网络供给资源池加载失败，请检查后端服务连接。'
                  }
                  onRetry={() => void refetchPools()}
                />
              ) : pools.length === 0 ? (
                <InlineEmpty bordered>暂无网络供给池</InlineEmpty>
              ) : (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {pools.map((pool) => {
                    const loadPct =
                      pool.max_concurrent_devices > 0
                        ? Math.round((pool.current_devices / pool.max_concurrent_devices) * 100)
                        : 0;
                    const isFull = pool.current_devices >= pool.max_concurrent_devices;
                    const egress = poolEgressOf(pool.config);
                    const isUsbNet = pool.resource_type === 'usb_net';

                    return (
                      <div key={pool.id} className={cn(PANEL.root, 'p-4')}>
                        <div className="flex items-start gap-3">
                          <div
                            className={cn(
                              'rounded-md p-2',
                              isFull ? 'bg-destructive/10' : 'bg-success/10',
                            )}
                          >
                            {isUsbNet ? (
                              <Cable
                                className={cn(
                                  'h-4 w-4',
                                  isFull ? 'text-destructive' : 'text-success',
                                )}
                              />
                            ) : isFull ? (
                              <WifiOff className="h-4 w-4 text-destructive" />
                            ) : (
                              <Wifi className="h-4 w-4 text-success" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className={cn('truncate font-medium', TEXT.heading)}>
                                {pool.name}
                              </span>
                              <span className={cn('rounded px-1.5 py-0.5 text-[11px]', STATUS_CHIP.primary)}>
                                {formatNetworkPoolTypeLabel(pool.resource_type)}
                              </span>
                              <span
                                className={cn(
                                  'rounded px-1.5 py-0.5 text-[11px]',
                                  egress === 'vpn' ? STATUS_CHIP.warning : STATUS_CHIP.muted,
                                )}
                              >
                                {formatNetworkEgressLabel(egress)}
                              </span>
                              {pool.host_group && (
                                <span className={cn('rounded px-1.5 py-0.5 text-[11px]', STATUS_CHIP.muted)}>
                                  {pool.host_group}
                                </span>
                              )}
                            </div>
                            <div className={cn('mt-1 font-mono text-xs', TEXT.subtitle)}>
                              {isUsbNet
                                ? `${configString(pool.config?.bridge_name) || 'br-stpusb-dir'} · ${configString(pool.config?.subnet_cidr) || '10.200.0.0/22'}`
                                : configString(pool.config?.ssid) || '-'}
                            </div>
                          </div>
                        </div>

                        <div className="mt-3">
                          <div className={cn('flex items-center justify-between text-xs', TEXT.subtitle)}>
                            <span>去重并发负载</span>
                            <span>
                              {pool.current_devices} / {pool.max_concurrent_devices}
                            </span>
                          </div>
                          <div className="mt-1 h-2 w-full rounded-full bg-muted">
                            <div
                              className={cn(
                                'h-2 rounded-full transition-all',
                                resourceUsageBgClass(loadPct),
                              )}
                              style={{ width: `${Math.min(loadPct, 100)}%` }}
                            />
                          </div>
                        </div>

                        <div className={cn('mt-3 flex flex-wrap items-center gap-2 text-xs', TEXT.subtitle)}>
                          <span>{pool.is_active ? '已启用' : '已停用'}</span>
                          {isUsbNet && typeof pool.config?.max_devices_per_host === 'number' ? (
                            <span>· 单机软上限 {pool.config.max_devices_per_host}</span>
                          ) : null}
                          {!isUsbNet && configString(pool.config?.router_ip) ? (
                            <span>· {configString(pool.config?.router_ip)}</span>
                          ) : null}
                        </div>

                        <div className="mt-3 flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => void startEdit(pool)}
                            className={cn(
                              'flex items-center gap-1 rounded px-2 py-1 text-xs',
                              INTERACTIVE.iconButton,
                              INTERACTIVE.hover,
                            )}
                            aria-label={`编辑 ${pool.name}`}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                            编辑
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleDeletePool(pool)}
                            className={cn(
                              'flex items-center gap-1 rounded px-2 py-1 text-xs',
                              INTERACTIVE.destructiveMenu,
                            )}
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
        </>
      ) : (
        <div className="space-y-4">
          <div
            data-testid="network-sessions-scope-banner"
            className={cn(ALERT_BOX.info, 'rounded-lg px-4 py-3 text-xs leading-5')}
          >
            <div className="font-semibold">受管供网设备口径说明（ADR-0066 D7.2）</div>
            <p className="mt-1">
              本视图仅统计控制面受管 <code className="font-mono">device_network_session</code> 会话（包含经资源池派发的新 <code className="font-mono">setup_network</code> 任务、经资源池派发的存量 <code className="font-mono">connect_wifi</code>/<code className="font-mono">monkey_setup</code>（标「存量 WiFi 路径」）及管理员手动调试会话）。
              不纳入两类非受管路径：① 脚本硬编码 SSID 或直接复用设备既有 WiFi 连接、未经资源池分配的任务；② 本能力上线激活前已在运行的存量 WiFi 任务（不回填历史会话）。
            </p>
          </div>

          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <CardTitle className="text-base">受管网络供给会话列表</CardTitle>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  {(['all', 'wifi', 'usb_net'] as const).map((kind) => (
                    <Button
                      key={kind}
                      type="button"
                      size="sm"
                      variant={sessionTypeFilter === kind ? 'default' : 'outline'}
                      onClick={() => setSessionTypeFilter(kind)}
                    >
                      {kind === 'all' ? '全部方式' : formatNetworkPoolTypeLabel(kind)}
                    </Button>
                  ))}
                  <Button
                    type="button"
                    size="sm"
                    variant={includeReleased ? 'default' : 'outline'}
                    onClick={() => setIncludeReleased((prev) => !prev)}
                  >
                    {includeReleased ? '含已释放' : '仅未释放'}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {sessionsLoading ? (
                <PageSkeleton.Cards count={3} layout="grid" />
              ) : sessionsIsError ? (
                <InlineError
                  message={
                    classifyApiError(sessionsError) === 'permission'
                      ? '无权限查看受管供网会话（该功能需要管理员权限）。'
                      : '受管供网会话加载失败，请检查后端服务连接。'
                  }
                  onRetry={() => void refetchSessions()}
                />
              ) : sessions.length === 0 ? (
                <InlineEmpty bordered>当前筛选条件下暂无受管供网设备会话</InlineEmpty>
              ) : (
                <div className="overflow-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>设备 / Host</TableHead>
                        <TableHead>方式 / 资源池</TableHead>
                        <TableHead>出口策略</TableHead>
                        <TableHead>分配 IP / 路径</TableHead>
                        <TableHead>状态</TableHead>
                        <TableHead>来源</TableHead>
                        <TableHead>有效期 / 释放原因</TableHead>
                        <TableHead className="text-right">操作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {sessions.map((sess) => {
                        const statusMeta = SESSION_STATUS_META[sess.status] ?? {
                          label: sess.status,
                          chipClass: STATUS_CHIP.muted,
                        };
                        const canManualRenew =
                          sess.source === 'manual' &&
                          (sess.status === 'PROVISIONING' || sess.status === 'ACTIVE');
                        const canManualRelease =
                          sess.source === 'manual' &&
                          (sess.status === 'PROVISIONING' || sess.status === 'ACTIVE');
                        const canAuditOverride =
                          sess.status !== 'RELEASED' &&
                          (sess.status === 'RELEASING' ||
                            sess.status === 'RELEASE_FAILED' ||
                            sess.source === 'manual' ||
                            sess.resource_type === 'usb_net');

                        return (
                          <TableRow key={sess.id}>
                            <TableCell>
                              <div className="font-mono text-xs font-medium">
                                {sess.device_serial ?? `#${sess.device_id}`}
                              </div>
                              <div className={cn('text-[11px]', TEXT.subtitle)}>{sess.host_id}</div>
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className={cn('rounded px-1.5 py-0.5 text-[11px]', STATUS_CHIP.primary)}>
                                  {formatNetworkPoolTypeLabel(sess.resource_type)}
                                </span>
                                <span className="text-xs">{sess.pool_name ?? `#${sess.resource_pool_id}`}</span>
                              </div>
                              {sess.legacy_path ? (
                                <span className={cn('mt-1 inline-block rounded px-1.5 py-0.5 text-[11px]', STATUS_CHIP.warning)}>
                                  存量 WiFi 路径
                                </span>
                              ) : null}
                            </TableCell>
                            <TableCell>
                              <span
                                className={cn(
                                  'rounded px-1.5 py-0.5 text-[11px]',
                                  sess.egress === 'vpn' ? STATUS_CHIP.warning : STATUS_CHIP.muted,
                                )}
                              >
                                {formatNetworkEgressLabel(sess.egress)}
                              </span>
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              <div>{sess.assigned_ip ?? '-'}</div>
                              {sess.usb_devpath ? (
                                <div className={cn('text-[11px]', TEXT.subtitle)}>
                                  usb:{sess.usb_devpath}
                                </div>
                              ) : null}
                            </TableCell>
                            <TableCell>
                              <span className={cn('rounded px-2 py-0.5 text-[11px]', statusMeta.chipClass)}>
                                {statusMeta.label}
                              </span>
                            </TableCell>
                            <TableCell className="text-xs">
                              {sess.source === 'job' && sess.plan_run_id ? (
                                <Link
                                  to={`/execution/plan-runs/${sess.plan_run_id}`}
                                  className="text-primary underline underline-offset-2"
                                >
                                  PlanRun #{sess.plan_run_id}
                                </Link>
                              ) : (
                                <span>
                                  手动调试{sess.created_by ? ` · ${sess.created_by}` : ''}
                                </span>
                              )}
                            </TableCell>
                            <TableCell className="text-xs">
                              {sess.status === 'RELEASED' ? (
                                <span className={TEXT.subtitle}>
                                  {sess.release_reason ?? 'released'}
                                </span>
                              ) : (
                                <span className="inline-flex items-center gap-1">
                                  <Clock3 className="h-3 w-3 text-muted-foreground" />
                                  {formatRemainingTtl(sess.expires_at)}
                                </span>
                              )}
                            </TableCell>
                            <TableCell className="text-right">
                              <div className="inline-flex flex-wrap justify-end gap-1.5">
                                {canManualRenew ? (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={renewManualMutation.isPending}
                                    onClick={() => renewManualMutation.mutate(sess.id)}
                                  >
                                    续期 +1h
                                  </Button>
                                ) : null}
                                {canManualRelease ? (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={releaseSessionMutation.isPending}
                                    onClick={() => void handleForceRelease(sess)}
                                  >
                                    强释
                                  </Button>
                                ) : null}
                                {canAuditOverride ? (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="destructive"
                                    onClick={() => {
                                      setOverrideTarget(sess);
                                      setOverrideCategory('device_unreachable');
                                      setOverrideEvidenceRef('');
                                    }}
                                  >
                                    <ShieldAlert className="mr-1 h-3.5 w-3.5" />
                                    审计放行
                                  </Button>
                                ) : null}
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* 手动开启调试供网弹窗 */}
      <Dialog open={enableModalOpen} onOpenChange={setEnableModalOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>手动开启调试供网</DialogTitle>
            <DialogDescription>
              同事务获取 MAINTENANCE 租约并与任务准入互斥；存在运行中任务或归还欠账的设备会被拒绝。
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleEnableSubmit} className="space-y-4">
            <div>
              <label htmlFor="manual-enable-device" className={FORM.label}>目标在线设备</label>
              <select
                id="manual-enable-device"
                value={enableDeviceId}
                onChange={(e) => setEnableDeviceId(e.target.value)}
                required
                className={FORM.input}
              >
                <option value="">请选择在线设备...</option>
                {onlineDevices.map((d) => (
                  <option key={d.id} value={String(d.id)}>
                    {d.serial} ({d.host_id ?? 'no-host'} · {d.model ?? 'unknown'})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="manual-enable-pool" className={FORM.label}>网络供给池</label>
              <select
                id="manual-enable-pool"
                value={enablePoolId}
                onChange={(e) => setEnablePoolId(e.target.value)}
                required
                className={FORM.input}
              >
                <option value="">请选择已启用供给池...</option>
                {activePools.map((p) => (
                  <option key={p.id} value={String(p.id)}>
                    {p.name} ({formatNetworkPoolTypeLabel(p.resource_type)} · {formatNetworkEgressLabel(poolEgressOf(p.config))})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="manual-enable-ttl" className={FORM.label}>有效时长（分钟，默认 240）</label>
              <input
                id="manual-enable-ttl"
                type="number"
                min={5}
                max={1440}
                value={enableTtlMinutes}
                onChange={(e) => setEnableTtlMinutes(e.target.value)}
                required
                className={FORM.input}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setEnableModalOpen(false)}>
                取消
              </Button>
              <Button type="submit" disabled={enableManualMutation.isPending}>
                {enableManualMutation.isPending ? '开启中...' : '确认开启'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 专用审计放行弹窗 */}
      <Dialog open={overrideTarget != null} onOpenChange={(open) => { if (!open) setOverrideTarget(null); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>专用审计放行（高危运维操作）</DialogTitle>
            <DialogDescription>
              将会话置为 RELEASED(manual_override) 并按全设备余债收口租约。
              {overrideTarget?.resource_type === 'usb_net'
                ? ' 对 usb_net 会同时关闭欠账、将归还态标为「未验证（人工放行）」，并向宿主下发 usbnet-revoke 指令。'
                : ' 手动 WiFi 仅收口会话与 manual_network_supply 租约，不产生伪欠账。'}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleAuditOverrideSubmit} className="space-y-4">
            <div>
              <label htmlFor="override-reason-category" className={FORM.label}>放行原因类别</label>
              <select
                id="override-reason-category"
                value={overrideCategory}
                onChange={(e) => setOverrideCategory(e.target.value)}
                required
                className={FORM.input}
              >
                {OVERRIDE_CATEGORIES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="override-evidence-ref" className={FORM.label}>凭据 / 工单 / 审批说明（必填）</label>
              <input
                id="override-evidence-ref"
                value={overrideEvidenceRef}
                onChange={(e) => setOverrideEvidenceRef(e.target.value)}
                required
                placeholder="例: OPS-4212 已现场确认拔线 / 设备退机"
                className={FORM.input}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setOverrideTarget(null)}>
                取消
              </Button>
              <Button
                type="submit"
                variant="destructive"
                disabled={auditOverrideMutation.isPending}
              >
                {auditOverrideMutation.isPending ? '提交中...' : '确认审计放行'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </PageContainer>
  );
}
