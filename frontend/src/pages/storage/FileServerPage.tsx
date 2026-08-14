import { useMemo, useState, type ReactNode } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Database,
  HardDrive,
  Network,
  RefreshCw,
  Server,
} from 'lucide-react';
import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { PageContainer, PageHeader } from '@/components/layout';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ErrorState } from '@/components/ui/error-state';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { CHART_COLORS, STATUS_TEXT_COLORS } from '@/design-system/colors';
import { tabLinkClass, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { api } from '@/utils/api';
import type {
  FileServerClientMount,
  FileServerMetricPoint,
  FileServerNodeIdentity,
  FileServerNodeMonitoring,
  FileServerNodeSystem,
  FileServerNfs,
  FileServerOverview,
  FileServerStorage,
} from '@/utils/api/types';

const HISTORY_RANGES = [
  { hours: 6, label: '6H' },
  { hours: 24, label: '24H' },
  { hours: 168, label: '7D' },
] as const;

type PanelKey = 'control-plane' | 'storage-server';

function formatBytes(value: number | null | undefined, rate = false): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let scaled = Math.max(0, value);
  let unit = 0;
  while (scaled >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit += 1;
  }
  const digits = scaled >= 100 || unit === 0 ? 0 : scaled >= 10 ? 1 : 2;
  return `${scaled.toFixed(digits)} ${units[unit]}${rate ? '/s' : ''}`;
}

