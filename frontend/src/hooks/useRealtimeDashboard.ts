import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSocketIO as useWebSocket } from './useSocketIO';
import { SOCKET_MESSAGE_TYPES, type SocketMessageType } from '@/utils/socketEvents';

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

export interface ReportReadyEvent {
  run_id: number;
  task_id: number;
}

interface WsMessage {
  type: SocketMessageType | 'HEARTBEAT';
  payload: DeviceUpdate | RunStatusUpdate | ReportReadyEvent | unknown;
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
      case SOCKET_MESSAGE_TYPES.DEVICE_UPDATE: {
        queryClient.invalidateQueries({ queryKey: ['dashboard-summary'] });
        break;
      }
      case SOCKET_MESSAGE_TYPES.RUN_UPDATE:
      case SOCKET_MESSAGE_TYPES.JOB_STATUS: {
        const now = Date.now();
        if (now - _lastInvalidateTime > INVALIDATE_THROTTLE_MS) {
          _lastInvalidateTime = now;
          queryClient.invalidateQueries({ queryKey: ['results'] });
          queryClient.invalidateQueries({ queryKey: ['results-summary'] });
        }
        break;
      }
      case SOCKET_MESSAGE_TYPES.REPORT_READY: {
        queryClient.invalidateQueries({ queryKey: ['results'] });
        queryClient.invalidateQueries({ queryKey: ['results-summary'] });
        break;
      }
      case SOCKET_MESSAGE_TYPES.PLAN_RUN_STATUS: {
        queryClient.invalidateQueries({ queryKey: ['plan-runs-list'] });
        break;
      }
      case SOCKET_MESSAGE_TYPES.DEPLOY_UPDATE: {
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
