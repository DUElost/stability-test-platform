import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useWebSocket } from './useWebSocket';
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

interface WsMessage {
  type: 'DEVICE_UPDATE' | 'HEARTBEAT';
  payload: DeviceUpdate | unknown;
}

const UPDATE_BATCH_INTERVAL = 500;

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
    queryFn: () => api.devices.list().then(res => res.data),
    refetchInterval: 10000,
  });

  useEffect(() => {
    if (lastMessage?.type === 'DEVICE_UPDATE') {
      const update = lastMessage.payload as unknown;
      const deviceUpdate = update as DeviceUpdate;
      if (deviceUpdate.serial) {
        updateQueue.current.set(deviceUpdate.serial, deviceUpdate);
      }
    }
  }, [lastMessage]);

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

  return { devices, isConnected, lastUpdateTime, isError, isLoading };
}
