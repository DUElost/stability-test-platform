import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSocketIO } from '@/hooks/useSocketIO';
import { DASHBOARD_SUBSCRIPTION } from '@/config';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';

/**
 * 多Worker 跨端一致性（#268 B2/B2c + #406）：
 * 1. 任一浏览器创建/更新/删除 Plan 后，服务端广播 plan_changed ——
 *    本端失效全部计划缓存（此前另一浏览器可陈旧 60s+）。
 * 2. 项目 facet / 归档 / 设备归属变更后广播 project_changed ——
 *    失效 projects / project / devices（ADR-0029 D8）。
 * 3. 后台 tab 恢复可见时全量失效缓存，让活跃查询立即重取
 *    （此前后台 tab 停更且不回追）。
 *
 * 挂载一次于 AppShell（全局常驻）。
 */
export function useCrossClientSync() {
  const qc = useQueryClient();

  useSocketIO(DASHBOARD_SUBSCRIPTION, {
    onMessage: (msg) => {
      if (msg.type === SOCKET_MESSAGE_TYPES.PLAN_CHANGED) {
        qc.invalidateQueries({ queryKey: ['plans'] });
        qc.invalidateQueries({ queryKey: ['plan'] });
      }
      if (msg.type === SOCKET_MESSAGE_TYPES.PROJECT_CHANGED) {
        qc.invalidateQueries({ queryKey: ['projects'] });
        qc.invalidateQueries({ queryKey: ['project'] });
        qc.invalidateQueries({ queryKey: ['devices'] });
        qc.invalidateQueries({ queryKey: ['project-devices'] });
        qc.invalidateQueries({ queryKey: ['projects-for-plan-editor'] });
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
