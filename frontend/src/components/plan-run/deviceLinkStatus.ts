import type { DeviceLinkStatus } from '@/utils/api/types';

export interface DeviceLinkStatusStyle {
  label: string;
  hint?: string;
}

export const DEVICE_LINK_STATUS: Record<DeviceLinkStatus, DeviceLinkStatusStyle> = {
  online: {
    label: '在线',
    hint: 'ADB 可达，可执行 patrol',
  },
  offline: {
    label: '离线',
    hint: '设备不在 ADB 列表，请检查 USB 或重启设备',
  },
  adb_error: {
    label: 'ADB 异常',
    hint: 'ADB 状态异常（offline/unauthorized），需现场排查',
  },
  host_offline: {
    label: 'Host 离线',
    hint: '执行主机不可达，需恢复 Host 后再试',
  },
  unknown: {
    label: '未知',
    hint: '设备连接状态未知',
  },
};

export function isDeviceLinkReachable(
  status: DeviceLinkStatus | null | undefined,
): boolean {
  return status === 'online';
}
