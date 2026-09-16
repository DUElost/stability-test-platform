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
/**
 * #2054：站内路径判定。`startsWith('/')` 会放行**协议相对 URL**——`//evil.example/x`
 * 与 `/\evil.example/x` 都以 `/` 开头，浏览器按 cross-origin 解析（React Router
 * 的 history 兜底会 `window.location.assign`，Ctrl/中键点击直接走 href），
 * 于是站内通知能把管理员带出应用。改为「单个前导斜杠且后面不是 `/` 或 `\`」。
 */
// #2288：在 #2054 的「单个前导斜杠」之上再拒绝 TAB/LF/CR —— WHATWG URL 解析会**移除**
// 输入里的这三个 ASCII 字符，于是 `/<TAB>/evil.com` 移除后等价于 `//evil.com`（协议相对
// → 跨源），而它第二个字符是 TAB，恰好通过只挡 `/` 与 `\` 的负向断言。会被移除的只有这
// 三个字符，且只有落在第二个位置才可能拼出 `//`，故拒绝集 = `/`、`\`、TAB、LF、CR。
const INTERNAL_LINK_RE = /^\/(?![/\\\t\n\r])/;

export function notificationTarget(log: NotificationLog): { to: string; label: string } | null {
  const ctx = log.context ?? {};
  if (typeof ctx.link === 'string' && INTERNAL_LINK_RE.test(ctx.link)) {
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
