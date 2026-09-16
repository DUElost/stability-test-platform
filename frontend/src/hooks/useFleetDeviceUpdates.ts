import { useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { FLEET_DEVICES_SUBSCRIPTION } from '@/config';
import { useSocketIO, type SocketIOMessage } from '@/hooks/useSocketIO';
import { deviceKeys } from '@/utils/api/queryKeys';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';

/** 设备列表在 DEVICE_UPDATE 下的合流窗口（#2369）。 */
export const FLEET_DEVICE_UPDATE_THROTTLE_MS = 2_000;

/**
 * 设备页订阅 `fleet:devices`：material DEVICE_UPDATE 合流后失效设备列表。
 * Dashboard / AppShell 不再收全局 DEVICE_UPDATE（#2324 / #2369）。
 */
export function useFleetDeviceUpdates(enabled = true) {
  const qc = useQueryClient();
  const lastInvalidateAt = useRef(0);

  const onMessage = useCallback(
    (msg: SocketIOMessage<unknown>) => {
      if (msg.type !== SOCKET_MESSAGE_TYPES.DEVICE_UPDATE) return;
      const now = Date.now();
      if (now - lastInvalidateAt.current < FLEET_DEVICE_UPDATE_THROTTLE_MS) return;
      lastInvalidateAt.current = now;
      qc.invalidateQueries({ queryKey: deviceKeys.allLists() });
    },
    [qc],
  );

  useSocketIO(FLEET_DEVICES_SUBSCRIPTION, {
    enabled,
    onMessage,
  });
}
