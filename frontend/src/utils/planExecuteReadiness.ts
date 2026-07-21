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
  tags?: string[] | null;
}

export interface ReadinessHost {
  id: string | number;
  name?: string | null;
  ip?: string | null;
  status: string;
}

export interface CapacityOverflowHost {
  id: string | number;
  name?: string | null;
  ip?: string | null;
  status?: string;
  health?: {
    status?: string | null;
    reasons?: string[];
  } | null;
  capacity?: {
    effective_slots?: number;
    available_slots?: number;
    active_jobs?: number;
    active_devices?: number;
    online_healthy_devices?: number;
  } | null;
}

export interface CapacityOverflowWarning {
  hostId: string;
  hostLabel: string;
  selected: number;
  effectiveSlots: number;
  message: string;
}

export interface CapacityPlanRow {
  hostId: string;
  hostLabel: string;
  selected: number;
  effectiveSlots: number | null;
  immediate: number | null;
  queued: number | null;
  healthStatus: string | null;
  healthReasons: string[];
}

export interface NodeSortEntry {
  id: string;
  label: string;
}

function parseIpv4(value: string): number[] | null {
  const match = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(value.trim());
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3]), Number(match[4])];
}

/**
 * 节点侧栏固定排序：IPv4 按八位组数值序，IP 优先于非 IP 名称，
 * 非 IP 之间按 numeric localeCompare，unassigned 恒置底。
 * 避免设备列表轮询刷新导致侧栏顺序漂移。
 */
export function compareNodeEntries(a: NodeSortEntry, b: NodeSortEntry): number {
  const aUnassigned = a.id === 'unassigned';
  const bUnassigned = b.id === 'unassigned';
  if (aUnassigned !== bUnassigned) return aUnassigned ? 1 : -1;
  const aIp = parseIpv4(a.label);
  const bIp = parseIpv4(b.label);
  if (aIp && bIp) {
    for (let i = 0; i < 4; i += 1) {
      if (aIp[i] !== bIp[i]) return aIp[i] - bIp[i];
    }
    return 0;
  }
  if (aIp) return -1;
  if (bIp) return 1;
  return a.label.localeCompare(b.label, undefined, { numeric: true });
}

/**
 * 按节点对比「本次选中数 vs effective_slots（剩余可派发槽位）」。
 * effective_slots 缺失时不告警，避免心跳未到/过期导致误报。
 */
export function evaluateCapacityOverflow(
  selectedDevices: ReadinessDevice[],
  hosts: CapacityOverflowHost[],
): CapacityOverflowWarning[] {
  const hostMap = new Map(hosts.map(host => [String(host.id), host]));
  const counts = new Map<string, number>();
  for (const device of selectedDevices) {
    const hostId = String(device.host_id ?? 'unassigned');
    if (hostId === 'unassigned') continue;
    counts.set(hostId, (counts.get(hostId) ?? 0) + 1);
  }
  const warnings: CapacityOverflowWarning[] = [];
  for (const [hostId, selected] of counts) {
    const host = hostMap.get(hostId);
    const slots = host?.capacity?.effective_slots;
    if (typeof slots !== 'number' || !Number.isFinite(slots)) continue;
    if (selected <= slots) continue;
    const hostLabel = host?.ip || host?.name || hostId;
    warnings.push({
      hostId,
      hostLabel,
      selected,
      effectiveSlots: slots,
      message: `节点 ${hostLabel} 本次选中 ${selected} 台，超出剩余可派发槽位 ${slots} 个，将排队执行`,
    });
  }
  return warnings;
}

export function buildCapacityPlan(
  selectedDevices: ReadinessDevice[],
  hosts: CapacityOverflowHost[],
): CapacityPlanRow[] {
  const hostMap = new Map(hosts.map(host => [String(host.id), host]));
  const counts = new Map<string, number>();
  for (const device of selectedDevices) {
    const hostId = String(device.host_id ?? 'unassigned');
    counts.set(hostId, (counts.get(hostId) ?? 0) + 1);
  }

  return Array.from(counts, ([hostId, selected]) => {
    const host = hostMap.get(hostId);
    const rawSlots = host?.capacity?.effective_slots;
    const effectiveSlots = typeof rawSlots === 'number' && Number.isFinite(rawSlots)
      ? Math.max(0, Math.floor(rawSlots))
      : null;
    const hostLabel = host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
    return {
      hostId,
      hostLabel,
      selected,
      effectiveSlots,
      immediate: effectiveSlots == null ? null : Math.min(selected, effectiveSlots),
      queued: effectiveSlots == null ? null : Math.max(0, selected - effectiveSlots),
      healthStatus: host?.health?.status ?? host?.status ?? null,
      healthReasons: host?.health?.reasons ?? [],
    };
  }).sort((a, b) => compareNodeEntries(
    { id: a.hostId, label: a.hostLabel },
    { id: b.hostId, label: b.hostLabel },
  ));
}

export function evaluateDeviceReadiness(devices: ReadinessDevice[], hosts: ReadinessHost[]) {
  const hostMap = new Map(hosts.map(host => [String(host.id), host]));
  const rows = devices.map(device => {
    const reasons: string[] = [];
    const host = device.host_id != null ? hostMap.get(String(device.host_id)) : undefined;
    if (device.schedulable === false || (typeof device.schedulable !== 'boolean' && device.status !== 'ONLINE')) reasons.push('设备不可调度');
    if (device.adb_connected === false || ['offline', 'unknown', 'unauthorized'].includes(device.adb_state ?? '')) reasons.push(`ADB ${device.adb_state || '离线'}`);
    if (host && host.status !== 'ONLINE') reasons.push('节点离线');
    return { device, host, reasons, ready: reasons.length === 0 };
  });
  const byHost = new Map<string, { label: string; total: number; ready: number }>();
  rows.forEach(row => {
    const key = String(row.device.host_id ?? 'unassigned');
    const current = byHost.get(key) ?? { label: row.host?.name || row.host?.ip || (key === 'unassigned' ? '未分配节点' : key), total: 0, ready: 0 };
    current.total += 1;
    if (row.ready) current.ready += 1;
    byHost.set(key, current);
  });
  return {
    rows,
    byHost: Array.from(byHost.values()),
    readyCount: rows.filter(row => row.ready).length,
    blockedCount: rows.filter(row => !row.ready).length,
    blockedDeviceIds: rows.filter(row => !row.ready).map(row => row.device.id),
    warnings: [
      ...((new Set(devices.map(device => device.build_display_id).filter(Boolean))).size > 1 ? ['已选设备包含多个版本'] : []),
      ...((new Set(devices.map(device => device.model).filter(Boolean))).size > 1 ? ['已选设备包含多个型号'] : []),
      ...(devices.some(device => !device.build_display_id) ? ['部分设备缺少版本信息'] : []),
    ],
    passed: devices.length > 0 && rows.every(row => row.ready),
  };
}
