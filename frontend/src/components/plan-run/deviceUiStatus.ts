import type { DeviceUiStatus } from '@/utils/api/types';

/**
 * 设备 UI 状态的视觉单一事实源(minimap 方块 / 中文标签 / KPI 数字 tone)。
 *
 * 语义色板:
 * - running  橙(进行中,带斜纹动画)
 * - completed 绿
 * - failed   红
 * - risk     琥珀(需关注)
 * - backoff  靛蓝(退避重试中)—— 独立色,与 unknown 的紫区分
 * - unknown  紫(失联)
 * - pending  灰(等待认领)
 */
export type DeviceUiTone =
  | 'orange'
  | 'green'
  | 'red'
  | 'amber'
  | 'indigo'
  | 'purple'
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
    cellCls: 'bg-green-400/90 hover:bg-green-500',
    tone: 'green',
  },
  running: {
    label: '运行中',
    cellCls:
      'bg-orange-500 bg-[linear-gradient(45deg,rgba(255,255,255,.35)_25%,transparent_25%,transparent_50%,rgba(255,255,255,.35)_50%,rgba(255,255,255,.35)_75%,transparent_75%)] bg-[length:8px_8px] [animation:dev-stripe_1s_linear_infinite]',
    tone: 'orange',
  },
  unknown: {
    label: '失联',
    cellCls: 'bg-purple-500/90 hover:bg-purple-600',
    tone: 'purple',
  },
  failed: {
    label: '失败',
    cellCls: 'bg-red-500/90 hover:bg-red-600',
    tone: 'red',
  },
  risk: {
    label: '风险',
    cellCls: 'bg-amber-400/90 hover:bg-amber-500',
    tone: 'amber',
  },
  backoff: {
    label: '退避',
    // 独立靛蓝色,脱离 unknown 的紫,解决 minimap 撞色
    cellCls: 'bg-indigo-500/90 hover:bg-indigo-600',
    tone: 'indigo',
  },
  pending: {
    label: '等待',
    cellCls: 'bg-gray-300 hover:bg-gray-400',
    tone: 'default',
  },
};
