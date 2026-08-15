import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSocketIO } from '@/hooks/useSocketIO';
import { WS_DASHBOARD_ENDPOINT } from '@/config';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';

/**
 * 多Worker 跨端一致性（#268 B2/B2c）：
 * 1. 任一浏览器创建/更新/删除 Plan 后，服务端广播 plan_changed ——
 *    本端失效全部计划缓存（此前另一浏览器可陈旧 60s+）。
 * 2. 后台 tab 恢复可见时全量失效缓存，让活跃查询立即重取
 *    （此前后台 tab 停更且不回追）。
 *
 * 挂载一次于 AppShell（全局常驻）。
 */
export function useCrossClientSync() {
  const qc = useQueryClient();

  useSocketIO(WS_DASHBOARD_ENDPOINT, {
    onMessage: (msg) => {
      if (msg.type === SOCKET_MESSAGE_TYPES.PLAN_CHANGED) {
        qc.invalidateQueries({ queryKey: ['plans'] });
        qc.invalidateQueries({ queryKey: ['plan'] });
      }
    },
  });

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        qc.invalidateQueries();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [qc]);
}
