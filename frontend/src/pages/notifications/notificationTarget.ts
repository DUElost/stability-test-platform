import type { NotificationLog } from '@/utils/api/types';

/**
 * 通知日志 → 站内跳转目标。context 来源：
 * - context.link（#625 约定字段，任意 source 可携带——后端为 Alertmanager
 *   告警从 labels 主机标识/annotations.link 推导；须以 ``/`` 开头的站内
 *   路径，防外部 URL）——优先盲拼
 * - RUN_COMPLETED / RUN_FAILED / RISK_HIGH：context.run_id（PlanRun 主键，
 *   backend/services/plan_run_aggregation.py:78）
 * - DEVICE_OFFLINE：device_id/host_id（backend/api/routes/heartbeat.py:156，
 *   设备无详情页，落到设备列表）
 * context 缺字段或未知事件类型时返回 null（不渲染跳转入口）。
 */
export function notificationTarget(log: NotificationLog): { to: string; label: string } | null {
  const ctx = log.context ?? {};
  if (typeof ctx.link === 'string' && ctx.link.startsWith('/')) {
    return { to: ctx.link, label: '查看详情' };
  }
  const runId = Number(ctx.run_id);
  if (
    (log.event_type === 'RUN_COMPLETED' ||
      log.event_type === 'RUN_FAILED' ||
      log.event_type === 'RISK_HIGH') &&
    Number.isInteger(runId) &&
    runId > 0
  ) {
    return { to: `/execution/plan-runs/${runId}`, label: '查看执行记录' };
  }
  if (log.event_type === 'DEVICE_OFFLINE') {
    return { to: '/devices', label: '查看设备' };
  }
  return null;
}
