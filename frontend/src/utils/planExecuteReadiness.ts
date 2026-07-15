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
