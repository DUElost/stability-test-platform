import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSocketIO as useWebSocket } from './useSocketIO';
import { api } from '../utils/api';

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
  type: 'DEVICE_UPDATE' | 'HEARTBEAT' | 'RUN_UPDATE' | 'TASK_UPDATE' | 'REPORT_READY' | 'WORKFLOW_UPDATE' | 'DEPLOY_UPDATE';
  payload: DeviceUpdate | RunStatusUpdate | TaskStatusUpdate | ReportReadyEvent | unknown;
}

const UPDATE_BATCH_INTERVAL = 500;

// 防重刷节流：在短时间内忽略重复的失效请求
const INVALIDATE_THROTTLE_MS = 2000;
let _lastInvalidateTime = 0;

interface Device {
  id: number;
  serial: string;
  model: string | null;
  status: string;
  host_id: number | null;
  last_seen: string | null;
  tags: string[];
  extra: Record<string, unknown>;
  adb_state?: string;
  adb_connected?: boolean;
  battery_level?: number;
  battery_temp?: number;
  temperature?: number;
  wifi_rssi?: number;
  wifi_ssid?: string;
  cpu_usage?: number;
  mem_total?: number;
  mem_used?: number;
  disk_total?: number;
  disk_used?: number;
}

export function useRealtimeDashboard(wsUrl: string) {
  const queryClient = useQueryClient();
  const { lastMessage, isConnected } = useWebSocket<WsMessage>(wsUrl);
  const updateQueue = useRef<Map<string, DeviceUpdate>>(new Map());
  const [lastUpdateTime, setLastUpdateTime] = useState<Date>(new Date());

  const { data: devices, isError, isLoading } = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.devices.list(0, 200).then(res => res.data.items),
    refetchInterval: 10000,
  });

  useEffect(() => {
    if (!lastMessage) return;

    switch (lastMessage.type) {
      case 'DEVICE_UPDATE': {
        const update = lastMessage.payload as unknown as DeviceUpdate;
        if (update.serial) {
          updateQueue.current.set(update.serial, update);
        }
        break;
      }
      case 'RUN_UPDATE': {
        // 防抖：避免高频更新触发大量查询失效
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
      case 'WORKFLOW_UPDATE': {
        queryClient.invalidateQueries({ queryKey: ['workflows'] });
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

  useEffect(() => {
    const interval = setInterval(() => {
      if (updateQueue.current.size === 0) return;

      queryClient.setQueryData(['devices'], (oldData: Device[] | undefined) => {
        if (!oldData) return oldData;

        const updates = updateQueue.current;
        const newData = oldData.map(device => {
          if (updates.has(device.serial)) {
            const update = updates.get(device.serial)!;
            return { ...device, ...update };
          }
          return device;
        });

        updateQueue.current.clear();
        setLastUpdateTime(new Date());
        return newData;
      });
    }, UPDATE_BATCH_INTERVAL);

    return () => clearInterval(interval);
  }, [queryClient]);

  return { devices, isConnected, lastUpdateTime, isError, isLoading, lastMessage };
}
