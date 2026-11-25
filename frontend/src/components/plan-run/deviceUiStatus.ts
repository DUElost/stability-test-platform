import type { DeviceLinkStatus, DeviceUiStatus, PlanRunMinimapDevice } from '@/utils/api/types';

/** B3-3（契约 §5.2）：支持真实并集筛选 `lost_or_backoff`（unknown OR backoff）与 `abnormal`（!= online）。 */
export type PlanRunDeviceStatusFilter = DeviceUiStatus | 'all' | 'lost_or_backoff';
export type PlanRunDeviceLinkFilter = DeviceLinkStatus | 'all' | 'abnormal';

export const UNASSIGNED_HOST_BUCKET = '__unassigned__';

export interface MinimapFilterCriteria {
  statusFilter: PlanRunDeviceStatusFilter;
  linkFilter: PlanRunDeviceLinkFilter;
  hostFilter: string | 'all';
  searchQuery?: string;
}

/**
 * B3-3（契约 §5.1 / §5.2）：Minimap 本地即时筛选纯函数（仅消费服务端已投影 7 字段），
 * 与后端表格分页接口保持完全相同的并集与无主机桶谓词。
 */
export function filterMinimapDevices(
  devices: readonly PlanRunMinimapDevice[],
  criteria: MinimapFilterCriteria,
): PlanRunMinimapDevice[] {
  const { statusFilter, linkFilter, hostFilter, searchQuery = '' } = criteria;
  const q = searchQuery.trim().toLowerCase();
  return devices.filter((d) => {
    if (statusFilter !== 'all') {
      if (statusFilter === 'lost_or_backoff') {
        if (d.job_exec_status !== 'unknown' && d.job_exec_status !== 'backoff') return false;
      } else if (d.job_exec_status !== statusFilter) {
        return false;
      }
    }
    if (linkFilter !== 'all') {
      if (linkFilter === 'abnormal') {
        if (d.device_link_status === 'online') return false;
      } else if (d.device_link_status !== linkFilter) {
        return false;
      }
    }
    if (hostFilter !== 'all') {
      const bucket = d.host_id ?? UNASSIGNED_HOST_BUCKET;
      if (bucket !== hostFilter) return false;
    }
    if (q) {
      const serial = (d.device_serial ?? '').toLowerCase();
      if (!serial.includes(q)) return false;
    }
    return true;
  });
}

/**
 * 设备 UI 状态的视觉单一事实源(minimap 方块 / 中文标签 / KPI 数字 tone)。
 *
 * 语义色板（与 index.css 变量对齐）:
 * - running  warning（进行中,带斜纹动画）
 * - completed success
 * - failed   destructive
 * - backoff  primary（退避重试中,与 unknown 区分）
 * - unknown  info（已断开）
 * - pending  muted（等待认领）
 */
export type DeviceUiTone =
  | 'warning'
  | 'success'
  | 'destructive'
  | 'primary'
  | 'info'
  | 'default';

export interface DeviceUiStatusStyle {
  label: string;
  /** minimap 方块背景(含 hover / 动画);running 的 dev-stripe keyframe 由 DeviceGrid 内联 <style> 提供。 */
  cellCls: string;
  tone: DeviceUiTone;
}

export const DEVICE_UI_STATUS: Record<DeviceUiStatus, DeviceUiStatusStyle> = {
  completed: {
    label: '完成',
    cellCls: 'bg-success/90 hover:bg-success',
    tone: 'success',
  },
  running: {
    label: '运行中',
    cellCls:
      'bg-warning bg-[linear-gradient(45deg,rgba(255,255,255,.35)_25%,transparent_25%,transparent_50%,rgba(255,255,255,.35)_50%,rgba(255,255,255,.35)_75%,transparent_75%)] bg-[length:8px_8px] [animation:dev-stripe_1s_linear_infinite]',
    tone: 'warning',
  },
  unknown: {
    label: '已断开',
    cellCls: 'bg-info/90 hover:bg-info',
    tone: 'info',
  },
  failed: {
    label: '失败',
    cellCls: 'bg-destructive/90 hover:bg-destructive',
    tone: 'destructive',
  },
  aborted: {
    label: '已中止',
    cellCls: 'bg-muted-foreground/60 hover:bg-muted-foreground/70',
    tone: 'default',
  },
  backoff: {
    label: '退避',
    cellCls: 'bg-primary/90 hover:bg-primary',
    tone: 'primary',
  },
  pending: {
    label: '等待',
    cellCls: 'bg-muted-foreground/30 hover:bg-muted-foreground/40',
    tone: 'default',
  },
};
