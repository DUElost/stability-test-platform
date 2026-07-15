import { CheckCircle2, Server, ShieldCheck, XCircle } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

export interface ReadinessDevice {
  id: number;
  serial: string;
  model?: string | null;
  host_id?: string | number | null;
  status: string;
  schedulable?: boolean;
  adb_connected?: boolean | null;
  adb_state?: string | null;
  build_display_id?: string | null;
}

export interface ReadinessHost {
  id: string | number;
  name?: string | null;
  ip?: string | null;
  status: string;
}

export function evaluateDeviceReadiness(
  devices: ReadinessDevice[],
  hosts: ReadinessHost[],
) {
  const hostMap = new Map(hosts.map((host) => [String(host.id), host]));
  const rows = devices.map((device) => {
    const reasons: string[] = [];
    const host = device.host_id != null ? hostMap.get(String(device.host_id)) : undefined;
    if (
      device.schedulable === false ||
      (typeof device.schedulable !== 'boolean' && device.status !== 'ONLINE')
    ) reasons.push('设备不可调度');
    if (device.adb_connected === false || ['offline', 'unknown', 'unauthorized'].includes(device.adb_state ?? '')) {
      reasons.push(`ADB ${device.adb_state || '离线'}`);
    }
    if (host && host.status !== 'ONLINE') reasons.push('节点离线');
    return { device, host, reasons, ready: reasons.length === 0 };
  });
  const byHost = new Map<string, { label: string; total: number; ready: number }>();
  rows.forEach((row) => {
    const key = String(row.device.host_id ?? 'unassigned');
    const current = byHost.get(key) ?? {
      label: row.host?.name || row.host?.ip || (key === 'unassigned' ? '未分配节点' : key),
      total: 0,
      ready: 0,
    };
    current.total += 1;
    if (row.ready) current.ready += 1;
    byHost.set(key, current);
  });
  return {
    rows,
    byHost: Array.from(byHost.values()),
    readyCount: rows.filter((row) => row.ready).length,
    blockedCount: rows.filter((row) => !row.ready).length,
    passed: devices.length > 0 && rows.every((row) => row.ready),
  };
}

export function PlanDeviceReadinessCard({
  result,
  versionFilter, hostFilter, modelFilter,
  onVersionFilterChange, onHostFilterChange, onModelFilterChange,
}: {
  result: ReturnType<typeof evaluateDeviceReadiness>;
  versionFilter: string; hostFilter: string; modelFilter: string;
  onVersionFilterChange: (value: string) => void;
  onHostFilterChange: (value: string) => void;
  onModelFilterChange: (value: string) => void;
}) {
  const versions = Array.from(new Set(result.rows.map(({ device }) => device.build_display_id).filter(Boolean) as string[])).sort();
  const models = Array.from(new Set(result.rows.map(({ device }) => device.model).filter(Boolean) as string[])).sort();
  const hosts = Array.from(new Map(result.rows.map(({ device, host }) => {
    const id = String(device.host_id ?? 'unassigned');
    return [id, host?.ip || host?.name || (id === 'unassigned' ? '未分配节点' : id)];
  })).entries());
  const visibleRows = result.rows.filter(({ device }) =>
    (versionFilter === 'all' || (device.build_display_id ?? '') === versionFilter) &&
    (modelFilter === 'all' || (device.model ?? '') === modelFilter) &&
    (hostFilter === 'all' || String(device.host_id ?? 'unassigned') === hostFilter)
  );
  return (
    <Card>
      <CardHeader><CardTitle className="text-base">3. 测试准备检查</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className={cn('mb-2 text-xs', TEXT.subtitle)}>按已选设备的平台数据聚合查看，不需要手工输入</div>
          <div className="grid gap-3 md:grid-cols-3">
            <Select value={versionFilter} onValueChange={onVersionFilterChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部版本（{versions.length}）</SelectItem>{versions.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
            <Select value={hostFilter} onValueChange={onHostFilterChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部节点（{hosts.length}）</SelectItem>{hosts.map(([id, label]) => <SelectItem key={id} value={id}>{label}</SelectItem>)}</SelectContent></Select>
            <Select value={modelFilter} onValueChange={onModelFilterChange}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部型号（{models.length}）</SelectItem>{models.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="rounded-lg bg-muted/50 p-3"><div className="text-xl font-semibold">{result.rows.length}</div><div className={cn('text-xs', TEXT.subtitle)}>已选择</div></div>
          <div className="rounded-lg bg-success/10 p-3 text-success"><div className="text-xl font-semibold">{result.readyCount}</div><div className="text-xs">已就绪</div></div>
          <div className="rounded-lg bg-destructive/10 p-3 text-destructive"><div className="text-xl font-semibold">{result.blockedCount}</div><div className="text-xs">阻塞</div></div>
          <div className="rounded-lg bg-muted/50 p-3"><div className="text-xl font-semibold">{result.byHost.length}</div><div className={cn('text-xs', TEXT.subtitle)}>节点数</div></div>
        </div>
        <div className="space-y-2">
          {result.byHost.map((host) => (
            <div key={host.label} className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm">
              <Server className="h-4 w-4 text-muted-foreground" /><span className="flex-1">{host.label}</span>
              <span className={host.ready === host.total ? 'text-success' : 'text-destructive'}>{host.ready}/{host.total} 就绪</span>
            </div>
          ))}
          {(versionFilter !== 'all' || hostFilter !== 'all' || modelFilter !== 'all') && <div className={cn('text-xs', TEXT.subtitle)}>当前聚合结果：{visibleRows.length} 台，其中 {visibleRows.filter(row => row.ready).length} 台就绪</div>}
          {visibleRows.filter(row => !row.ready).slice(0, 5).map(row => <div key={row.device.id} className="flex items-center gap-2 text-xs text-destructive"><XCircle className="h-3.5 w-3.5" />{row.device.serial}：{row.reasons.join('、')}</div>)}
        </div>
        <div className={cn('flex items-center gap-2 rounded-lg px-3 py-2 text-sm', result.passed ? 'bg-success/10 text-success' : 'bg-warning/10 text-warning')}>
          {result.passed ? <ShieldCheck className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
          {result.passed ? '测试准备检查通过，可以预览执行。' : '请处理阻塞设备或调整测试准备配置。'}
        </div>
      </CardContent>
    </Card>
  );
}