function formatRate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${value < 10 ? value.toFixed(2) : value.toFixed(1)}/s`;
}

function formatUptime(value: number | null): string {
  if (value == null) return '—';
  const days = Math.floor(value / 86400);
  const hours = Math.floor((value % 86400) / 3600);
  return days > 0 ? `${days} 天 ${hours} 小时` : `${hours} 小时`;
}

function formatTime(value: string | null): string {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value));
}

function statusBadge(status: FileServerOverview['status']) {
  if (status === 'healthy') return <Badge variant="success">正常</Badge>;
  if (status === 'warning') return <Badge variant="warning">需关注</Badge>;
  return <Badge variant="destructive">异常</Badge>;
}

function metricTone(value: number | null, warning: number, critical: number): string {
  if (value == null) return STATUS_TEXT_COLORS.muted;
  if (value >= critical) return STATUS_TEXT_COLORS.error;
  if (value >= warning) return STATUS_TEXT_COLORS.warning;
  return STATUS_TEXT_COLORS.success;
}

function MetricCard({
  icon: Icon,
  label,
  value,
  detail,
}: {
  icon: typeof HardDrive;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <Card className="min-w-0 rounded-md p-4 shadow-none">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className={cn('text-xs', TEXT.caption)}>{label}</div>
          <div className={cn('mt-2 truncate text-xl font-semibold', TEXT.heading)}>{value}</div>
          <div className={cn('mt-1 truncate text-xs', TEXT.subtitle)}>{detail}</div>
        </div>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted">
          <Icon className={cn('h-4 w-4', TEXT.subtitle)} />
        </div>
      </div>
    </Card>
  );
}

type ChartPoint = {
  timestamp: number;
  capacity?: number;
  cpu?: number;
  memory?: number;
};

function mergeHistory(data: FileServerOverview['history']): ChartPoint[] {
  const points = new Map<number, ChartPoint>();
  const add = (series: FileServerMetricPoint[], key: keyof Omit<ChartPoint, 'timestamp'>) => {
    series.forEach((point) => {
      const current = points.get(point.timestamp) ?? { timestamp: point.timestamp };
      current[key] = point.value;
      points.set(point.timestamp, current);
    });
  };
  add(data.capacity_usage_pct, 'capacity');
  add(data.cpu_usage_pct, 'cpu');
  add(data.memory_usage_pct, 'memory');
  return [...points.values()].sort((a, b) => a.timestamp - b.timestamp);
}

function CapacityChart({ data }: { data: FileServerOverview['history'] }) {
  const points = useMemo(() => mergeHistory(data), [data]);
  return (
    <div className="h-60 w-full" data-testid="file-server-history-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
          <XAxis
            dataKey="timestamp"
            tickFormatter={(value) => formatTime(new Date(value * 1000).toISOString()).slice(0, 5)}
            minTickGap={32}
            tick={{ fontSize: 11 }}
          />
          <YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} tick={{ fontSize: 11 }} />
          <Tooltip
            labelFormatter={(value) => new Date(Number(value) * 1000).toLocaleString('zh-CN')}
            formatter={(value, name) => [
              `${Number(value).toFixed(1)}%`,
              name === 'capacity' ? '存储' : name === 'cpu' ? 'CPU' : '内存',
            ]}
          />
          <Line type="monotone" dataKey="capacity" stroke={CHART_COLORS.warning} dot={false} strokeWidth={2} connectNulls />
          <Line type="monotone" dataKey="cpu" stroke={CHART_COLORS.primary} dot={false} strokeWidth={1.5} connectNulls />
          <Line type="monotone" dataKey="memory" stroke={CHART_COLORS.success} dot={false} strokeWidth={1.5} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((item) => <Skeleton key={item} className="h-28" />)}
      </div>
      <Skeleton className="h-72" />
      <Skeleton className="h-80" />
    </div>
  );
}

const alertText: Record<string, string> = {
  STORAGE_NOT_MOUNTED: '存储盘未挂载',
  NFS_EXPORT_MISSING: 'NFS export 缺失',
  METRICS_UNAVAILABLE: '控制面监控指标暂不可用',
  STORAGE_METRICS_UNAVAILABLE: '存储机监控指标暂不可用',
  CAPACITY_CRITICAL: '存储容量超过 90%',
  CAPACITY_WARNING: '存储容量超过 80%',
  AGENT_MOUNT_INCOMPLETE: '部分 Agent 尚未确认共享挂载',
  DEVICE_LOG_DISK_CRITICAL: '设备日志盘超过 95%',
  DEVICE_LOG_DISK_WARNING: '设备日志盘超过 90%',
};

function MonitoringBadge({ monitoring }: { monitoring: FileServerNodeMonitoring }) {
  return monitoring.prometheus_available ? (
    <Badge variant="success">Prometheus 在线</Badge>
  ) : (
    <Badge variant="destructive">Prometheus 不可用</Badge>
  );
}

function NodeHeader({
  title,
  node,
  monitoring,
  trailing,
}: {
  title: string;
  node: FileServerNodeIdentity;
  monitoring: FileServerNodeMonitoring;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
      <div>
        <h2 className={cn('text-sm font-semibold', TEXT.heading)}>{title}</h2>
        <p className={cn('mt-0.5 text-xs', TEXT.caption)}>
          {node.hostname} · {node.address}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {trailing}
        <MonitoringBadge monitoring={monitoring} />
      </div>
    </div>
  );
}

function SystemLoadSection({
  system,
  node,
  idPrefix,
}: {
  system: FileServerNodeSystem;
  node: FileServerNodeIdentity;
  idPrefix: string;
}) {
  const cpu = system.cpu_usage_pct;
  const memory = system.memory_usage_pct;
  const titleId = `${idPrefix}-load-title`;
  return (
    <section className="py-4" aria-labelledby={titleId}>
      <div className="mb-4 flex items-center gap-2">
        <Cpu className={cn('h-4 w-4', TEXT.subtitle)} />
        <h2 id={titleId} className={cn('text-sm font-semibold', TEXT.heading)}>主机负载</h2>
      </div>
      <div className="space-y-4">
        <div>
          <div className="mb-1.5 flex justify-between text-xs">
            <span className={TEXT.subtitle}>CPU</span>
            <span className={metricTone(cpu, 75, 90)}>{cpu != null ? cpu.toFixed(1) : '—'}%</span>
          </div>
          <Progress value={cpu ?? 0} />
        </div>
        <div>
          <div className="mb-1.5 flex justify-between text-xs">
            <span className={TEXT.subtitle}>内存</span>
            <span className={metricTone(memory, 80, 90)}>{memory != null ? memory.toFixed(1) : '—'}%</span>
          </div>
          <Progress value={memory ?? 0} />
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-4">
          <div><div className={TEXT.caption}>Load 1m</div><div className="mt-1 font-mono">{system.load1 != null ? system.load1.toFixed(2) : '—'}</div></div>
          <div><div className={TEXT.caption}>CPU 核心</div><div className="mt-1 font-mono">{node.cpu_count ?? '—'}</div></div>
          <div><div className={TEXT.caption}>总内存</div><div className="mt-1 font-mono">{formatBytes(system.memory_total_bytes)}</div></div>
          <div><div className={TEXT.caption}>运行时间</div><div className="mt-1 font-mono">{formatUptime(node.uptime_seconds)}</div></div>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 border-t pt-3 text-xs sm:grid-cols-4">
          <div><div className={TEXT.caption}>磁盘读取</div><div className="mt-1 font-mono">{formatBytes(system.disk_read_bytes_per_second, true)}</div></div>
          <div><div className={TEXT.caption}>磁盘写入</div><div className="mt-1 font-mono">{formatBytes(system.disk_write_bytes_per_second, true)}</div></div>
          <div><div className={TEXT.caption}>网络接收</div><div className="mt-1 font-mono">{formatBytes(system.network_receive_bytes_per_second, true)}</div></div>
          <div><div className={TEXT.caption}>网络发送</div><div className="mt-1 font-mono">{formatBytes(system.network_transmit_bytes_per_second, true)}</div></div>
        </div>
      </div>
    </section>
  );
}

function ClientMountSection({
  mount,
  idPrefix,
}: {
  mount: FileServerClientMount;
  idPrefix: string;
}) {
  const titleId = `${idPrefix}-client-mount-title`;
  return (
    <section className="border-t py-4" aria-labelledby={titleId}>
      <div className="mb-4 flex items-center gap-2">
        <HardDrive className={cn('h-4 w-4', TEXT.subtitle)} />
        <h2 id={titleId} className={cn('text-sm font-semibold', TEXT.heading)}>客户端挂载</h2>
      </div>
      <dl className="divide-y text-sm">
        <div className="flex items-center justify-between gap-4 py-2"><dt className={cn('shrink-0', TEXT.subtitle)}>挂载路径</dt><dd className="truncate font-mono text-xs">{mount.path}</dd></div>
        <div className="flex items-center justify-between gap-4 py-2"><dt className={cn('shrink-0', TEXT.subtitle)}>源</dt><dd className="truncate font-mono text-xs">{[mount.source, mount.filesystem].filter(Boolean).join(' · ') || '—'}</dd></div>
        <div className="flex items-center justify-between py-2"><dt className={TEXT.subtitle}>挂载状态</dt><dd>{mount.mounted ? <Badge variant="success">已挂载</Badge> : <Badge variant="destructive">未挂载</Badge>}</dd></div>
        <div className="flex items-center justify-between py-2"><dt className={TEXT.subtitle}>写权限</dt><dd>{mount.backend_write_access ? <Badge variant="success">可写</Badge> : <Badge variant="destructive">不可写</Badge>}</dd></div>
      </dl>
    </section>
  );
}

function StorageMetricCards({
  disk,
  nfs,
  agents,
}: {
  disk: FileServerStorage;
  nfs: FileServerNfs;
  agents: FileServerOverview['agents'];
}) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard
        icon={HardDrive}
        label="可用容量"
        value={formatBytes(disk.available_bytes)}
        detail={`总计 ${formatBytes(disk.total_bytes)}`}
      />
      <MetricCard
        icon={Database}
        label="存储使用率"
        value={`${disk.used_pct.toFixed(1)}%`}
        detail={`inode ${disk.inode_used_pct.toFixed(1)}%`}
      />
      <MetricCard
        icon={Activity}
        label="NFS 请求"
        value={formatRate(nfs.requests_per_second)}
        detail={`累计连接 ${nfs.connections_total ?? '—'}`}
      />
      <MetricCard
        icon={Server}
        label="Agent 挂载"
        value={`${agents.mounted}/${agents.total}`}
        detail={`异常 ${agents.failed} · 未上报 ${agents.unreported}`}
      />
    </div>
  );
}

function NfsSection({
  nfs,
  idPrefix,
}: {
  nfs: FileServerNfs;
  idPrefix: string;
}) {
  const titleId = `${idPrefix}-nfs-service-title`;
  return (
    <section className="border-t py-4" aria-labelledby={titleId}>
      <div className="mb-4 flex items-center gap-2">
        <Network className={cn('h-4 w-4', TEXT.subtitle)} />
        <h2 id={titleId} className={cn('text-sm font-semibold', TEXT.heading)}>NFS 服务</h2>
      </div>
      <dl className="divide-y text-sm">
        <div className="flex items-center justify-between py-2"><dt className={TEXT.subtitle}>服务状态</dt><dd>{nfs.service_ready ? <Badge variant="success">运行中</Badge> : <Badge variant="destructive">异常</Badge>}</dd></div>
        <div className="flex items-center justify-between gap-4 py-2"><dt className={cn('shrink-0', TEXT.subtitle)}>Export</dt><dd className="truncate text-right font-mono text-xs">{nfs.export_targets.join(', ') || '—'}</dd></div>
        <div className="flex items-center justify-between py-2"><dt className={TEXT.subtitle}>服务线程</dt><dd className="font-mono">{nfs.server_threads ?? '—'}</dd></div>
        <div className="flex items-center justify-between py-2"><dt className={TEXT.subtitle}>RPC 错误</dt><dd className={cn('font-mono', (nfs.rpc_errors_per_second ?? 0) > 0 && 'text-destructive')}>{formatRate(nfs.rpc_errors_per_second)}</dd></div>
        <div className="flex items-center justify-between py-2"><dt className={TEXT.subtitle}>Stale handle</dt><dd className={cn('font-mono', (nfs.stale_file_handles_total ?? 0) > 0 && 'text-destructive')}>{nfs.stale_file_handles_total ?? '—'}</dd></div>
      </dl>
    </section>
  );
}

function AgentStatusSection({
  agents,
  disks,
}: {
  agents: FileServerOverview['agents'];
  disks: FileServerOverview['device_log_disks'];
}) {
  const diskByHost = new Map(disks.items.map((item) => [item.host_id, item]));
  const warningCount = disks.warning + disks.critical;
  return (
    <section className="rounded-md border" aria-labelledby="agent-status-title">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div>
          <h2 id="agent-status-title" className={cn('text-sm font-semibold', TEXT.heading)}>Agent 状态（挂载与设备日志盘）</h2>
          <p className={cn('mt-0.5 text-xs', TEXT.caption)}>
            中心存储挂载 {agents.mounted}/{agents.total}
            {' · 设备日志盘上报 '}{disks.reported}/{disks.total}
            {` · 告警 ${warningCount}`}
          </p>
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>主机</TableHead>
            <TableHead>Agent</TableHead>
            <TableHead>中心存储挂载</TableHead>
            <TableHead>设备日志盘</TableHead>
            <TableHead>已用 / 总容量</TableHead>
            <TableHead className="w-36">使用率</TableHead>
            <TableHead className="text-right">最近心跳</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {agents.items.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} className="text-center text-muted-foreground">暂无在线 host</TableCell>
            </TableRow>
          ) : agents.items.map((host) => {
            const disk = diskByHost.get(host.host_id);
            return (
              <TableRow key={host.host_id}>
                <TableCell className="font-mono text-xs">{host.ip ?? host.host_id}</TableCell>
                <TableCell><Badge variant={host.status === 'ONLINE' ? 'success' : 'secondary'}>{host.status}</Badge></TableCell>
                <TableCell>
                  {host.mounted === true ? (
                    <Badge variant="success">已挂载</Badge>
                  ) : host.mounted === false ? (
                    <Badge variant="destructive">异常</Badge>
                  ) : (
                    <Badge variant="outline">未上报</Badge>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">{disk?.path || '—'}</TableCell>
                <TableCell className="font-mono text-xs">
                  {disk ? `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)}` : '—'}
                </TableCell>
                <TableCell>
                  {disk ? (
                    <div className="flex items-center gap-2">
                      <Progress
                        value={disk.usage_percent}
                        indicatorClassName={
                          disk.usage_percent >= 95 ? 'bg-destructive' : disk.usage_percent >= 90 ? 'bg-warning' : 'bg-success'
                        }
                      />
                      <span className={cn('font-mono text-xs', metricTone(disk.usage_percent, 90, 95))}>
                        {disk.usage_percent.toFixed(1)}%
                      </span>
                    </div>
                  ) : (
                    <span className={cn('text-xs', TEXT.subtitle)}>未上报</span>
                  )}
                </TableCell>
                <TableCell className="text-right font-mono text-xs">{formatTime(host.last_heartbeat)}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </section>
  );
}

export default function FileServerPage() {
  const [hours, setHours] = useState(6);
  const [activePanel, setActivePanel] = useState<PanelKey>('control-plane');
  const query = useQuery({
    queryKey: ['file-server-overview', hours],
    queryFn: () => api.stats.fileServer(hours),
    refetchInterval: hours <= 6 ? 15000 : 60000,
    placeholderData: keepPreviousData,
  });

  const data = query.data;
  const refreshAction = (
    <Button
      type="button"
      variant="outline"
      size="icon"
      onClick={() => query.refetch()}
      disabled={query.isFetching}
      aria-label="刷新文件服务器状态"
      title="刷新"
    >
      <RefreshCw className={cn('h-4 w-4', query.isFetching && 'animate-spin')} />
    </Button>
  );

  if (query.isError && !data) {
    return (
      <PageContainer fullBleed className="space-y-6 p-4 lg:p-6">
        <PageHeader title="文件服务器" subtitle="控制面与中心存储" action={refreshAction} />
        <ErrorState title="文件服务器状态加载失败" onRetry={() => query.refetch()} />
      </PageContainer>
    );
  }

  return (
    <PageContainer fullBleed className="space-y-6 p-4 lg:p-6">
      <PageHeader
        title="文件服务器"
        subtitle={
          data
            ? `${data.control_plane.node.address}（控制面） · ${data.storage_server.node.address}（存储机）`
            : '控制面与中心存储'
        }
        action={refreshAction}
      />

      {query.isLoading || !data ? <LoadingState /> : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 border-y py-3">
            <div className="flex items-center gap-2">
              {data.status === 'healthy' ? (
                <CheckCircle2 className="h-5 w-5 text-success" />
              ) : (
                <AlertTriangle className={cn('h-5 w-5', data.status === 'critical' ? 'text-destructive' : 'text-warning')} />
              )}
              <span className={cn('text-sm font-medium', TEXT.heading)}>共享存储健康</span>
              {statusBadge(data.status)}
            </div>
            <div className={cn('text-xs', TEXT.caption)}>
              更新于 {formatTime(data.generated_at)}
            </div>
          </div>

          {data.alerts.length > 0 && (
            <div className="space-y-2" aria-label="存储告警">
              {data.alerts.map((alert) => (
                <div
                  key={alert.code}
                  className={cn(
                    'flex items-center gap-2 rounded-md border px-3 py-2 text-sm',
                    alert.severity === 'critical'
                      ? 'border-destructive/30 bg-destructive/10 text-destructive'
                      : 'border-warning/30 bg-warning/10 text-warning',
                  )}
                >
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>{alertText[alert.code] ?? alert.message}</span>
                </div>
              ))}
            </div>
          )}

          <section className="rounded-md border" aria-labelledby="resource-trend-title">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
              <div>
                <h2 id="resource-trend-title" className={cn('text-sm font-semibold', TEXT.heading)}>资源趋势</h2>
                <p className={cn('mt-0.5 text-xs', TEXT.caption)}>最近 {data.history.hours} 小时</p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <div className={cn('flex gap-4 text-xs', TEXT.caption)}>
                  <span className="text-warning">存储</span>
                  <span className="text-primary">CPU</span>
                  <span className="text-success">内存</span>
                </div>
                <div className="flex rounded-md border" role="group" aria-label="历史时间范围">
                  {HISTORY_RANGES.map((range) => (
                    <Button
                      key={range.hours}
                      type="button"
                      variant="ghost"
                      size="sm"
                      className={cn(
                        'rounded-none first:rounded-l-md last:rounded-r-md',
                        hours === range.hours && 'bg-muted font-medium',
                      )}
                      aria-pressed={hours === range.hours}
                      onClick={() => setHours(range.hours)}
                    >
                      {range.label}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
            <div className="p-3">
              <CapacityChart data={data.history} />
            </div>
          </section>

          <div className="flex border-b" role="tablist" aria-label="节点面板">
            <button
              id="panel-tab-control-plane"
              type="button"
              role="tab"
              aria-selected={activePanel === 'control-plane'}
              aria-controls="panel-control-plane"
              className={tabLinkClass(activePanel === 'control-plane')}
              onClick={() => setActivePanel('control-plane')}
            >
              控制平面
            </button>
            <button
              id="panel-tab-storage-server"
              type="button"
              role="tab"
              aria-selected={activePanel === 'storage-server'}
              aria-controls="panel-storage-server"
              className={tabLinkClass(activePanel === 'storage-server')}
              onClick={() => setActivePanel('storage-server')}
            >
              中心存储机
            </button>
          </div>

          <div
            id="panel-control-plane"
            role="tabpanel"
            aria-labelledby="panel-tab-control-plane"
            hidden={activePanel !== 'control-plane'}
          >
            <div className="rounded-md border">
              <NodeHeader
                title="控制面"
                node={data.control_plane.node}
                monitoring={data.control_plane.monitoring}
              />
              <div className="space-y-4 px-4 pb-4">
                <SystemLoadSection system={data.control_plane.system} node={data.control_plane.node} idPrefix="control-plane" />
                <ClientMountSection mount={data.control_plane.client_mount} idPrefix="control-plane" />
              </div>
            </div>
          </div>

          <div
            id="panel-storage-server"
            role="tabpanel"
            aria-labelledby="panel-tab-storage-server"
            hidden={activePanel !== 'storage-server'}
          >
            <div className="space-y-6">
              <StorageMetricCards
                disk={data.storage_server.disk}
                nfs={data.storage_server.nfs}
                agents={data.agents}
              />
              <div className="rounded-md border">
                <NodeHeader
                  title="中心存储机"
                  node={data.storage_server.node}
                  monitoring={data.storage_server.monitoring}
                  trailing={
                    data.storage_server.same_source ? (
                      <Badge variant="outline">与控制面同机</Badge>
                    ) : null
                  }
                />
                <div className="space-y-4 px-4 pb-4">
                  <NfsSection nfs={data.storage_server.nfs} idPrefix="storage-server" />
                  <SystemLoadSection system={data.storage_server.system} node={data.storage_server.node} idPrefix="storage-server" />
                </div>
              </div>
            </div>
          </div>

          <AgentStatusSection agents={data.agents} disks={data.device_log_disks} />
        </>
      )}
    </PageContainer>
  );
}
