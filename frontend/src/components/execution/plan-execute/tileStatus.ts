import type { HostActiveJob } from '@/utils/api';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';
import type { DeviceTileStatus } from './types';

/**
 * #786：后端不产出 `schedulable`（DeviceOut 全仓零产出），准入判据以 status 为准。
 * ADR-0063 D7 第 6 条：选中 vbus 刷机计划时，派发页给关机候选打 `vbus_selectable`，也可勾选。
 */
export const isSchedulable = (device: ReadinessDevice) =>
  device.status === 'ONLINE' || device.vbus_selectable === true;

export function resolveDeviceTileStatus(
  device: ReadinessDevice,
  opts: { readinessReady?: boolean | null; occupancy?: HostActiveJob | null } = {},
): DeviceTileStatus {
  if (!isSchedulable(device) && device.status === 'OFFLINE') return 'offline';
  if (!isSchedulable(device) && device.status !== 'BUSY') return 'offline';
  if (opts.occupancy || device.status === 'BUSY') return 'busy';
  if (opts.readinessReady === false) return 'blocked';
  if (!isSchedulable(device)) return 'offline';
  return 'ready';
}
