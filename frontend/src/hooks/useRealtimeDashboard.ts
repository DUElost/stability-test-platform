import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSocketIO as useWebSocket } from './useSocketIO';

interface DeviceUpdate {
  serial: string;
  status?: string;
  battery_level?: number;
  temperature?: number;
  model?: string;
  adb_state?: string;
  adb_connected?: boolean;
  wifi_rssi?: number;
  wifi_ssid?: string;
  cpu_usage?: number;
  mem_total?: number;
  mem_used?: number;
  disk_total?: number;
  disk_used?: number;
}

export interface RunStatusUpdate {
  run_id: number;
  task_id: number;
  status: string;
  progress?: number;
  message?: string;
  error_code?: string;
}

export interface TaskStatusUpdate {
  task_id: number;
  status: string | null;
}

export interface ReportReadyEvent {
  run_id: number;
  task_id: number;
}

interface WsMessage {
  type: 'DEVICE_UPDATE' | 'HEARTBEAT' | 'RUN_UPDATE' | 'TASK_UPDATE' | 'REPORT_READY' | 'PLAN_RUN_STATUS' | 'DEPLOY_UPDATE';
  payload: DeviceUpdate | RunStatusUpdate | TaskStatusUpdate | ReportReadyEvent | unknown;
}

const INVALIDATE_THROTTLE_MS = 2000;
let _lastInvalidateTime = 0;

export function useRealtimeDashboard(wsUrl: string) {
  const queryClient = useQueryClient();
  const { lastMessage, isConnected } = useWebSocket<WsMessage>(wsUrl);
  const [lastUpdateTime, setLastUpdateTime] = useState<Date>(new Date());

  useEffect(() => {
    if (!lastMessage) return;
    setLastUpdateTime(new Date());

    switch (lastMessage.type) {
      case 'DEVICE_UPDATE': {
        queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] });
        break;
      }
      case 'RUN_UPDATE': {
        const now = Date.now();
        if (now - _lastInvalidateTime > INVALIDATE_THROTTLE_MS) {
          _lastInvalidateTime = now;
          queryClient.invalidateQueries({ queryKey: ['tasks'] });
          queryClient.invalidateQueries({ queryKey: ['results'] });
          queryClient.invalidateQueries({ queryKey: ['results-summary'] });
        }
        break;
      }
      case 'TASK_UPDATE': {
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
        break;
      }
      case 'REPORT_READY': {
        queryClient.invalidateQueries({ queryKey: ['results'] });
        queryClient.invalidateQueries({ queryKey: ['results-summary'] });
        break;
      }
      case 'PLAN_RUN_STATUS': {
        queryClient.invalidateQueries({ queryKey: ['plan-runs-list'] });
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
        break;
      }
      case 'DEPLOY_UPDATE': {
        queryClient.invalidateQueries({ queryKey: ['deployments'] });
        break;
      }
      default:
        break;
    }
  }, [lastMessage, queryClient]);

  return {
    isConnected,
    lastUpdateTime,
    lastMessage,
  };
}
