import { compareNodeEntries, type ReadinessDevice } from '@/utils/planExecuteReadiness';

export interface HostLabelLookup {
  get(hostId: string): { ip?: string | null; name?: string | null } | undefined;
}

export function compareDevicesStable(a: ReadinessDevice, b: ReadinessDevice, hostMap: HostLabelLookup): number {
  const aHostId = String(a.host_id ?? 'unassigned');
  const bHostId = String(b.host_id ?? 'unassigned');
  const aHost = hostMap.get(aHostId);
  const bHost = hostMap.get(bHostId);
  const aLabel = aHost?.ip || aHost?.name || (aHostId === 'unassigned' ? '未分配节点' : aHostId);
  const bLabel = bHost?.ip || bHost?.name || (bHostId === 'unassigned' ? '未分配节点' : bHostId);
  const hostCmp = compareNodeEntries({ id: aHostId, label: aLabel }, { id: bHostId, label: bLabel });
  if (hostCmp !== 0) return hostCmp;
  const serialCmp = a.serial.localeCompare(b.serial, undefined, { sensitivity: 'base' });
  if (serialCmp !== 0) return serialCmp;
  return a.id - b.id;
}

export function sortDevicesStable(devices: ReadinessDevice[], hostMap: HostLabelLookup): ReadinessDevice[] {
  return [...devices].sort((a, b) => compareDevicesStable(a, b, hostMap));
}

export function rangeSelectIds(ordered: ReadinessDevice[], fromIndex: number, toIndex: number): number[] {
  if (ordered.length === 0) return [];
  const lo = Math.max(0, Math.min(fromIndex, toIndex));
  const hi = Math.min(ordered.length - 1, Math.max(fromIndex, toIndex));
  return ordered.slice(lo, hi + 1).map((d) => d.id);
}
