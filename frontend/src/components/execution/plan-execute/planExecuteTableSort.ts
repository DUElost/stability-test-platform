import type { ReadinessDevice } from '@/utils/planExecuteReadiness';
import type { HostLabelLookup } from './planExecuteSelection';
import { compareDevicesStable } from './planExecuteSelection';

export type DeviceSortKey = 'serial' | 'host' | 'model' | 'version';
export type SortDir = 'asc' | 'desc';

export const DEVICE_SORT_KEYS: DeviceSortKey[] = ['serial', 'host', 'model', 'version'];

export interface DeviceTableSort {
  key: DeviceSortKey;
  dir: SortDir;
}

/** URL query `sort=serial:asc` */
export function parseTableSortParam(raw: string | null | undefined): DeviceTableSort | null {
  if (!raw) return null;
  const [key, dir] = raw.split(':');
  if (!DEVICE_SORT_KEYS.includes(key as DeviceSortKey)) return null;
  if (dir !== 'asc' && dir !== 'desc') return null;
  return { key: key as DeviceSortKey, dir };
}

export function formatTableSortParam(sort: DeviceTableSort | null | undefined): string | null {
  if (!sort) return null;
  return `${sort.key}:${sort.dir}`;
}

function hostLabel(device: ReadinessDevice, hostMap: HostLabelLookup): string {
  const hostId = String(device.host_id ?? 'unassigned');
  const host = hostMap.get(hostId);
  return host?.ip || host?.name || (hostId === 'unassigned' ? '未分配节点' : hostId);
}

function compareByKey(
  a: ReadinessDevice,
  b: ReadinessDevice,
  key: DeviceSortKey,
  hostMap: HostLabelLookup,
): number {
  switch (key) {
    case 'serial':
      return a.serial.localeCompare(b.serial, undefined, { sensitivity: 'base' });
    case 'host':
      return hostLabel(a, hostMap).localeCompare(hostLabel(b, hostMap), undefined, {
        sensitivity: 'base',
        numeric: true,
      });
    case 'model':
      return (a.model || '').localeCompare(b.model || '', undefined, { sensitivity: 'base' });
    case 'version':
      return (a.build_display_id || '').localeCompare(b.build_display_id || '', undefined, {
        sensitivity: 'base',
        numeric: true,
      });
    default:
      return 0;
  }
}

export function sortDevicesByColumn(
  devices: ReadinessDevice[],
  sort: DeviceTableSort | null,
  hostMap: HostLabelLookup,
): ReadinessDevice[] {
  if (!sort) return [...devices].sort((a, b) => compareDevicesStable(a, b, hostMap));
  const dir = sort.dir === 'asc' ? 1 : -1;
  return [...devices].sort((a, b) => {
    const primary = compareByKey(a, b, sort.key, hostMap);
    if (primary !== 0) return primary * dir;
    return compareDevicesStable(a, b, hostMap);
  });
}

export function toggleDeviceTableSort(
  prev: DeviceTableSort | null,
  key: DeviceSortKey,
): DeviceTableSort {
  if (prev?.key === key) {
    return { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' };
  }
  return { key, dir: 'asc' };
}
