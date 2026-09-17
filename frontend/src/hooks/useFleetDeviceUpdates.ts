import { useCallback, useEffect, useRef } from 'react';
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
  const trailingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (trailingTimer.current) clearTimeout(trailingTimer.current);
    },
    [],
  );

  const onMessage = useCallback(
    (msg: SocketIOMessage<unknown>) => {
      if (msg.type !== SOCKET_MESSAGE_TYPES.DEVICE_UPDATE) return;

      const invalidate = () => {
        lastInvalidateAt.current = Date.now();
        qc.invalidateQueries({ queryKey: deviceKeys.allLists() });
      };

      const elapsed = Date.now() - lastInvalidateAt.current;
      if (elapsed >= FLEET_DEVICE_UPDATE_THROTTLE_MS) {
        if (trailingTimer.current) {
          clearTimeout(trailingTimer.current);
          trailingTimer.current = null;
        }
        invalidate();
        return;
      }

      // #2447：窗口内的推送此前是**整条丢弃**（前缘节流、无尾部补偿）——设备页在
      // 事件密集时最长陈旧一个窗口（原靠 10s 轮询兜底，轮询一撤就长期陈旧）。
      // 改为「前缘立即 + 尾部补一次」：窗口内只登记一次尾单，窗口末统一失效；
      // 不做纯 debounce——持续密集推送会让失效永远推迟（饥饿）。
      if (trailingTimer.current) return;
      trailingTimer.current = setTimeout(() => {
        trailingTimer.current = null;
        invalidate();
      }, FLEET_DEVICE_UPDATE_THROTTLE_MS - elapsed);
    },
    [qc],
  );

  useSocketIO(FLEET_DEVICES_SUBSCRIPTION, {
    enabled,
    onMessage,
  });
}
